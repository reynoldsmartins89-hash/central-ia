"""Testes offline do que veio do Grok Bot: regras de aprovação, rotinas por evento, "salve como
habilidade", voz e busca ao vivo; e a lembrança das conversas do app do Claude."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from superagente import agendador, aprendizado, aprovacoes, memoria, provedores, sessoes, voz


class TestAprovacoes(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(aprovacoes, "ARQUIVO", Path(tempfile.mkdtemp()) / "regras.json")
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_sempre_permitir_aprova_a_mesma_ordem(self):
        ordem = "liste os 10 maiores arquivos do HD E: e me mande a lista"
        self.assertFalse(aprovacoes.decidir(ordem)[0])
        aprovacoes.permitir_sempre(ordem)
        self.assertTrue(aprovacoes.decidir("Liste os 10 maiores arquivos do HD E: e me mande a lista!")[0])
        self.assertFalse(aprovacoes.decidir("crie uma pasta nova chamada Projetos no Drive")[0])

    def test_lista_fixa_vence_qualquer_regra(self):
        ordem = "apague os arquivos temporários da pasta Downloads"
        self.assertIn("continuo perguntando", aprovacoes.permitir_sempre(ordem))
        self.assertFalse(aprovacoes.decidir(ordem)[0])

    def test_plano_sensivel_volta_a_pedir_ok(self):
        ordem = "organize a pasta Documentos"
        aprovacoes.permitir_sempre(ordem)
        self.assertTrue(aprovacoes.decidir(ordem, "1. Mover PDFs para Documentos/PDFs")[0])
        ok, porque = aprovacoes.decidir(ordem, "1. Remove-Item C:\\x -Recurse")
        self.assertFalse(ok)
        self.assertIn("apaga", porque)

    def test_regra_propria_de_perguntar(self):
        aprovacoes.perguntar_antes("HD E:")
        self.assertTrue(aprovacoes.motivos_para_perguntar("copie tudo do hd e: para o drive"))
        self.assertIn("Perguntar antes", aprovacoes.comando(""))


class TestGatilhos(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(agendador, "PASTA", self.tmp),
                        mock.patch.object(agendador, "AGENDA", self.tmp / "agenda.json"),
                        mock.patch.object(agendador, "PASTA_SAIDA", self.tmp / "saida"),
                        mock.patch.object(agendador, "PENDENTES_NATURAIS", self.tmp / "nat.json"),
                        mock.patch.object(agendador, "_banco_emails", lambda: self.tmp / "emails.db"),
                        mock.patch("superagente.diario.PASTA", self.tmp / "diario")]
        for p in self.patches:
            p.start()
        con = sqlite3.connect(self.tmp / "emails.db")
        con.execute("CREATE TABLE emails (de_nome TEXT, de_email TEXT, assunto TEXT, data TEXT, trecho TEXT)")
        con.commit()
        con.close()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _email(self, de, assunto):
        con = sqlite3.connect(self.tmp / "emails.db")
        con.execute("INSERT INTO emails VALUES (?, ?, ?, '21/11', 'corpo')", ("Banco X", de, assunto))
        con.commit()
        con.close()

    def test_pasta_nova_dispara_uma_vez(self):
        pasta = self.tmp / "entrada"
        pasta.mkdir()
        (pasta / "antigo.pdf").write_text("x")
        agendador.criar_gatilho("pasta", str(pasta), "resuma o arquivo novo")
        rodados = []
        self.assertEqual(agendador.tick(rodar=lambda p: rodados.append(p) or "ok"), 0)   # o antigo não conta
        (pasta / "novo.pdf").write_text("y")
        self.assertEqual(agendador.tick(rodar=lambda p: rodados.append(p) or "ok"), 1)
        self.assertIn("novo.pdf", rodados[0])
        self.assertEqual(agendador.tick(rodar=lambda p: rodados.append(p) or "ok"), 0)

    def test_email_do_remetente_dispara(self):
        self._email("velho@banco.com", "antes")
        agendador.criar_gatilho("email", "banco.com", "me avise do que se trata")
        rodados = []
        self._email("outro@loja.com", "promo")
        self.assertEqual(agendador.tick(rodar=lambda p: rodados.append(p) or "ok"), 0)
        self._email("gerente@banco.com", "Seu extrato")
        self.assertEqual(agendador.tick(rodar=lambda p: rodados.append(p) or "ok"), 1)
        self.assertIn("Seu extrato", rodados[0])
        self.assertNotIn("antes", rodados[0])

    def test_frase_natural_vira_rotina_com_confirmacao(self):
        enviados = []
        self.assertTrue(agendador.propor_gatilho("quando chegar e-mail do banco.com, me avise do que se trata",
                                                 lambda t, b=None: enviados.append(b)))
        codigo = enviados[0][0][1].split(":")[2]
        self.assertIn("Rotina", agendador.confirmar_natural(codigo, True))
        [t] = agendador.listar()
        self.assertEqual(t["gatilho"]["tipo"], "email")
        self.assertIn("ao acontecer", agendador.formatar())
        self.assertFalse(agendador.propor_gatilho("quando você puder, me explique isso", print))


class TestSalvarHabilidade(unittest.TestCase):
    def test_pedido_e_reconhecido_e_vai_com_foco(self):
        self.assertTrue(aprendizado.PEDE_HABILIDADE.search("salve isso como habilidade"))
        self.assertTrue(aprendizado.PEDE_HABILIDADE.search("guarda esse passo a passo pra próxima"))
        self.assertFalse(aprendizado.PEDE_HABILIDADE.search("qual a melhor habilidade de um líder?"))
        with mock.patch.object(aprendizado, "revisar", return_value=[]) as revisar:
            avisos = []
            aprendizado.salvar_como_habilidade("trecho", avisos.append)
            self.assertIs(revisar.call_args.kwargs["foco"], aprendizado.FOCO_HABILIDADE)
            self.assertIn("Não achei", avisos[0])


class TestVozEBusca(unittest.TestCase):
    def test_texto_limpo_para_falar(self):
        self.assertEqual(voz.para_falar("**Oi**, veja [o site](http://a.b) 🔔\n- item"), "Oi, veja o site item")
        longo = "Frase um. " * 200
        falado = voz.para_falar(longo, limite=100)
        self.assertTrue(falado.endswith("O resto está na mensagem escrita."))

    def test_codex_com_busca_monta_o_comando(self):
        chamados = []

        class Processo:
            returncode = 0
            pid = 1

            def communicate(self, entrada=None, timeout=None):
                return "resposta", ""

        def popen(comando, **_):
            chamados.append((comando, None))
            return Processo()

        with mock.patch.object(provedores, "_achar_cli", return_value="codex"), \
             mock.patch.object(provedores.subprocess, "Popen", side_effect=popen):
            provedores._cli("codex+web", "sistema", "notícias de hoje")
            provedores._cli("codex", "sistema", "texto")
        self.assertIn('web_search="live"', chamados[0][0])
        self.assertNotIn('web_search="live"', chamados[1][0])
        self.assertEqual(provedores._chave_descanso("cli:codex+web"), "cli:codex")

    def test_pesquisador_usa_busca_e_redator_nao(self):
        from superagente.agentes import ordenar_assinaturas
        cfg = {"ativo": True, "frente": ["cli:codex"], "com_busca": ["pesquisador"]}
        self.assertEqual(ordenar_assinaturas(["gemini:x"], cfg, agente="pesquisador")[0], "cli:codex+web")
        self.assertEqual(ordenar_assinaturas(["gemini:x"], cfg, agente="redator")[0], "cli:codex")


class TestLembrancaDoClaude(unittest.TestCase):
    def test_importa_so_o_texto_e_pula_o_que_nao_mudou(self):
        tmp = Path(tempfile.mkdtemp())
        projeto = tmp / "projects" / "C--central"
        projeto.mkdir(parents=True)
        linhas = [
            {"type": "custom-title", "customTitle": "Plano do resgate"},
            {"type": "user", "timestamp": "2026-11-21T10:00:00Z", "message": {"role": "user",
             "content": "Vamos clonar o disco de um terabyte"}},
            {"type": "assistant", "timestamp": "2026-11-21T10:00:05Z", "message": {"role": "assistant",
             "content": [{"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "Clone iniciado no E:"}]}},
            {"type": "user", "timestamp": "2026-11-21T10:00:06Z", "message": {"role": "user",
             "content": [{"type": "tool_result", "content": "saída enorme"}]}},
            {"type": "user", "timestamp": "2026-11-21T10:00:07Z", "message": {"role": "user",
             "content": "<system-reminder>interno</system-reminder>"}},
        ]
        (projeto / "abc.jsonl").write_text("\n".join(json.dumps(l) for l in linhas), encoding="utf-8")
        banco, estado = tmp / "s.db", tmp / "estado.json"
        self.assertEqual(sessoes.importar_claude_code(tmp / "projects", banco, estado), 2)
        self.assertEqual(sessoes.importar_claude_code(tmp / "projects", banco, estado), 0)
        [achado] = sessoes.buscar("terabyte", banco=banco)
        self.assertEqual(achado.origem, "claude")
        self.assertFalse(sessoes.buscar("enorme", banco=banco))      # saída de ferramenta não entra
        self.assertIn("no app do Claude", sessoes.formatar([achado]))


if __name__ == "__main__":
    unittest.main()
