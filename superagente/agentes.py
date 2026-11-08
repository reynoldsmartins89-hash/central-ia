"""Agentes especialistas: uma habilidade + uma lista de modelos com reserva."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import habilidades, provedores

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_AGENTES = RAIZ / "agentes" / "agentes.toml"

REGRAS_GERAIS = (
    "Responda em português do Brasil, em Markdown. "
    "Nunca invente fatos, números, citações ou fontes; sinalize incertezas. "
    "Você faz parte de uma equipe: entregue apenas a sua parte, completa e pronta para a próxima etapa."
)


def carregar_env(arquivo: Path = RAIZ / ".env") -> None:
    """Carrega variáveis do .env sem sobrescrever as já definidas no sistema."""
    if not arquivo.exists():
        return
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if linha and not linha.startswith("#") and "=" in linha:
            chave, _, valor = linha.partition("=")
            os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


@dataclass
class Agente:
    nome: str
    descricao: str
    habilidade: str
    modelos: list[str] = field(default_factory=list)

    def executar(self, tarefa: str, contexto: str = "", modelos: list[str] | None = None,
                 log=print) -> tuple[str, str]:
        """Executa a tarefa. Retorna (modelo_usado, resposta)."""
        hab = habilidades.carregar(self.habilidade)
        sistema = f"{hab.como_prompt()}\n\n---\n{REGRAS_GERAIS}"
        mensagem = f"## Tarefa\n{tarefa}"
        if contexto:
            mensagem = f"## Contexto (resultados anteriores)\n{contexto}\n\n{mensagem}"
        return provedores.chamar_com_reserva(modelos or self.modelos, sistema, mensagem, log=log)


def carregar_equipe(arquivo: Path = ARQUIVO_AGENTES) -> tuple[Agente, dict[str, Agente]]:
    """Retorna (superagente, {nome: agente})."""
    with open(arquivo, "rb") as f:
        dados = tomllib.load(f)
    s = dados["superagente"]
    superagente = Agente("superagente", "Orquestrador", s["habilidade"], s["modelos"])
    equipe = {
        nome: Agente(nome, a.get("descricao", ""), a["habilidade"], a["modelos"])
        for nome, a in dados.get("agentes", {}).items()
    }
    return superagente, equipe
