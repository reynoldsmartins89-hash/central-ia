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

    def test_duplicados_e_integridade(self):
        import tempfile
        from pathlib import Path
        from pathlib import Path
        from superagente import curador, duplicados, integridade
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp) / "hd"
            (raiz / "a").mkdir(parents=True); (raiz / "b").mkdir()
            conteudo = b"conteudo identico " * 100_000
            (raiz / "a" / "aula.mp4").write_bytes(conteudo)
            (raiz / "b" / "copia com outro nome.mp4").write_bytes(conteudo)
            (raiz / "b" / "diferente.mp4").write_bytes(b"x" * len(conteudo))
            (raiz / "quebrado.docx").write_bytes(b"isto nao e um docx")
            (raiz / "baixando.crdownload").write_bytes(b"...")
            curador.PASTA = Path(tmp) / "estado"
            curador.CATALOGO = curador.PASTA / "catalogo.db"
            curador.REGISTRO_MOVIMENTOS = curador.PASTA / "mov.jsonl"
            duplicados.REGISTRO_MOVIMENTOS = curador.REGISTRO_MOVIMENTOS
            curador.catalogar(raiz, log=lambda *_: None)
            self.assertEqual(duplicados.encontrar([raiz], tamanho_minimo=1, log=lambda *_: None)["grupos"], 1)
            self.assertEqual(duplicados.separar(raiz, log=lambda *_: None), 1)   # só a cópia sai
            quarentena = list((raiz / duplicados.PASTA_QUARENTENA).rglob("*.mp4"))
            self.assertEqual(len(quarentena), 1)
            self.assertEqual(curador.desfazer(log=lambda *_: None), 1)
            self.assertEqual(integridade.verificar(raiz / "quebrado.docx"), "pacote (zip/Office) corrompido")
            self.assertEqual(integridade.verificar(raiz / "baixando.crdownload"), "download/cópia incompleto")
            self.assertIn("vídeo", integridade.verificar(raiz / "b" / "diferente.mp4"))

    def test_leitor_le_documento_e_alimenta_o_acervo(self):
        import tempfile
        from pathlib import Path
        from pathlib import Path
        from superagente import curador, leitor
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp) / "hd"
            raiz.mkdir()
            (raiz / "aula sem nome claro.md").write_text(
                "A batalha de Lepanto em 1571 reuniu a Liga Santa contra o Império Otomano.", encoding="utf-8")
            curador.PASTA = Path(tmp) / "estado"
            curador.CATALOGO = curador.PASTA / "catalogo.db"
            leitor.PASTA_LEITURAS = Path(tmp) / "leituras"
            self.assertEqual(leitor.tipo_de("x.mp4"), "audio/video")
            self.assertEqual(leitor.tipo_de("foto.jpg"), "imagem")
            self.assertIsNone(leitor.tipo_de("programa.dll"))
            curador.catalogar(raiz, log=lambda *_: None)
            self.assertEqual(leitor.processar(log=lambda *_: None)["lidos"], 1)
            self.assertEqual(len(list(leitor.PASTA_LEITURAS.glob("*.md"))), 1)   # ficha .md criada
            self.assertIn("Lepanto", leitor.buscar("batalha de Lepanto")[0]["texto"])

    def test_idioma_dos_trechos(self):
        from superagente.leitor import idioma_do_texto, eh_primeiro_volume, tipo_de
        self.assertEqual(idioma_do_texto("Where are you from? I'm from Brazil."), "en")
        self.assertEqual(idioma_do_texto("Então você é do Brasil, que legal."), "pt")
        self.assertEqual(idioma_do_texto("我是巴西人"), "zh")
        self.assertEqual(idioma_do_texto("こんにちは"), "ja")
        self.assertTrue(eh_primeiro_volume("curso.part1.rar"))
        self.assertFalse(eh_primeiro_volume("curso.part2.rar"))
        self.assertTrue(eh_primeiro_volume("latim.7z.001"))
        self.assertIsNone(tipo_de("latim.7z.002"))
        self.assertEqual(tipo_de("aulas.rar"), "compactado")

    def test_leitor_abre_compactado(self):
        import tempfile, zipfile
        from pathlib import Path
        from superagente import curador, leitor
        if not leitor._sete_zip():
            self.skipTest("7-Zip não instalado")
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp) / "hd"
            raiz.mkdir()
            with zipfile.ZipFile(raiz / "curso.zip", "w") as z:
                z.writestr("modulo1/aula.md", "Declinações latinas: rosa, rosae, rosae, rosam, rosa.")
            curador.PASTA = Path(tmp) / "estado"
            curador.CATALOGO = curador.PASTA / "catalogo.db"
            leitor.PASTA_LEITURAS = Path(tmp) / "leituras"
            curador.catalogar(raiz, log=lambda *_: None)
            leitor.processar(log=lambda *_: None)
            achado = leitor.buscar("declinações latinas rosae")
            self.assertTrue(achado and "::modulo1/aula.md" in achado[0]["caminho"])
            self.assertFalse((raiz / "_central_ia_temp").exists() and any((raiz / "_central_ia_temp").iterdir()))

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


