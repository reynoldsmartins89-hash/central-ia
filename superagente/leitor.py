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
PRIORIDADE = {"documento": 1, "imagem": 2, "pdf escaneado": 3, "audio/video": 4, "compactado": 5}
TAMANHO_MINIMO_IMAGEM = 30_000   # ignora ícones e miniaturas

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
    if ext in COMPACTADOS or re.search(r"\.(r\d\d|\d{3})$", ext):
        return "compactado" if eh_primeiro_volume(caminho) else None
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
    # multilingual=True: o idioma é detectado em CADA trecho — aulas que misturam
    # português e inglês (ou qualquer par) são transcritas por inteiro, sem perder nada.
    segmentos, info = _whisper.transcribe(caminho, language=None, multilingual=True, vad_filter=True)
    linhas, por_idioma = [], {}
    for s in segmentos:
        texto = s.text.strip()
        if not texto:
            continue
        idioma = idioma_do_texto(texto) or info.language
        minuto, segundo = divmod(int(s.start), 60)
        linhas.append(f"[{minuto:02d}:{segundo:02d}] [{idioma}] {texto}")
        por_idioma.setdefault(idioma, []).append(f"[{minuto:02d}:{segundo:02d}] {texto}")
    global ultima_transcricao_por_idioma
    ultima_transcricao_por_idioma = {i: "\n".join(t) for i, t in por_idioma.items() if len(t) >= 3}
    idiomas = ", ".join(f"{i} ({len(t)} trechos)" for i, t in sorted(por_idioma.items(), key=lambda x: -len(x[1])))
    return (f"whisper-{MODELO_WHISPER} multilíngue ({_whisper.dispositivo}; {idiomas}; "
            f"{info.duration / 60:.0f} min)"), "\n".join(linhas)


ultima_transcricao_por_idioma: dict[str, str] = {}

PALAVRAS_TIPICAS = {
    "pt": {"que", "não", "é", "uma", "você", "para", "com", "isso", "está", "mais", "como", "muito", "então"},
    "en": {"the", "is", "you", "and", "are", "what", "this", "it's", "i'm", "your", "with", "have", "do", "my"},
    "es": {"el", "es", "usted", "una", "está", "qué", "muy", "pero", "los", "las", "y", "yo", "tú"},
    "fr": {"le", "la", "est", "vous", "je", "et", "les", "des", "une", "c'est", "pas", "très", "nous"},
    "it": {"il", "è", "che", "sono", "una", "non", "per", "della", "io", "lei", "molto", "anche"},
    "de": {"der", "die", "das", "und", "ist", "ich", "sie", "nicht", "ein", "eine", "wir", "auch"},
    "la": {"est", "et", "in", "non", "ad", "cum", "quod", "sunt", "deus", "enim", "sed", "esse"},
}


def idioma_do_texto(texto: str) -> str | None:
    """Detecção leve do idioma de um trecho (alfabeto + palavras típicas)."""
    for intervalo, codigo in ((r"[぀-ヿ]", "ja"), (r"[가-힯]", "ko"), (r"[一-鿿]", "zh"),
                              (r"[؀-ۿ]", "ar"), (r"[֐-׿]", "he"), (r"[Ͱ-Ͽ]", "el"),
                              (r"[Ѐ-ӿ]", "ru")):
        if re.search(intervalo, texto):
            return codigo
    palavras = set(re.findall(r"[\w']+", texto.lower()))
    pontos = {codigo: len(palavras & tipicas) for codigo, tipicas in PALAVRAS_TIPICAS.items()}
    melhor = max(pontos, key=pontos.get)
    return melhor if pontos[melhor] > 0 else None


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


