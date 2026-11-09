"""Agente Leitor Universal: transforma QUALQUER arquivo do acervo numa ficha .md pesquisável.

  • Documentos (PDF, Word, PPT, Excel, e-book, HTML, texto) → texto extraído
  • Áudio e vídeo (mp3, opus, m4a, wav, mp4, mkv, avi, mov…)   → TRANSCRIÇÃO (Whisper, local)
  • Imagens (jpg, png, webp…)                                  → texto da imagem + descrição (visão local)
  • PDF escaneado (sem texto)                                  → páginas lidas pela visão local

Tudo roda NO SEU PC (privado, sem custo por uso). As fichas ficam em
~/.central-ia/leituras/ (ou LEITURAS_PASTA no .env) e entram no ÍNDICE DO ACERVO,
que os agentes consultam antes de escrever. Os arquivos originais nunca são alterados.
O trabalho é retomável: rode quantas vezes quiser; o que já foi lido é pulado.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from . import extrator
from .curador import PASTAS_DE_PROGRAMA, SINAIS_DE_PROGRAMA, _conectar

PASTA_LEITURAS = Path(os.getenv("LEITURAS_PASTA") or (Path.home() / ".central-ia" / "leituras"))
AUDIO_VIDEO = {".mp3", ".opus", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".wma",
               ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".3gp", ".mpg", ".mpeg"}
IMAGEM = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic"}
MODELO_VISAO = os.getenv("MODELO_VISAO", "gemma3:4b")
MODELO_WHISPER = os.getenv("WHISPER_MODELO", "small")   # tiny | base | small | medium | large-v3
PRIORIDADE = {"documento": 1, "imagem": 2, "pdf escaneado": 3, "audio/video": 4}

_whisper = None


# ------------------------------------------------------------------ banco
def _tabelas() -> sqlite3.Connection:
    con = _conectar()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS leituras (
            caminho TEXT PRIMARY KEY, tipo TEXT, metodo TEXT, status TEXT,
            ficha TEXT, caracteres INTEGER, segundos REAL, lido_em TEXT, erro TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS acervo USING fts5(
            texto, titulo UNINDEXED, caminho UNINDEXED, tipo UNINDEXED,
            tokenize = 'unicode61 remove_diacritics 2');
    """)
    return con


def tipo_de(caminho: str, raiz: str | None = None) -> str | None:
    """Tipo de leitura pela extensão. Com `raiz`, também ignora arquivos dentro de pastas de
    programa/sistema (verificando só o trecho do caminho DENTRO da raiz)."""
    p = Path(caminho)
    ext = p.suffix.lower()
    if ext in SINAIS_DE_PROGRAMA:
        return None
    if raiz:
        try:
            partes = p.relative_to(raiz).parts[:-1]
        except ValueError:
            partes = ()
        if {x.lower() for x in partes} & PASTAS_DE_PROGRAMA:
            return None
    if ext in AUDIO_VIDEO:
        return "audio/video"
    if ext in IMAGEM:
        return "imagem"
    if extrator.tem_texto(caminho):
        return "documento"
    return None


# ------------------------------------------------------------------ leitores
def _transcrever(caminho: str) -> tuple[str, str]:
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        _carregar_dlls_cuda()
        try:
            _whisper = WhisperModel(MODELO_WHISPER, device="cuda", compute_type="int8_float16")
            _whisper_dispositivo = "GPU"
        except Exception:  # noqa: BLE001 — sem CUDA: usa o processador
            _whisper = WhisperModel(MODELO_WHISPER, device="cpu", compute_type="int8")
            _whisper_dispositivo = "CPU"
        _whisper.dispositivo = _whisper_dispositivo  # type: ignore[attr-defined]
    segmentos, info = _whisper.transcribe(caminho, language=None, vad_filter=True)
    linhas = []
    for s in segmentos:
        minuto, segundo = divmod(int(s.start), 60)
        linhas.append(f"[{minuto:02d}:{segundo:02d}] {s.text.strip()}")
    return (f"whisper-{MODELO_WHISPER} ({_whisper.dispositivo}, idioma {info.language}, "
            f"{info.duration / 60:.0f} min)"), "\n".join(linhas)


def _carregar_dlls_cuda() -> None:
    """No Windows, as bibliotecas CUDA instaladas pelo pip precisam ser apontadas manualmente."""
    try:
        import nvidia.cublas
        import nvidia.cudnn
        for pacote in (nvidia.cublas, nvidia.cudnn):
            pasta = Path(pacote.__path__[0]) / "bin"
            if pasta.is_dir():
                os.add_dll_directory(str(pasta))
                os.environ["PATH"] = str(pasta) + os.pathsep + os.environ.get("PATH", "")
    except Exception:  # noqa: BLE001
        pass


