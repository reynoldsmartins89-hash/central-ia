"""Acha as conversas do Claude (app desktop) que pararam por limite de uso.

Uma conversa conta como "parada por limite" quando a última mensagem dela é o
aviso de limite atingido ("You've hit your ... limit") ou um erro 429 de limite.
Ela só é listada depois que o limite já voltou (resetsAt no passado) e se ainda
não foi retomada.

Uso:
    python scripts/retomar_apos_limite.py            # lista em JSON as que devem ser retomadas
    python scripts/retomar_apos_limite.py --marcar local_xxx   # anota que essa já foi retomada

Este script só lê. Quem manda a mensagem "continue" é a tarefa agendada
"retomar-apos-limite" do app, usando o id `local_...` que ele devolve.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

HOME = Path.home()
PASTA_SESSOES = HOME / "AppData" / "Local" / "Claude-Data"
PASTA_PROJETOS = HOME / ".claude" / "projects"
ESTADO = HOME / ".central-ia" / "retomadas_limite.json"
JANELA_HORAS = 48  # ignora paradas mais antigas que isso
FOLGA_SEGUNDOS = 120  # espera 2 min depois do horário de volta do limite


def _ler_json(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def ultimo_registro(transcricao: Path) -> dict | None:
    """Último registro de conversa (usuário ou assistente) fora de subagentes."""
    ultimo = None
    try:
        with transcricao.open(encoding="utf-8", errors="replace") as f:
            for linha in f:
                if '"type":"user"' not in linha and '"type":"assistant"' not in linha:
                    continue
                try:
                    d = json.loads(linha)
                except ValueError:
                    continue
                if d.get("isSidechain") or d.get("type") not in ("user", "assistant"):
                    continue
                ultimo = d
    except OSError:
        return None
    return ultimo


def parada_por_limite(reg: dict | None) -> tuple[bool, float | None]:
    """(parou por limite?, horário em que o limite volta, em epoch)."""
    if not reg or reg.get("type") != "assistant":
        return False, None
    erro = reg.get("apiError") or ""
    tipo = reg.get("apiErrorType") or ""
    texto = ""
    for b in (reg.get("message") or {}).get("content") or []:
        if isinstance(b, dict) and b.get("type") == "text":
            texto += b.get("text", "")
    limite = (
        erro == "usage_limit_reached"
        or tipo == "rate_limit_error"
        or texto.startswith("You've hit your")
    )
    if not limite:
        return False, None
    info = (reg.get("apiErrorParams") or {}).get("rate_limit_info") or reg.get("quotaLimits") or {}
    volta = info.get("resetsAt")
    return True, float(volta) if volta else None


def _epoch(ts: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def achar_transcricao(cli_id: str) -> Path | None:
    achados = glob.glob(str(PASTA_PROJETOS / "*" / f"{cli_id}.jsonl"))
    return Path(achados[0]) if achados else None


def carregar_estado() -> dict:
    return _ler_json(ESTADO)


def salvar_estado(estado: dict) -> None:
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


def listar(agora: float | None = None, ignorar_tempo: bool = False) -> list[dict]:
    agora = agora or time.time()
    estado = carregar_estado()
    saida = []
    for meta_path in glob.glob(str(PASTA_SESSOES / "**" / "claude-code-sessions" / "**" / "local_*.json"), recursive=True):
        meta = _ler_json(Path(meta_path))
        if not meta or meta.get("isArchived"):
            continue
        if meta.get("scheduledTaskId"):
            continue  # rodadas de tarefas agendadas: a própria tarefa roda de novo sozinha

        cli_id = meta.get("lastAssistantCliSessionId") or meta.get("cliSessionId")
        if not cli_id:
            continue
        transcricao = achar_transcricao(cli_id)
        if not transcricao:
            continue
        reg = ultimo_registro(transcricao)
        parou, volta = parada_por_limite(reg)
        if not parou:
            continue
        quando = _epoch(reg.get("timestamp", ""))
        if not ignorar_tempo:
            if agora - quando > JANELA_HORAS * 3600:
                continue
            if volta and agora < volta + FOLGA_SEGUNDOS:
                continue  # o limite ainda não voltou
        sid = meta.get("sessionId")
        if not ignorar_tempo and estado.get(sid) == reg.get("uuid"):
            continue  # essa parada já foi retomada
        saida.append({
            "sessionId": sid,
            "titulo": meta.get("title", ""),
            "parouEm": reg.get("timestamp"),
            "limiteVoltouEm": time.strftime("%Y-%m-%d %H:%M", time.localtime(volta)) if volta else None,
            "marcador": reg.get("uuid"),
        })
    return saida


def marcar(session_id: str) -> bool:
    for item in listar(ignorar_tempo=True):  # só acha o marcador atual
        if item["sessionId"] == session_id:
            estado = carregar_estado()
            estado[session_id] = item["marcador"]
            salvar_estado(estado)
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--marcar", metavar="SESSION_ID", help="anota que essa conversa já foi retomada")
    a = p.parse_args(argv)
    if a.marcar:
        ok = marcar(a.marcar)
        print("marcada" if ok else "não encontrada entre as paradas por limite")
        return 0 if ok else 1
    print(json.dumps(listar(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
