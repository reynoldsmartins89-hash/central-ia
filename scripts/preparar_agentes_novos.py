"""Prepara as bibliotecas dos agentes novos (09/11/2026): converte as pastas em Markdown e indexa."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superagente import conhecimento, conversor  # noqa: E402
from superagente.agentes import carregar_env, carregar_equipe  # noqa: E402

carregar_env()
PASTAS = ["G:/Meu Drive/MBA em Gestão de Projetos", "G:/Meu Drive/EBOOKS/05. ESTRATÉGIA, INTELIGÊNCIA & DEFESA"]
AGENTES = ["mestrado_china", "gestao_projetos", "a_guerra_ensina"]

if __name__ == "__main__":
    for pasta in PASTAS:
        conversor.converter_pasta(pasta)
    equipe = carregar_equipe()[1]
    for nome in AGENTES:
        print(f"📚 indexando {nome}…", flush=True)
        conhecimento.indexar(nome, equipe[nome].base)
