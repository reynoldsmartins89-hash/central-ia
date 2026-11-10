"""Conector da Central de IA para o app do Claude (claude.ai, celular e computador).

Roda no servidor da nuvem (Oracle) e deixa o Claude CONSULTAR — só leitura — o que a Central sabe:
  • o acervo lido pelo Agente Leitor (Drive e HDs: livros, apostilas, cursos, transcrições);
  • a biblioteca de cada especialista (COF, Filosofia Católica, Geopolítica, Mentor de Educação…);
  • as instruções de cada especialista (para o Claude trabalhar do jeito dele).
Nada é alterado, apagado ou enviado: o conector só lê. Agentes privados (Cibersegurança e
Segurança Pública) não ficam no servidor e não aparecem aqui.

Endereço: https://<servidor>/<CONECTOR_SEGREDO>/mcp   (o segredo fica no .env do servidor)
Rodar:    python mcp_servidor/conector_nuvem.py         (o serviço central-conector faz isso sozinho)
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

from superagente import conhecimento, habilidades, leitor  # noqa: E402
from superagente.agentes import carregar_env, carregar_equipe  # noqa: E402

carregar_env()
SEGREDO = os.getenv("CONECTOR_SEGREDO", "").strip()
HOST = os.getenv("CONECTOR_HOST", "").strip()          # ex.: 159-112-177-123.sslip.io
PARTE = 30_000                                          # caracteres por parte ao ler um material inteiro

INSTRUCOES = """Você está ligado à CENTRAL DE IA do Reynolds: o acervo pessoal dele (livros, apostilas, cursos e
aulas transcritas do Google Drive e dos HDs) e as bibliotecas dos agentes especialistas.
Use as ferramentas sempre que a conversa tocar nos estudos, cursos ou materiais dele:
- buscar_no_acervo: procura em TODO o acervo; buscar_na_biblioteca: procura na biblioteca de um especialista;
- ler_material: lê um material inteiro (em partes) quando um trecho não basta;
- instrucoes_do_especialista: o método de trabalho de um especialista (ex.: mentor_educacao, cof).
Diga de onde veio a informação (nome do material) e diferencie o que é do acervo do que é conhecimento geral."""

mcp = FastMCP(
    "Central de IA do Reynolds", instructions=INSTRUCOES, host="127.0.0.1", port=8765,
    streamable_http_path=f"/{SEGREDO}/mcp" if SEGREDO else "/mcp", stateless_http=True, json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(HOST), allowed_hosts=[HOST, f"{HOST}:443"] if HOST else [],
        allowed_origins=["https://claude.ai", "https://www.claude.ai"]),
)


def _publicos() -> dict:
    """Especialistas não privados que têm biblioteca neste servidor."""
    return {n: a for n, a in carregar_equipe()[1].items() if a.privacidade != "local"}


@mcp.tool()
def listar_especialistas() -> str:
    """Lista os agentes especialistas da Central, o que cada um faz e se a biblioteca dele está disponível."""
    linhas = []
    for nome, a in sorted(_publicos().items()):
        biblioteca = "📚 com biblioteca" if a.base and conhecimento.indexado(nome) else "sem biblioteca própria"
        linhas.append(f"- {nome} ({biblioteca}): {a.descricao}")
    return "Especialistas da Central de IA:\n" + "\n".join(linhas)


@mcp.tool()
def instrucoes_do_especialista(especialista: str) -> str:
    """Devolve o método de trabalho (instruções) de um especialista, ex.: 'mentor_educacao', 'cof', 'geopolitica'."""
    agente = _publicos().get(especialista.strip().lower())
    if not agente:
        return f"Especialista '{especialista}' não encontrado. Use listar_especialistas."
    return habilidades.carregar(agente.habilidade).como_prompt()


def _formatar(trechos: list[dict]) -> str:
    if not trechos:
        return "Nada encontrado. Tente outras palavras (nomes de autores, termos técnicos, títulos)."
    return "\n\n---\n\n".join(f"[{i}] {t['titulo']}\nMaterial: {t.get('caminho', '')}\n{t['texto']}"
                              for i, t in enumerate(trechos, 1))


@mcp.tool()
def buscar_no_acervo(pergunta: str, quantos: int = 8) -> str:
    """Procura em TODO o acervo do Reynolds (Drive e HDs: livros, apostilas, cursos, aulas transcritas).
    `pergunta`: palavras ou frase sobre o assunto. Devolve os trechos mais relevantes com o nome do material."""
    quantos = max(1, min(int(quantos), 20))
    return _formatar(leitor.buscar(pergunta, limite=quantos, max_caracteres=3_000 * quantos))


@mcp.tool()
def buscar_na_biblioteca(especialista: str, pergunta: str, quantos: int = 8) -> str:
    """Procura na biblioteca de um especialista (ex.: 'cof', 'filosofia_catolica', 'mentor_educacao',
    'geopolitica', 'comunicacao'). Use listar_especialistas para ver todos."""
    nome = especialista.strip().lower()
    if nome not in _publicos():
        return f"Especialista '{especialista}' não encontrado (ou é privado). Use listar_especialistas."
    quantos = max(1, min(int(quantos), 20))
    return _formatar(conhecimento.buscar(nome, pergunta, limite=quantos, max_caracteres=3_000 * quantos))


@mcp.tool()
def ler_material(material: str, parte: int = 1) -> str:
    """Lê um material INTEIRO do acervo ou de uma biblioteca, em partes de ~30 mil caracteres.
    `material`: o caminho exatamente como veio em 'Material:' nas buscas. `parte`: 1, 2, 3…"""
    textos = []
    con = sqlite3.connect(Path.home() / ".central-ia" / "catalogo.db", timeout=30)
    textos = [t for (t,) in con.execute("SELECT texto FROM acervo WHERE caminho = ? ORDER BY rowid", (material,))]
    con.close()
    if not textos:
        for nome in _publicos():
            if not conhecimento.indexado(nome):
                continue
            con = sqlite3.connect(conhecimento._caminho_indice(nome), timeout=30)
            textos = [t for (t,) in con.execute("SELECT texto FROM trechos WHERE caminho = ? ORDER BY rowid",
                                                (material,))]
            con.close()
            if textos:
                break
    if not textos:
        return "Material não encontrado. Copie o caminho exatamente como apareceu em 'Material:'."
    inteiro = "\n".join(textos)
    total = max(1, -(-len(inteiro) // PARTE))
    parte = max(1, min(int(parte), total))
    return (f"[parte {parte} de {total} · {len(inteiro):,} caracteres no total]\n\n"
            + inteiro[(parte - 1) * PARTE: parte * PARTE])


if __name__ == "__main__":
    if not SEGREDO:
        raise SystemExit("Defina CONECTOR_SEGREDO no .env (o endereço do conector fica secreto).")
    mcp.run(transport="streamable-http")
