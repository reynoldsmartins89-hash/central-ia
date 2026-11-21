"""Diário da Central: uma nota por dia com o que foi feito e decidido.

Adaptado da memória diária do OpenClaw (`memory/AAAA-MM-DD.md`). Lá, cada dia ganha um arquivo em
Markdown. Antes de comprimir a conversa, uma "gravação" silenciosa anota o essencial no dia, e de
madrugada o que é durável sobe para a memória permanente. Aqui fica igual, com a regra da casa:
  • cada trabalho em equipe, ordem do Executor, tarefa agendada e aviso proativo vira uma linha do dia;
  • quando a conversa é comprimida, o resumo também entra no dia (nada do que foi dito se perde);
  • hoje e ontem acompanham cada pedido; os dias antigos ficam pesquisáveis (sessoes.py);
  • à noite, o batimento relê o dia e PROPÕE o que vale guardar para sempre (você aprova com ✅).
Pasta: ~/.central-ia/diario/AAAA-MM-DD.md (só no seu PC; ainda não vai para o servidor da nuvem).
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from pathlib import Path

PASTA = Path.home() / ".central-ia" / "diario"
LIMITE_CONTEXTO = 2500      # caracteres de hoje + ontem que vão junto com cada pedido
LIMITE_LINHA = 400
_trava = threading.Lock()


def arquivo(dia: date | None = None) -> Path:
    return PASTA / f"{(dia or date.today()).isoformat()}.md"


def registrar(texto: str, tipo: str = "nota", quando: datetime | None = None) -> None:
    """Acrescenta uma linha ao dia. Linha repetida (mesmo texto no mesmo dia) não duplica."""
    texto = " ".join((texto or "").split())[:LIMITE_LINHA]
    if not texto:
        return
    quando = quando or datetime.now()
    alvo = arquivo(quando.date())
    with _trava:
        PASTA.mkdir(parents=True, exist_ok=True)
        atual = alvo.read_text(encoding="utf-8") if alvo.exists() else \
            f"# Diário da Central — {quando:%d/%m/%Y}\n\n"
        if f"] {texto}\n" in atual:
            return
        alvo.write_text(atual + f"- {quando:%H:%M} [{tipo}] {texto}\n", encoding="utf-8")
    try:   # o dia também entra na lembrança de longo prazo (pesquisável)
        from . import sessoes
        sessoes.registrar(f"diario-{quando:%Y-%m-%d}", "central", f"[{tipo}] {texto}",
                          quando.timestamp(), origem="diario", titulo=f"Diário {quando:%d/%m/%Y}")
    except Exception:  # noqa: BLE001
        pass


def ler(dia: date | None = None) -> str:
    try:
        return arquivo(dia).read_text(encoding="utf-8")
    except OSError:
        return ""


def contexto(hoje: date | None = None, limite: int = LIMITE_CONTEXTO) -> str:
    """Bloco com o diário de hoje e de ontem (o mais recente vence quando falta espaço)."""
    hoje = hoje or date.today()
    partes = []
    for dia, rotulo in ((hoje, "hoje"), (hoje - timedelta(days=1), "ontem")):
        linhas = [l for l in ler(dia).splitlines() if l.startswith("- ")]
        if linhas:
            partes.append((rotulo, linhas))
    if not partes:
        return ""
    saida, total = [], 0
    for rotulo, linhas in partes:
        escolhidas = []
        for linha in reversed(linhas):
            if total + len(linha) > limite:
                break
            escolhidas.append(linha)
            total += len(linha)
        if escolhidas:
            saida.append(f"### {rotulo.capitalize()}\n" + "\n".join(reversed(escolhidas)))
    return "## Diário da Central (o que já foi feito)\n" + "\n\n".join(saida) if saida else ""
