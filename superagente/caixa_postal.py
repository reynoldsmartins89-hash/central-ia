"""Caixa postal da Central: o servidor na nuvem recebe TODAS as mensagens do Telegram e as guarda
por até 72 h. Assim o Telegram só tem UM leitor (o servidor) e nunca dá conflito.

  • PC LIGADO   → o bot do PC busca as mensagens na caixa (por SSH, com a chave da Central) e faz
                  tudo como sempre: Executor, HDs, IA local, transcrição na placa de vídeo.
  • PC DESLIGADO → o bot da nuvem atende o que dá para atender na nuvem (conversa, especialistas,
                  áudio, foto, documento, produção). O que só o PC sabe fazer (mexer em arquivos,
                  catálogo dos HDs, agentes privados) fica guardado na caixa e o PC faz quando ligar.

Configuração:
  • no SERVIDOR (.env):  CENTRAL_PAPEL=nuvem
  • no PC (.env):        CAIXA_POSTAL=ubuntu@<ip do servidor>
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

PASTA = Path.home() / ".central-ia"
BANCO = PASTA / "caixa.db"
JANELA = int(os.getenv("JANELA_PENDENTES_HORAS", "72")) * 3600
PC_AUSENTE_APOS = 90          # segundos sem o PC buscar mensagens = PC desligado
CHAVE_SSH = Path.home() / ".ssh" / "central_ia_oracle"


def papel() -> str:
    """'nuvem' (servidor), 'pc_com_caixa' (PC usando o servidor) ou 'sozinho' (PC sem servidor)."""
    if os.getenv("CENTRAL_PAPEL", "").strip().lower() == "nuvem":
        return "nuvem"
    return "pc_com_caixa" if os.getenv("CAIXA_POSTAL", "").strip() else "sozinho"


class PrecisaDoPC(Exception):
    """A mensagem só pode ser atendida pelo PC: fica guardada na caixa até ele ligar."""


# ------------------------------------------------------------------ lado do SERVIDOR
def _banco() -> sqlite3.Connection:
    PASTA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BANCO, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS mensagens (update_id INTEGER PRIMARY KEY, dados TEXT,
                   data REAL, dono TEXT, so_pc INTEGER DEFAULT 0)""")
    con.execute("CREATE TABLE IF NOT EXISTS estado (chave TEXT PRIMARY KEY, valor REAL)")
    return con


def pc_online(con: sqlite3.Connection | None = None) -> bool:
    con = con or _banco()
    visto = con.execute("SELECT valor FROM estado WHERE chave = 'pc_visto'").fetchone()
    return bool(visto and time.time() - visto[0] < PC_AUSENTE_APOS)


def receber() -> None:
    """Único leitor do Telegram: guarda cada mensagem do SEU chat na caixa (roda no servidor)."""
    from . import telegram
    meu_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    con = _banco()
    ultimo = con.execute("SELECT MAX(update_id) FROM mensagens").fetchone()[0] or 0
    print("📬 Caixa postal recebendo mensagens do Telegram.")
    while True:
        try:
            r = telegram._api("getUpdates", {"offset": ultimo + 1, "timeout": 50,
                                             "allowed_updates": ["message", "callback_query"]}, timeout=60)
        except Exception as erro:  # noqa: BLE001
            print(f"(sem conexão com o Telegram: {erro}; tentando de novo em 10s)")
            time.sleep(10)
            continue
        for u in r.get("result", []):
            ultimo = max(ultimo, u["update_id"])
            origem = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
            if str(origem.get("chat", {}).get("id")) != meu_chat:
                continue
            data = (u.get("message") or {}).get("date") or time.time()
            con.execute("INSERT OR IGNORE INTO mensagens (update_id, dados, data) VALUES (?, ?, ?)",
                        (u["update_id"], json.dumps(u, ensure_ascii=False), data))
        con.execute("DELETE FROM mensagens WHERE data < ?", (time.time() - JANELA - 3600,))
        con.commit()


def pegar(quem: str, espera: float = 25) -> list[dict]:
    """Entrega (e marca como entregues) as mensagens ainda não atendidas.
    quem='pc' sempre recebe (e avisa que o PC está ligado); quem='nuvem' só recebe com o PC desligado
    e nunca recebe as que ficaram guardadas para o PC."""
    con = _banco()
    limite = time.time() + espera
    while True:
        if quem == "pc":
            con.execute("INSERT OR REPLACE INTO estado VALUES ('pc_visto', ?)", (time.time(),))
            con.commit()
            filtro = ""
        elif pc_online(con):
            filtro = None
        else:
            filtro = "AND so_pc = 0"
        if filtro is not None:
            linhas = con.execute(f"SELECT update_id, dados FROM mensagens WHERE dono IS NULL {filtro} "
                                 "AND data >= ? ORDER BY update_id", (time.time() - JANELA,)).fetchall()
            if linhas:
                con.executemany("UPDATE mensagens SET dono = ? WHERE update_id = ?",
                                [(quem, uid) for uid, _ in linhas])
                con.commit()
                return [json.loads(d) for _, d in linhas]
        if time.time() >= limite:
            return []
        time.sleep(1)


def espiar_pc() -> list[dict]:
    """Mensagens esperando o PC (sem marcar como entregues)."""
    con = _banco()
    return [json.loads(d) for (d,) in con.execute(
        "SELECT dados FROM mensagens WHERE dono IS NULL AND data >= ? ORDER BY update_id",
        (time.time() - JANELA,)).fetchall()]