def ler_completo(caminho: str, limite: int = 3_000_000) -> tuple[str, str, str]:
    """Como ler(), mas com o TEXTO INTEIRO (livros, apostilas): ler() só traz o início, para classificar."""
    tipo = tipo_de(caminho) or "outro"
    if tipo in ("audio/video", "imagem"):
        return ler(caminho)
    antigo = extrator.LIMITE
    extrator.LIMITE = limite
    try:
        metodo, texto = extrator.extrair(caminho)
    finally:
        extrator.LIMITE = antigo
    if Path(caminho).suffix.lower() == ".pdf" and metodo == "ilegível":
        return ("pdf escaneado", *_ler_pdf_escaneado(caminho, paginas=100_000))   # todas as páginas
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
def _salvar_ficha(caminho: str, tipo: str, metodo: str, texto: str, sufixo: str = "") -> Path:
    PASTA_LEITURAS.mkdir(parents=True, exist_ok=True)
    nome = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", Path(caminho).stem)[:80].strip()
    codigo = hashlib.sha1(caminho.encode()).hexdigest()[:8]
    ficha = PASTA_LEITURAS / f"{nome} [{codigo}]{(' ' + sufixo) if sufixo else ''}.md"
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


def ler_e_registrar(con, origem: str, arquivo: str) -> int:
    """Lê `arquivo` (pode ser um temporário) e registra tudo sob o nome `origem`.
    Para áudios com mais de um idioma, grava também uma ficha por idioma. Retorna nº de fichas."""
    global ultima_transcricao_por_idioma
    ultima_transcricao_por_idioma = {}
    inicio = time.time()
    tipo, metodo, texto = ler(arquivo)
    if tipo == "documento":
        texto = _extrair_tudo(arquivo) or texto
    ficha = _salvar_ficha(origem, tipo, metodo, texto)
    _indexar(con, origem, tipo, texto)
    fichas = 1
    if len(ultima_transcricao_por_idioma) > 1:          # nada se perde: um arquivo por idioma também
        for idioma, parte in ultima_transcricao_por_idioma.items():
            _salvar_ficha(origem, tipo, f"{metodo} — somente [{idioma}]", parte, sufixo=idioma)
            fichas += 1
    con.execute("INSERT OR REPLACE INTO leituras VALUES (?, ?, ?, 'lido', ?, ?, ?, ?, NULL)",
                (origem, tipo, metodo, str(ficha), len(texto), time.time() - inicio,
                 datetime.now().isoformat(timespec="minutes")))
    return fichas


# ------------------------------------------------------------------ compactados (.rar .zip .7z .001)
COMPACTADOS = {".rar", ".zip", ".7z", ".001", ".tar", ".gz"}


def _sete_zip() -> str | None:
    import shutil as _sh
    for candidato in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if Path(candidato).exists():
            return candidato
    return _sh.which("7z")


def eh_primeiro_volume(caminho: str) -> bool:
    nome = Path(caminho).name.lower()
    if re.search(r"\.part0*([2-9]|\d{2,})\.rar$", nome):     # .part2.rar, .part10.rar…
        return False
    if re.search(r"\.(r\d\d|\d{3})$", nome) and not nome.endswith(".001"):   # .r00, .002…
        return False
    return Path(nome).suffix in COMPACTADOS