class TestReparadorEVigia(unittest.TestCase):
    def test_tipo_real_pelo_conteudo(self):
        from superagente.reparador import tipo_real
        self.assertEqual(tipo_real(b"\x00\x00\x00 ftypisom" + b"\x00" * 20), ".mp4")
        self.assertEqual(tipo_real(b"%PDF-1.7"), ".pdf")
        self.assertEqual(tipo_real(b"Rar!\x1a\x07"), ".rar")
        self.assertIsNone(tipo_real(b"\x137lixo aleatorio"))

    def test_diagnostico_sem_tocar_no_original(self):
        import tempfile
        from pathlib import Path
        from superagente.reparador import diagnosticar
        with tempfile.TemporaryDirectory() as d:
            zeros = Path(d) / "curso.rar"
            zeros.write_bytes(b"\x00" * 200_000)
            self.assertEqual(diagnosticar(zeros)[0], "perdido")
            video = Path(d) / "aula.rar"
            video.write_bytes(b"\x00\x00\x00 ftypisom" + b"\x01" * 5000)
            self.assertEqual(diagnosticar(video)[0], "tipo_errado")
            self.assertEqual(diagnosticar(Path(d) / "~$doc.docx")[0], "falso_alarme")
            self.assertEqual(zeros.read_bytes(), b"\x00" * 200_000)

    def test_vigia_reconhece_processo_vivo(self):
        import os
        from superagente.vigia import processo_vivo
        self.assertTrue(processo_vivo(os.getpid()))
        self.assertFalse(processo_vivo(-1))

    def test_drive_caminho_local_troca_dois_pontos(self):
        from superagente.drive import caminho_local
        self.assertNotIn(":", str(caminho_local("EBOOKS/Maçonaria: História/x.pdf"))[2:])



class TestExecutor(unittest.TestCase):
    def test_reconhece_ordens_de_acao(self):
        from superagente.executor import parece_acao
        self.assertTrue(parece_acao("Mova os PDFs da área de trabalho para Documentos"))
        self.assertTrue(parece_acao("repare os arquivos corrompidos do HD"))
        self.assertFalse(parece_acao("Escreva um artigo sobre geopolítica do petróleo"))

    def test_plano_sem_acao(self):
        from superagente.executor import sem_acao
        self.assertTrue(sem_acao("Isso é só uma pergunta.\nSEM AÇÃO"))
        self.assertFalse(sem_acao("1. Mover arquivos"))

    def test_regras_de_seguranca_presentes(self):
        from superagente.executor import REGRAS
        for regra in ("NUNCA apague definitivamente", "senhas", "NUNCA envie mensagens"):
            self.assertIn(regra, REGRAS)
