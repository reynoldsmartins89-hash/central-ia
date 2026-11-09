"""Agente Fotos: envia fotos e vídeos BONS do seu HD para o Google Fotos (pelo rclone).

Conexão única: você só AUTORIZA no navegador (python main.py fotos conectar).
O Google Fotos já ignora sozinho um arquivo IDÊNTICO a um que você já tem; para as fotos
parecidas (não idênticas), peça depois ao Gemini dentro do Google Fotos.
Só vão fotos/vídeos que abriram sem problema na verificação de integridade — nada quebrado.
Nada é apagado do HD.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .curador import _conectar, _raiz
from .drive import _rclone, _rodar

REMOTO = os.getenv("FOTOS_REMOTO", "gfotos")
ALBUM_PADRAO = "Fotos do HD (Central IA)"
FOTOS = {".jpg", ".jpeg", ".jpe", ".png", ".heic", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEOS = {".mp4", ".mov", ".avi", ".wmv", ".mkv", ".m4v", ".3gp", ".mpg", ".mts", ".m2ts"}
TAMANHO_MINIMO = 30_000   # menos que isso costuma ser ícone/figura de página, não foto


def configurado() -> bool:
    try:
        return f"{REMOTO}:" in _rodar("listremotes")
    except Exception:  # noqa: BLE001
        return False


def comando_conexao() -> list[str]:
    return [_rclone(), "config", "create", REMOTO, "google photos", "read_only=false"]


def selecionar(pasta: str | Path) -> tuple[Path, list[str], int]:
    """Fotos/vídeos da pasta que NÃO têm problema de integridade. Devolve (raiz, relativos, bytes)."""
    pasta = _raiz(pasta)
    raiz = Path(pasta.anchor)
    con = _conectar()
    linhas = con.execute("""SELECT a.caminho, a.tamanho FROM arquivos a LEFT JOIN integridade i
                            ON i.caminho = a.caminho WHERE i.caminho IS NULL AND a.caminho LIKE ?""",
                         (str(pasta).rstrip("\\/") + "\\%",)).fetchall()
    con.close()
    escolhidos, total = [], 0
    for caminho, tamanho in linhas:
        p = Path(caminho)
        ext = p.suffix.lower()
        if (ext in FOTOS and (tamanho or 0) >= TAMANHO_MINIMO or ext in VIDEOS) and p.exists():
            escolhidos.append(p.relative_to(raiz).as_posix())
            total += tamanho or 0
    return raiz, escolhidos, total


def enviar(pasta: str | Path, album: str = ALBUM_PADRAO, log=print) -> int:
    raiz, arquivos, total = selecionar(pasta)
    log(f"📸 {len(arquivos):,} fotos/vídeos bons ({total / 1e9:.1f} GB) → Google Fotos, álbum '{album}'")
    if not arquivos:
        return 0
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as lista:
        lista.write("\n".join(arquivos))
    try:
        _rodar("copy", str(raiz), f"{REMOTO}:album/{album}", "--files-from", lista.name,
               "--transfers", "4", "--retries", "5", "--low-level-retries", "20", timeout=None)
    finally:
        os.unlink(lista.name)
    log(f"✅ Envio concluído. Abra photos.google.com → Álbuns → '{album}'.")
    return len(arquivos)
