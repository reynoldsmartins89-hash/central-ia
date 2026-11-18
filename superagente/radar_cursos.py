"""Radar de Cursos de Segurança: 3 agentes que vigiam, de verdade, a abertura de cursos, estágios e
capacitações de segurança de dignitários, proteção de autoridades, escolta e direção evasiva.

  Agente 1 — Judiciário, Legislativo e MP   (Polícia Judicial, Polícia Legislativa, CNJ/CEAJUD, MPs)
  Agente 2 — Forças Armadas e GSI           (Exército/PE/BGP, Marinha, FAB, GSI/PR, MD)
  Agente 3 — Polícias e Segurança Pública   (PF/ANP, PRF/UniPRF, PMs, PCs, SENASP, academias)

Fontes REAIS (nada inventado: todo aviso traz o link de onde veio):
  • Diário Oficial da União — busca pública da Imprensa Nacional (in.gov.br), últimos 7 dias.
  • Web e Instagram — busca na internet por perfis oficiais, portais gov.br, tribunais e notícias:
      - Perplexity (sonar), se PERPLEXITY_API_KEY estiver no .env;
      - senão, o Claude Code (cli:claude) com a ferramenta de busca na web, pela sua assinatura.
    O Instagram não é lido "por dentro" (sem login, sem robô na sua conta — isso pode bloquear a
    conta); as postagens públicas entram pelos buscadores.

Já avisados: ~/.central-ia/radar_cursos.db — nada é avisado duas vezes.
Relatório de cada rodada: resultados/radar-cursos/AAAA-MM-DD.md   ·   Avisos no Telegram.

Uso:  python main.py radar [--sem-web] [--sem-aviso]        (o Vigia roda sozinho a cada 6 horas)
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .agentes import RAIZ

PASTA = Path.home() / ".central-ia"
BANCO = PASTA / "radar_cursos.db"
PASTA_RELATORIOS = RAIZ / "resultados" / "radar-cursos"
NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/130.0 Safari/537.36")

POSITIVOS = [r"seguran[çc]a de dignit[áa]rios?", r"prote[çc][ãa]o (de|a|à) autoridades?", r"seguran[çc]a de autoridades?",
             r"seguran[çc]a aproximada", r"seguran[çc]a pessoal", r"prote[çc][ãa]o (à|a) pessoa", r"pessoa protegida",
             r"motorista de seguran[çc]a", r"condutor de autoridades?", r"escolta de (autoridades|dignit)",
             r"dire[çc][ãa]o (evasiva|operacional|t[áa]tica|defensiva e evasiva)", r"pilotagem (evasiva|policial|operacional)",
             r"condu[çc][ãa]o (operacional|de ve[íi]culos? de emerg)", r"batedor(es)?", r"seguran[çc]a presidencial",
             r"seguran[çc]a institucional", r"pol[íi]cia judicial", r"pol[íi]cia legislativa", r"agente de seguran[çc]a judici"]
NEGATIVOS = [r"vigilantes?\b", r"transporte de valores", r"seguran[çc]a privada", r"escolta armada"]
EXCECOES = ("autoridade", "dignitári", "dignitari", "magistrad", "parlamentar", "presiden")
CURSO = re.compile(r"(?i)curso|est[áa]gio|capacita[çc]|treinamento|instru[çc][ãa]o|forma[çc][ãa]o|"
                   r"reciclagem|aperfei[çc]oamento|especializa[çc]|vagas?|inscri[çc]|turma|edital|matr[íi]cula")


@dataclass
class Agente:
    chave: str
    nome: str
    dou: list[str]          # termos para o Diário Oficial
    orgaos: str             # quem vigiar na web e no Instagram


AGENTES = [
    Agente("judiciario", "Judiciário, Legislativo e MP",
           ['"polícia judicial" curso', '"polícia legislativa" curso', '"segurança institucional" curso',
            '"proteção de magistrados"'],
           "Polícia Judicial dos tribunais (STF, STJ, TST, TSE, TRFs, TJs, TRTs, TREs), CNJ/CEAJUD e escolas "
           "judiciais, Polícia Legislativa da Câmara (DEPOL) e do Senado (SPOL), Assembleias Legislativas, "
           "Ministério Público (MPF, MPs estaduais) e seus perfis oficiais no Instagram"),
    Agente("forcas_armadas", "Forças Armadas e GSI",
           ['"segurança de autoridades" estágio', '"segurança de dignitários"', '"segurança presidencial"',
            '"direção evasiva"', '"proteção de autoridades"'],
           "Exército (Polícia do Exército, Batalhão da Guarda Presidencial, DECEx, EsIE, CIOpGLO), Marinha "
           "(Fuzileiros, DEnsM), Aeronáutica (Infantaria, DEPENS), GSI/PR, Ministério da Defesa e seus perfis "
           "oficiais no Instagram"),
    Agente("policias", "Polícias e Segurança Pública",
           ['"segurança de dignitários" curso', '"proteção de autoridades" curso', '"escolta de autoridades"',
            '"pilotagem policial"', '"motorista de segurança"'],
           "Polícia Federal (ANP), PRF (UniPRF), Polícias Militares (Casas Militares, batalhões de guarda, "
           "academias), Polícias Civis (ACADEPOLs), Polícias Penais, SENASP/EAD-SENASP, Guardas Municipais e "
           "seus perfis oficiais no Instagram"),
]


@dataclass
class Achado:
    agente: str
    fonte: str
    orgao: str
    titulo: str
    url: str
    data: str = ""
    prazo: str = ""
    trecho: str = ""

    def chave(self) -> str:
        p = urllib.parse.urlparse(self.url.strip())
        url = urllib.parse.urlunparse((p.scheme, p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), "", "", ""))
        return hashlib.sha256((url or self.titulo.lower()).encode()).hexdigest()[:24]


def relevante(texto: str, exigir_curso: bool = True) -> bool:
    t = texto.lower()
    if any(re.search(p, t) for p in NEGATIVOS) and not any(e in t for e in EXCECOES):
        return False
    return any(re.search(p, t) for p in POSITIVOS) and (not exigir_curso or bool(CURSO.search(t)))


# ------------------------------------------------------------------ fonte 1: Diário Oficial da União
def _baixar(url: str, timeout: float = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": NAVEGADOR, "Accept": "text/html",
                                               "Accept-Language": "pt-BR,pt;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def ler_pagina_dou(pagina: str) -> list[dict]:
    m = re.search(r'BuscaDouPortlet_params" type="application/json">(.*?)</script>', pagina, re.S)
    return json.loads(m.group(1)).get("jsonArray", []) if m else []


PAUSA_DOU = 4          # segundos entre consultas: a Imprensa Nacional corta quem pergunta rápido demais
DOU_FORA = {"ate": 0.0}


def buscar_dou(agente: Agente, periodo: str = "semana", log=print) -> list[Achado]:
    import time
    achados = []
    for termo in agente.dou:
        if time.time() < DOU_FORA["ate"]:
            break                      # o DOU não respondeu nesta rodada: tenta de novo na próxima
        url = "https://www.in.gov.br/consulta/-/buscar/dou?" + urllib.parse.urlencode(
            {"q": termo, "s": "todos", "exactDate": periodo, "sortType": "0"})
        try:
            itens = ler_pagina_dou(_baixar(url, timeout=30))
        except Exception as erro:  # noqa: BLE001 — um termo que falha não derruba o agente
            log(f"   ⚠ DOU ({termo}): {str(erro)[:80]} — pulo o DOU até a próxima rodada")
            DOU_FORA["ate"] = time.time() + 1800
            continue
        time.sleep(PAUSA_DOU)
        for i in itens:
            trecho = html.unescape(re.sub(r"<[^>]+>", "", i.get("content", "")))
            titulo = html.unescape(i.get("title", ""))
            if relevante(f"{titulo} {trecho}"):
                achados.append(Achado(agente.chave, "DOU", i.get("hierarchyStr", "") or i.get("pubName", ""),
                                      titulo, f"https://www.in.gov.br/web/dou/-/{i.get('urlTitle', '')}",
                                      i.get("pubDate", ""), "", trecho[:400]))
    return achados


# ------------------------------------------------------------------ fonte 2: web e Instagram
PEDIDO_WEB = """Você é o {nome} do Radar de Cursos de Segurança. Pesquise na internet (faça várias buscas,
inclusive "site:instagram.com" e portais gov.br/.jus.br/.leg.br/.mil.br) publicações dos ÚLTIMOS 30 DIAS
sobre cursos, estágios, capacitações ou vagas de: segurança de dignitários, proteção de autoridades,
segurança aproximada, escolta, motorista de segurança, direção/pilotagem evasiva ou operacional.
Órgãos a vigiar: {orgaos}.
Ignore segurança privada comercial (vigilantes, transporte de valores).
Responda SOMENTE com JSON: uma lista de objetos com as chaves
"orgao", "titulo", "data", "prazo", "url", "fonte" (instagram, gov.br, notícia…) e "resumo" (1 frase).
Inclua SÓ itens cuja URL você realmente viu nos resultados. Se não achar nada, responda [].
"""


def _json_da_resposta(texto: str) -> list[dict]:
    m = re.search(r"\[.*\]", texto, re.S)
    if not m:
        return []
    try:
        dados = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [d for d in dados if isinstance(d, dict) and str(d.get("url", "")).startswith("http")]


def _perplexity(pedido: str) -> str:
    corpo = {"model": os.getenv("PERPLEXITY_MODELO", "sonar"), "messages": [{"role": "user", "content": pedido}],
             "search_recency_filter": "month"}
    req = urllib.request.Request("https://api.perplexity.ai/chat/completions", data=json.dumps(corpo).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + os.getenv("PERPLEXITY_API_KEY", "").strip()})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def _claude_com_busca(pedido: str) -> str:
    from .provedores import PASTA_CLI, _achar_cli
    exe = _achar_cli("claude")
    if not exe:
        raise RuntimeError("Claude Code não instalado e sem PERPLEXITY_API_KEY: sem busca na web")
    PASTA_CLI.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([exe, "-p", "--allowedTools", "WebSearch", "--output-format", "text",
                        "--model", os.getenv("RADAR_MODELO_CLAUDE", "haiku")],
                       input=pedido, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600, cwd=PASTA_CLI)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-200:])
    return r.stdout


def buscar_web(agente: Agente, log=print) -> list[Achado]:
    pedido = PEDIDO_WEB.format(nome=f"agente {agente.nome}", orgaos=agente.orgaos)
    motor = "Perplexity" if os.getenv("PERPLEXITY_API_KEY", "").strip() else "Claude (busca na web)"
    try:
        resposta = _perplexity(pedido) if motor == "Perplexity" else _claude_com_busca(pedido)
    except Exception as erro:  # noqa: BLE001
        log(f"   ⚠ web ({motor}): {str(erro)[:120]}")
        return []
    achados = []
    for d in _json_da_resposta(resposta):
        texto = " ".join(str(d.get(k, "")) for k in ("titulo", "resumo", "orgao"))
        if relevante(texto, exigir_curso=False):
            achados.append(Achado(agente.chave, str(d.get("fonte") or motor), str(d.get("orgao", "")),
                                  str(d.get("titulo", "")), str(d["url"]), str(d.get("data", "")),
                                  str(d.get("prazo", "")), str(d.get("resumo", ""))[:400]))
    return achados


# ------------------------------------------------------------------ memória, relatório e aviso
def _banco(caminho: Path | None = None) -> sqlite3.Connection:
    caminho = caminho or BANCO
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho)
    con.execute("""CREATE TABLE IF NOT EXISTS achados (chave TEXT PRIMARY KEY, agente TEXT, fonte TEXT, orgao TEXT,
                   titulo TEXT, url TEXT, data TEXT, prazo TEXT, visto_em TEXT)""")
    return con


def novos(con: sqlite3.Connection, achados: list[Achado]) -> list[Achado]:
    saida, vistos = [], set()
    for a in achados:
        k = a.chave()
        if k in vistos or con.execute("SELECT 1 FROM achados WHERE chave = ?", (k,)).fetchone():
            continue
        vistos.add(k)
        saida.append(a)
    return saida


def guardar(con: sqlite3.Connection, achados: list[Achado]) -> None:
    agora = datetime.now().isoformat(timespec="seconds")
    con.executemany("INSERT OR IGNORE INTO achados VALUES (?,?,?,?,?,?,?,?,?)",
                    [(a.chave(), a.agente, a.fonte, a.orgao, a.titulo, a.url, a.data, a.prazo, agora) for a in achados])
    con.commit()


def relatorio(achados: list[Achado]) -> str:
    linhas = [f"# Radar de Cursos de Segurança — {datetime.now():%d/%m/%Y %H:%M}", ""]
    for ag in AGENTES:
        meus = [a for a in achados if a.agente == ag.chave]
        linhas += [f"## {ag.nome} ({len(meus)} novidade(s))", ""]
        for a in meus:
            extra = " · ".join(x for x in (a.data, f"prazo: {a.prazo}" if a.prazo else "", a.fonte) if x)
            linhas += [f"- **{a.titulo}** — {a.orgao}", f"  {extra}", f"  {a.url}"]
            if a.trecho:
                linhas.append(f"  > {a.trecho[:300]}")
        linhas.append("")
    return "\n".join(linhas)


def mensagem(achados: list[Achado], limite: int = 12) -> str:
    texto = [f"🛡️ RADAR DE CURSOS DE SEGURANÇA — {len(achados)} novidade(s)"]
    for ag in AGENTES:
        meus = [a for a in achados if a.agente == ag.chave][:limite]
        if meus:
            texto.append(f"\n**{ag.nome}**")
            texto += [f"• {a.titulo[:120]} ({a.orgao[:60]}){' — prazo: ' + a.prazo[:40] if a.prazo else ''}\n  {a.url}"
                      for a in meus]
    return "\n".join(texto)


def rodar(web: bool = True, avisar: bool = True, log=print) -> list[Achado]:
    con = _banco()
    primeira_vez = not con.execute("SELECT 1 FROM achados LIMIT 1").fetchone()
    achados = []
    for ag in AGENTES:
        log(f"🔎 {ag.nome}…")
        achados += buscar_dou(ag, "ano" if primeira_vez else "semana", log)
        if web:
            achados += buscar_web(ag, log)
    try:
        novidades = novos(con, achados)
        guardar(con, novidades)
    finally:
        con.close()
    PASTA_RELATORIOS.mkdir(parents=True, exist_ok=True)
    arquivo = PASTA_RELATORIOS / f"{datetime.now():%Y-%m-%d_%Hh%M}.md"
    arquivo.write_text(relatorio(novidades), encoding="utf-8")
    log(f"✅ {len(novidades)} novidade(s) · relatório: {arquivo}")
    if novidades and avisar:
        from . import telegram
        telegram.enviar(mensagem(novidades))
    return novidades
