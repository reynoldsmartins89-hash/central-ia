"""Leitura das habilidades (SKILL.md) da pasta habilidades/."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA_HABILIDADES = RAIZ / "habilidades"


@dataclass
class Habilidade:
    caminho: str          # ex.: "conteudo/artigo-mae"
    nome: str
    descricao: str
    instrucoes: str       # corpo do SKILL.md, sem o frontmatter

    def como_prompt(self) -> str:
        return f"Habilidade: {self.nome} — {self.descricao}\n\n{self.instrucoes}"


def ler_frontmatter(texto: str) -> tuple[dict[str, str], str]:
    """Separa o bloco '--- chave: valor ---' do corpo do Markdown."""
    texto = texto.lstrip("﻿")
    if not texto.startswith("---"):
        return {}, texto
    _, cabecalho, corpo = texto.split("---", 2)
    dados = {}
    for linha in cabecalho.strip().splitlines():
        chave, sep, valor = linha.partition(":")
        if sep:
            dados[chave.strip()] = valor.strip().strip('"').strip("'")
    return dados, corpo.strip()


def carregar(caminho: str) -> Habilidade:
    arquivo = PASTA_HABILIDADES / caminho / "SKILL.md"
    if not arquivo.exists():
        raise FileNotFoundError(f"habilidade não encontrada: {arquivo}")
    dados, corpo = ler_frontmatter(arquivo.read_text(encoding="utf-8"))
    return Habilidade(caminho, dados.get("name", caminho), dados.get("description", ""), corpo)


def listar() -> list[Habilidade]:
    return [carregar(a.parent.relative_to(PASTA_HABILIDADES).as_posix())
            for a in sorted(PASTA_HABILIDADES.rglob("SKILL.md"))]
