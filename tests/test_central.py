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

    def test_superagente_sem_plano_valido_usa_generalista(self):
        sa = Superagente(forcar_modelo="eco:teste", log=lambda *_: None)
        self.assertEqual(sa.planejar("qualquer coisa")[0]["agente"], "generalista")


if __name__ == "__main__":
    unittest.main()
