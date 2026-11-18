import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from superagente import cerebro


def _nota(raiz: Path, ia: str, nome: str, data: str, pergunta: str, resposta: str) -> Path:
    arq = raiz / "Central IA" / "Histórico das IAs" / ia / f"{data} - {nome}.md"
    arq.parent.mkdir(parents=True, exist_ok=True)
    arq.write_text(f'---\nia: "{ia}"\ndata: "{data}"\ntitulo: "{nome}"\n---\n\n# {nome}\n\n'
                   f"### Você\n\n{pergunta}\n\n### {ia}\n\n{resposta}\n", encoding="utf-8")
    return arq


INFO = {"tema": "Carreira e LinkedIn", "temas_extras": ["Imigração e trabalho fora"], "agentes": ["orientador_linkedin"],
        "resumo": "Melhorou o currículo.", "ideias": ["Refazer o resumo do LinkedIn"], "entidades": ["Texas Tech", "LinkedIn"]}


class TestCerebro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = Path(self.tmp.name)
        texto = "currículo profissional segurança experiência liderança inglês vaga remota dólar " * 5
        self.a = _nota(self.raiz, "Qwen", "Currículo", "2026-07-20", "Melhore meu currículo " + texto, texto)
        self.b = _nota(self.raiz, "Grok", "Currículo moderno", "2026-07-21", "Melhore meu currículo " + texto, texto)
        self.c = _nota(self.raiz, "Gemini", "Receita de bolo", "2026-08-01", "farinha ovos açúcar forno",
                       "misture farinha ovos açúcar e leve ao forno quente")

    def test_cache_evita_reclassificar_e_bloco_nao_duplica(self):
        notas = cerebro.ler_notas(self.raiz)
        self.assertEqual(len(notas), 3)
        banco = self.raiz / "c.db"
        respostas = {n.rel: dict(INFO) for n in notas}
        with mock.patch.object(cerebro, "_classificar_lote", side_effect=lambda lote, _: {n.rel: respostas[n.rel] for n in lote}) as cl, \
             mock.patch.object(cerebro, "_agentes", return_value={"orientador_linkedin": "LinkedIn"}):
            cerebro.classificar(notas, banco=banco, log=lambda *_: None)
            self.assertEqual(cl.call_count, 1)
            de_novo = cerebro.ler_notas(self.raiz)
            cerebro.classificar(de_novo, banco=banco, log=lambda *_: None)
            self.assertEqual(cl.call_count, 1)                  # tudo veio do cache
            self.assertEqual(de_novo[0].info["tema"], "Carreira e LinkedIn")
            cerebro.montar(self.raiz, de_novo, log=lambda *_: None)
            cerebro.montar(self.raiz, de_novo, log=lambda *_: None)
        texto = self.a.read_text(encoding="utf-8")
        self.assertEqual(texto.count(cerebro.INICIO), 1)        # rodar duas vezes não duplica o bloco
        self.assertIn("Currículo moderno", texto)               # ligada à mesma pergunta feita ao Grok
        self.assertNotIn("Receita de bolo", texto)
        self.assertIn("Texas Tech", texto)                      # entidade citada em 2+ notas vira link
        base = self.raiz / "Central IA" / "Cérebro"
        self.assertTrue((base / "Temas" / "Carreira e LinkedIn.md").exists())
        self.assertTrue((base / "Entidades" / "Texas Tech.md").exists())
        mesma = (base / "Mesma pergunta, várias IAs.md").read_text(encoding="utf-8")
        self.assertIn("Qwen", mesma)
        self.assertIn("Grok", mesma)

    def test_le_o_json_mesmo_com_texto_em_volta(self):
        lote = cerebro.ler_notas(self.raiz)[:2]
        resposta = 'Claro:\n```json\n' + json.dumps([
            {"n": 1, "tema": "Tema inventado", "agentes": ["nao_existe", "orientador_linkedin"], "entidades": ["X"]},
            {"n": 2, "tema": "Idiomas", "temas_extras": ["Idiomas", "Outros"]}]) + "\n```"
        with mock.patch("superagente.provedores.chamar_com_reserva", return_value=("m", resposta)):
            r = cerebro._classificar_lote(lote, {"orientador_linkedin": "x"})
        self.assertEqual(r[lote[0].rel]["tema"], "Outros")      # tema fora da lista não entra
        self.assertEqual(r[lote[0].rel]["agentes"], ["orientador_linkedin"])
        self.assertEqual(r[lote[1].rel]["temas_extras"], ["Outros"])


if __name__ == "__main__":
    unittest.main()
