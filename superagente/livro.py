"""Agente Editor de Livros: transforma um conjunto de fontes num LIVRO denso, em PDF.

Fontes aceitas: exportação de grupo do Telegram (HTML), os links citados nas mensagens (artigos,
documentos, vídeos do YouTube com legenda), PDFs/documentos e videoaulas (transcritas na placa de vídeo).

Etapas (cada uma guarda o resultado; rodar de novo continua de onde parou):
  coletar → planejar (sumário) → escrever (capítulo a capítulo) → pdf
Tudo fica em ~/.central-ia/livros/<livro>/ ; o PDF final vai também para resultados/.
O texto é uma SÍNTESE ORIGINAL, com as fontes citadas — nunca cópia dos materiais.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import html as _html
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PASTA_LIVROS = Path.home() / ".central-ia" / "livros"
NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/130.0 Safari/537.36")
IGNORAR_LINKS = ("t.me/", "telegram.me/", "wa.me/", "instagram.com/", "facebook.com/", "twitter.com/", "x.com/",
                 "tiktok.com/", ".jpg", ".jpeg", ".png", ".gif", ".webp")
MODELOS_ESCRITA = ["cli:claude", "gemini:gemini-3.5-flash", "cli:codex"]
LIMITE_FONTES_CAPITULO = 160_000   # caracteres de fontes entregues para cada capítulo


class Livro:
    def __init__(self, slug: str, titulo: str = ""):
        self.slug = slug
        self.pasta = PASTA_LIVROS / slug
        self.fontes = self.pasta / "fontes"
        self.capitulos = self.pasta / "capitulos"
        for p in (self.fontes, self.capitulos):
            p.mkdir(parents=True, exist_ok=True)
        self.arquivo_estado = self.pasta / "estado.json"
        self.estado = (json.loads(self.arquivo_estado.read_text(encoding="utf-8"))
                       if self.arquivo_estado.exists() else {"titulo": titulo})
        if titulo:
            self.estado["titulo"] = titulo

    def gravar(self) -> None:
        self.arquivo_estado.write_text(json.dumps(self.estado, ensure_ascii=False, indent=1), encoding="utf-8")

    def fonte(self, chave: str, titulo: str, tipo: str, origem: str, texto: str) -> Path:
        nome = f"{tipo}_{hashlib.sha1(chave.encode()).hexdigest()[:10]}.md"
        destino = self.fontes / nome
        destino.write_text(f"---\ntitulo: {titulo}\ntipo: {tipo}\norigem: {origem}\n---\n\n{texto.strip()}\n",
                           encoding="utf-8")
        return destino

    def ja_tem(self, chave: str, tipo: str) -> bool:
        return (self.fontes / f"{tipo}_{hashlib.sha1(chave.encode()).hexdigest()[:10]}.md").exists()


# ------------------------------------------------------------------ coleta
def coletar_telegram(livro: Livro, html_path: str | Path, log=print) -> list[str]:
    from datetime import datetime
    from .grupos import ler_telegram_html
    nome, msgs, links = ler_telegram_html(Path(html_path).read_text(encoding="utf-8", errors="replace"))
    texto = "\n\n".join(f"[{datetime.fromtimestamp(q):%d/%m/%Y %H:%M}] {t}" for q, _, t in msgs)
    livro.fonte(str(html_path), f"Mensagens do canal {nome}", "mensagens", "Telegram", texto)
    log(f"💬 {len(msgs)} mensagens do canal ({len(links)} links)")
    livro.estado["links"] = links
    livro.gravar()
    return links


def _baixar(url: str, limite: int = 8_000_000) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": NAVEGADOR, "Accept-Language": "pt-BR,pt;q=0.9"})
    with urllib.request.urlopen(req, timeout=40) as r:
        tipo = r.headers.get("Content-Type", "")
        dados = r.read(limite)
    return tipo, dados.decode("utf-8", "replace") if "pdf" not in tipo else ""


def _texto_da_pagina(pagina: str) -> tuple[str, str]:
    titulo = re.search(r"<title[^>]*>(.*?)</title>", pagina, re.S | re.I)
    titulo = _html.unescape(re.sub(r"\s+", " ", titulo.group(1))).strip() if titulo else ""
    corpo = pagina
    for padrao in (r'<div[^>]+class="[^"]*entry-content[^"]*"[^>]*>(.*?)<(?:footer|div[^>]+class="[^"]*(?:sharedaddy|jp-relatedposts|wpcnt))',
                   r"<article[^>]*>(.*?)</article>", r"<main[^>]*>(.*?)</main>"):
        achado = re.search(padrao, pagina, re.S | re.I)
        if achado and len(achado.group(1)) > 1500:
            corpo = achado.group(1)
            break
    corpo = re.sub(r"<(script|style|nav|header|footer|aside|form|noscript|svg)[^>]*>.*?</\1>", " ", corpo, flags=re.S | re.I)
    corpo = re.sub(r"<(br|/p|/h\d|/li|/blockquote|/div)[^>]*>", "\n", corpo, flags=re.I)
    corpo = _html.unescape(re.sub(r"<[^>]+>", " ", corpo))
    linhas = [re.sub(r"[ \t\xa0]+", " ", l).strip() for l in corpo.splitlines()]
    texto = "\n".join(l for l in linhas if len(l) > 2)
    return titulo, re.sub(r"\n{3,}", "\n\n", texto).strip()


def _youtube(url: str, pasta_tmp: Path) -> tuple[str, str]:
    pasta_tmp.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([sys.executable, "-m", "yt_dlp", "--skip-download", "--no-simulate", "--write-subs",
                        "--write-auto-subs", "--sub-langs", "pt.*,pt,es.*,en.*", "--sub-format", "vtt",
                        "--print", "%(title)s", "-o", str(pasta_tmp / "%(id)s.%(ext)s"), url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    titulo = (r.stdout or "").strip().splitlines()[0] if (r.stdout or "").strip() else url
    legendas = sorted(pasta_tmp.glob("*.vtt"), key=lambda p: (".pt" not in p.name, p.name))
    if not legendas:
        return titulo, ""
    vistos, linhas = set(), []
    for l in legendas[0].read_text(encoding="utf-8", errors="replace").splitlines():
        l = re.sub(r"<[^>]+>", "", l).strip()
        if not l or "-->" in l or l.startswith(("WEBVTT", "Kind:", "Language:")) or l in vistos:
            continue
        vistos.add(l)
        linhas.append(l)
    for p in pasta_tmp.glob("*"):
        p.unlink(missing_ok=True)
    return titulo, " ".join(linhas)


def coletar_links(livro: Livro, links: list[str] | None = None, log=print) -> dict:
    links = [l for l in (links or livro.estado.get("links", [])) if not any(x in l.lower() for x in IGNORAR_LINKS)]
    pendentes = [l for l in links if not (livro.ja_tem(l, "link") or l in livro.estado.get("links_falhos", {}))]
    log(f"🔗 {len(links)} links úteis · {len(pendentes)} ainda não lidos")
    falhos = livro.estado.setdefault("links_falhos", {})

    def um(url: str) -> tuple[str, str]:
        try:
            if "youtube.com" in url or "youtu.be" in url:
                titulo, texto = _youtube(url, livro.pasta / "tmp" / hashlib.sha1(url.encode()).hexdigest()[:8])
                tipo = "video"
            else:
                tipo_http, pagina = _baixar(url)
                if "pdf" in tipo_http:
                    return url, "pdf (baixar à parte)"
                titulo, texto = _texto_da_pagina(pagina)
                tipo = "artigo"
            if len(texto) < 400:
                return url, "sem texto aproveitável"
            livro.fonte(url, titulo or url, "link", url, f"({tipo})\n\n{texto}")
            return url, ""
        except Exception as erro:  # noqa: BLE001
            return url, str(erro)[:120]

    with cf.ThreadPoolExecutor(6) as pool:
        for n, (url, erro) in enumerate(pool.map(um, pendentes), 1):
            if erro:
                falhos[url] = erro
            if n % 25 == 0:
                log(f"   {n}/{len(pendentes)} links lidos")
                livro.gravar()
    livro.gravar()
    ok = len(list(livro.fontes.glob("link_*.md")))
    log(f"🔗 {ok} links com texto aproveitável · {len(falhos)} sem acesso/sem texto")
    return {"ok": ok, "falhos": len(falhos)}


def coletar_arquivos(livro: Livro, caminhos: list[str | Path], log=print) -> int:
    from . import leitor
    feitos = 0
    for caminho in caminhos:
        caminho = Path(caminho)
        if livro.ja_tem(str(caminho), "arquivo"):
            continue
        try:
            _, metodo, texto = leitor.ler_completo(str(caminho))
        except Exception as erro:  # noqa: BLE001
            log(f"   ⚠ {caminho.name}: {erro}")
            continue
        if (texto or "").strip():
            livro.fonte(str(caminho), caminho.stem, "arquivo", str(caminho), texto)
            feitos += 1
            log(f"📄 {caminho.name}: {len(texto):,} caracteres ({metodo})")
        else:
            log(f"   ⚠ {caminho.name}: sem texto")
    return feitos


def coletar_videos(livro: Livro, caminhos: list[str | Path], log=print) -> int:
    from . import leitor
    feitos = 0
    for n, caminho in enumerate(caminhos, 1):
        caminho = Path(caminho)
        if livro.ja_tem(str(caminho), "aula"):
            continue
        inicio = time.time()
        try:
            _, texto = leitor._transcrever(str(caminho))
        except Exception as erro:  # noqa: BLE001
            log(f"   ⚠ {caminho.name}: {erro}")
            continue
        texto = re.sub(r"^\[\d+:\d+(?::\d+)?\]\s*(\[\w+\]\s*)?", "", texto, flags=re.M)
        livro.fonte(str(caminho), f"{caminho.parent.name} — {caminho.stem}", "aula", str(caminho), texto)
        feitos += 1
        log(f"🎬 {n}/{len(caminhos)} {caminho.stem}: {len(texto):,} caracteres ({time.time() - inicio:.0f}s)")
    return feitos


# ------------------------------------------------------------------ plano e escrita
def _ler_fonte(p: Path) -> tuple[dict, str]:
    bruto = p.read_text(encoding="utf-8")
    cab, _, corpo = bruto.partition("\n---\n")
    meta = dict(l.split(": ", 1) for l in cab.splitlines()[1:] if ": " in l)
    return meta, corpo.strip()


def catalogo(livro: Livro) -> list[dict]:
    itens = []
    for p in sorted(livro.fontes.glob("*.md")):
        meta, corpo = _ler_fonte(p)
        itens.append({"id": p.stem, "titulo": meta.get("titulo", p.stem), "tipo": meta.get("tipo", ""),
                      "origem": meta.get("origem", ""), "tamanho": len(corpo),
                      "inicio": re.sub(r"\s+", " ", corpo[:450])})
    return itens


def _chamar(sistema: str, pedido: str, log=print) -> str:
    from . import provedores
    modelo, texto = provedores.chamar_com_reserva(MODELOS_ESCRITA, sistema, pedido, log=log)
    return texto


def planejar(livro: Livro, paginas: int = 250, log=print) -> dict:
    itens = catalogo(livro)
    lista = "\n".join(f"- {i['id']} | {i['tipo']} | {i['titulo'][:90]} | {i['tamanho']:,} car. | {i['inicio'][:300]}"
                      for i in itens)
    capitulos = max(10, min(24, paginas // 12))
    sistema = ("Você é um editor de livros de não ficção, especialista em doutrina social da Igreja, economia "
               "escolástica (Escola de Salamanca, São Tomás, usura, preço justo), distributismo (Belloc, Chesterton) "
               "e história econômica. Planeje um livro DENSO, rigoroso e bem encadeado. Responda SOMENTE JSON.")
    pedido = (f"Título de trabalho: {livro.estado['titulo']}\nExtensão desejada: cerca de {paginas} páginas "
              f"({capitulos} capítulos).\n\nFontes disponíveis (id | tipo | título | tamanho | início):\n{lista}\n\n"
              "Monte o plano do livro: uma progressão lógica (fundamentos → doutrina → história → crítica do sistema "
              "atual → caminhos de restauração → aplicações práticas), cobrindo TODO o conteúdo relevante das fontes. "
              "Cada capítulo recebe as fontes que o alimentam (ids exatos; uma fonte pode servir a vários).\n"
              'JSON: {"titulo": "...", "subtitulo": "...", "apresentacao": "2 parágrafos sobre o livro", '
              '"capitulos": [{"n": 1, "titulo": "...", "objetivo": "...", "secoes": ["...", "..."], '
              '"fontes": ["id", "..."]}]}')
    texto = _chamar(sistema, pedido, log)
    plano = json.loads(re.search(r"\{.*\}", texto, re.S).group(0))
    livro.estado["plano"] = plano
    livro.gravar()
    log(f"🗂 Plano: {len(plano['capitulos'])} capítulos — {plano['titulo']}")
    return plano


def escrever(livro: Livro, palavras: int = 6000, log=print) -> int:
    plano = livro.estado["plano"]
    fontes = {p.stem: p for p in livro.fontes.glob("*.md")}
    sumario = "\n".join(f"{c['n']}. {c['titulo']}" for c in plano["capitulos"])
    feitos = 0
    for cap in plano["capitulos"]:
        destino = livro.capitulos / f"{cap['n']:02d}.md"
        if destino.exists():
            continue
        ids = [i for i in cap.get("fontes", []) if i in fontes]
        cota = LIMITE_FONTES_CAPITULO // max(1, len(ids))
        material = []
        for i in ids:
            meta, corpo = _ler_fonte(fontes[i])
            material.append(f"### FONTE {i} — {meta.get('titulo', '')} ({meta.get('origem', '')})\n{corpo[:cota]}")
        sistema = ("Você é um autor católico erudito escrevendo um livro de não ficção em português do Brasil, com a "
                   "densidade de um tratado e a clareza de um bom professor. Escreva em prosa contínua e bem "
                   "argumentada; use as fontes como base, explicando com SUAS palavras (nunca copie parágrafos "
                   "das fontes; citações literais só curtas e entre aspas, com o autor). Cite autores e documentos "
                   "pelo nome (ex.: Rerum Novarum, São Tomás, Belloc). Não invente fatos, datas nem citações.")
        pedido = (f"LIVRO: {plano['titulo']} — {plano.get('subtitulo', '')}\nSUMÁRIO:\n{sumario}\n\n"
                  f"Escreva agora o CAPÍTULO {cap['n']}: {cap['titulo']}\nObjetivo: {cap.get('objetivo', '')}\n"
                  f"Seções: {'; '.join(cap.get('secoes', []))}\n\nExtensão: cerca de {palavras} palavras. "
                  "Formato Markdown: comece com '# Capítulo N — Título', use '## ' para as seções, notas e "
                  "referências ao fim do capítulo em '## Referências do capítulo'. Não escreva nada fora do capítulo."
                  f"\n\n## Material de base\n\n" + "\n\n".join(material))
        inicio = time.time()
        log(f"✍️ Capítulo {cap['n']}/{len(plano['capitulos'])}: {cap['titulo']}…")
        texto = _chamar(sistema, pedido, log)
        destino.write_text(texto.strip() + "\n", encoding="utf-8")
        feitos += 1
        log(f"   {len(texto.split()):,} palavras ({(time.time() - inicio) / 60:.1f} min)")
    return feitos


# ------------------------------------------------------------------ PDF
CSS = """
@page { size: A5; margin: 18mm 16mm 20mm 16mm; @bottom-center { content: counter(page); } }
body { font-family: 'Georgia', 'Cambria', serif; font-size: 10.5pt; line-height: 1.55; color: #1a1a1a; text-align: justify; hyphens: auto; }
h1 { font-size: 19pt; text-align: left; margin: 0 0 14pt; page-break-before: always; line-height: 1.2; }
h2 { font-size: 13pt; margin: 18pt 0 6pt; text-align: left; }
h3 { font-size: 11pt; margin: 12pt 0 4pt; }
p { margin: 0 0 7pt; text-indent: 1.2em; }
blockquote { margin: 8pt 1.2em; font-style: italic; color: #333; }
.capa { text-align: center; page-break-after: always; padding-top: 38%; }
.capa h1 { page-break-before: avoid; font-size: 26pt; text-align: center; }
.capa .sub { font-size: 13pt; font-style: italic; margin-top: 10pt; }
.capa .nota { font-size: 9pt; margin-top: 60pt; color: #555; }
.sumario h1 { page-break-before: always; } .sumario li { margin: 3pt 0; text-indent: 0; }
"""


def pdf(livro: Livro, destino: Path | None = None, log=print) -> Path:
    import markdown
    plano = livro.estado["plano"]
    partes = [f"<div class='capa'><h1>{_html.escape(plano['titulo'])}</h1>"
              f"<div class='sub'>{_html.escape(plano.get('subtitulo', ''))}</div>"
              "<div class='nota'>Síntese organizada pela Central de IA a partir do canal do Telegram, dos textos e "
              "vídeos nele citados e dos materiais do acervo pessoal — com as fontes indicadas em cada capítulo."
              "</div></div>",
              "<div class='sumario'><h1>Sumário</h1><ol>" + "".join(
                  f"<li>{_html.escape(c['titulo'])}</li>" for c in plano["capitulos"]) + "</ol></div>",
              f"<h1>Apresentação</h1>{markdown.markdown(plano.get('apresentacao', ''))}"]
    for arquivo in sorted(livro.capitulos.glob("*.md")):
        partes.append(markdown.markdown(arquivo.read_text(encoding="utf-8"), extensions=["extra", "sane_lists"]))
    pagina = (f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><title>{_html.escape(plano['titulo'])}"
              f"</title><style>{CSS}</style></head><body>{''.join(partes)}</body></html>")
    arquivo_html = livro.pasta / "livro.html"
    arquivo_html.write_text(pagina, encoding="utf-8")
    destino = destino or (livro.pasta / f"{livro.slug}.pdf")
    edge = next((p for p in (Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                             Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")) if p.exists()), None)
    if not edge:
        raise RuntimeError("Microsoft Edge não encontrado para gerar o PDF")
    subprocess.run([str(edge), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={destino}", arquivo_html.as_uri()], capture_output=True, timeout=600)
    if not destino.exists():
        raise RuntimeError("o PDF não foi gerado")
    log(f"📕 PDF: {destino} ({destino.stat().st_size / 1e6:.1f} MB)")
    return destino
