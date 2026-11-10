"""A CENTRAL como "modelo único" para o Open WebUI (ou qualquer tela compatível com a API da OpenAI).

Você não escolhe modelo: escreve, e a Central decide, a cada mensagem,
  1. QUAL ESPECIALISTA responde (com a biblioteca dele) — a IA rápida lê o pedido e escolhe;
  2. QUAL IA usa, pelo custo-benefício:
       • pedido simples/rápido  → IA gratuita (Gemini → Groq), poupando a cota do Claude;
       • pedido profundo (estudo, análise, texto, plano) → Claude (sua assinatura) → Gemini → Groq;
     se uma falhar ou estiver sem cota, passa para a próxima sozinha.
Endereço local: http://127.0.0.1:8790/v1   (só o seu PC acessa)
Rodar:          python main.py roteador
"""
from __future__ import annotations

import json
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import provedores

PORTA = 8790
RAPIDOS = ["gemini:gemini-3.5-flash", "groq:openai/gpt-oss-120b", "gemini:gemini-flash-lite-latest", "cli:claude"]
PROFUNDOS = ["cli:claude", "gemini:gemini-3.5-flash", "groq:openai/gpt-oss-120b"]
NOMES_IA = {"cli": "Claude", "gemini": "Gemini", "groq": "Groq", "ollama": "IA local", "codex": "Codex"}
SINAIS_PROFUNDOS = re.compile(
    r"analis|aprofund|explique|expliq|compare|estud|plano|planej|estrat|escreva|redija|artigo|resum|"
    r"por que|porque|como (fa[çz]o|funciona|posso)|fundament|detalh|passo a passo|crie|elabore|revis", re.I)


def _texto(conteudo) -> str:
    if isinstance(conteudo, list):   # mensagens com partes (texto + imagem)
        return " ".join(p.get("text", "") for p in conteudo if isinstance(p, dict))
    return conteudo or ""


def profundidade(pergunta: str) -> str:
    """'profundo' ou 'rapido' — o custo-benefício decide qual IA usar."""
    p = pergunta.strip()
    if len(p) > 280 or (SINAIS_PROFUNDOS.search(p) and len(p.split()) > 6):
        return "profundo"
    return "rapido"


def responder(mensagens: list[dict]) -> tuple[str, str]:
    """Devolve (resposta, rodapé com quem respondeu)."""
    from .agentes import carregar_equipe
    from .bot import NOMES_BONITOS, VOZ_DA_CONVERSA, _escolher_especialista
    falas = [(m.get("role"), _texto(m.get("content"))) for m in mensagens if m.get("role") in ("user", "assistant")]
    pergunta = next((t for r, t in reversed(falas) if r == "user"), "")
    historico = "\n\n".join(f"{'Reynolds' if r == 'user' else 'Central'}: {t[:4000]}" for r, t in falas[:-1][-12:])
    anterior = next((t for r, t in reversed(falas[:-1]) if r == "user"), "")
    agente = _escolher_especialista(f"{anterior} {pergunta}")
    nivel = profundidade(pergunta)
    ordem = PROFUNDOS if nivel == "profundo" else RAPIDOS
    modelos = ordem + [m for m in agente.modelos if m not in ordem]
    tarefa = (f"{VOZ_DA_CONVERSA}\n\n## Conversa até aqui\n{historico or '(início)'}\n\n"
              f"## Mensagem atual do Reynolds\n{pergunta}\n\nResponda à mensagem atual.")
    modelo, texto = agente.executar(tarefa, modelos=modelos, log=lambda *_: None,
                                    consulta=f"{anterior} {pergunta}", conversa=True)
    especialista = "Central" if agente.nome == "generalista" else NOMES_BONITOS.get(agente.nome, agente.nome)
    ia = NOMES_IA.get(modelo.split(":")[0], modelo.split(":")[0])
    return texto, f"\n\n— _{especialista} · {ia}_"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):   # silencioso
        pass

    def _json(self, codigo: int, dados: dict) -> None:
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [
                {"id": "central", "object": "model", "created": 0, "owned_by": "Central de IA",
                 "name": "Central de IA (escolhe o especialista e a IA sozinha)"}]})
        else:
            self._json(404, {"error": "não encontrado"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": "não encontrado"})
            return
        pedido = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            texto, rodape = responder(pedido.get("messages", []))
            texto += rodape
        except Exception as erro:  # noqa: BLE001
            texto = f"⚠️ A Central não conseguiu responder agora: {str(erro)[:300]}"
        ident, agora = f"chatcmpl-{uuid.uuid4().hex[:12]}", int(time.time())
        if not pedido.get("stream"):
            self._json(200, {"id": ident, "object": "chat.completion", "created": agora, "model": "central",
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": texto}}]})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for i in range(0, len(texto), 60):   # entrega em pedaços: a resposta "aparece" na tela
            pedaco = {"id": ident, "object": "chat.completion.chunk", "created": agora, "model": "central",
                      "choices": [{"index": 0, "delta": {"content": texto[i:i + 60]}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(pedaco, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.01)
        fim = {"id": ident, "object": "chat.completion.chunk", "created": agora, "model": "central",
               "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(fim)}\n\ndata: [DONE]\n\n".encode("utf-8"))
        self.wfile.flush()


def rodar(porta: int = PORTA) -> None:
    from .agentes import carregar_env
    carregar_env()
    print(f"🧭 Central como modelo único em http://127.0.0.1:{porta}/v1 (Open WebUI)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", porta), _Handler).serve_forever()
