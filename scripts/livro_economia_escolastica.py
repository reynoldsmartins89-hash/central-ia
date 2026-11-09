"""Livro "Economia Escolástica Católica" — pedido do Reynolds pelo Telegram (09/11/2026).

Fontes: canal do Telegram Economia Escolástica Católica (Pedro P. Figueiredo), todos os links citados,
os livros anexados no canal (pasta do Drive) e o curso "Introdução ao Distributismo" (HD E:).

Uso:  python scripts/livro_economia_escolastica.py coletar     (links + PDFs)
      python scripts/livro_economia_escolastica.py aulas       (transcreve as 30 videoaulas)
      python scripts/livro_economia_escolastica.py escrever    (plano + capítulos + PDF + Telegram)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from superagente import agentes, livro as L  # noqa: E402

agentes.carregar_env()
LIVRO = L.Livro("economia-escolastica-catolica", "Economia Escolástica Católica")
PASTA_DRIVE_ID = "1-QmaTSUB04--CCtfrxh64XJGLbaJrF7c"
ANEXOS = LIVRO.pasta / "anexos"
CURSO = Path(r"E:\Introdução ao Distributismo")


def coletar() -> None:
    from superagente.drive import _rclone
    ANEXOS.mkdir(parents=True, exist_ok=True)
    print("☁️ Baixando os anexos do canal (pasta do Drive, só leitura)…", flush=True)
    subprocess.run([_rclone(), "copy", "gdrive:", str(ANEXOS), "--drive-root-folder-id", PASTA_DRIVE_ID,
                    "--include", "*.pdf", "--include", "messages*.html"], check=False)
    htmls = sorted(ANEXOS.rglob("messages*.html"))
    links = []
    for h in htmls:
        links += L.coletar_telegram(LIVRO, h)
    LIVRO.estado["links"] = list(dict.fromkeys(links))
    LIVRO.gravar()
    L.coletar_arquivos(LIVRO, sorted(p for p in ANEXOS.rglob("*.pdf") if "_thumb" not in p.name))
    L.coletar_links(LIVRO)


def arquivos(escaneados: bool = False) -> None:
    """(Re)lê os livros anexados com o TEXTO INTEIRO. escaneados=True inclui os digitalizados (OCR, demorado)."""
    import pypdfium2
    pdfs = sorted(p for p in ANEXOS.rglob("*.pdf") if "_thumb" not in p.name)
    escolhidos = []
    for p in pdfs:
        doc = pypdfium2.PdfDocument(str(p))
        tem_texto = sum(len(doc[i].get_textpage().get_text_range()) for i in range(0, len(doc), max(1, len(doc) // 8))) > 500
        if tem_texto != escaneados:
            escolhidos.append(p)
    for p in escolhidos:   # apaga a versão cortada (só o início) para reler por inteiro
        fonte = LIVRO.fontes / f"arquivo_{__import__('hashlib').sha1(str(p).encode()).hexdigest()[:10]}.md"
        if fonte.exists() and fonte.stat().st_size < 20_000:
            fonte.unlink()
    L.coletar_arquivos(LIVRO, escolhidos)


def aulas() -> None:
    videos = sorted(CURSO.rglob("*.mp4"))
    print(f"🎬 {len(videos)} videoaulas do curso", flush=True)
    L.coletar_videos(LIVRO, videos)


def escrever() -> None:
    from superagente import telegram
    if "plano" not in LIVRO.estado:
        L.planejar(LIVRO, paginas=250)
    L.escrever(LIVRO, palavras=6000)
    destino = L.pdf(LIVRO)
    final = RAIZ / "resultados" / f"Livro - {LIVRO.estado['plano']['titulo']}.pdf"
    final.parent.mkdir(exist_ok=True)
    final.write_bytes(destino.read_bytes())
    telegram.enviar(f"📕 Pronto: o livro “{LIVRO.estado['plano']['titulo']}” — {len(LIVRO.estado['plano']['capitulos'])} "
                    "capítulos, feito a partir de todas as mensagens do canal, dos links, dos livros anexados e do curso "
                    "de Distributismo.")
    telegram.enviar_arquivo(final, f"📕 {LIVRO.estado['plano']['titulo']}")


if __name__ == "__main__":
    {"coletar": coletar, "aulas": aulas, "escrever": escrever, "arquivos": arquivos,
     "escaneados": lambda: arquivos(escaneados=True)}[sys.argv[1]]()
