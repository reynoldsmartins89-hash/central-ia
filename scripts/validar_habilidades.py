"""Valida a estrutura do repositório (usado também no GitHub Actions).

Confere: todo SKILL.md tem name/description, nomes em minúsculas-com-hífen,
toda categoria tem DESCRIPTION.md e todo agente/fluxo aponta para algo que existe.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.stdout.reconfigure(encoding="utf-8")

from superagente.habilidades import PASTA_HABILIDADES, ler_frontmatter  # noqa: E402

erros: list[str] = []

for skill in PASTA_HABILIDADES.rglob("SKILL.md"):
    dados, corpo = ler_frontmatter(skill.read_text(encoding="utf-8"))
    rel = skill.relative_to(RAIZ).as_posix()
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", dados.get("name", "")):
        erros.append(f"{rel}: 'name' ausente ou fora do padrão minúsculas-com-hífen")
    if len(dados.get("description", "")) < 20:
        erros.append(f"{rel}: 'description' ausente ou curta demais")
    if not corpo:
        erros.append(f"{rel}: corpo vazio")
    if dados.get("name") and dados["name"] != skill.parent.name:
        erros.append(f"{rel}: 'name' deve ser igual ao nome da pasta ({skill.parent.name})")

for categoria in (p for p in PASTA_HABILIDADES.iterdir() if p.is_dir()):
    if not (categoria / "DESCRIPTION.md").exists():
        erros.append(f"habilidades/{categoria.name}: falta DESCRIPTION.md")

with open(RAIZ / "agentes" / "agentes.toml", "rb") as f:
    config = tomllib.load(f)
agentes = {"superagente": config["superagente"], **config.get("agentes", {})}
for nome, ag in agentes.items():
    if not (PASTA_HABILIDADES / ag["habilidade"] / "SKILL.md").exists():
        erros.append(f"agente {nome}: habilidade inexistente '{ag['habilidade']}'")
    if not ag.get("modelos"):
        erros.append(f"agente {nome}: lista 'modelos' vazia")

for nome, combo in config.get("combinacoes", {}).items():
    for ag in combo.get("agentes", []):
        if ag not in agentes:
            erros.append(f"combinação {nome}: agente inexistente '{ag}'")
    if not combo.get("quando"):
        erros.append(f"combinação {nome}: falta 'quando'")

for fluxo in (RAIZ / "fluxos").glob("*.toml"):
    with open(fluxo, "rb") as f:
        for etapa in tomllib.load(f).get("etapas", []):
            if etapa["agente"] not in agentes:
                erros.append(f"fluxo {fluxo.stem}: agente inexistente '{etapa['agente']}'")

if erros:
    print("❌ Problemas encontrados:\n  - " + "\n  - ".join(erros))
    sys.exit(1)
print(f"✅ Estrutura válida: {len(agentes)} agentes, "
      f"{len(list(PASTA_HABILIDADES.rglob('SKILL.md')))} habilidades.")