def _ver_imagem(dados_png: bytes, pedido: str) -> str:
    url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/api/chat"
    corpo = {"model": MODELO_VISAO, "stream": False, "messages": [{
        "role": "user", "content": pedido, "images": [base64.b64encode(dados_png).decode()]}]}
    req = urllib.request.Request(url, data=json.dumps(corpo).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=600).read())["message"]["content"].strip()


def _png(imagem, lado_max: int = 1280) -> bytes:
    imagem = imagem.convert("RGB")
    imagem.thumbnail((lado_max, lado_max))
    saida = io.BytesIO()
    imagem.save(saida, format="PNG")
    return saida.getvalue()


PEDIDO_IMAGEM = ("Leia esta imagem em português. 1) TRANSCREVA todo o texto visível, fielmente. "
                 "2) Descreva em 2-4 frases o que a imagem mostra e para que serve (ex.: slide de aula, "
                 "gráfico, foto de documento, foto pessoal, diagrama). Seja objetivo.")


def _ler_imagem(caminho: str) -> tuple[str, str]:
    from PIL import Image
    with Image.open(caminho) as img:
        return f"visão local ({MODELO_VISAO})", _ver_imagem(_png(img), PEDIDO_IMAGEM)


def _ler_pdf_escaneado(caminho: str, paginas: int = 6) -> tuple[str, str]:
    import pypdfium2
    pdf = pypdfium2.PdfDocument(caminho)
    total = len(pdf)
    partes = []
    for i in range(min(paginas, total)):
        imagem = pdf[i].render(scale=1.5).to_pil()
        partes.append(f"## Página {i + 1}\n" + _ver_imagem(_png(imagem, 1600),
                      "Transcreva fielmente todo o texto desta página digitalizada, em português."))
    aviso = f"\n\n_(lidas {min(paginas, total)} de {total} páginas)_" if total > paginas else ""
    return f"OCR por visão local ({MODELO_VISAO})", "\n\n".join(partes) + aviso


def ler(caminho: str) -> tuple[str, str, str]:
    """Lê um arquivo. Retorna (tipo, método, texto)."""
    tipo = tipo_de(caminho) or "outro"
    if tipo == "audio/video":
        return (tipo, *_transcrever(caminho))
    if tipo == "imagem":
        return (tipo, *_ler_imagem(caminho))
    metodo, texto = extrator.extrair(caminho)
    if Path(caminho).suffix.lower() == ".pdf" and metodo == "ilegível":
        return ("pdf escaneado", *_ler_pdf_escaneado(caminho))
    return tipo, metodo, texto


def _extrair_tudo(caminho: str) -> str:
    """Para a ficha de documentos queremos o texto inteiro (o extrator lê só o início)."""
    antigo = extrator.LIMITE
    extrator.LIMITE = 400_000
    try:
        return extrator.extrair(caminho)[1]
    finally:
        extrator.LIMITE = antigo


# ------------------------------------------------------------------ fichas e índice
def _salvar_ficha(caminho: str, tipo: str, metodo: str, texto: str) -> Path:
    PASTA_LEITURAS.mkdir(parents=True, exist_ok=True)
    nome = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", Path(caminho).stem)[:80].strip()
    codigo = hashlib.sha1(caminho.encode()).hexdigest()[:8]
    ficha = PASTA_LEITURAS / f"{nome} [{codigo}].md"
    ficha.write_text(
        "---\n"
        f"origem: {json.dumps(caminho, ensure_ascii=False)}\n"
        f"tipo: {tipo}\nmetodo: {json.dumps(metodo, ensure_ascii=False)}\n"
        f"lido_em: {datetime.now():%Y-%m-%dT%H:%M}\n"
        "---\n"
        f"# {Path(caminho).name}\n\n{texto}\n", encoding="utf-8")
    return ficha


def _indexar(con, caminho: str, tipo: str, texto: str) -> None:
    con.execute("DELETE FROM acervo WHERE caminho = ?", (caminho,))
    titulo = Path(caminho).stem[:200]
    trechos, atual = [], ""
    for linha in texto.splitlines(keepends=True):
        atual += linha
        if len(atual) > 1500:
            trechos.append(atual)
            atual = ""
    if atual.strip():
        trechos.append(atual)
    con.executemany("INSERT INTO acervo (texto, titulo, caminho, tipo) VALUES (?, ?, ?, ?)",
                    ((t, titulo, caminho, tipo) for t in trechos if len(t.strip()) > 40))


