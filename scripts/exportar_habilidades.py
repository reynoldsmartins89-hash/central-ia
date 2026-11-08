"""Copia as habilidades para o Claude Code e/ou Gemini CLI usarem diretamente.

    python scripts/exportar_habilidades.py claude   # → ~/.claude/skills/<nome>/SKILL.md
    python scripts/exportar_habilidades.py gemini   # → ~/.gemini/skills/<nome>/SKILL.md

Assim a mesma biblioteca (versionada no GitHub) alimenta várias ferramentas.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINOS = {
    "claude": Path.home() / ".claude" / "skills",
    "gemini": Path.home() / ".gemini" / "skills",
}

if len(sys.argv) != 2 or sys.argv[1] not in DESTINOS:
    sys.exit(f"Uso: python {Path(__file__).name} [{'|'.join(DESTINOS)}]")

destino = DESTINOS[sys.argv[1]]
for skill in (RAIZ / "habilidades").rglob("SKILL.md"):
    pasta = destino / skill.parent.name
    pasta.mkdir(parents=True, exist_ok=True)
    shutil.copy2(skill, pasta / "SKILL.md")
    print(f"✔ {skill.parent.name} → {pasta}")
