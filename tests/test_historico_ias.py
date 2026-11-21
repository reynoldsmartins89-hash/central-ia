import json
import tempfile
import unittest
from pathlib import Path

from superagente import historico_ias


class TestHistoricoIAs(unittest.TestCase):
    def test_takeout_do_notebooklm_vira_caderno_com_chats_e_indice(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            arq = Path(tmp) / "takeout.zip"
            with zipfile.ZipFile(arq, "w") as z:
                b = "Takeout/NotebookLM/Patrística/"
                z.writestr(b + "Patrística metadata.json",
                           json.dumps({"title": "Patrística", "metadata": {"createTime": "2026-10-01T10:00:00Z"}}))
                z.writestr(b + "Sources/Confissões.pdf.html", "<p>livro</p>")
                z.writestr(b + "Chat History/Chat Session - 1.html",
                           "MODEL: <p>Resposta sobre <b>Agostinho</b></p>\nUSER: <p>Quem foi Agostinho?</p>\n")
                z.writestr(b + "Notes/Resumo.html", "<h2>Resumo</h2><p>Texto da nota</p>")
                z.writestr(b + "Artifacts/Audio.wav", b"RIFF")
                z.writestr(b + "Artifacts/Slide_1", bytes([0x89, 0x50, 0xff, 0xfe, 0x00]))
            raiz = Path(tmp) / "cofre"
            historico_ias.importar([arq], raiz, log=lambda *_: None)
            indice = (raiz / "NotebookLM" / "Patrística.md").read_text(encoding="utf-8")
            chat = next((raiz / "NotebookLM" / "Patrística").glob("Chat - *.md")).read_text(encoding="utf-8")
            nota = (raiz / "NotebookLM" / "Patrística" / "Nota - Resumo.md").read_text(encoding="utf-8")
        self.assertIn("Confissões.pdf", indice)
        self.assertIn("Audio.wav", indice)
        self.assertIn("Slide_1", indice)
        self.assertLess(chat.index("Quem foi Agostinho?"), chat.index("**Agostinho**"))
        self.assertIn("Texto da nota", nota)

    def test_takeout_do_gemini_vira_uma_nota_por_dia(self):
        atividades = [
            {"header": "Gemini Apps", "title": "Prompted Segunda pergunta", "time": "2026-04-07T15:00:00Z",
             "safeHtmlItem": [{"html": "<p><strong>Resposta 2</strong></p>"}]},
            {"header": "Gemini Apps", "title": "Prompted Primeira pergunta", "time": "2026-04-07T14:00:00Z",
             "attachedFiles": ["foto.jpg"], "safeHtmlItem": [{"html": "<p>Resposta 1</p>"}]},
            {"header": "Gemini Apps", "title": "Cleared Gemini Apps activity", "time": "2026-04-07T16:00:00Z"}]
        with tempfile.TemporaryDirectory() as tmp:
            arq = Path(tmp) / "Minhaatividade.json"
            arq.write_text(json.dumps(atividades), encoding="utf-8")
            raiz = Path(tmp) / "cofre"
            historico_ias.importar([arq], raiz, log=lambda *_: None)
            notas = list((raiz / "Gemini").glob("*.md"))
            self.assertEqual(len(notas), 1)
            nota = notas[0].read_text(encoding="utf-8")
        self.assertIn("Primeira pergunta (+1)", notas[0].name)
        self.assertLess(nota.index("Resposta 1"), nota.index("Segunda pergunta"))
        self.assertIn("**Resposta 2**", nota)
        self.assertIn("foto.jpg", nota)
        self.assertNotIn("Cleared", nota)

    def test_qwen_grok_e_perplexity_viram_notas(self):
        qwen = {"success": True, "data": [{"title": "Vida como Empresa", "created_at": 1792139802, "chat": {"history": {
            "currentId": "b", "messages": {
                "a": {"id": "a", "role": "user", "content": "Pergunta Q", "parentId": None},
                "b": {"id": "b", "role": "assistant", "content": "", "parentId": "a", "content_list": [
                    {"phase": "thinking_summary", "content": "rascunho"}, {"phase": "answer", "content": "Resposta Q"}]}}}}}]}
        grok = {"conversations": [{"conversation": {"title": "Currículo", "create_time": "2026-07-21T10:00:00Z",
                                                    "leaf_response_id": "2"},
                                   "responses": [{"response": {"_id": "1", "sender": "human", "message": "Pergunta G"}},
                                                 {"response": {"_id": "2", "sender": "assistant", "message": "Resposta G",
                                                               "parent_response_id": "1"}}]}]}
        pplx = {"conversations": [{"context_uuid": "u", "context_title": "Concorrentes", "created_at": "2026-11-07T12:00:00Z",
                                   "entries": [{"query": "Pergunta P", "answer": "Resposta P"}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            arquivos = []
            for nome, dados in (("qwen.json", qwen), ("grok.json", grok), ("pplx.json", pplx)):
                (Path(tmp) / nome).write_text(json.dumps(dados), encoding="utf-8")
                arquivos.append(Path(tmp) / nome)
            raiz = Path(tmp) / "cofre"
            historico_ias.importar(arquivos, raiz, log=lambda *_: None)
            q = next((raiz / "Qwen").glob("* - Vida como Empresa.md")).read_text(encoding="utf-8")
            g = (raiz / "Grok" / "2026-07-21 - Currículo.md").read_text(encoding="utf-8")
            p = (raiz / "Perplexity" / "2026-11-07 - Concorrentes.md").read_text(encoding="utf-8")
        self.assertIn("Resposta Q", q)
        self.assertNotIn("rascunho", q)
        self.assertLess(g.index("Pergunta G"), g.index("Resposta G"))
        self.assertIn("Resposta P", p)

    def test_export_do_deepseek_segue_o_ramo_e_ignora_o_pensamento(self):
        conversa = {"id": "x", "title": "Pilares da vida", "current_message_id": 4, "inserted_at": "2026-10-13T17:20:18Z",
                    "messages": [
                        {"id": 1, "parent_id": None, "role": "USER", "fragments": [{"type": "REQUEST", "content": "Quais os pilares?"}]},
                        {"id": 2, "parent_id": 1, "role": "ASSISTANT", "fragments": [{"type": "THINK", "content": "rascunho"},
                                                                                     {"type": "RESPONSE", "content": "Resposta velha"}]},
                        {"id": 4, "parent_id": 1, "role": "ASSISTANT", "fragments": [{"type": "THINK", "content": "rascunho"},
                                                                                     {"type": "TOOL_SEARCH", "content": ""},
                                                                                     {"type": "RESPONSE", "content": "São quatro."}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            arq = Path(tmp) / "chat_sessions" / "x.json"
            arq.parent.mkdir()
            arq.write_text(json.dumps(conversa), encoding="utf-8")
            raiz = Path(tmp) / "cofre"
            historico_ias.importar([arq.parent], raiz, log=lambda *_: None)
            nota = (raiz / "DeepSeek" / "2026-10-13 - Pilares da vida.md").read_text(encoding="utf-8")
        self.assertIn("Quais os pilares?", nota)
        self.assertIn("São quatro.", nota)
        self.assertNotIn("Resposta velha", nota)
        self.assertNotIn("rascunho", nota)

    def test_export_do_chatgpt_vira_uma_nota_por_conversa_sem_sobrescrever(self):
        export = [{
            "title": "Plano de estudos", "create_time": 1733169164.7, "current_node": "c",
            "mapping": {
                "a": {"message": None, "parent": None},
                "b": {"parent": "a", "message": {"author": {"role": "user"},
                                                 "content": {"content_type": "text", "parts": ["Monte um plano"]}}},
                "x": {"parent": "b", "message": {"author": {"role": "assistant"},   # ramo abandonado
                                                 "content": {"content_type": "text", "parts": ["versão antiga"]}}},
                "c": {"parent": "b", "message": {"author": {"role": "assistant"}, "content": {
                    "content_type": "multimodal_text", "parts": [{"content_type": "audio_transcription",
                                                                  "text": "Aqui está o plano"}]}}},
            }}, {"title": "Vazia", "create_time": 1, "current_node": None, "mapping": {}}]
        claude = [{"name": "Resumo", "created_at": "2026-10-01T10:00:00Z", "chat_messages": [
            {"sender": "human", "text": "Resuma"}, {"sender": "assistant", "text": "Resumo feito"}]}]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "exp").mkdir()
            (tmp / "exp" / "conversations.json").write_text(json.dumps(export), encoding="utf-8")
            (tmp / "claude.json").write_text(json.dumps(claude), encoding="utf-8")
            raiz = tmp / "cofre"
            total = historico_ias.importar([tmp / "exp", tmp / "claude.json"], raiz=raiz, log=lambda *_: None)
            self.assertEqual((total["criadas"], total["vazias"]), (2, 1))
            nota = next((raiz / "ChatGPT").glob("*.md"))
            texto = nota.read_text(encoding="utf-8")
            self.assertIn("Monte um plano", texto)
            self.assertIn("Aqui está o plano", texto)
            self.assertNotIn("versão antiga", texto)
            self.assertIn("Resumo feito", next((raiz / "Claude").glob("*.md")).read_text(encoding="utf-8"))
            self.assertIn("[[ChatGPT/", (raiz / "Histórico das IAs.md").read_text(encoding="utf-8"))

            nota.write_text("minha edição no Obsidian", encoding="utf-8")
            total = historico_ias.importar([tmp / "exp"], raiz=raiz, log=lambda *_: None)
            self.assertEqual(total["criadas"], 0)
            self.assertEqual(nota.read_text(encoding="utf-8"), "minha edição no Obsidian")

    def test_export_novo_do_claude_traz_projetos_e_artefatos(self):
        projeto = {"uuid": "p1", "name": "Renda online", "description": "Gerar renda", "is_private": True,
                   "prompt_template": "Seja direto", "created_at": "2026-09-01T10:00:00Z",
                   "docs": [{"filename": "plano.md", "content": "Passo 1"}]}
        artefato = {"id": "a1", "kind": "page", "active_version": "v2", "versions": [
            {"id": "v2", "title": "Guia de carreira", "created_at": "2026-11-07T12:00:00+00:00"},
            {"id": "v1", "title": "Guia de carreira", "created_at": "2026-11-05T01:00:00+00:00"}]}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "projects").mkdir()
            (tmp / "projects" / "p1.json").write_text(json.dumps(projeto), encoding="utf-8")
            pasta = tmp / "frames" / "artifacts" / "a1"
            pasta.mkdir(parents=True)
            (pasta / "artifact.json").write_text(json.dumps(artefato), encoding="utf-8")
            (pasta / "page.md").write_text("Texto do guia", encoding="utf-8")
            (pasta / "comments.json").write_text("[]", encoding="utf-8")
            raiz = tmp / "cofre"
            total = historico_ias.importar([tmp], raiz=raiz, log=lambda *_: None)
            self.assertEqual(total["criadas"], 2)
            proj = (raiz / "Claude" / "Projetos" / "2026-09-01 - Renda online.md").read_text(encoding="utf-8")
            for trecho in ("Gerar renda", "Seja direto", "plano.md", "Passo 1"):
                self.assertIn(trecho, proj)
            art = (raiz / "Claude" / "Artefatos" / "2026-11-05 - Guia de carreira.md").read_text(encoding="utf-8")
            self.assertIn("Texto do guia", art)
            self.assertIn("[[Claude/Projetos/2026-09-01 - Renda online|", (raiz / "Histórico das IAs.md").read_text(encoding="utf-8"))
            self.assertEqual(historico_ias.importar([tmp], raiz=raiz, log=lambda *_: None)["criadas"], 0)


if __name__ == "__main__":
    unittest.main()
