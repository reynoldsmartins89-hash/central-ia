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

    def test_idiomas_primeiro_e_copia_para_o_drive(self):
        import tempfile
        from pathlib import Path
        from superagente import curador, idiomas, leitor
        with tempfile.TemporaryDirectory() as tmp:
            hd, drive = Path(tmp) / "IDIOMAS", Path(tmp) / "Drive" / "Idiomas"
            (hd / "LATIM" / "curso").mkdir(parents=True)
            (Path(tmp) / "outros").mkdir()
            drive.parent.mkdir()                                     # "Drive montado"
            (Path(tmp) / "outros" / "a.md").write_text("Curto.", encoding="utf-8")   # menor, mas não é idioma
            (hd / "LATIM" / "curso" / "aula 1.md").write_text("Declinações latinas: rosa, rosae, rosam.",
                                                              encoding="utf-8")
            curador.PASTA = Path(tmp) / "estado"
            curador.CATALOGO = curador.PASTA / "catalogo.db"
            leitor.PASTA_LEITURAS = Path(tmp) / "leituras"
            idiomas.HD, idiomas.DRIVE = hd, drive
            self.assertEqual(idiomas.destino(str(hd / "LATIM" / "x.rar") + "::m1/aula.mp3"),
                             drive / "Latim" / "x.rar" / "m1" / "aula.mp3.md")
            self.assertIsNone(idiomas.destino(str(Path(tmp) / "outros" / "a.md")))
            curador.catalogar(Path(tmp), log=lambda *_: None)
            leitor.processar(limite=1, log=lambda *_: None)          # só 1 arquivo: tem de ser o de idioma
            self.assertEqual(idiomas.espelhar(log=lambda *_: None)["copiadas"], 1)
            md = drive / "Latim" / "curso" / "aula 1.md.md"
            self.assertIn("rosae", md.read_text(encoding="utf-8"))
            self.assertEqual(idiomas.espelhar(log=lambda *_: None)["copiadas"], 0)   # retomável
            self.assertIn("| Latim | 1 | 1 |", (drive / "_Progresso.md").read_text(encoding="utf-8"))

            # lembrete ao ligar o PC: só retoma com o SIM; quando acaba tudo, remove a si mesmo
            idiomas.LEMBRETE = Path(tmp) / "Startup" / "lembrete.bat"
            idiomas.LEMBRETE.parent.mkdir()
            idiomas.LEMBRETE.write_text("x")
            (hd / "LATIM" / "curso" / "aula 2.md").write_text("Segunda declinação: dominus, domini.",
                                                              encoding="utf-8")
            curador.catalogar(Path(tmp), log=lambda *_: None)
            ligou, avisos = [], []
            self.assertEqual(idiomas.lembrete(perguntar=lambda t: False, ligar=lambda: ligou.append(1),
                                              avisar=avisos.append), "pausado")
            self.assertFalse(ligou)
            self.assertIn("Faltam 1 arquivos", avisos[0])
            self.assertEqual(idiomas.lembrete(perguntar=lambda t: True, ligar=lambda: ligou.append(1),
                                              avisar=avisos.append), "retomado")
            self.assertEqual(ligou, [1])
            leitor.processar(log=lambda *_: None, primeiro=[str(hd / "LATIM")])
            self.assertEqual(idiomas.lembrete(perguntar=lambda t: True, ligar=lambda: ligou.append(1),
                                              avisar=avisos.append), "concluido")
            self.assertFalse(idiomas.LEMBRETE.exists())

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


class TestOrdensComPcDesligado(unittest.TestCase):
    def test_atende_ordens_recentes_e_ignora_velhas_e_estranhos(self):
        import time as _t
        from unittest import mock
        from superagente import bot
        agora = _t.time()
        updates = {"result": [
            {"update_id": 10, "message": {"chat": {"id": 1}, "text": "velha", "date": agora - 80 * 3600}},
            {"update_id": 11, "message": {"chat": {"id": 999}, "text": "estranho", "date": agora}},
            {"update_id": 12, "message": {"chat": {"id": 1}, "text": "/pc feche o Obsidian", "date": agora - 600}},
        ]}
        enviados = []
        with mock.patch.object(bot.telegram, "_api", return_value=updates), \
                mock.patch.object(bot.telegram, "enviar", side_effect=lambda t, *a: enviados.append(t)):
            self.assertEqual(bot._pendentes_ao_ligar("1"), 12)
        self.assertIn("feche o Obsidian", enviados[0])
        self.assertNotIn("estranho", enviados[0])

    def test_sem_pendentes_comeca_do_fim(self):
        from unittest import mock
        from superagente import bot
        with mock.patch.object(bot.telegram, "_api", return_value={"result": [{"update_id": 5, "message": {}}]}), \
                mock.patch.object(bot.telegram, "enviar"):
            self.assertEqual(bot._pendentes_ao_ligar("1"), 6)


