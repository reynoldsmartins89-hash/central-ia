"""Testa as partes do extrator NTFS com registros montados à mão."""
import struct
import unittest

from ferramentas.extrair_ntfs import Pendencias, corrigir_fixup, decodificar_runlist, ler_registro


def registro(numero_pai=5, nome="foto.jpg", dados=None, runs=b"", tamanho=0, em_uso=True):
    rec = bytearray(1024)
    rec[0:4] = b"FILE"
    struct.pack_into("<HH", rec, 4, 0x30, 3)       # update sequence: 3 entradas em 0x30
    struct.pack_into("<H", rec, 0x30, 0xABCD)        # marca
    struct.pack_into("<HH", rec, 0x32, 0x1111, 0x2222)  # valores originais do fim de cada setor
    struct.pack_into("<H", rec, 0x14, 0x38)
    struct.pack_into("<H", rec, 0x16, 1 if em_uso else 0)
    pos = 0x38
    # $FILE_NAME residente
    nome_b = nome.encode("utf-16-le")
    valor = bytearray(66) + nome_b
    struct.pack_into("<Q", valor, 0, numero_pai)
    valor[64], valor[65] = len(nome), 1
    compr = (24 + len(valor) + 7) // 8 * 8
    struct.pack_into("<IIBBHHHIH", rec, pos, 0x30, compr, 0, 0, 0, 0, 0, len(valor), 24)
    rec[pos + 24:pos + 24 + len(valor)] = valor
    pos += compr
    if dados is not None:  # $DATA residente
        compr = (24 + len(dados) + 7) // 8 * 8
        struct.pack_into("<IIBBHHHIH", rec, pos, 0x80, compr, 0, 0, 0, 0, 0, len(dados), 24)
        rec[pos + 24:pos + 24 + len(dados)] = dados
        pos += compr
    else:  # $DATA não residente
        compr = (64 + len(runs) + 1 + 7) // 8 * 8
        struct.pack_into("<IIBB", rec, pos, 0x80, compr, 1, 0)
        struct.pack_into("<QQH", rec, pos + 16, 0, 0, 64)
        struct.pack_into("<QQQ", rec, pos + 40, tamanho, tamanho, tamanho)
        rec[pos + 64:pos + 64 + len(runs)] = runs
        pos += compr
    struct.pack_into("<I", rec, pos, 0xFFFFFFFF)
    # aplica a marca no fim de cada setor (como o NTFS grava no disco)
    rec[510:512] = rec[1022:1024] = struct.pack("<H", 0xABCD)
    return bytes(rec)


class TestExtrairNTFS(unittest.TestCase):
    def test_runlist_com_salto_negativo_e_esparso(self):
        # 3 clusters em 100; 2 clusters em 100-10=90; 4 esparsos
        runs = bytes([0x11, 3, 100, 0x11, 2, 0xF6, 0x01, 4, 0])
        self.assertEqual(decodificar_runlist(runs, 0), [(100, 3), (90, 2), (None, 4)])

    def test_fixup_restaura_e_detecta_corrupcao(self):
        rec = bytearray(registro(dados=b"oi"))
        self.assertTrue(corrigir_fixup(bytearray(rec)))
        rec[1023] ^= 0xFF
        self.assertFalse(corrigir_fixup(rec))

    def test_le_nome_pai_e_dados_residentes(self):
        r = ler_registro(30, registro(numero_pai=40, nome="carta.txt", dados=b"ola mundo"))
        self.assertEqual(r.nomes[0][0], 40)
        self.assertEqual(r.nomes[0][2], "carta.txt")
        self.assertEqual(r.residente, b"ola mundo")
        self.assertTrue(r.em_uso)

    def test_le_dados_nao_residentes(self):
        r = ler_registro(31, registro(runs=bytes([0x21, 5, 0x34, 0x12]), tamanho=9000))
        self.assertIsNone(r.residente)
        self.assertEqual(r.tamanho, 9000)
        self.assertEqual(r.pedacos, [(0, [(0x1234, 5)])])

    def test_registro_sem_assinatura_e_ignorado(self):
        self.assertIsNone(ler_registro(1, bytes(1024)))

    def test_pendencias_do_mapa(self):
        mib = 1024 * 1024
        mapa = {"tamanho": 100 * mib, "posicao": 100 * mib, "pulados": [[10 * mib, 20 * mib]],
                "pulados_prox": [], "blocos_ruins": [50 * mib, 60 * mib],
                "passada3_feita": [60 * mib], "setores_ruins": [60 * mib + 512]}
        p = Pendencias(mapa, 100 * mib)
        self.assertTrue(p.pendente(15 * mib, 15 * mib + 10))
        self.assertTrue(p.pendente(50 * mib + 5, 50 * mib + 6))   # bloco ruim ainda não refeito
        self.assertFalse(p.pendente(60 * mib, 61 * mib))          # refeito: não pendente…
        self.assertTrue(p.perdido(60 * mib, 61 * mib))            # …mas tem setor perdido
        self.assertFalse(p.pendente(30 * mib, 40 * mib))
        self.assertTrue(p.pendente(100 * mib, 100 * mib + 1))     # além do fim da imagem


if __name__ == "__main__":
    unittest.main()
