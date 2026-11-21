"""Testa a clonagem com um 'disco' falso que tem setores ruins."""
import tempfile
import unittest
from pathlib import Path

from ferramentas.resgate_hd import clonar


class DiscoFalso:
    def __init__(self, dados: bytes, ruins: set[int], setor: int = 512):
        self.dados, self.ruins, self.setor = dados, ruins, setor

    def ler(self, pos: int, qtd: int) -> bytes:
        for s in range(pos // self.setor, -(-(pos + qtd) // self.setor)):
            if s in self.ruins:
                raise OSError("erro de CRC simulado")
        return self.dados[pos:pos + qtd]


class TestResgateHD(unittest.TestCase):
    def test_copia_tudo_menos_setores_ruins(self):
        dados = bytes(range(256)) * 64  # 16 KiB
        disco = DiscoFalso(dados, ruins={5, 20})
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "hd.img"
            mapa = clonar(disco.ler, len(dados), img, bloco=4096, salvar_a_cada=1,
                          log=lambda _m: None)
            copia = img.read_bytes()
        self.assertEqual(sorted(mapa.setores_ruins), [5 * 512, 20 * 512])
        for i in range(len(dados) // 512):
            trecho = copia[i * 512:(i + 1) * 512]
            esperado = bytes(512) if i in (5, 20) else dados[i * 512:(i + 1) * 512]
            self.assertEqual(trecho, esperado, f"setor {i}")

    def test_area_ruim_grande_e_pulada_sem_perder_o_resto(self):
        setor, bloco = 512, 4096
        dados = bytes(i % 251 for i in range(2 * 1024 * 1024))  # 2 MiB
        ruins = set(range(1000, 1400))  # ~200 KiB ruins no meio
        disco = DiscoFalso(dados, ruins)
        leituras_ruins = {"n": 0}

        def ler(pos, qtd):
            try:
                return disco.ler(pos, qtd)
            except OSError:
                leituras_ruins["n"] += 1
                raise

        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "hd.img"
            mapa = clonar(ler, len(dados), img, bloco=bloco, salvar_a_cada=1,
                          log=lambda _m: None, salto_min=8 * bloco, salto_max=64 * bloco)
            copia = img.read_bytes()
        self.assertEqual(len(copia), len(dados))
        perdidos = {p // setor for p in mapa.setores_ruins}
        self.assertTrue(ruins <= perdidos)
        for s in range(len(dados) // setor):  # tudo que não foi dado como perdido está certo
            if s not in perdidos:
                self.assertEqual(copia[s * setor:(s + 1) * setor], dados[s * setor:(s + 1) * setor])
        # sem insistir: ~1 tentativa por bloco ruim + poucas por setor na 3ª passada
        self.assertLess(leituras_ruins["n"], 600)
        self.assertLess(len(perdidos), 2 * len(ruins))  # não jogou fora muita coisa boa

    def test_copia_so_as_areas_pedidas(self):
        dados = bytes(i % 253 for i in range(64 * 1024))
        lidos = []

        def ler(pos, qtd):
            lidos.append(pos)
            return dados[pos:pos + qtd]

        areas = [(4096, 12288), (40960, 49152)]
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "hd.img"
            clonar(ler, len(dados), img, bloco=4096, log=lambda _m: None, areas=areas)
            copia = img.read_bytes()
        self.assertEqual(len(copia), len(dados))
        self.assertTrue(all(any(i <= p < f for i, f in areas) for p in lidos))
        for i, f in areas:
            self.assertEqual(copia[i:f], dados[i:f])
        self.assertEqual(copia[:4096], bytes(4096))  # fora das áreas: zeros

    def test_regiao_lenta_fica_para_depois_mas_e_copiada(self):
        import time as _t
        dados = bytes(i % 241 for i in range(256 * 1024))
        ordem = []

        def ler(pos, qtd):
            ordem.append(pos)
            if 64 * 1024 <= pos < 96 * 1024:
                _t.sleep(0.03)  # região fraca: lê, mas devagar
            return dados[pos:pos + qtd]

        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "hd.img"
            mapa = clonar(ler, len(dados), img, bloco=4096, log=lambda _m: None,
                          salto_min=8 * 4096, salto_max=32 * 4096, lento_s=0.01)
            self.assertEqual(img.read_bytes(), dados)  # nada perdido
        self.assertEqual(mapa.setores_ruins, [])
        # o fim do disco foi lido antes de terminar a região lenta
        ultimo_bom = max(i for i, p in enumerate(ordem) if p >= 128 * 1024)
        self.assertTrue(any(64 * 1024 <= p < 96 * 1024 for p in ordem[ultimo_bom:]))

    def test_segunda_passada_le_o_menos_lento_antes_do_pior(self):
        import time as _t
        kb = 1024
        dados = bytes(i % 239 for i in range(256 * kb))
        ordem = []
        pior, medio = (32 * kb, 96 * kb), (160 * kb, 224 * kb)

        def ler(pos, qtd):
            ordem.append(pos)
            if pior[0] <= pos < pior[1]:
                _t.sleep(0.06)  # lento até para a 1ª rodada da 2ª passada
            elif medio[0] <= pos < medio[1]:
                _t.sleep(0.02)  # lento só para a 1ª passada
            return dados[pos:pos + qtd]

        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "hd.img"
            mapa = clonar(ler, len(dados), img, bloco=4 * kb, log=lambda _m: None,
                          salto_min=8 * 4 * kb, salto_max=16 * 4 * kb, lento_s=0.01,
                          rodadas=(4,))
            self.assertEqual(img.read_bytes(), dados)  # no fim, nada perdido
        self.assertEqual(mapa.rodada, 1)
        self.assertEqual(mapa.pulados + mapa.pulados_prox, [])
        # a região média terminou de ser lida antes da pior
        ult_medio = max(i for i, p in enumerate(ordem) if medio[0] <= p < medio[1])
        ult_pior = max(i for i, p in enumerate(ordem) if pior[0] <= p < pior[1])
        self.assertLess(ult_medio, ult_pior)

    def test_retoma_de_onde_parou(self):
        dados = bytes(range(256)) * 64
        chamadas = {"n": 0}

        def ler_e_cair(pos, qtd):
            chamadas["n"] += 1
            if chamadas["n"] == 3:
                raise KeyboardInterrupt
            return dados[pos:pos + qtd]

        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "hd.img"
            with self.assertRaises(KeyboardInterrupt):
                clonar(ler_e_cair, len(dados), img, bloco=4096, salvar_a_cada=1,
                       log=lambda _m: None)
            lidos = []
            clonar(lambda p, q: (lidos.append(p), dados[p:p + q])[1], len(dados), img,
                   bloco=4096, salvar_a_cada=1, log=lambda _m: None)
            self.assertEqual(img.read_bytes(), dados)
            self.assertNotIn(0, lidos)  # não releu o começo


if __name__ == "__main__":
    unittest.main()