def guardar_para_o_pc(update_id: int) -> None:
    con = _banco()
    con.execute("UPDATE mensagens SET dono = NULL, so_pc = 1 WHERE update_id = ?", (update_id,))
    con.commit()


# ------------------------------------------------------------------ lado do PC (fala com o servidor por SSH)
def _ssh(comando: str, timeout: float = 60) -> str:
    destino = os.getenv("CAIXA_POSTAL", "").strip()
    r = subprocess.run(["ssh", "-i", str(CHAVE_SSH), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                        "-o", "ServerAliveInterval=15", destino, comando],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:] or "falha no SSH")
    return r.stdout


def _no_servidor(*args: str, timeout: float = 60) -> str:
    return _ssh("cd ~/central-ia && .venv/bin/python main.py caixa " + " ".join(args), timeout=timeout)


def remoto_pegar(espera: float = 25) -> list[dict]:
    return json.loads(_no_servidor("pegar", str(int(espera)), timeout=espera + 40) or "[]")


def remoto_espiar() -> list[dict]:
    return json.loads(_no_servidor("espiar") or "[]")


def _scp(origem: str, destino: str, timeout: float = 3600) -> None:
    r = subprocess.run(["scp", "-q", "-p", "-i", str(CHAVE_SSH), "-o", "BatchMode=yes", origem, destino],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "falha no scp").strip()[-300:])


MEMORIA = ("conversa.json", "perfil.json")


def baixar_memoria() -> int:
    """Traz do servidor a memória da conversa, se lá estiver mais nova (conversas feitas com o PC desligado)."""
    import tempfile
    host = os.getenv("CAIXA_POSTAL", "").strip()
    trazidos = 0
    with tempfile.TemporaryDirectory() as tmp:
        for nome in MEMORIA:
            try:
                _scp(f"{host}:.central-ia/{nome}", str(Path(tmp) / nome), timeout=60)
            except RuntimeError:
                continue   # ainda não existe no servidor
            novo, local = Path(tmp) / nome, PASTA / nome
            if not local.exists() or novo.stat().st_mtime > local.stat().st_mtime + 1:
                local.write_bytes(novo.read_bytes())
                os.utime(local, (novo.stat().st_mtime, novo.stat().st_mtime))
                trazidos += 1
    return trazidos


def enviar_memoria() -> None:
    """Leva a memória do PC para o servidor (para a nuvem continuar a conversa quando o PC desligar)."""
    host = os.getenv("CAIXA_POSTAL", "").strip()
    for nome in MEMORIA:
        if (PASTA / nome).exists():
            try:
                _scp(str(PASTA / nome), f"{host}:.central-ia/{nome}", timeout=60)
            except Exception:  # noqa: BLE001
                pass


def sincronizar_dados(log=print) -> int:
    """Envia ao servidor as bibliotecas dos agentes e o acervo lido (só o que mudou)."""
    host = os.getenv("CAIXA_POSTAL", "").strip()
    if not host:
        return 0
    import tempfile
    registro = PASTA / "sincronizado.json"
    feitos = json.loads(registro.read_text(encoding="utf-8")) if registro.exists() else {}
    bancos = [PASTA / "catalogo.db"] + sorted((PASTA / "indices").glob("*.db"))
    _ssh("mkdir -p ~/.central-ia/indices")
    enviados = 0
    for banco in bancos:
        marca = max([banco.stat().st_mtime] + [p.stat().st_mtime for p in banco.parent.glob(banco.name + "-wal")])
        if feitos.get(str(banco)) == marca:
            continue
        relativo = banco.relative_to(PASTA).as_posix()
        log(f"☁️ enviando {relativo} ({banco.stat().st_size / 1e6:.0f} MB)…")
        with tempfile.TemporaryDirectory() as tmp:
            copia = Path(tmp) / banco.name
            origem, destino = sqlite3.connect(banco, timeout=300), sqlite3.connect(copia)
            origem.backup(destino)   # cópia consistente mesmo com o banco em uso
            origem.close()
            destino.close()
            _scp(str(copia), f"{host}:.central-ia/{relativo}.novo")
        _ssh(f"mv ~/.central-ia/{relativo}.novo ~/.central-ia/{relativo} && rm -f ~/.central-ia/{relativo}-wal "
             f"~/.central-ia/{relativo}-shm")
        feitos[str(banco)] = marca
        registro.write_text(json.dumps(feitos, ensure_ascii=False, indent=1), encoding="utf-8")
        enviados += 1
    log(f"☁️ servidor atualizado ({enviados} bancos enviados)")
    return enviados


def trazer_resultados(log=print) -> int:
    """Traz para o PC os trabalhos que a nuvem produziu com o PC desligado."""
    from .orquestrador import PASTA_RESULTADOS
    host = os.getenv("CAIXA_POSTAL", "").strip()
    remotos = [l.strip() for l in _ssh("ls ~/central-ia/resultados 2>/dev/null || true").splitlines() if l.strip()]
    novos = [n for n in remotos if not (PASTA_RESULTADOS / n).exists()]
    for nome in novos:
        _scp_dir = ["scp", "-q", "-r", "-p", "-i", str(CHAVE_SSH), "-o", "BatchMode=yes",
                    f"{host}:central-ia/resultados/{nome}", str(PASTA_RESULTADOS)]
        subprocess.run(_scp_dir, capture_output=True, timeout=600)
    if novos:
        log(f"☁️ {len(novos)} trabalho(s) feitos na nuvem trazidos para resultados/")
    return len(novos)
