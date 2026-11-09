"""Testes offline (usam o provedor falso 'eco'). Rodar: python -m unittest -v"""
import shutil
import unittest

from superagente import habilidades, provedores
from superagente.agentes import carregar_equipe
from superagente.orquestrador import Superagente, _extrair_json


class TestCentral(unittest.TestCase):
    def test_habilidades_carregam(self):
        nomes = {h.nome for h in habilidades.listar()}
        self.assertIn("superagente", nomes)
        self.assertIn("artigo-mae", nomes)

    def test_equipe_aponta_para_habilidades_existentes(self):
        mestre, equipe = carregar_equipe()
        for agente in [mestre, *equipe.values()]:
            habilidades.carregar(agente.habilidade)

    def test_reserva_pula_modelo_que_falha(self):
        modelo, resposta = provedores.chamar_com_reserva(
            ["inexistente:x", "eco:teste"], "sistema", "olá", log=lambda *_: None)
        self.assertEqual(modelo, "eco:teste")
        self.assertIn("olá", resposta)

    def test_extrair_json_de_resposta_com_texto(self):
        texto = 'Aqui está:\n```json\n[{"agente": "redator", "tarefa": "x"}]\n```'
        self.assertEqual(_extrair_json(texto)[0]["agente"], "redator")

    def test_fluxo_completo_offline(self):
        sa = Superagente(forcar_modelo="eco:teste", log=lambda *_: None)
        pasta = sa.fluxo("producao-editorial", "teste automatizado")
        try:
            self.assertTrue((pasta / "registro.json").exists())
            self.assertTrue((pasta / "07-roteirista.md").exists())
            self.assertTrue((pasta / "08-sintese-final.md").exists())  # 7 agentes ≥ 4 → síntese
            self.assertTrue((pasta / "RESULTADO-COMPLETO.md").exists())
        finally:
            shutil.rmtree(pasta)

    def test_lista_de_imagens_do_diretor_de_arte(self):
        from superagente.imagens import extrair_lista
        texto = 'Conceito...\n```json\n[{"canal": "blog", "nome": "blog-capa", "prompt_en": "x"}]\n```'
        self.assertEqual(extrair_lista(texto)[0]["nome"], "blog-capa")
        self.assertEqual(extrair_lista("sem json"), [])

    def test_carrossel_monta_laminas(self):
        import tempfile
        from pathlib import Path
        from superagente import carrossel
        texto = ('```json\n[{"lamina": 1, "titulo": "IA para pequenas empresas", "texto": ""},'
                 ' {"lamina": 2, "titulo": "Comece pelo atendimento", "texto": "Respostas automáticas no WhatsApp."}]\n```')
        with tempfile.TemporaryDirectory() as tmp:
            laminas = carrossel.montar(texto, Path(tmp), log=lambda *_: None)
            self.assertEqual(len(laminas), 2)
            from PIL import Image
            self.assertEqual(Image.open(laminas[0]).size, (1080, 1350))

    def test_main_e_bot_compilam(self):
        import importlib
        importlib.import_module("main")
        importlib.import_module("superagente.bot")

    def test_biblioteca_indexa_e_busca(self):
        import tempfile
        from pathlib import Path
        from superagente import conhecimento
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "aula1.md").write_text(
                "A geopolítica do Ártico envolve rotas marítimas e recursos naturais disputados.\n" * 3,
                encoding="utf-8")
            conhecimento.PASTA_INDICES = Path(tmp) / "indices"
            conhecimento.indexar("teste", tmp, log=lambda *_: None)
            trechos = conhecimento.buscar("teste", "Explique as rotas do Ártico")
            self.assertEqual(trechos[0]["caminho"], "aula1.md")

    def test_plano_mostra_o_porque(self):
        from superagente.orquestrador import formatar_plano
        texto = formatar_plano([{"agente": "marketing_digital", "tarefa": "x", "porque": "tem material sobre funis"}])
        self.assertIn("↳ tem material sobre funis", texto)
        self.assertEqual(len(texto.splitlines()), 3)

    def test_contexto_divide_espaco_entre_muitos_agentes(self):
        from superagente.orquestrador import montar_contexto
        resultados = [(f"agente{i}", "x" * 20_000) for i in range(10)]
        ctx = montar_contexto("pedido", resultados, limite=30_000)
        for i in range(10):  # nenhum agente some, mesmo com 10 resultados grandes
            self.assertIn(f"agente{i}", ctx)
        self.assertLess(len(ctx), 40_000)

    def test_curador_move_so_com_proposta_e_desfaz(self):
        import tempfile
        from pathlib import Path
        from superagente import curador
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp) / "drive"
            (raiz / "bagunca").mkdir(parents=True)
            arquivo = raiz / "bagunca" / "historia_de_roma.pdf"
            arquivo.write_text("x")
            curador.PASTA = Path(tmp) / "estado"
            curador.CATALOGO = curador.PASTA / "catalogo.db"
            curador.REGISTRO_MOVIMENTOS = curador.PASTA / "mov.jsonl"
            (raiz / "QGIS" / "apps").mkdir(parents=True)
            (raiz / "QGIS" / "apps" / "qgis.exe").write_text("x")
            self.assertEqual(curador.catalogar(raiz, log=lambda *_: None), 2)
            self.assertEqual(curador.identificar_colecoes(raiz, log=lambda *_: None), 2)
            con = curador._conectar_colecoes()
            protegida = dict(con.execute("SELECT caminho, protegida FROM colecoes").fetchall())
            self.assertEqual(protegida[str(raiz / "QGIS")], 1)   # programa: nunca será movido
            con.execute("UPDATE colecoes SET tema='08. HISTÓRIA & BIOGRAFIAS', confianca=0.9, "
                        "status='classificada'")              # simula a IA (inclusive na pasta protegida)
            con.commit(); con.close()
            self.assertEqual(curador.propor(raiz), 1)          # só a pasta comum vira proposta
            self.assertTrue(arquivo.exists())                  # propor não move nada
            self.assertEqual(curador.aplicar(raiz, log=lambda *_: None), 1)
            self.assertFalse(arquivo.exists())
            self.assertTrue((raiz / "QGIS" / "apps" / "qgis.exe").exists())  # programa intacto
            movido = raiz / "ORGANIZADO PELO CURADOR" / "08. HISTÓRIA & BIOGRAFIAS" / "Geral" / "bagunca" / "historia_de_roma.pdf"
            self.assertTrue(movido.exists())                   # a PASTA inteira foi junto
            self.assertEqual(curador.desfazer(log=lambda *_: None), 1)
            self.assertTrue(arquivo.exists())                  # voltou ao lugar original

    def test_combinacoes_existem(self):
        from superagente.agentes import carregar_combinacoes
        combos = carregar_combinacoes()
        self.assertIn("comunicacao", combos["conteudo_digital"]["agentes"])
        self.assertIn("marketing_digital", combos["conteudo_digital"]["agentes"])

    def test_superagente_sem_plano_valido_usa_generalista(self):
        sa = Superagente(forcar_modelo="eco:teste", log=lambda *_: None)
        self.assertEqual(sa.planejar("qualquer coisa")[0]["agente"], "generalista")


if __name__ == "__main__":
    unittest.main()