class TestExclusaoSegura(unittest.TestCase):
    def test_so_apaga_copia_identica_com_original_presente(self):
        import tempfile
        from pathlib import Path
        from unittest import mock
        from superagente import duplicados
        with tempfile.TemporaryDirectory() as d:
            original, copia, sozinha = Path(d) / "o.bin", Path(d) / "c.bin", Path(d) / "s.bin"
            original.write_bytes(b"x" * 5000)
            copia.write_bytes(b"x" * 5000)
            sozinha.write_bytes(b"y" * 5000)
            grupo = duplicados._completo(str(original))

            class Con:
                def __init__(self, existe):
                    self.existe = existe

                def execute(self, *_):
                    return mock.Mock(fetchall=lambda: [(str(original) if self.existe else str(Path(d) / "x"),)])

            r, _, _ = duplicados._conferir_um(Con(True), "antiga", grupo, {"antiga": str(copia)})
            self.assertEqual(r, "identico")
            r, _, _ = duplicados._conferir_um(Con(False), "antiga", grupo, {"antiga": str(copia)})
            self.assertEqual(r, "sem_original")
            r, _, _ = duplicados._conferir_um(Con(True), "antiga", grupo, {"antiga": str(sozinha)})
            self.assertEqual(r, "divergente")


class TestCaixaPostal(unittest.TestCase):
    """Servidor guarda as mensagens; o PC tem prioridade; a nuvem só atende com o PC desligado."""

    def setUp(self):
        import json
        import tempfile
        import time
        from pathlib import Path
        from superagente import caixa_postal
        self.cp = caixa_postal
        self.pasta = tempfile.mkdtemp()
        self.banco_original = caixa_postal.BANCO
        caixa_postal.BANCO = Path(self.pasta) / "caixa.db"
        con = caixa_postal._banco()
        for uid in (1, 2):
            con.execute("INSERT INTO mensagens (update_id, dados, data) VALUES (?, ?, ?)",
                        (uid, json.dumps({"update_id": uid, "message": {"text": f"m{uid}"}}), time.time()))
        con.commit()
        con.close()

    def tearDown(self):
        self.cp.BANCO = self.banco_original
        shutil.rmtree(self.pasta, ignore_errors=True)

    def test_nuvem_atende_com_pc_desligado_e_guarda_o_que_e_do_pc(self):
        entregues = self.cp.pegar("nuvem", espera=0)
        self.assertEqual([u["update_id"] for u in entregues], [1, 2])
        self.assertEqual(self.cp.pegar("nuvem", espera=0), [])        # cada mensagem uma vez só
        self.cp.guardar_para_o_pc(2)                                  # precisa do PC
        self.assertEqual(self.cp.pegar("nuvem", espera=0), [])        # a nuvem não pega de volta
        self.assertEqual([u["update_id"] for u in self.cp.pegar("pc", espera=0)], [2])

    def test_pc_ligado_tem_prioridade(self):
        self.assertEqual(len(self.cp.pegar("pc", espera=0)), 2)
        import json
        import time
        con = self.cp._banco()
        con.execute("INSERT INTO mensagens (update_id, dados, data) VALUES (3, ?, ?)",
                    (json.dumps({"update_id": 3}), time.time()))
        con.commit()
        con.close()
        self.assertTrue(self.cp.pc_online())
        self.assertEqual(self.cp.pegar("nuvem", espera=0), [])        # PC ligado: nuvem não atende


class TestConfirmacao(unittest.TestCase):
    def test_sim_vira_plano_com_botoes_e_pergunta_nao(self):
        from superagente.bot import eh_confirmacao
        for t in ("✅", "sim", "Sim, pode fazer.", "ok execute", "pode", "aprovado"):
            self.assertTrue(eh_confirmacao(t), t)
        for t in ("não", "vai chover amanhã?", "simples pergunta: o que é COF?",
                  "sim, mas antes me explique melhor o terceiro item da lista"):
            self.assertFalse(eh_confirmacao(t), t)


