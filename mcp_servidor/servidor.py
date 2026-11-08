"""Servidor MCP da Central de IA.

Expõe os agentes e habilidades como ferramentas MCP, para que o Claude Code,
o Gemini CLI, o VS Code (Copilot) ou qualquer cliente MCP possam usá-los.

Instalar:   pip install "mcp[cli]"
Registrar no Claude Code (rode na pasta do repositório):
    claude mcp add central-ia -- python mcp_servidor/servidor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from superagente import habilidades  # noqa: E402
from superagente.agentes import carregar_env, carregar_equipe  # noqa: E402
from superagente.orquestrador import Superagente  # noqa: E402

carregar_env()
mcp = FastMCP("central-ia")
_log = lambda *_: None  # stdout é o canal do MCP; não imprimir nada  # noqa: E731


@mcp.tool()
def listar_agentes() -> str:
    """Lista os agentes especialistas disponíveis e o que cada um faz."""
    _, equipe = carregar_equipe()
    return "\n".join(f"{n}: {a.descricao}" for n, a in equipe.items())


@mcp.tool()
def ler_habilidade(caminho: str) -> str:
    """Retorna as instruções de uma habilidade, ex.: 'conteudo/artigo-mae'."""
    return habilidades.carregar(caminho).como_prompt()


@mcp.tool()
def perguntar_agente(agente: str, tarefa: str, contexto: str = "") -> str:
    """Envia uma tarefa a um agente especialista e devolve a resposta."""
    _, equipe = carregar_equipe()
    if agente not in equipe:
        return f"Agente inexistente. Opções: {', '.join(equipe)}"
    modelo, resposta = equipe[agente].executar(tarefa, contexto, log=_log)
    return f"[{modelo}]\n\n{resposta}"


@mcp.tool()
def executar_fluxo(nome: str, entrada: str) -> str:
    """Executa um fluxo fixo (ex.: 'producao-editorial') e devolve a pasta de resultados."""
    return str(Superagente(log=_log).fluxo(nome, entrada))


if __name__ == "__main__":
    mcp.run()
