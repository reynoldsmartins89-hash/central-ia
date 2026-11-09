"""Agente Drive: lê o Google Drive — SEM baixar o Drive para o seu disco e SEM Google Cloud.

Usa o rclone (gratuito, de código aberto). Conexão única: você só AUTORIZA no navegador
(permissão SOMENTE LEITURA):   python main.py drive conectar

  • Catálogo completo (nome, pasta, tipo, tamanho, data) — só metadados.
  • Impressão digital MD5 que o PRÓPRIO GOOGLE calcula → duplicados sem baixar nada.
  • Leitura: cada arquivo vem para um TEMPORÁRIO, o Agente Leitor o lê/transcreve e o
    temporário é apagado na hora. Documentos Google (Docs/Planilhas/Apresentações) viram texto.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from .curador import _conectar

PASTA = Path.home() / ".central-ia"
REMOTO = os.getenv("DRIVE_REMOTO", "gdrive")
EXPORTAVEIS = {"application/vnd.google-apps.document", "application/vnd.google-apps.presentation",
               "application/vnd.google-apps.spreadsheet"}


def _rclone() -> str:
    for c in (PASTA / "ferramentas" / "rclone.exe", shutil.which("rclone")):
        if c and Path(c).exists():
            return str(c)
    raise FileNotFoundError("rclone não encontrado (instale: winget install Rclone.Rclone)")


def _rodar(*args: str, timeout: float | None = None) -> str:
    r = subprocess.run([_rclone(), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout) else "erro")
    return r.stdout


def configurado() -> bool:
    try:
        return f"{REMOTO}:" in _rodar("listremotes")
    except Exception:  # noqa: BLE001
        return False


def comando_conexao() -> list[str]:
    """Comando que abre o navegador para você autorizar (somente leitura)."""
    return [_rclone(), "config", "create", REMOTO, "drive", "scope=drive.readonly"]


def _tabela() -> sqlite3.Connection:
    con = _conectar()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS drive (
            id TEXT PRIMARY KEY, nome TEXT, mime TEXT, tamanho INTEGER, md5 TEXT,
            pais TEXT, modificado TEXT, caminho TEXT);
        CREATE INDEX IF NOT EXISTS idx_drive_md5 ON drive(md5);
    """)
    return con


def status() -> str:
    sobre = json.loads(_rodar("about", f"{REMOTO}:", "--json", timeout=120))
    return (f"✅ Drive conectado (somente leitura): {sobre.get('used', 0) / 1e12:.2f} TB em uso"
            if sobre else "Drive conectado")


def catalogar(log=print) -> int:
    """Lista TODOS os arquivos do Drive com MD5 do Google. Nada é baixado."""
    inicio = time.time()
    log("   pedindo ao Google a lista completa do Drive (pode levar alguns minutos)…")
    itens = json.loads(_rodar("lsjson", f"{REMOTO}:", "-R", "--files-only", "--hash", "--hash-type", "md5",
                              "--fast-list"))
    con = _tabela()
    con.execute("DELETE FROM drive")
    con.executemany("INSERT OR REPLACE INTO drive (id, nome, mime, tamanho, md5, modificado, caminho) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(i.get("ID") or i["Path"], i["Name"], i.get("MimeType", ""), max(int(i.get("Size", 0)), 0),
                      (i.get("Hashes") or {}).get("md5"), i.get("ModTime"), i["Path"]) for i in itens])
    con.commit()
    con.close()
    tamanho = sum(max(int(i.get("Size", 0)), 0) for i in itens)
    log(f"✅ Drive: {len(itens):,} arquivos ({tamanho / 1e12:.2f} TB) catalogados em "
        f"{time.time() - inicio:.0f}s — nada foi baixado")
    return len(itens)


def duplicados(limite: int = 15) -> str:
    """Duplicados pelo MD5 do Google — sem baixar nada."""
    con = _tabela()
    grupos = con.execute("""SELECT md5, max(tamanho), count(*), group_concat(caminho, char(10))
                            FROM drive WHERE md5 IS NOT NULL AND md5 != '' AND tamanho > 0
                            GROUP BY md5 HAVING count(*) > 1
                            ORDER BY max(tamanho) * (count(*) - 1) DESC""").fetchall()
    con.close()
    espaco = sum(t * (n - 1) for _, t, n, _ in grupos)
    linhas = [f"{len(grupos):,} conjuntos de duplicados no Drive · {espaco / 1e9:.1f} GB em cópias (nada baixado)"]
    for _, tamanho, n, caminhos in grupos[:limite]:
        linhas.append(f"\n{n} cópias de {tamanho / 1e6:.0f} MB:")
        linhas += [f"  • {c}" for c in caminhos.split("\n")]
    return "\n".join(linhas)


def baixar_temporario(caminho: str, mime: str) -> Path:
    """Traz UM arquivo para um temporário (apague depois de ler). Docs Google viram texto."""
    pasta = Path(tempfile.mkdtemp(prefix="central-ia-drive-"))
    extra = ["--drive-export-formats", "txt,csv,txt"] if mime in EXPORTAVEIS else []
    _rodar("copy", f"{REMOTO}:{caminho}", str(pasta), *extra, timeout=3600)
    arquivos = [p for p in pasta.rglob("*") if p.is_file()]
    if not arquivos:
        shutil.rmtree(pasta, ignore_errors=True)
        raise FileNotFoundError(caminho)
    return arquivos[0]


def ler_tudo(limite: int | None = None, minutos: float | None = None, log=print) -> dict:
    """Agente Leitor aplicado ao Drive. Retomável: o que já foi lido é pulado."""
    from . import leitor
    con = leitor._tabelas()
    fila = con.execute("""SELECT d.caminho, d.nome, d.mime FROM drive d LEFT JOIN leituras l
                          ON l.caminho = 'drive://' || d.caminho WHERE l.caminho IS NULL
                          ORDER BY d.tamanho""").fetchall()
    fila = [f for f in fila if f[2] in EXPORTAVEIS or leitor.tipo_de(f[1])][:limite]
    log(f"📚 Drive: {len(fila):,} arquivos na fila de leitura")
    fim = time.time() + minutos * 60 if minutos else None
    lidos = erros = 0
    for caminho, nome, mime in fila:
        if fim and time.time() > fim:
            log("⏸ tempo da rodada esgotado; rode de novo para continuar")
            break
        temp = None
        try:
            temp = baixar_temporario(caminho, mime)
            origem = f"drive://{caminho}"
            if leitor.tipo_de(str(temp)) == "compactado":
                lidos += leitor._ler_compactado(con, str(temp), log)
            else:
                lidos += leitor.ler_e_registrar(con, origem, str(temp))
            con.commit()
        except Exception as erro:  # noqa: BLE001
            erros += 1
            log(f"   ⚠ {nome}: {str(erro)[:100]}")
        finally:
            if temp:
                shutil.rmtree(temp.parent, ignore_errors=True)   # nada fica ocupando espaço
    con.close()
    log(f"✅ Drive: {lidos:,} fichas, {erros:,} com erro")
    return {"lidos": lidos, "erros": erros}
