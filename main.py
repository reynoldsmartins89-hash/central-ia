"""Central de IA — linha de comando do superagente.

Exemplos (no terminal do VS Code):
    python main.py diagnostico
    python main.py agentes
    python main.py perguntar pesquisador "Tendências de IA para pequenas empresas"
    python main.py executar "Crie um plano para lançar uma newsletter sobre IA aplicada"
    python main.py fluxo producao-editorial "IA nas pequenas empresas"
Use --modelo eco:teste para testar tudo offline, sem chamar nenhuma IA.
"""
from __future__ import annotations

import argparse
import sys

from superagente import habilidades, provedores
from superagente.agentes import carregar_env, carregar_equipe
from superagente.orquestrador import PASTA_FLUXOS, Superagente

for fluxo_saida in (sys.stdout, sys.stderr):
    try:
        fluxo_saida.reconfigure(encoding="utf-8")  # acentos e emojis no PowerShell
    except AttributeError:
        pass


def confirmar_plano(_plano) -> bool:
    resposta = input("\nExecutar este plano? [S/n] ").strip().lower()
    return resposta in ("", "s", "sim", "y")


def main(argv: list[str] | None = None) -> int:
    carregar_env()
    p = argparse.ArgumentParser(prog="central-ia", description="Superagente e equipe de agentes de IA")
    p.add_argument("--modelo", help='força um modelo para todos, ex.: "eco:teste" ou "ollama:qwen3:4b"')
    p.add_argument("-s", "--sim", action="store_true", help="não pede confirmação do plano")
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("diagnostico", help="mostra quais provedores de IA estão prontos")
    sub.add_parser("agentes", help="lista a equipe de agentes")
    sub.add_parser("habilidades", help="lista as habilidades (SKILL.md)")
    sub.add_parser("fluxos", help="lista os fluxos fixos")

    c = sub.add_parser("perguntar", help="fala direto com um agente")
    c.add_argument("agente")
    c.add_argument("texto")

    c = sub.add_parser("executar", help="o superagente planeja e delega sozinho")
    c.add_argument("objetivo")

    c = sub.add_parser("fluxo", help="executa um fluxo fixo de fluxos/")
    c.add_argument("nome")
    c.add_argument("entrada")

    a = p.parse_args(argv)

    if a.comando == "diagnostico":
        for nome, ok, detalhe in provedores.diagnostico():
            print(f"{'✅' if ok else '⬜'} {nome:<11} {detalhe}")
        return 0

    if a.comando == "agentes":
        mestre, equipe = carregar_equipe()
        print(f"🧠 superagente  → {mestre.habilidade}  ({mestre.modelos[0]})")
        for nome, ag in equipe.items():
            print(f"   • {nome:<13} {ag.descricao}")
        return 0

    if a.comando == "habilidades":
        for h in habilidades.listar():
            print(f"• {h.caminho:<45} {h.descricao[:70]}")
        return 0

    if a.comando == "fluxos":
        for f in sorted(PASTA_FLUXOS.glob("*.toml")):
            print(f"• {f.stem}")
        return 0

    if a.comando == "perguntar":
        _, equipe = carregar_equipe()
        if a.agente not in equipe:
            print(f"Agente '{a.agente}' não existe. Use: python main.py agentes")
            return 1
        modelo, resposta = equipe[a.agente].executar(a.texto, modelos=[a.modelo] if a.modelo else None)
        print(f"[{modelo}]\n\n{resposta}")
        return 0

    aprovar = None if a.sim else confirmar_plano
    sa = Superagente(forcar_modelo=a.modelo, aprovar=aprovar)
    if a.comando == "executar":
        sa.executar(a.objetivo)
    elif a.comando == "fluxo":
        sa.fluxo(a.nome, a.entrada)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except provedores.ErroProvedor as erro:
        print(f"\n❌ {erro}\nDica: rode 'python main.py diagnostico' e configure o arquivo .env")
        sys.exit(1)
