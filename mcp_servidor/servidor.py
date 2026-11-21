"""Servidor MCP da Central de IA.

Expõe os agentes e habilidades como ferramentas MCP, para que o Claude Code,
o Gemini CLI, o VS Code (Copilot) ou qualquer cliente MCP possam usá-los.

Instalar:   pip install "mcp[cli]"
Registrar no Claude Code (rode na pasta do repositório):
    claude mcp add central-ia -- python mcp_servidor/servidor.py
"""
from __future__ import annotations

import os
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


@mcp.tool()
def buscar_no_acervo(pergunta: str, quantos: int = 8) -> str:
    """Busca no acervo pessoal do Reynolds (Drive e HDs lidos pelo Agente Leitor): livros, cursos, transcrições."""
    from superagente import conhecimento, leitor
    achados = leitor.buscar(pergunta, limite=max(1, min(quantos, 20)))
    return conhecimento.como_contexto(achados) if achados else "Nada encontrado no acervo."


@mcp.tool()
def lembrar_conversas(assunto: str, quantos: int = 8) -> str:
    """O que o Reynolds e a Central já conversaram ou produziram sobre um assunto (todas as conversas antigas)."""
    from superagente import sessoes
    return sessoes.formatar(sessoes.buscar(assunto, limite=max(1, min(quantos, 20))))


@mcp.tool()
def memoria_da_central() -> str:
    """O perfil do Reynolds e o caderno da Central (fatos duráveis e combinados). Leia antes de trabalhar."""
    from superagente import memoria
    partes = ["## Perfil do Reynolds", *(f"- {f}" for f in memoria.fatos()),
              "", "## Caderno da Central", *(f"- {f}" for f in memoria.caderno())]
    alma = Path(__file__).resolve().parent.parent / "agentes" / "ALMA.md"
    if alma.exists():
        partes = [alma.read_text(encoding="utf-8"), ""] + partes
    return "\n".join(partes)


@mcp.tool()
def listar_habilidades() -> str:
    """Catálogo compacto das habilidades (caminho: descrição). Use ler_habilidade para abrir uma."""
    return habilidades.indice()


@mcp.tool()
def navegador_ler(url: str, com_elementos: bool = True) -> str:
    """Abre uma página no navegador próprio da Central (perfil separado, sem logins) e devolve o texto
    e os elementos numerados [ref]. Só lê. Rede interna e arquivos locais são bloqueados."""
    from superagente import navegador
    try:
        return navegador.ler(url).como_texto(com_elementos)
    except navegador.Bloqueado as erro:
        return f"Bloqueado: {erro}"


@mcp.tool()
def navegador_agir(url: str, acoes: list[dict]) -> str:
    """AGE numa página: acoes = [{"tipo": "clicar"|"digitar"|"escolher", "ref": 3, "valor": "..."}],
    com as refs de navegador_ler. Pede aprovação do Reynolds antes de rodar. Recusa senha, cartão e documentos."""
    from superagente import navegador
    try:
        return navegador.agir(url, acoes, aprovado=True).como_texto()
    except navegador.Bloqueado as erro:
        return f"Bloqueado: {erro}"


@mcp.tool()
def diario_da_central(dia: str = "") -> str:
    """Diário da Central (o que foi feito e decidido). dia no formato AAAA-MM-DD; vazio = hoje."""
    from datetime import date
    from superagente import diario
    try:
        alvo = date.fromisoformat(dia) if dia else None
    except ValueError:
        return "Use o formato AAAA-MM-DD."
    return diario.ler(alvo) or "Diário vazio nesse dia."


@mcp.tool()
def ver_agenda() -> str:
    """Tarefas agendadas da Central (só leitura)."""
    from superagente import agendador
    return agendador.formatar()


if __name__ == "__main__":
    # Quem chama este servidor já é uma IA de assinatura (Codex, Claude Code…): os agentes daqui
    # usam as APIs, sem abrir outra ferramenta de terminal por dentro (evita IA chamando IA em cascata).
    os.environ.setdefault("CENTRAL_SEM_CLI", "1")
    mcp.run()
