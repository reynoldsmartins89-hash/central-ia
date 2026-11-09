"""Agente de integridade: encontra arquivos corrompidos, vazios ou incompletos. SOMENTE LEITURA.

Verificações (rápidas, sem abrir tudo por inteiro):
  • vazio (0 bytes) e downloads incompletos (.crdownload, .part, .tmp, .!ut)
  • PDF: cabeçalho e estrutura (abre com o leitor de PDF)
  • Office/e-book/zip (.docx .xlsx .pptx .epub .odt .zip): estrutura interna do pacote
  • Imagens (.jpg .png .gif .bmp .webp): verificação completa da imagem
  • Vídeo MP4/MOV: presença dos blocos 'ftyp' e 'moov' (vídeo sem 'moov' = gravação cortada)
  • MP3: cabeçalho de áudio; RAR/7z: assinatura do arquivo
O REPARO é uma fase separada e sempre trabalha numa CÓPIA — o original nunca é alterado.
"""
from __future__ import annotations

import logging
import os
import struct
import time
import zipfile
from pathlib import Path

from .curador import _conectar, _raiz

logging.getLogger("pypdf").setLevel(logging.ERROR)

INCOMPLETOS = {".crdownload", ".part", ".partial", ".tmp", ".!ut", ".download"}
PACOTES_ZIP = {".docx", ".xlsx", ".pptx", ".epub", ".odt", ".ods", ".odp", ".zip"}
IMAGENS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
VIDEOS_MP4 = {".mp4", ".m4v", ".mov", ".m4a", ".3gp"}


def _tabela():
    con = _conectar()
    con.execute("CREATE TABLE IF NOT EXISTS integridade (caminho TEXT PRIMARY KEY, problema TEXT, detalhe TEXT)")
    return con


def _mp4(caminho: Path, tamanho: int) -> str | None:
    """Percorre os blocos de nível superior do MP4/MOV procurando 'ftyp' e 'moov'."""
    vistos, posicao = set(), 0
    with open(caminho, "rb") as f:
        while posicao < tamanho and len(vistos) < 50:
            f.seek(posicao)
            cabecalho = f.read(16)
            if len(cabecalho) < 8:
                break
            tam, tipo = struct.unpack(">I4s", cabecalho[:8])
            if tam == 1 and len(cabecalho) >= 16:
                tam = struct.unpack(">Q", cabecalho[8:16])[0]
            elif tam == 0:
                tam = tamanho - posicao
            vistos.add(tipo)
            if tam < 8:
                return "estrutura do vídeo inválida"
            posicao += tam
    if b"ftyp" not in vistos and b"moov" not in vistos:
        return "não parece um vídeo MP4/MOV válido"
    if b"moov" not in vistos:
        return "vídeo incompleto (sem índice 'moov' — gravação/cópia interrompida)"
    if posicao > tamanho + 8:
        return "vídeo truncado (arquivo menor que o esperado)"
    return None


def verificar(caminho: str | Path) -> str | None:
    """Retorna a descrição do problema, ou None se o arquivo parecer íntegro."""
    p = Path(caminho)
    ext = p.suffix.lower()
    try:
        tamanho = p.stat().st_size
        if ext in INCOMPLETOS:
            return "download/cópia incompleto"
        if tamanho == 0:
            return "arquivo vazio (0 bytes)"
        if ext == ".pdf":
            with open(p, "rb") as f:
                if b"%PDF" not in f.read(1024):
                    return "PDF sem cabeçalho válido"
            from pypdf import PdfReader
            if len(PdfReader(str(p)).pages) == 0:
                return "PDF sem páginas"
        elif ext in PACOTES_ZIP:
            with zipfile.ZipFile(p) as z:
                if tamanho < 200_000_000 and z.testzip() is not None:
                    return "pacote com conteúdo danificado"
        elif ext in IMAGENS:
            from PIL import Image
            with Image.open(p) as img:
                img.verify()
        elif ext in VIDEOS_MP4:
            return _mp4(p, tamanho)
        elif ext == ".mp3":
            with open(p, "rb") as f:
                inicio = f.read(3)
            if inicio != b"ID3" and not (inicio[:1] == b"\xff" and inicio[1] & 0xE0 == 0xE0):
                return "MP3 sem cabeçalho de áudio válido"
        elif ext == ".rar":
            with open(p, "rb") as f:
                if not f.read(7).startswith(b"Rar!"):
                    return "RAR com assinatura inválida"
        elif ext == ".7z":
            with open(p, "rb") as f:
                if f.read(6) != b"7z\xbc\xaf\x27\x1c":
                    return "7z com assinatura inválida"
    except zipfile.BadZipFile:
        return "pacote (zip/Office) corrompido"
    except OSError as erro:
        return f"erro de leitura do disco ({erro.__class__.__name__})"
    except Exception as erro:  # noqa: BLE001 — qualquer falha ao abrir indica dano
        return f"não abre: {erro.__class__.__name__}: {str(erro)[:80]}"
    return None


VERIFICAVEIS = INCOMPLETOS | PACOTES_ZIP | IMAGENS | VIDEOS_MP4 | {".pdf", ".mp3", ".rar", ".7z"}


def varrer(raiz: str | Path, limite: int | None = None, log=print) -> dict:
    """Verifica os arquivos catalogados de uma raiz. Somente leitura."""
    raiz = _raiz(raiz)
    con = _tabela()
    linhas = con.execute("SELECT caminho, extensao, tamanho FROM arquivos WHERE raiz = ?", (str(raiz),)).fetchall()
    alvos = [c for c, ext, tam in linhas if ext in VERIFICAVEIS or tam == 0][:limite]
    problemas, inicio = 0, time.time()
    for n, caminho in enumerate(alvos, 1):
        problema = verificar(caminho)
        if problema:
            problemas += 1
            con.execute("INSERT OR REPLACE INTO integridade VALUES (?, ?, ?)",
                        (caminho, problema.split(":")[0], problema))
        else:
            con.execute("DELETE FROM integridade WHERE caminho = ?", (caminho,))
        if n % 2000 == 0:
            con.commit()
            log(f"   {n:,}/{len(alvos):,} verificados, {problemas:,} com problema ({time.time() - inicio:.0f}s)")
    con.commit()
    con.close()
    log(f"✅ {len(alvos):,} arquivos verificados em {raiz}: {problemas:,} com problema")
    return {"verificados": len(alvos), "problemas": problemas}


def relatorio(raiz: str | Path | None = None, exemplos: int = 5) -> str:
    con = _tabela()
    filtro, args = ("WHERE caminho LIKE ?", (str(_raiz(raiz)).rstrip("\\") + "\\%",)) if raiz else ("", ())
    tipos = con.execute(f"SELECT problema, count(*) FROM integridade {filtro} GROUP BY problema "
                        "ORDER BY count(*) DESC", args).fetchall()
    linhas = [f"{sum(n for _, n in tipos):,} arquivos com problema"]
    for problema, n in tipos:
        linhas.append(f"\n• {problema}: {n:,}")
        for (c,) in con.execute(f"SELECT caminho FROM integridade {filtro} {'AND' if filtro else 'WHERE'} "
                                "problema = ? LIMIT ?", (*args, problema, exemplos)):
            linhas.append(f"    {c}")
    con.close()
    return "\n".join(linhas)