class TestGrupos(unittest.TestCase):
    ANDROID = ("09/11/2026 18:30 - Mensagens e ligações são protegidas com a criptografia de ponta a ponta.\n"
               "09/11/2026 18:31 - Ana Souza: Bom dia, pessoal\n"
               "09/11/2026 18:32 - Bruno: Reunião sexta às 19h\n"
               "segunda linha da mensagem\n"
               "09/11/2026 18:33 - Ana Souza: <Mídia oculta>\n"
               "10/11/26 07:05 - Carla entrou usando o link de convite deste grupo\n"
               "10/11/26 07:06 - Carla: Link: https://exemplo.com/a\n")
    IOS = ("[09/11/2026, 18:31:02] Ana Souza: Olá\n"
           "‎[09/11/2026, 18:31:40] Bruno: ‎imagem ocultada\n"
           "[09/11/2026, 18:32:10] Bruno: Prova dia 20\n")

    def setUp(self):
        import tempfile
        from pathlib import Path
        from superagente import grupos
        self.g = grupos
        self.pasta = Path(tempfile.mkdtemp())
        self.banco = grupos.BANCO
        grupos.BANCO = self.pasta / "grupos.db"

    def tearDown(self):
        self.g.BANCO = self.banco
        shutil.rmtree(self.pasta, ignore_errors=True)

    def test_le_whatsapp_android_e_iphone(self):
        msgs = self.g.ler_whatsapp(self.ANDROID)
        self.assertEqual([a for _, a, _ in msgs], ["Ana Souza", "Bruno", "Carla"])
        self.assertIn("segunda linha", msgs[1][2])            # mensagem com várias linhas
        self.assertEqual(msgs[2][2], "Link: https://exemplo.com/a")
        ios = self.g.ler_whatsapp(self.IOS)
        self.assertEqual([t for _, _, t in ios], ["Olá", "Prova dia 20"])

    def test_importar_duas_vezes_nao_duplica_e_le_zip(self):
        import zipfile
        arquivo = self.pasta / "Conversa do WhatsApp com Turma COF.txt"
        arquivo.write_text(self.ANDROID, encoding="utf-8")
        self.assertEqual(self.g.importar(arquivo), ("Turma COF", "whatsapp", 3, 3))
        zipado = self.pasta / "20261109-190000_WhatsApp Chat with Turma COF.zip"
        with zipfile.ZipFile(zipado, "w") as z:
            z.writestr("WhatsApp Chat with Turma COF.txt", self.ANDROID + "11/11/2026 08:00 - Bruno: Novidade\n")
        self.assertEqual(self.g.importar(zipado), ("Turma COF", "whatsapp", 1, 4))   # só a nova entra
        self.assertEqual(self.g.achar_grupo("turma cof")[1], "Turma COF")
        self.assertTrue(self.g.eh_exportacao(zipado.name))
        self.assertFalse(self.g.eh_exportacao("contrato.pdf"))

    def test_reconhece_conversa_pelo_conteudo(self):
        renomeado = self.pasta / "arquivo qualquer.txt"
        renomeado.write_text(self.ANDROID * 3, encoding="utf-8")
        self.assertTrue(self.g.parece_conversa(renomeado))
        comum = self.pasta / "anotacoes.txt"
        comum.write_text("Lista de compras\npão\nleite\n09/11/2026 reunião\n", encoding="utf-8")
        self.assertFalse(self.g.parece_conversa(comum))

    def test_telegram_json_e_partes(self):
        dados = {"name": "Grupo X", "messages": [
            {"id": 1, "type": "message", "date": "2026-11-09T18:00:00", "from": "Ana", "text": "oi"},
            {"id": 2, "type": "service", "date": "2026-11-09T18:01:00", "text": ""},
            {"id": 3, "type": "message", "date": "2026-11-09T18:02:00", "from": "Bia",
             "text": ["veja ", {"type": "link", "text": "https://x.org"}]}]}
        msgs = self.g.ler_telegram_json(dados)
        self.assertEqual([(a, t, i) for _, a, t, i in msgs], [("Ana", "oi", 1), ("Bia", "veja https://x.org", 3)])
        partes = self.g._trechos("\n".join(["x" * 100] * 30), tamanho=1000)
        self.assertEqual(len(partes), 4)
        self.assertEqual(sum(p.count("\n") + 1 for p in partes), 30)   # nenhuma linha perdida


