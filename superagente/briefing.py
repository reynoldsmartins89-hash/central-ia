"""Briefings diários no Telegram — a Central entrega valor sem você pedir.

  • 7h30 (manhã): o que a Central produziu, o que espera o seu ✅, o foco do dia (produto 1).
  • 20h (noite): fechamento — o que foi feito hoje e o que vem amanhã.
Roda no servidor (cron), então chega mesmo com o PC desligado.
Uso:  python main.py briefing manha | noite
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from .agentes import RAIZ

RESULTADOS = RAIZ / "resultados"


def _trabalhos_recentes(horas: float) -> list[str]:
    limite = time.time() - horas * 3600
    if not RESULTADOS.exists():
        return []
    pastas = sorted((p for p in RESULTADOS.iterdir() if p.is_dir() and p.stat().st_mtime >= limite),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name.split("_", 2)[-1].replace("-", " ") for p in pastas[:8]]


def _guerra_ensina() -> str:
    pasta = RESULTADOS / "a-guerra-ensina"
    if not pasta.exists():
        return "A Guerra Ensina: ainda sem artigos prontos."
    prontos = sorted(pasta.glob("*.md"))
    return f"A Guerra Ensina: {len(prontos)} texto(s) prontos para sua revisão em resultados/a-guerra-ensina."


def montar(periodo: str) -> str:
    hoje = datetime.now()
    if periodo == "manha":
        recentes = _trabalhos_recentes(16)
        linhas = [f"☀️ Bom dia, Reynolds — {hoje:%d/%m}", ""]
        linhas.append("**Produzido desde ontem:** " + (", ".join(recentes) if recentes else "nada novo."))
        linhas.append(_guerra_ensina())
        linhas += ["", "🎯 **Foco de hoje:** revisar e aprovar o próximo texto de A Guerra Ensina (≈ 20 min).",
                   "Responda aqui com áudio se quiser ajustar algo."]
    else:
        recentes = _trabalhos_recentes(14)
        linhas = [f"🌙 Fechamento do dia — {hoje:%d/%m}", ""]
        linhas.append("**Feito hoje:** " + (", ".join(recentes) if recentes else "nenhum trabalho novo registrado."))
        linhas.append(_guerra_ensina())
        linhas += ["", "📅 **Amanhã:** a Central prepara o próximo artigo; o briefing das 7h30 traz o que revisar."]
    return "\n".join(linhas)


def enviar(periodo: str) -> None:
    from . import telegram
    telegram.enviar(montar(periodo))
