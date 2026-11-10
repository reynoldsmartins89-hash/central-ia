"""Fontes da web para os especialistas: lê os artigos de um site inteiro (pelo sitemap) e guarda em .md.

Uso:  python main.py site https://www.settee.io mentor_imigracao
Os textos ficam em ~/.central-ia/fontes_web/<agente>/<site>/ e entram como base FIXA da curadoria do agente.
Leitura educada: uma página por vez, com pausa, só páginas públicas do próprio site.
"""
from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from pathlib import Path

PASTA = Path.home() / ".central-ia" / "fontes_web"
IGNORAR = re.compile(r"/(tag|tags|categoria|category|author|autor|page|wp-json|feed|carrinho|cart|checkout|login|"
                     r"conta|account)/|\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|xml)$", re.I)


def _baixar(url: str) -> str:
    from .livro import _baixar as baixar
    return baixar(url)[1]


def _urls_do_sitemap(base: str, limite: int) -> list[str]:
    dominio = urllib.parse.urlparse(base).netloc
    pendentes, vistos, paginas = [urllib.parse.urljoin(base, "/sitemap.xml"),
                                  urllib.parse.urljoin(base, "/sitemap_index.xml")], set(), []
    while pendentes and len(paginas) < limite * 3:
        mapa = pendentes.pop(0)
        if mapa in vistos:
            continue
        vistos.add(mapa)
        try:
            xml = _baixar(mapa)
        except Exception:  # noqa: BLE001
            continue
        for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml):
            loc = loc.replace("&amp;", "&")
            if urllib.parse.urlparse(loc).netloc != dominio:
                continue
            if loc.endswith(".xml"):
                pendentes.append(loc)
            elif not IGNORAR.search(loc):
                paginas.append(loc)
    # artigos primeiro (costumam ter /article/, /blog/ ou datas no endereço)
    paginas = list(dict.fromkeys(paginas))
    unicas = set(paginas)   # mesmo artigo em "/en/…" e sem "/en/": fica uma cópia só
    paginas = [u for u in paginas if not ("/en/" in u and u.replace("/en/", "/", 1) in unicas)]
    paginas.sort(key=lambda u: 0 if re.search(r"/(article|artigo|blog|post|noticia)s?/|/20\d\d/", u) else 1)
    return paginas[:limite]


def coletar_site(base: str, agente: str, limite: int = 300, log=print) -> int:
    from .livro import _texto_da_pagina
    dominio = urllib.parse.urlparse(base).netloc.replace("www.", "")
    destino = PASTA / agente / dominio
    destino.mkdir(parents=True, exist_ok=True)
    urls = _urls_do_sitemap(base, limite)
    log(f"🌐 {dominio}: {len(urls)} páginas no mapa do site")
    feitos = 0
    for url in urls:
        arquivo = destino / f"{hashlib.sha1(url.encode()).hexdigest()[:12]}.md"
        if arquivo.exists():
            continue
        try:
            titulo, texto = _texto_da_pagina(_baixar(url))
        except Exception as erro:  # noqa: BLE001
            log(f"   ⚠ {url}: {str(erro)[:80]}")
            continue
        if len(texto) < 600:
            continue
        titulo = re.sub(r"\s*[-|–]\s*(Settee|Capital Global).*$", "", titulo or url).strip()
        arquivo.write_text(f"---\ntitulo: {titulo}\norigem: {url}\nsite: {dominio}\n---\n\n# {titulo}\n\n"
                           f"Fonte: {url}\n\n{texto}\n", encoding="utf-8")
        feitos += 1
        time.sleep(1.0)
    log(f"✅ {dominio}: {feitos} páginas novas guardadas em {destino}")
    return feitos