class TestConversaNatural(unittest.TestCase):
    def test_markdown_vira_formatacao_do_telegram(self):
        from superagente.telegram import formatar_html
        html = formatar_html("### Título\n| A | B |\n|---|---|\n| **x** | y < z |\n- item\n[site](https://x.org)")
        self.assertNotIn("#", html)
        self.assertNotIn("|", html)
        self.assertNotIn("**", html)
        self.assertIn("<b>Título</b>", html)
        self.assertIn("• <b>x</b> — y &lt; z", html)
        self.assertIn('<a href="https://x.org">site</a>', html)


class TestMemoriaUnida(unittest.TestCase):
    def test_pc_e_nuvem_juntam_sem_perder_falas(self):
        from superagente import memoria
        pc = {"inicio": 10, "atualizado": 20, "falas": [
            {"quem": "voce", "texto": "pedido no PC", "quando": "09/11 18:32", "ts": 100}]}
        nuvem = {"inicio": 15, "atualizado": 30, "falas": [
            {"quem": "voce", "texto": "pedido na nuvem", "quando": "09/11 18:36", "ts": 200},
            {"quem": "voce", "texto": "pedido no PC", "quando": "09/11 18:32", "ts": 100}]}
        unido = memoria.juntar_conversas(pc, nuvem)
        self.assertEqual([f["texto"] for f in unido["falas"]], ["pedido no PC", "pedido na nuvem"])
        self.assertEqual((unido["inicio"], unido["atualizado"]), (10, 30))
        self.assertEqual(memoria.juntar_perfis(["a"], ["a", "b"]), ["a", "b"])


class TestAcervoCompleto(unittest.TestCase):
    def test_texto_sem_quebras_vira_trechos_de_tamanho_bom(self):
        import sqlite3
        from superagente import leitor
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE acervo USING fts5(texto, titulo UNINDEXED, caminho UNINDEXED, tipo UNINDEXED)")
        leitor._indexar(con, "livro.pdf", "documento", "palavra " * 20000)   # 160 mil caracteres numa linha só
        tamanhos = [len(t) for (t,) in con.execute("SELECT texto FROM acervo")]
        self.assertGreater(len(tamanhos), 90)
        self.assertLessEqual(max(tamanhos), 1900)
        self.assertEqual(sum(t.count("palavra") for (t,) in con.execute("SELECT texto FROM acervo")), 20000)


class TestRoteador(unittest.TestCase):
    def test_custo_beneficio(self):
        from superagente.roteador_api import profundidade
        self.assertEqual(profundidade("bom dia"), "rapido")
        self.assertEqual(profundidade("quanto é 15% de 300?"), "rapido")
        self.assertEqual(profundidade("explique a doutrina do preço justo em São Tomás e compare com Salamanca"),
                         "profundo")


