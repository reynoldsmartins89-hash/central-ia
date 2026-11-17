"""Testes offline do Caçador de Vagas. Rodar: python -m unittest -v"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from superagente import vagas
from superagente.vagas import Vaga

PERFIL = {"resumo": "Gestor de projetos com MBA.", "termos_pt": ["gestão de projetos", "gerente de projetos"],
          "termos_en": ["project manager"], "excluir_titulo": ["estágio", "intern"],
          "modalidades": ["remoto", "hibrido"], "cidades_hibrido": ["Brasília"], "dias_maximos": 21,
          "descartar_restritas_a_outros_paises": True}


def recente(dias=1):
    return (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()


class TestFiltro(unittest.TestCase):
    def test_vaga_boa_passa(self):
        v = Vaga("Gupy", "Gerente de Projetos de TI", "https://x.gupy.io/job/1", "Empresa", "", "remoto",
                 "Gestão de projetos com PMBOK", recente())
        self.assertEqual(vagas.motivo_descarte(v, PERFIL), "")

    def test_titulo_excluido_presencial_e_cidade(self):
        base = dict(url="https://a/1", empresa="E", descricao="gestão de projetos", publicada=recente())
        self.assertEqual(vagas.motivo_descarte(Vaga("Gupy", "Estágio em Gestão de Projetos", modalidade="remoto", **base),
                                               PERFIL), "título excluído")
        self.assertEqual(vagas.motivo_descarte(Vaga("Gupy", "Gerente de Projetos", modalidade="presencial", **base),
                                               PERFIL), "presencial")
        self.assertEqual(vagas.motivo_descarte(Vaga("Gupy", "Gerente de Projetos", modalidade="hibrido",
                                                    local="São Paulo, SP", **base), PERFIL), "híbrida em outra cidade")
        self.assertEqual(vagas.motivo_descarte(Vaga("Gupy", "Gerente de Projetos", modalidade="hibrido",
                                                    local="Brasília, DF", **base), PERFIL), "")

    def test_restricao_de_pais(self):
        base = dict(url="https://a/1", empresa="E", modalidade="remoto", descricao="", publicada=recente())
        self.assertEqual(vagas.motivo_descarte(Vaga("Himalayas", "Senior Project Manager", local="United States", **base),
                                               PERFIL), "restrita a outro país")
        self.assertEqual(vagas.motivo_descarte(Vaga("Remotive", "Project Manager", local="LATAM", **base), PERFIL), "")
        self.assertEqual(vagas.motivo_descarte(Vaga("Remotive", "Project Manager", local="Remote", **base), PERFIL), "")

    def test_antiga_e_fora_do_perfil(self):
        self.assertEqual(vagas.motivo_descarte(Vaga("Remotive", "Project Manager", "https://a/1", local="Worldwide",
                                                    publicada=recente(60)), PERFIL), "antiga")
        self.assertEqual(vagas.motivo_descarte(Vaga("Remotive", "Frontend Developer", "https://a/2", local="Worldwide",
                                                    descricao="React", publicada=recente()), PERFIL), "fora do perfil")


class TestFontes(unittest.TestCase):
    def test_gupy(self):
        v = vagas.vaga_gupy({"name": "Gerente de Projetos", "careerPageName": "ACME", "workplaceType": "hybrid",
                             "city": "Brasília", "state": "Distrito Federal", "jobUrl": "https://acme.gupy.io/job/eyJ?x=1",
                             "description": "<p>Gestão &amp; projetos</p>", "publishedDate": "2026-11-16T21:49:09.579Z"})
        self.assertEqual((v.modalidade, v.empresa, v.descricao), ("hibrido", "ACME", "Gestão & projetos"))
        self.assertIn("Brasília", v.local)
        self.assertTrue(v.publicada.startswith("2026-11-16"))

    def test_alerta_do_linkedin(self):
        corpo = ('<a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc"><img></a>'
                 '<a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trk=t">Gerente de Projetos PMO</a>'
                 '<a href="https://www.linkedin.com/comm/jobs/view/4012345678/">Ver vaga</a>'
                 '<a href="https://www.linkedin.com/comm/feed/">Feed</a>')
        achadas = vagas.vagas_do_email(corpo, "LinkedIn <jobalerts-noreply@linkedin.com>")
        self.assertEqual(len(achadas), 1)
        self.assertEqual(achadas[0].titulo, "Gerente de Projetos PMO")
        self.assertEqual(achadas[0].url, "https://www.linkedin.com/jobs/view/4012345678")
        self.assertTrue(achadas[0].so_titulo)

    def test_alerta_do_indeed_com_link_codificado(self):
        corpo = '<a href="https://cts.indeed.com/v3/x?u=https%3A%2F%2Fbr.indeed.com%2Frc%2Fclk%3Fjk%3Dab12cd34ef56">Analista de Riscos</a>'
        achadas = vagas.vagas_do_email(corpo, "alert@indeed.com")
        self.assertEqual(achadas[0].url, "https://br.indeed.com/viewjob?jk=ab12cd34ef56")

    def test_remetente_desconhecido(self):
        self.assertEqual(vagas.vagas_do_email('<a href="https://x/jobs/view/1">Vaga</a>', "loja@exemplo.com"), [])


class TestBancoEAvaliacao(unittest.TestCase):
    def test_nao_repete_vaga(self):
        with tempfile.TemporaryDirectory() as pasta, mock.patch.object(vagas, "BANCO", Path(pasta) / "v.db"):
            con = vagas._banco()
            a = Vaga("Gupy", "Gerente de Projetos", "https://a.gupy.io/job/1?x=1", "ACME")
            b = Vaga("Remotive", "Gerente de  projetos", "https://remotive.com/2", "Acme")   # mesma vaga, outro site
            c = Vaga("Gupy", "Analista PMO", "https://a.gupy.io/job/3", "ACME")
            self.assertEqual(len(vagas.ja_vistas(con, [a, b, c])), 2)
            vagas._gravar(con, a, nota=80)
            self.assertEqual([v.titulo for v in vagas.ja_vistas(con, [a, c])], ["Analista PMO"])
            con.close()

    def test_avaliar_lote_le_json_da_ia(self):
        resposta = ('```json\n[{"id": 0, "nota": 88, "modalidade": "remoto", "presencial": "nenhum", '
                    '"motivo": "casa com o MBA", "alerta": ""}, {"id": 9, "nota": 50}]\n```')
        lote = [Vaga("Gupy", "Gerente de Projetos", "https://a/1", descricao="PMBOK")]
        with mock.patch("superagente.provedores.chamar_com_reserva", return_value=("eco:teste", resposta)):
            notas = vagas.avaliar_lote(lote, PERFIL, modelos=["eco:teste"], log=lambda *_: None)
        self.assertEqual(list(notas), [0])
        self.assertEqual(notas[0]["nota"], 88)

    def test_avaliar_lote_sem_json_nao_quebra(self):
        lote = [Vaga("Gupy", "Gerente de Projetos", "https://a/1")]
        with mock.patch("superagente.provedores.chamar_com_reserva", return_value=("eco:teste", "sem json")):
            self.assertEqual(vagas.avaliar_lote(lote, PERFIL, modelos=["eco:teste"], log=lambda *_: None), {})

    def test_mensagem_de_aviso(self):
        texto = vagas.mensagem_aviso([(Vaga("Gupy", "Gerente de Projetos", "https://a/1", "ACME", modalidade="remoto"),
                                       {"nota": 91, "modalidade": "remoto", "presencial": "nenhum",
                                        "motivo": "Bate com o MBA.", "alerta": "Inglês avançado."})])
        self.assertIn("91/100", texto)
        self.assertIn("https://a/1", texto)
        self.assertNotIn("Presencial", texto)


if __name__ == "__main__":
    unittest.main()
