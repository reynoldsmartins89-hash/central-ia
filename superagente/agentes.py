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
    base: str = ""            # pasta da biblioteca (agentes de conhecimento)
    privacidade: str = "nuvem"  # "local" = só modelos no seu PC (Ollama); nada sai da máquina

    def modelos_permitidos(self, modelos: list[str] | None = None) -> list[str]:
        lista = modelos or self.modelos
        if self.privacidade == "local":
            lista = [m for m in lista if m.split(":", 1)[0] in ("ollama", "eco")] or ["ollama:qwen3:4b"]
        return lista

    def executar(self, tarefa: str, contexto: str = "", modelos: list[str] | None = None,
                 log=print) -> tuple[str, str]:
        """Executa a tarefa. Retorna (modelo_usado, resposta)."""
        hab = habilidades.carregar(self.habilidade)
        sistema = f"{hab.como_prompt()}\n\n---\n{REGRAS_GERAIS}"
        mensagem = f"## Tarefa\n{tarefa}"
        if self.base:
            from . import conhecimento
            trechos = conhecimento.buscar(self.nome, tarefa)
            if trechos:
                log(f"   📚 {len(trechos)} trechos da biblioteca de {self.nome}")
                mensagem = f"{conhecimento.como_contexto(trechos)}\n\n{mensagem}"
                sistema += REGRAS_BIBLIOTECA
            else:
                log(f"   📚 nada encontrado na biblioteca de {self.nome} (está indexada?)")
                sistema += "\nNenhum trecho da biblioteca foi encontrado para esta tarefa: avise isso no início da resposta."
        if contexto:
            mensagem = f"## Contexto (resultados anteriores)\n{contexto}\n\n{mensagem}"
        return provedores.chamar_com_reserva(self.modelos_permitidos(modelos), sistema, mensagem, log=log)


REGRAS_BIBLIOTECA = (
    "\n\nVocê é um agente de conhecimento: fundamente a resposta nos trechos da biblioteca fornecidos, "
    "cite-os como [n] e liste as fontes usadas no final. Explique com suas próprias palavras — "
    "nunca copie trechos longos do material. Se os trechos não cobrirem a pergunta, diga claramente "
    "o que veio da biblioteca e o que é conhecimento geral."
)


def carregar_equipe(arquivo: Path = ARQUIVO_AGENTES) -> tuple[Agente, dict[str, Agente]]:
    """Retorna (superagente, {nome: agente})."""
    with open(arquivo, "rb") as f:
        dados = tomllib.load(f)
    s = dados["superagente"]
    superagente = Agente("superagente", "Orquestrador", s["habilidade"], s["modelos"])
    equipe = {
        nome: Agente(nome, a.get("descricao", ""), a["habilidade"], a["modelos"],
                     a.get("base", ""), a.get("privacidade", "nuvem"))
        for nome, a in dados.get("agentes", {}).items()
    }
    return superagente, equipe
