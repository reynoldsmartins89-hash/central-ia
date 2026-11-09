"""Lê o CONTEÚDO dos arquivos (não só o nome) para o curador classificar com precisão.

Formatos com texto lido de verdade:
  PDF (.pdf), Word (.docx), PowerPoint (.pptx), Excel (.xlsx), e-book (.epub),
  OpenDocument (.odt/.ods/.odp), Word antigo (.doc, texto aproximado), textos e
  páginas (.txt .md .html .htm .csv .json .xml .rtf), código (.py .js .r .sql …).
Sem texto (vídeo, áudio, imagem, compactados): devolve só metadados — a
classificação usa a pasta e os arquivos vizinhos, e isso fica registrado.
Somente leitura: nenhum arquivo é alterado.
"""
from __future__ import annotations

import html
import logging
import re
import zipfile
from pathlib import Path

logging.getLogger("pypdf").setLevel(logging.ERROR)  # PDFs imperfeitos geram avisos demais

LIMITE = 3000  # caracteres lidos por arquivo (o início costuma bastar para identificar o assunto)

TEXTO = {".txt", ".md", ".csv", ".json", ".xml", ".rtf", ".py", ".js", ".r", ".sql", ".tex", ".srt", ".vtt", ".log"}
PAGINA = {".html", ".htm", ".xhtml"}
ZIP_XML = {
    ".docx": ("word/document.xml",),
    ".pptx": ("ppt/slides/",),
    ".xlsx": ("xl/sharedStrings.xml",),
    ".odt": ("content.xml",), ".ods": ("content.xml",), ".odp": ("content.xml",),
    ".epub": (".xhtml", ".html", ".htm"),
}
SEM_TEXTO = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".mp3", ".wav", ".opus", ".m4a", ".ogg", ".flac",
             ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".svg", ".zip", ".rar", ".7z",
             ".iso", ".exe", ".dll", ".msi"}


def tem_texto(caminho: str | Path) -> bool:
    ext = Path(caminho).suffix.lower()
    return ext in TEXTO or ext in PAGINA or ext in ZIP_XML or ext in {".pdf", ".doc"}


def _sem_tags(xml: str) -> str:
    xml = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", xml, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", xml))


def _normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip()[:LIMITE]


def _pdf(caminho: Path) -> str:
    from pypdf import PdfReader
    leitor = PdfReader(str(caminho))
    if leitor.is_encrypted:
        leitor.decrypt("")   # PDF só "protegido contra cópia" (senha vazia) abre normalmente
    partes = []
    if leitor.metadata and leitor.metadata.title:
        partes.append(f"Título: {leitor.metadata.title}")
    # para CLASSIFICAR basta o início (4 páginas); no modo "texto inteiro" (LIMITE grande) lê o livro todo
    for pagina in leitor.pages[:4] if LIMITE <= 3000 else leitor.pages:
        partes.append(pagina.extract_text() or "")
        if sum(map(len, partes)) > LIMITE:
            break
    return " ".join(partes)


def _zip(caminho: Path, ext: str) -> str:
    alvos = ZIP_XML[ext]
    partes = []
    with zipfile.ZipFile(caminho) as z:
        nomes = sorted((n for n in z.namelist()
                        if "_rels" not in n and any(n.startswith(a) or n.endswith(a) for a in alvos)
                        and not n.endswith("/")),
                       key=lambda n: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", n)])
        for nome in nomes[:15]:
            partes.append(_sem_tags(z.read(nome).decode("utf-8", errors="ignore")))
            if sum(map(len, partes)) > LIMITE * 2:
                break
    return " ".join(partes)


def _doc_antigo(caminho: Path) -> str:
    """Word 97-2003: extrai sequências de texto legível do binário (aproximado)."""
    dados = caminho.read_bytes()[:2_000_000]
    utf16 = re.findall(rb"(?:[\x20-\x7e\xc0-\xff]\x00){6,}", dados)
    texto = " ".join(t.decode("utf-16le", errors="ignore") for t in utf16)
    if len(texto) < 200:
        texto = " ".join(t.decode("latin-1") for t in re.findall(rb"[\x20-\x7e\xc0-\xff]{6,}", dados))
    return texto


def extrair(caminho: str | Path) -> tuple[str, str]:
    """Retorna (método, texto). método: 'conteúdo', 'sem texto', 'ilegível' ou 'erro: ...'."""
    caminho = Path(caminho)
    ext = caminho.suffix.lower()
    try:
        if ext in TEXTO:
            with open(caminho, "r", encoding="utf-8", errors="ignore") as f:
                return "conteúdo", _normalizar(f.read(LIMITE * 2))
        if ext in PAGINA:
            with open(caminho, "r", encoding="utf-8", errors="ignore") as f:
                return "conteúdo", _normalizar(_sem_tags(f.read(LIMITE * 6)))
        if ext == ".pdf":
            texto = _normalizar(_pdf(caminho))
            return ("conteúdo", texto) if len(texto) > 50 else ("ilegível", "PDF sem texto (provavelmente escaneado)")
        if ext in ZIP_XML:
            return "conteúdo", _normalizar(_zip(caminho, ext))
        if ext == ".doc":
            return "conteúdo", _normalizar(_doc_antigo(caminho))
        return "sem texto", ""
    except Exception as erro:  # noqa: BLE001 — arquivo danificado ou formato inesperado
        return f"erro: {type(erro).__name__}", ""