def _ler_compactado(con, caminho: str, log=print) -> int:
    """Extrai para uma pasta TEMPORÁRIA no mesmo disco, lê cada arquivo de dentro e apaga tudo."""
    import shutil as _sh
    import subprocess
    sete = _sete_zip()
    if not sete:
        raise RuntimeError("7-Zip não instalado")
    origem_base = Path(caminho)
    volumes = [p for p in origem_base.parent.glob(re.sub(r"(\.part0*1\.rar|\.001)$", "", origem_base.name) + "*")]
    tamanho = sum(p.stat().st_size for p in volumes) or origem_base.stat().st_size
    temporaria = Path(origem_base.anchor) / "_central_ia_temp" / hashlib.sha1(caminho.encode()).hexdigest()[:10]
    if _sh.disk_usage(origem_base.anchor).free < tamanho * 1.3:
        raise RuntimeError("espaço livre insuficiente para extrair temporariamente")
    temporaria.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([sete, "x", "-y", "-p-", f"-o{temporaria}", caminho],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode not in (0, 1):                   # 1 = avisos, mas extraiu
            raise RuntimeError(f"7-Zip: {(r.stderr or r.stdout).strip().splitlines()[-1:]} ")
        fichas = 0
        for interno in sorted(p for p in temporaria.rglob("*") if p.is_file()):
            relativo = interno.relative_to(temporaria).as_posix()
            if not tipo_de(str(interno), str(temporaria)):
                continue
            try:
                fichas += ler_e_registrar(con, f"{caminho}::{relativo}", str(interno))
            except Exception as erro:  # noqa: BLE001
                log(f"   ⚠ {relativo}: {str(erro)[:80]}")
            con.commit()   # salva a cada arquivo de dentro do pacote (não segura o catálogo por horas)
        con.execute("INSERT OR REPLACE INTO leituras VALUES (?, 'compactado', '7-Zip', 'lido', NULL, ?, 0, ?, NULL)",
                    (caminho, fichas, datetime.now().isoformat(timespec="minutes")))
        log(f"   📦 {origem_base.name}: {fichas} fichas de dentro do compactado")
        return fichas
    finally:
        _sh.rmtree(temporaria, ignore_errors=True)       # nada fica ocupando espaço
        try:
            temporaria.parent.rmdir()                    # remove a pasta-mãe se ficou vazia
        except OSError:
            pass


def processar(raiz: str | None = None, tipos: list[str] | None = None, limite: int | None = None,
              minutos: float | None = None, log=print) -> dict:
    """Lê os arquivos ainda não lidos (prioridade: documentos → imagens → PDFs escaneados → áudio/vídeo).
    `minutos` limita o tempo desta rodada; rode de novo para continuar de onde parou."""
    con = _tabelas()
    filtro, args = "", []
    if raiz:  # um disco ("E:\") ou qualquer pasta dentro dele ("E:\IDIOMAS")
        from .curador import _raiz
        filtro, args = "AND a.caminho LIKE ?", [str(_raiz(raiz)).rstrip("\\/") + "\\%"]
    linhas = con.execute(f"""SELECT a.caminho, a.tamanho, a.raiz FROM arquivos a LEFT JOIN leituras l
                             ON l.caminho = a.caminho WHERE l.caminho IS NULL {filtro}""", args).fetchall()
    fila = [(c, t, tipo_de(c, r)) for c, t, r in linhas]
    fila = [(c, t, tp) for c, t, tp in fila if tp and (not tipos or tp in tipos)
            and not (tp == "imagem" and (t or 0) < TAMANHO_MINIMO_IMAGEM)]
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
            if _tipo == "compactado":
                contagem["lidos"] += _ler_compactado(con, caminho, log)
            else:
                ler_e_registrar(con, caminho, caminho)
                contagem["lidos"] += 1
        except Exception as erro:  # noqa: BLE001 — anota e segue; integridade investiga depois
            con.execute("INSERT OR REPLACE INTO leituras VALUES (?, ?, NULL, 'erro', NULL, 0, ?, ?, ?)",
                        (caminho, _tipo, time.time() - inicio, datetime.now().isoformat(timespec="minutes"),
                         f"{type(erro).__name__}: {str(erro)[:200]}"))
            contagem["erros"] += 1
        con.commit()   # um arquivo por vez: nunca segura o catálogo enquanto transcreve
        if n % 25 == 0:
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


def buscar(pergunta: str, limite: int = 6, max_caracteres: int = 9000, prefixo: str | None = None) -> list[dict]:
    """Busca no ACERVO (tudo que o leitor já leu). `prefixo` restringe a uma pasta (ex.: E:\\IDIOMAS\\INGLÊS)."""
    palavras = {p for p in re.findall(r"\w{4,}", pergunta.lower())}
    if not palavras:
        return []
    filtro, args = "", []
    if prefixo:
        filtro, args = "AND caminho LIKE ?", [str(Path(prefixo)).rstrip("\\/") + "%"]
    con = _tabelas()
    try:
        linhas = con.execute(f"SELECT texto, titulo, caminho, tipo FROM acervo WHERE acervo MATCH ? {filtro} "
                             "ORDER BY bm25(acervo) LIMIT ?",
                             (" OR ".join(f'"{p}"' for p in sorted(palavras, key=len, reverse=True)[:15]),
                              *args, limite * 3)).fetchall()
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