class TestGerenciadorEmails(unittest.TestCase):
    """Sem rede: um servidor IMAP falso confere que nada é apagado e que ler não marca como lido."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from superagente import emails
        self.emails = emails
        self.tmp = tempfile.mkdtemp()
        self._orig = (emails.BANCO, emails.PASTA_SAIDA, emails.conectar)
        emails.BANCO = Path(self.tmp) / "emails.db"
        emails.PASTA_SAIDA = Path(self.tmp) / "saida"

    def tearDown(self):
        self.emails.BANCO, self.emails.PASTA_SAIDA, self.emails.conectar = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_nome_de_pasta_com_acento_ida_e_volta(self):
        e = self.emails
        self.assertEqual(e.utf7_codificar("Promoções"), "Promo&AOcA9Q-es")
        for nome in ("Promoções", "A & B", "Central/10 Promoções", "Notificações", "日本"):
            self.assertEqual(e.utf7_decodificar(e.utf7_codificar(nome)), nome)

    def test_le_linha_list(self):
        flags, delim, nome = self.emails.analisar_list(rb'(\HasNoChildren) "/" "Faculdade/USP 2019"')
        self.assertEqual((delim, nome), ("/", "Faculdade/USP 2019"))
        self.assertIn(r"\Noselect", self.emails.analisar_list(rb'(\Noselect) "/" "[Gmail]"')[0])

    def test_regras_de_reserva(self):
        r = self.emails.classificar_por_regras
        self.assertEqual(r({"de_email": "secretaria@funib.edu.br", "assunto": "Notas"})[0], "faculdade")
        self.assertEqual(r({"de_email": "loja@x.com", "assunto": "50% OFF só hoje",
                            "sinais": "descadastrar"})[0], "promocoes")
        self.assertEqual(r({"de_email": "fulano@gmail.com", "assunto": "Oi"})[0], "revisar")
        self.assertNotEqual(r({"de_email": "a@b.com", "assunto": "Conta suspensa"})[0], "faculdade")

    def test_caixa_de_entrada_guarda_so_o_que_pede_voce(self):
        d = self.emails.destino
        base = {"pasta": "INBOX", "confianca": 0.9, "acao": 0}
        self.assertEqual(d({**base, "categoria": "promocoes"}), "10 Promoções")
        self.assertEqual(d({**base, "categoria": "importante"}), "")
        self.assertEqual(d({**base, "categoria": "faculdade", "acao": 1}), "")
        self.assertEqual(d({**base, "categoria": "promocoes", "confianca": 0.5}), "")

    def test_ler_classificar_mover_e_desfazer_sem_apagar(self):
        e = self.emails
        servidor = _ImapFalso()
        e.conectar = lambda conta: servidor
        conta = {"id": "YAHOO", "rotulo": "Yahoo", "endereco": "x@y", "senha": "s", "servidor": "-"}
        self.assertEqual(e.ler(conta, log=lambda *_: None), 3)
        self.assertTrue(all("PEEK" in c for c in servidor.fetches))
        self.assertEqual(e.classificar(conta, usar_ia=False, log=lambda *_: None), 3)
        movidos = e.aplicar(conta, log=lambda *_: None)
        self.assertEqual(movidos, 2)  # promoção + faculdade; o e-mail pessoal fica
        self.assertEqual(sorted(servidor.caixas), ["Central", "Central/02 Faculdade", "Central/10 Promo&AOcA9Q-es",
                                                   "INBOX"])
        self.assertEqual(sum(len(v) for v in servidor.caixas.values()), 3)  # nada sumiu
        self.assertFalse(servidor.apagou)
        self.assertTrue(e.painel(abrir=False).exists())
        self.assertEqual(e.desfazer(conta, log=lambda *_: None), 2)
        self.assertEqual(len(servidor.caixas["INBOX"]), 3)


def _mensagem(remetente, assunto, extra=""):
    return (f"From: {remetente}\r\nSubject: {assunto}\r\nMessage-ID: <{abs(hash(assunto))}@t>\r\n"
            f"Date: Tue, 10 Nov 2026 10:00:00 -0300\r\n{extra}Content-Type: text/plain; charset=utf-8\r\n\r\n"
            f"Corpo de {assunto}").encode()


class _ImapFalso:
    def __init__(self):
        self.caixas = {"INBOX": {1: _mensagem("Loja <oferta@loja.com>", "Cupom 30% OFF",
                                              "List-Unsubscribe: <mailto:sair@loja.com>\r\n"),
                                 2: _mensagem("FUNIB <secretaria@funib.edu.br>", "Calendário de provas"),
                                 3: _mensagem("Maria <maria@gmail.com>", "Almoço domingo?")}}
        self.atual, self.fetches, self.apagou, self.proximo = "INBOX", [], False, 100

    def _nome(self, n):
        return n.strip('"').replace('\\"', '"')

    def select(self, nome, readonly=False):
        self.atual = self._nome(nome)
        return ("OK", [b"1"]) if self.atual in self.caixas else ("NO", [b""])

    def response(self, codigo):
        return codigo, [b"7"]

    def capability(self):
        return "OK", [b"IMAP4rev1 MOVE UIDPLUS"]

    def list(self):
        return "OK", [rf'(\HasNoChildren) "/" "{n}"'.encode() for n in self.caixas]

    def create(self, nome):
        self.caixas.setdefault(self._nome(nome), {})
        return "OK", [b""]

    def subscribe(self, nome):
        return "OK", [b""]

    def logout(self):
        pass

    def uid(self, comando, *args):
        caixa = self.caixas[self.atual]
        if comando == "SEARCH":
            if args[1] == "ALL":
                return "OK", [" ".join(map(str, caixa)).encode()]
            alvo = self._nome(args[3])
            return "OK", [" ".join(str(u) for u, m in caixa.items() if alvo.encode() in m).encode()]
        if comando == "FETCH":
            self.fetches.append(args[1])
            dados = []
            for u in map(int, args[0].split(",")):
                dados += [(f"{u} (UID {u} BODY[]<0> {{{len(caixa[u])}}}".encode(), caixa[u]), b" FLAGS ())"]
            return "OK", dados
        if comando == "MOVE":
            destino = self._nome(args[1])
            for u in map(int, args[0].split(",")):
                self.caixas[destino][self.proximo] = caixa.pop(u)
                self.proximo += 1
            return "OK", [b""]
        if comando in ("STORE", "EXPUNGE"):
            self.apagou = True
        return "NO", [b""]


class TestAprovacaoTardia(unittest.TestCase):
    """Cliques em ✅ depois do prazo não podem ser ignorados em silêncio."""

    def setUp(self):
        import tempfile
        from superagente import telegram
        self.tg = telegram
        self.tmp = tempfile.mkdtemp()
        self.original = telegram.ARQUIVO_EXPIRADOS
        telegram.ARQUIVO_EXPIRADOS = f"{self.tmp}/planos_expirados.json"

    def tearDown(self):
        self.tg.ARQUIVO_EXPIRADOS = self.original
        self.tg.APROVACAO_ANTECIPADA = False
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plano_expirado_e_retomado_uma_vez(self):
        self.tg.guardar_expirado("123", "crie três agentes")
        self.assertEqual(self.tg.retomar_expirado("123"), "crie três agentes")
        self.assertEqual(self.tg.retomar_expirado("123"), "")

    def test_aprovacao_antecipada_passa_direto(self):
        from unittest import mock
        self.tg.APROVACAO_ANTECIPADA = True
        with mock.patch.object(self.tg, "enviar") as enviar, mock.patch.object(self.tg, "atualizacoes") as att:
            self.assertTrue(self.tg.pedir_aprovacao("plano"))
            att.assert_not_called()
            enviar.assert_called_once()
        self.assertFalse(self.tg.APROVACAO_ANTECIPADA)

    def test_clique_tardio_refaz_o_pedido(self):
        from unittest import mock
        from superagente import bot
        self.tg.guardar_expirado("999", "pedido antigo")
        cb = {"id": "x", "data": "sim:999", "message": {"chat": {"id": 42}}}
        with mock.patch.object(self.tg, "_api"), mock.patch.object(self.tg, "enviar"), \
                mock.patch.object(bot, "tratar") as tratar:
            bot._clique_tardio(cb, "42")
            tratar.assert_called_once_with("pedido antigo")

    def test_clique_tardio_sem_plano_avisa(self):
        from unittest import mock
        from superagente import bot
        cb = {"id": "x", "data": "sim:000", "message": {"chat": {"id": 42}}}
        with mock.patch.object(self.tg, "_api"), mock.patch.object(self.tg, "enviar") as enviar, \
                mock.patch.object(bot, "tratar") as tratar:
            bot._clique_tardio(cb, "42")
            tratar.assert_not_called()
            self.assertIn("Mande o pedido de novo", enviar.call_args[0][0])


class TestExportarCuradoria(unittest.TestCase):
    def test_exporta_aprovados_em_md_sem_duplicar(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from unittest import mock
        from superagente import curadoria
        tmp = Path(tempfile.mkdtemp())
        try:
            banco = tmp / "lider.db"
            con = sqlite3.connect(banco)
            con.execute("CREATE TABLE trechos (texto, titulo, caminho)")
            con.executemany("INSERT INTO trechos VALUES (?, ?, ?)", [
                ("Liderar é servir.", "Liderança: servir", "D:/a.pdf"),
                ("Segunda parte.", "Liderança: servir", "D:/a.pdf"),
                ("Liderar é servir.", "Cópia", "drive:DUPLICADOS/a.pdf"),
                ("Comando e controle.", "Comando", "drive:EBOOKS/c.pdf")])
            con.commit()
            con.close()
            with mock.patch("superagente.conhecimento._caminho_indice", return_value=banco):
                n = curadoria.exportar_md("lider", str(tmp / "saida"), log=lambda *_: None)
            self.assertEqual(n, 3)   # o duplicado só tem o 1º trecho igual, mas o corpo inteiro difere
            nomes = sorted(p.name for p in (tmp / "saida").glob("*.md"))
            self.assertIn("Liderança servir.md", nomes)
            texto = (tmp / "saida" / "Liderança servir.md").read_text(encoding="utf-8")
            self.assertIn("Segunda parte.", texto)
            self.assertIn("Origem: `D:/a.pdf`", texto)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
