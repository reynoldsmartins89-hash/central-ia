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
from . import drive
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
    except drive.ConfigIndisponivel:
        raise
    except RuntimeError as erro:   # o Google recusou alguma foto: as demais já foram enviadas
        log(f"⚠ O Google recusou parte das fotos ({str(erro)[-120:]}). Rode de novo para tentar só as que faltam.")
    finally:
        os.unlink(lista.name)
    log(f"✅ Envio concluído. Abra photos.google.com → Álbuns → '{album}'.")
    return len(arquivos)


def enviar_recuperadas(album: str = "Fotos recuperadas (Central IA)", tamanho: str = "foto", log=print) -> int:
    """Envia as fotos que o Reparador garimpou de arquivos ilegíveis.
    tamanho='foto' → só as de 400 px ou mais; 'tudo' → inclui as miniaturas."""
    con = _conectar()
    filtro = "detalhe LIKE 'foto %'" if tamanho == "foto" else "(detalhe LIKE 'foto %' OR detalhe LIKE 'miniatura %')"
    destinos = [d for (d,) in con.execute(f"SELECT destino FROM reparos WHERE situacao = 'reparado' AND {filtro} "
                                          "AND destino IS NOT NULL").fetchall()]
    con.close()
    arquivos = [Path(d) for d in destinos if d and Path(d).exists()]
    if not arquivos:
        log("Nenhuma foto recuperada encontrada.")
        return 0
    total = sum(a.stat().st_size for a in arquivos)
    log(f"📸 {len(arquivos):,} fotos recuperadas ({total / 1e9:.2f} GB) → Google Fotos, álbum '{album}'")
    por_pasta: dict[Path, list[str]] = {}
    for a in arquivos:   # envia pasta por pasta para cair tudo num ÚNICO álbum (sem subálbuns)
        por_pasta.setdefault(a.parent, []).append(a.name)
    for n, (pasta, nomes) in enumerate(por_pasta.items(), 1):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as lista:
            lista.write("\n".join(nomes))
        try:
            _rodar("copy", str(pasta), f"{REMOTO}:album/{album}", "--files-from", lista.name,
                   "--transfers", "4", "--retries", "5", "--low-level-retries", "20", timeout=None)
            log(f"   pasta {n}/{len(por_pasta)} enviada ({len(nomes)} fotos)")
        except drive.ConfigIndisponivel:
            raise
        except RuntimeError as erro:   # o Google recusou alguma foto: segue com as outras
            log(f"   ⚠ pasta {n}/{len(por_pasta)}: o Google recusou parte das fotos ({str(erro)[-120:]})")
        finally:
            os.unlink(lista.name)
    log(f"✅ Enviadas. Abra photos.google.com → Álbuns → '{album}'.")
    return len(arquivos)
