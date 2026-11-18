import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

_CAMINHO = Path(__file__).resolve().parent.parent / "scripts" / "retomar_apos_limite.py"
_spec = importlib.util.spec_from_file_location("retomar_apos_limite", _CAMINHO)
r = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r)


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


class RetomarAposLimite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        r.PASTA_SESSOES = base / "Claude-Data"
        r.PASTA_PROJETOS = base / "projects"
        r.ESTADO = base / "estado.json"
        self.meta_dir = r.PASTA_SESSOES / "x" / "claude-code-sessions" / "a" / "b"
        self.meta_dir.mkdir(parents=True)
        (r.PASTA_PROJETOS / "proj").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _sessao(self, sid, cli, registros, arquivada=False, tarefa=None):
        meta = {"sessionId": sid, "cliSessionId": cli, "title": sid, "isArchived": arquivada}
        if tarefa:
            meta["scheduledTaskId"] = tarefa
        (self.meta_dir / f"{sid}.json").write_text(json.dumps(meta), encoding="utf-8")
        linhas = [json.dumps(x, separators=(",", ":")) for x in registros]
        (r.PASTA_PROJETOS / "proj" / f"{cli}.jsonl").write_text("\n".join(linhas), encoding="utf-8")

    def _limite(self, quando, volta, uuid="u-lim"):
        return {"type": "assistant", "uuid": uuid, "timestamp": _iso(quando), "apiError": "usage_limit_reached",
                "apiErrorParams": {"rate_limit_info": {"resetsAt": volta}},
                "message": {"content": [{"type": "text", "text": "You've hit your 5-hour limit"}]}}

    def test_lista_so_depois_que_o_limite_volta_e_uma_vez(self):
        agora = time.time()
        user = {"type": "user", "uuid": "u1", "timestamp": _iso(agora - 3600), "message": {"content": "faça"}}
        self._sessao("local_a", "cli-a", [user, self._limite(agora - 3000, agora + 600)])
        self.assertEqual(r.listar(agora), [])  # limite ainda não voltou
        depois = agora + 600 + r.FOLGA_SEGUNDOS + 1
        achadas = r.listar(depois)
        self.assertEqual([x["sessionId"] for x in achadas], ["local_a"])
        self.assertTrue(r.marcar("local_a"))
        self.assertEqual(r.listar(depois), [])  # já retomada

    def test_ignora_conversa_que_continuou_ou_arquivada(self):
        agora = time.time()
        resposta = {"type": "assistant", "uuid": "u3", "timestamp": _iso(agora - 100),
                    "message": {"content": [{"type": "text", "text": "ok"}]}}
        self._sessao("local_b", "cli-b", [self._limite(agora - 3000, agora - 2000), resposta])
        self._sessao("local_c", "cli-c", [self._limite(agora - 3000, agora - 2000)], arquivada=True)
        self._sessao("local_t", "cli-t", [self._limite(agora - 3000, agora - 2000)], tarefa="retomar-apos-limite")
        self.assertEqual(r.listar(agora), [])

    def test_ignora_registros_de_subagente(self):
        agora = time.time()
        sub = {"type": "assistant", "isSidechain": True, "uuid": "s", "timestamp": _iso(agora - 50),
               "message": {"content": [{"type": "text", "text": "sub"}]}}
        self._sessao("local_d", "cli-d", [self._limite(agora - 3000, agora - 2000), sub])
        self.assertEqual([x["sessionId"] for x in r.listar(agora)], ["local_d"])


if __name__ == "__main__":
    unittest.main()
