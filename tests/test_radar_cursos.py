import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from superagente import radar_cursos as rc


class TestRadarCursos(unittest.TestCase):
    def setUp(self):
        rc.DOU_FORA["ate"] = 0
        self.enterContext(mock.patch.object(rc, "PAUSA_DOU", 0))

    def test_filtro_aceita_protecao_de_autoridades_e_recusa_seguranca_privada(self):
        self.assertTrue(rc.relevante("Abertas as inscrições do Curso de Proteção de Autoridades da PMERJ"))
        self.assertTrue(rc.relevante("Estágio de Segurança de Dignitários — vagas para magistrados"))
        self.assertFalse(rc.relevante("Curso de reciclagem de vigilantes e segurança privada"))
        self.assertTrue(rc.relevante("Curso de escolta armada para segurança de dignitários e autoridades"))
        self.assertFalse(rc.relevante("Portaria nomeia servidor da polícia judicial"))      # não é curso
        self.assertTrue(rc.relevante("Policial judicial: nota sobre proteção de autoridades", exigir_curso=False))

    def test_le_a_busca_do_dou_e_monta_o_link_real(self):
        dados = {"jsonArray": [
            {"title": "PORTARIA GSI/PR Nº 167", "urlTitle": "portaria-gsi-pr-n-167", "pubDate": "04/08/2026",
             "hierarchyStr": "Presidência/GSI", "content": "Aprova o <b>Curso de Segurança de Dignitários</b> 2026"},
            {"title": "PORTARIA X", "urlTitle": "x", "pubDate": "01/08/2026", "content": "Nomeia fulano"}]}
        pagina = ('<script id="_br_com_seatecnologia_in_buscadou_BuscaDouPortlet_params" '
                  f'type="application/json">{json.dumps(dados)}</script>')
        ag = rc.Agente("forcas_armadas", "FA", ['"segurança de dignitários"'], "")
        with mock.patch.object(rc, "_baixar", return_value=pagina):
            achados = rc.buscar_dou(ag, log=lambda *_: None)
        self.assertEqual(len(achados), 1)
        self.assertEqual(achados[0].url, "https://www.in.gov.br/web/dou/-/portaria-gsi-pr-n-167")
        self.assertIn("Segurança de Dignitários", achados[0].trecho)

    def test_web_so_aceita_itens_com_url_e_rodar_nao_avisa_duas_vezes(self):
        resposta = ('Aqui está:\n```json\n[{"orgao":"PMERJ","titulo":"Curso de Segurança de Dignitários",'
                    '"url":"https://www.instagram.com/p/abc/?igsh=1","fonte":"instagram","prazo":"30/11"},'
                    '{"orgao":"X","titulo":"Curso de proteção de autoridades","url":"sem link"}]\n```')
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(rc, "BANCO", Path(tmp) / "r.db"), \
             mock.patch.object(rc, "PASTA_RELATORIOS", Path(tmp) / "rel"), \
             mock.patch.object(rc, "_baixar", return_value=""), \
             mock.patch.object(rc, "_claude_com_busca", return_value=resposta), \
             mock.patch.dict("os.environ", {"PERPLEXITY_API_KEY": ""}), \
             mock.patch("superagente.telegram.enviar") as enviar:
            primeira = rc.rodar(log=lambda *_: None)
            self.assertEqual(len(primeira), 1)               # o mesmo post achado pelos 3 agentes conta uma vez
            self.assertIn("instagram.com/p/abc", primeira[0].url)
            self.assertEqual(enviar.call_count, 1)
            self.assertEqual(rc.rodar(log=lambda *_: None), [])   # já avisado: não repete
            self.assertEqual(enviar.call_count, 1)
            self.assertTrue(list((Path(tmp) / "rel").glob("*.md")))


if __name__ == "__main__":
    unittest.main()