def processar(raiz: str | None = None, tipos: list[str] | None = None, limite: int | None = None,
              minutos: float | None = None, log=print) -> dict:
    """Lê os arquivos ainda não lidos (prioridade: documentos → imagens → PDFs escaneados → áudio/vídeo).
    `minutos` limita o tempo desta rodada; rode de novo para continuar de onde parou."""
    con = _tabelas()
    filtro, args = "", []
    if raiz:
        from .curador import _raiz
        filtro, args = "AND a.raiz = ?", [str(_raiz(raiz))]
    linhas = con.execute(f"""SELECT a.caminho, a.tamanho, a.raiz FROM arquivos a LEFT JOIN leituras l
                             ON l.caminho = a.caminho WHERE l.caminho IS NULL {filtro}""", args).fetchall()
    fila = [(c, t, tipo_de(c, r)) for c, t, r in linhas]
    fila = [(c, t, tp) for c, t, tp in fila if tp and (not tipos or tp in tipos)]
    fila.sort(key=lambda x: (PRIORIDADE.get(x[2], 9), x[1]))
    if limite:
        fila = fila[:limite]
    log(f"📚 {len(fila):,} arquivos na fila de leitura")
    fim = time.time() + minutos * 60 if minutos else None
    contagem = {"lidos": 0, "erros": 0}
    for n, (caminho, _, _tipo) in enumerate(fila, 1):
        if fim and time.time() > fim:
            log("⏸ tempo da rodada esgotado; rode de novo para continuar")
            break
        inicio = time.time()
        try:
            tipo, metodo, texto = ler(caminho)
            if tipo == "documento":
                texto = _extrair_tudo(caminho) or texto
            ficha = _salvar_ficha(caminho, tipo, metodo, texto)
            _indexar(con, caminho, tipo, texto)
            con.execute("INSERT OR REPLACE INTO leituras VALUES (?, ?, ?, 'lido', ?, ?, ?, ?, NULL)",
                        (caminho, tipo, metodo, str(ficha), len(texto), time.time() - inicio,
                         datetime.now().isoformat(timespec="minutes")))
            contagem["lidos"] += 1
        except Exception as erro:  # noqa: BLE001 — anota e segue; integridade investiga depois
            con.execute("INSERT OR REPLACE INTO leituras VALUES (?, ?, NULL, 'erro', NULL, 0, ?, ?, ?)",
                        (caminho, _tipo, time.time() - inicio, datetime.now().isoformat(timespec="minutes"),
                         f"{type(erro).__name__}: {str(erro)[:200]}"))
            contagem["erros"] += 1
        if n % 25 == 0:
            con.commit()
            log(f"   {n:,}/{len(fila):,} lidos ({contagem['erros']} com erro)")
    con.commit()
    con.close()
    log(f"✅ rodada concluída: {contagem['lidos']:,} lidos, {contagem['erros']:,} com erro")
    return contagem


def progresso() -> str:
    con = _tabelas()
    total = {}
    for c, r in con.execute("SELECT caminho, raiz FROM arquivos"):
        tp = tipo_de(c, r)
        if tp:
            total[tp] = total.get(tp, 0) + 1
    lidos = dict(con.execute("SELECT tipo, count(*) FROM leituras WHERE status = 'lido' GROUP BY tipo").fetchall())
    erros = con.execute("SELECT count(*) FROM leituras WHERE status = 'erro'").fetchone()[0]
    con.close()
    linhas = ["📚 Leitura do acervo:"]
    for tp in sorted(total, key=lambda t: PRIORIDADE.get(t, 9)):
        feitos = lidos.get(tp, 0) + (lidos.get("pdf escaneado", 0) if tp == "documento" else 0)
        linhas.append(f"• {tp}: {feitos:,} de {total[tp]:,} ({100 * feitos / total[tp]:.1f}%)")
    linhas.append(f"• com erro de leitura: {erros:,}")
    return "\n".join(linhas)


def buscar(pergunta: str, limite: int = 6, max_caracteres: int = 9000) -> list[dict]:
    """Busca no ACERVO INTEIRO (tudo que o leitor já leu) — usada pelos agentes."""
    palavras = {p for p in re.findall(r"\w{4,}", pergunta.lower())}
    if not palavras:
        return []
    con = _tabelas()
    try:
        linhas = con.execute("SELECT texto, titulo, caminho, tipo FROM acervo WHERE acervo MATCH ? "
                             "ORDER BY bm25(acervo) LIMIT ?",
                             (" OR ".join(f'"{p}"' for p in sorted(palavras, key=len, reverse=True)[:15]),
                              limite * 3)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    resultado, usados, vistos = [], 0, set()
    for texto, titulo, caminho, tipo in linhas:
        if caminho in vistos or usados + len(texto) > max_caracteres:
            continue
        vistos.add(caminho)
        usados += len(texto)
        resultado.append({"texto": texto, "titulo": f"{titulo} ({tipo})", "caminho": caminho})
        if len(resultado) >= limite:
            break
    return resultado
