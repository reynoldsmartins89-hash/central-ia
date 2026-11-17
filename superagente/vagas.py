"""Caçador de Vagas: procura vagas remotas (ou híbridas com pouca presença) nos maiores sites,
compara cada uma com o seu perfil usando IA e avisa no Telegram só as que realmente têm a ver.

Fontes (sem login e dentro das regras de cada site):
  • Gupy (maior portal do Brasil, filtro remoto/híbrido)   • Remotive   • Remote OK
  • We Work Remotely   • Himalayas   • Working Nomads   • Arbeitnow
  • Adzuna Brasil (opcional: ADZUNA_APP_ID / ADZUNA_APP_KEY no .env, cadastro grátis)
  • Alertas que chegam no seu e-mail: LinkedIn, Indeed, Glassdoor, Catho, InfoJobs, Vagas.com
    (crie os alertas nesses sites; o caçador lê os e-mails sem marcar como lidos)

Perfil (dados pessoais, fora do repositório): ~/.central-ia/vagas/perfil.toml
Vagas já vistas: ~/.central-ia/vagas.db — nenhuma vaga é avisada duas vezes.
Relatório do dia: resultados/vagas/AAAA-MM-DD.md

Uso:  python main.py vagas perfil | buscar [--sem-ia] [--sem-aviso] | ultimas | fontes
O Vigia roda a busca sozinho a cada 4 horas.
"""
from __future__ import annotations

import email
import hashlib
import html
import imaplib
import json
import os
import re
import sqlite3
import time
import tomllib
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import policy
from pathlib import Path

from .agentes import RAIZ

PASTA = Path.home() / ".central-ia" / "vagas"
PERFIL = PASTA / "perfil.toml"
MODELO_PERFIL = RAIZ / "agentes" / "perfil-vagas.exemplo.toml"
BANCO = Path.home() / ".central-ia" / "vagas.db"
SAIDA = RAIZ / "resultados" / "vagas"
HABILIDADE = "pesquisa/cacador-de-vagas"
NAVEGADOR = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
PAUSA = 1.5          # segundos entre pedidos ao mesmo site (leitura educada)
TAMANHO_LOTE = 6     # vagas por chamada de IA

# palavras que indicam que uma vaga internacional aceita quem mora no Brasil
ACEITA_BRASIL = ("brazil", "brasil", "latam", "latin america", "south america", "americas", "worldwide",
                 "anywhere", "global", "international", "world")
SO_REMOTO = re.compile(r"\b(100% )?(remote|remoto|fully remote|home office)\b")


@dataclass
class Vaga:
    fonte: str
    titulo: str
    url: str
    empresa: str = ""
    local: str = ""
    modalidade: str = ""          # remoto | hibrido | presencial | "" (não informado)
    descricao: str = ""
    publicada: str = ""           # ISO
    so_titulo: bool = False       # veio de alerta de e-mail: só o título, sem descrição
    extra: dict = field(default_factory=dict)

    @property
    def chave(self) -> str:
        base = self.url if self.so_titulo else re.sub(r"[?#].*$", "", self.url)   # alertas: o id está na query
        return hashlib.sha1(base.lower().encode()).hexdigest()[:16]

    @property
    def assinatura(self) -> str:
        """Mesma vaga publicada em dois sites: título + empresa normalizados."""
        return hashlib.sha1(f"{normalizar(self.titulo)}|{normalizar(self.empresa)}".encode()).hexdigest()[:16]


# ------------------------------------------------------------------ utilidades
def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def limpar_html(texto: str) -> str:
    sem = re.sub(r"(?is)<(script|style).*?</\1>", " ", texto or "")
    sem = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", sem)
    sem = html.unescape(re.sub(r"<[^>]+>", " ", sem))
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", sem)).strip()


def _baixar(url: str, timeout: float = 40) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": NAVEGADOR, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def _json(url: str) -> object:
    return json.loads(_baixar(url))


def _data_iso(valor) -> str:
    if valor in (None, ""):
        return ""
    try:
        if isinstance(valor, (int, float)) or str(valor).isdigit():
            return datetime.fromtimestamp(int(valor), timezone.utc).isoformat()
        texto = str(valor).strip()
        try:
            return datetime.fromisoformat(texto.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return email.utils.parsedate_to_datetime(texto).isoformat()
    except Exception:  # noqa: BLE001
        return ""


# ------------------------------------------------------------------ perfil
def carregar_perfil() -> dict:
    if not PERFIL.exists():
        PASTA.mkdir(parents=True, exist_ok=True)
        PERFIL.write_text(MODELO_PERFIL.read_text(encoding="utf-8"), encoding="utf-8")
    with open(PERFIL, "rb") as f:
        perfil = tomllib.load(f)
    with open(MODELO_PERFIL, "rb") as f:
        padrao = tomllib.load(f)
    return {**padrao, **perfil}


def perfil_preenchido(perfil: dict) -> bool:
    return "..." not in perfil.get("resumo", "...")


# ------------------------------------------------------------------ fontes
def gupy(termos: list[str], modalidades: list[str], log=print) -> list[Vaga]:
    tipos = [{"remoto": "remote", "hibrido": "hybrid"}[m] for m in modalidades if m in ("remoto", "hibrido")]
    vagas = []
    for termo in termos:
        for tipo in tipos:
            url = (f"https://portal.gupy.io/job-search/term={urllib.parse.quote(termo)}"
                   f"&workplaceTypes[]={tipo}")
            try:
                pagina = _baixar(url)
                dados = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', pagina, re.S)
                lista = json.loads(dados.group(1))["props"]["pageProps"]["initialJobList"]["data"]
            except Exception as erro:  # noqa: BLE001
                log(f"   ⚠️ Gupy ({termo}, {tipo}): {str(erro)[:80]}")
                continue
            vagas += [vaga_gupy(j) for j in lista]
            time.sleep(PAUSA)
    return vagas


def vaga_gupy(j: dict) -> Vaga:
    local = ", ".join(x for x in (j.get("city"), j.get("state")) if x)
    modalidade = {"remote": "remoto", "hybrid": "hibrido", "on-site": "presencial"}.get(j.get("workplaceType", ""), "")
    return Vaga("Gupy", j.get("name", "").strip(), j.get("jobUrl", ""), j.get("careerPageName", ""), local,
                modalidade, limpar_html(j.get("description", "")), _data_iso(j.get("publishedDate")))


def remotive(termos: list[str], log=print) -> list[Vaga]:
    vagas = []
    for termo in termos:
        try:
            dados = _json(f"https://remotive.com/api/remote-jobs?search={urllib.parse.quote(termo)}&limit=50")
        except Exception as erro:  # noqa: BLE001
            log(f"   ⚠️ Remotive ({termo}): {str(erro)[:80]}")
            continue
        for j in dados.get("jobs", []):
            vagas.append(Vaga("Remotive", j.get("title", ""), j.get("url", ""), j.get("company_name", ""),
                              j.get("candidate_required_location", ""), "remoto",
                              limpar_html(j.get("description", "")), _data_iso(j.get("publication_date"))))
        time.sleep(PAUSA)
    return vagas


def remoteok(log=print) -> list[Vaga]:
    try:
        dados = _json("https://remoteok.com/api")
    except Exception as erro:  # noqa: BLE001
        log(f"   ⚠️ Remote OK: {str(erro)[:80]}")
        return []
    return [Vaga("Remote OK", j.get("position", ""), j.get("url", ""), j.get("company", ""),
                 j.get("location", ""), "remoto", limpar_html(j.get("description", "")),
                 _data_iso(j.get("epoch") or j.get("date")))
            for j in dados if isinstance(j, dict) and j.get("position")]


def weworkremotely(log=print) -> list[Vaga]:
    try:
        raiz = ET.fromstring(_baixar("https://weworkremotely.com/remote-jobs.rss", timeout=60))
    except Exception as erro:  # noqa: BLE001
        log(f"   ⚠️ We Work Remotely: {str(erro)[:80]}")
        return []
    vagas = []
    for item in raiz.iter("item"):
        titulo = item.findtext("title", "")
        empresa, _, cargo = titulo.partition(": ")
        vagas.append(Vaga("We Work Remotely", cargo or titulo, item.findtext("link", ""), empresa if cargo else "",
                          item.findtext("region", ""), "remoto", limpar_html(item.findtext("description", "")),
                          _data_iso(item.findtext("pubDate"))))
    return vagas


def himalayas(termos: list[str], log=print) -> list[Vaga]:
    vagas = []
    for termo in termos:
        try:
            dados = _json(f"https://himalayas.app/jobs/api/search?q={urllib.parse.quote(termo)}")
        except Exception as erro:  # noqa: BLE001
            log(f"   ⚠️ Himalayas ({termo}): {str(erro)[:80]}")
            continue
        for j in dados.get("jobs", []):
            vagas.append(Vaga("Himalayas", j.get("title", ""), j.get("guid") or j.get("applicationLink", ""),
                              j.get("companyName", ""), ", ".join(j.get("locationRestrictions") or []) or "Worldwide",
                              "remoto", limpar_html(j.get("description", "")), _data_iso(j.get("pubDate"))))
        time.sleep(PAUSA)
    return vagas


def workingnomads(log=print) -> list[Vaga]:
    try:
        dados = _json("https://www.workingnomads.com/api/exposed_jobs/")
    except Exception as erro:  # noqa: BLE001
        log(f"   ⚠️ Working Nomads: {str(erro)[:80]}")
        return []
    return [Vaga("Working Nomads", j.get("title", ""), j.get("url", ""), j.get("company_name", ""),
                 j.get("location", ""), "remoto", limpar_html(j.get("description", "")), _data_iso(j.get("pub_date")))
            for j in dados if isinstance(j, dict)]


def arbeitnow(log=print) -> list[Vaga]:
    try:
        dados = _json("https://www.arbeitnow.com/api/job-board-api")
    except Exception as erro:  # noqa: BLE001
        log(f"   ⚠️ Arbeitnow: {str(erro)[:80]}")
        return []
    return [Vaga("Arbeitnow", j.get("title", ""), j.get("url", ""), j.get("company_name", ""), j.get("location", ""),
                 "remoto", limpar_html(j.get("description", "")), _data_iso(j.get("created_at")))
            for j in dados.get("data", []) if j.get("remote")]


def adzuna(termos: list[str], log=print) -> list[Vaga]:
    app_id, app_key = os.getenv("ADZUNA_APP_ID", "").strip(), os.getenv("ADZUNA_APP_KEY", "").strip()
    if not (app_id and app_key):
        return []
    vagas = []
    for termo in termos:
        consulta = urllib.parse.urlencode({"app_id": app_id, "app_key": app_key, "what": termo,
                                           "what_or": "remoto home office híbrido", "results_per_page": 50,
                                           "max_days_old": 21, "content-type": "application/json"})
        try:
            dados = _json(f"https://api.adzuna.com/v1/api/jobs/br/search/1?{consulta}")
        except Exception as erro:  # noqa: BLE001
            log(f"   ⚠️ Adzuna ({termo}): {str(erro)[:80]}")
            continue
        for j in dados.get("results", []):
            vagas.append(Vaga("Adzuna", limpar_html(j.get("title", "")), j.get("redirect_url", ""),
                              (j.get("company") or {}).get("display_name", ""),
                              (j.get("location") or {}).get("display_name", ""), "",
                              limpar_html(j.get("description", "")), _data_iso(j.get("created"))))
        time.sleep(PAUSA)
    return vagas


# ------------------------------------------------------------------ alertas por e-mail
# remetente (trecho do endereço) → (nome do site, padrão do link de vaga, como montar o link limpo)
ALERTAS = {
    "linkedin.com": ("LinkedIn", r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", "https://www.linkedin.com/jobs/view/{}"),
    "indeed.com": ("Indeed", r"[?&]jk=([0-9a-f]{8,})", "https://br.indeed.com/viewjob?jk={}"),
    "glassdoor.com": ("Glassdoor", r"(?:jobListingId|jl)=(\d+)", "https://www.glassdoor.com.br/job-listing/?jl={}"),
    "catho.com.br": ("Catho", r"catho\.com\.br/vagas/[^\s\"'?]+?/(\d+)", "https://www.catho.com.br/vagas/x/{}/"),
    "infojobs.com.br": ("InfoJobs", r"infojobs\.com\.br/(vaga-de-[^\s\"'?]+?\.aspx)", "https://www.infojobs.com.br/{}"),
    "vagas.com.br": ("Vagas.com", r"vagas\.com\.br/vagas/(v\d+)", "https://www.vagas.com.br/vagas/{}"),
    "gupy.io": ("Gupy", r"(https://[a-z0-9-]+\.gupy\.io/jobs?/[^\s\"'?]+)", "{}"),
}
TEXTO_INUTIL = re.compile(r"^(ver|view|see|apply|candidat|saiba|mais|more|abrir|open|ver vaga|aplicar)\b", re.I)


def vagas_do_email(corpo_html: str, remetente: str, data: str = "") -> list[Vaga]:
    """Tira as vagas (título + link) de um e-mail de alerta."""
    site = next((v for k, v in ALERTAS.items() if k in remetente.lower()), None)
    if not site:
        return []
    nome, padrao, modelo = site
    achadas: dict[str, str] = {}
    for href, texto in re.findall(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', corpo_html):
        alvo = urllib.parse.unquote(html.unescape(href))
        m = re.search(padrao, alvo)
        if not m:
            continue
        titulo = re.sub(r"\s+", " ", limpar_html(texto)).strip()
        if len(titulo) < 4 or len(titulo) > 140 or TEXTO_INUTIL.match(titulo):
            titulo = ""
        anterior = achadas.get(m.group(1), "")
        if titulo and (not anterior or len(titulo) < len(anterior)):   # o mais curto costuma ser só o cargo
            achadas[m.group(1)] = titulo
        else:
            achadas.setdefault(m.group(1), anterior)
    return [Vaga(nome, t, modelo.format(i), publicada=data, so_titulo=True) for i, t in achadas.items() if t]


def alertas_por_email(perfil: dict, dias: int = 3, log=print) -> list[Vaga]:
    from . import emails
    contas = [c for c in emails.contas_configuradas() if c["senha"]]
    escolhidas = [x.upper() for x in perfil.get("contas_email") or []]
    contas = [c for c in contas if c["id"] in escolhidas] if escolhidas else [c for c in contas if c["id"] != "EB"]
    desde = (datetime.now() - timedelta(days=dias)).strftime("%d-%b-%Y")
    vagas = []
    for conta in contas:
        try:
            imap = emails.conectar(conta)
        except Exception as erro:  # noqa: BLE001
            log(f"   ⚠️ e-mail {conta['rotulo']}: {str(erro)[:80]}")
            continue
        try:
            imap.select("INBOX", readonly=True)
            for dominio in ALERTAS:
                tipo, dados = imap.uid("SEARCH", None, "SINCE", desde, "FROM", dominio)
                uids = (dados[0] or b"").split() if tipo == "OK" else []
                for uid in uids[-30:]:
                    tipo, partes = imap.uid("FETCH", uid, "(BODY.PEEK[]<0.400000>)")
                    bruto = next((p[1] for p in partes if isinstance(p, tuple)), b"")
                    msg = email.message_from_bytes(bruto, policy=policy.default)
                    corpo = ""
                    for parte in msg.walk():
                        if parte.get_content_type() == "text/html":
                            try:
                                corpo = parte.get_content()
                            except Exception:  # noqa: BLE001
                                corpo = (parte.get_payload(decode=True) or b"").decode("utf-8", "replace")
                            break
                    vagas += vagas_do_email(corpo, str(msg.get("From", "")), _data_iso(msg.get("Date")))
        except (imaplib.IMAP4.error, OSError) as erro:
            log(f"   ⚠️ e-mail {conta['rotulo']}: {str(erro)[:80]}")
        finally:
            emails._sair(imap)
    return vagas


def coletar(perfil: dict, log=print) -> list[Vaga]:
    termos_pt, termos_en = perfil.get("termos_pt", []), perfil.get("termos_en", [])
    modalidades = perfil.get("modalidades", ["remoto", "hibrido"])
    etapas = [("Gupy", lambda: gupy(termos_pt, modalidades, log)),
              ("Adzuna", lambda: adzuna(termos_pt, log))]
    if perfil.get("aceita_vagas_do_exterior", True):
        etapas += [("Remotive", lambda: remotive(termos_en, log)), ("Remote OK", lambda: remoteok(log)),
                   ("We Work Remotely", lambda: weworkremotely(log)), ("Himalayas", lambda: himalayas(termos_en, log)),
                   ("Working Nomads", lambda: workingnomads(log)), ("Arbeitnow", lambda: arbeitnow(log))]
    if perfil.get("ler_alertas_por_email", True):
        etapas.append(("alertas por e-mail", lambda: alertas_por_email(perfil, log=log)))
    todas = []
    for nome, funcao in etapas:
        achadas = funcao()
        if achadas:
            log(f"   🔎 {nome}: {len(achadas)} vagas")
        todas += achadas
    return todas


# ------------------------------------------------------------------ filtro (antes da IA, sem custo)
def relevancia(vaga: Vaga, termos: list[str]) -> int:
    titulo, texto = normalizar(vaga.titulo), normalizar(vaga.descricao)
    pontos = 0
    for termo in (normalizar(t) for t in termos if t.strip()):
        if re.search(rf"\b{re.escape(termo)}\b", titulo):
            pontos += 3
        elif re.search(rf"\b{re.escape(termo)}\b", texto):
            pontos += 1
    return pontos


def motivo_descarte(vaga: Vaga, perfil: dict) -> str:
    """'' se a vaga passa no filtro; senão, o motivo do descarte."""
    titulo = normalizar(vaga.titulo)
    if not vaga.titulo or not vaga.url:
        return "incompleta"
    if any(re.search(rf"\b{re.escape(normalizar(p))}\b", titulo) for p in perfil.get("excluir_titulo", [])):
        return "título excluído"
    if vaga.modalidade == "presencial":
        return "presencial"
    if vaga.modalidade and vaga.modalidade not in perfil.get("modalidades", ["remoto", "hibrido"]):
        return "modalidade"
    cidades = [normalizar(c) for c in perfil.get("cidades_hibrido", [])]
    if vaga.modalidade == "hibrido" and cidades and not any(c in normalizar(vaga.local) for c in cidades):
        return "híbrida em outra cidade"
    local = SO_REMOTO.sub("", normalizar(vaga.local)).strip(" -,/()")   # "Remote" sozinho não restringe país
    if (perfil.get("descartar_restritas_a_outros_paises", True) and vaga.fonte not in ("Gupy", "Adzuna")
            and not vaga.so_titulo and local and not any(p in local for p in ACEITA_BRASIL)):
        return "restrita a outro país"
    if vaga.publicada:
        try:
            idade = datetime.now(timezone.utc) - datetime.fromisoformat(vaga.publicada).astimezone(timezone.utc)
            if idade.days > int(perfil.get("dias_maximos", 21)):
                return "antiga"
        except ValueError:
            pass
    termos = perfil.get("termos_pt", []) + perfil.get("termos_en", [])
    if not vaga.so_titulo and relevancia(vaga, termos) < 2:
        return "fora do perfil"
    return ""


# ------------------------------------------------------------------ banco (vagas já vistas)
def _banco() -> sqlite3.Connection:
    BANCO.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BANCO)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS vagas (
            chave TEXT PRIMARY KEY, assinatura TEXT, fonte TEXT, titulo TEXT, empresa TEXT, local TEXT,
            url TEXT, modalidade TEXT, presencial TEXT, nota INTEGER, motivo TEXT, alerta TEXT,
            avaliada_por TEXT, descartada TEXT, avisada INTEGER DEFAULT 0, vista_em TEXT);
        CREATE INDEX IF NOT EXISTS idx_assinatura ON vagas(assinatura);
    """)
    return con


def ja_vistas(con: sqlite3.Connection, vagas: list[Vaga]) -> list[Vaga]:
    """Tira as vagas já vistas e as repetidas (mesma vaga em dois sites)."""
    novas, assinaturas = [], set()
    for v in vagas:
        if v.assinatura in assinaturas or con.execute(
                "SELECT 1 FROM vagas WHERE chave=? OR (assinatura=? AND empresa<>'')", (v.chave, v.assinatura)).fetchone():
            continue
        assinaturas.add(v.assinatura)
        novas.append(v)
    return novas


def _gravar(con, v: Vaga, **campos) -> None:
    dados = {"chave": v.chave, "assinatura": v.assinatura, "fonte": v.fonte, "titulo": v.titulo,
             "empresa": v.empresa, "local": v.local, "url": v.url, "modalidade": v.modalidade,
             "vista_em": datetime.now().isoformat(timespec="minutes"), **campos}
    con.execute(f"INSERT OR REPLACE INTO vagas ({','.join(dados)}) VALUES ({','.join('?' * len(dados))})",
                list(dados.values()))


# ------------------------------------------------------------------ avaliação por IA
def _modelos() -> list[str]:
    with open(RAIZ / "agentes" / "agentes.toml", "rb") as f:
        agente = tomllib.load(f).get("agentes", {}).get("cacador_vagas", {})
    padrao = agente.get("modelos") or ["gemini:gemini-3.5-flash", "groq:openai/gpt-oss-120b", "ollama:qwen3:4b"]
    return [m.strip() for m in os.getenv("VAGAS_IA_MODELOS", ",".join(padrao)).split(",") if m.strip()]


def avaliar_lote(lote: list[Vaga], perfil: dict, modelos: list[str] | None = None, log=print) -> dict[int, dict]:
    from . import habilidades
    from .orquestrador import _extrair_json
    from .provedores import ErroProvedor, chamar_com_reserva
    sistema = (habilidades.carregar(HABILIDADE).como_prompt()
               + "\n\n---\n## Perfil do candidato\n" + perfil.get("resumo", "").strip()
               + f"\n\nModalidades aceitas: {', '.join(perfil.get('modalidades', []))}."
               + (f" Híbrido só em: {', '.join(perfil['cidades_hibrido'])}." if perfil.get("cidades_hibrido") else ""))
    linhas = [json.dumps({"id": i, "titulo": v.titulo, "empresa": v.empresa, "local": v.local,
                          "modalidade_informada": v.modalidade or "não informada", "site": v.fonte,
                          "descricao": v.descricao[:1200] if v.descricao else "(só o título — veio de alerta de e-mail)"},
                         ensure_ascii=False) for i, v in enumerate(lote)]
    try:
        modelo, resposta = chamar_com_reserva(modelos or _modelos(), sistema,
                                              "Vagas (um JSON por linha):\n" + "\n".join(linhas), log=log)
        itens = _extrair_json(resposta)
    except (ErroProvedor, ValueError) as erro:
        log(f"   ⚠️ IA indisponível neste lote ({str(erro)[:80]})")
        return {}
    saida = {}
    for item in itens if isinstance(itens, list) else []:
        try:
            i = int(item["id"])
            if 0 <= i < len(lote):
                saida[i] = {"nota": max(0, min(100, int(float(item.get("nota", 0))))),
                            "modalidade": str(item.get("modalidade", ""))[:20],
                            "presencial": str(item.get("presencial", ""))[:120],
                            "motivo": str(item.get("motivo", ""))[:400], "alerta": str(item.get("alerta", ""))[:200],
                            "avaliada_por": modelo}
        except (KeyError, TypeError, ValueError):
            continue
    return saida


# ------------------------------------------------------------------ aviso e relatório
def mensagem_aviso(itens: list[tuple[Vaga, dict]]) -> str:
    linhas = [f"💼 **Caçador de Vagas: {len(itens)} vaga(s) com a sua cara**", ""]
    for v, a in itens:
        onde = " · ".join(x for x in (v.empresa, a.get("modalidade") or v.modalidade, v.fonte) if x)
        linhas.append(f"🎯 **{a['nota']}/100 — {v.titulo}**")
        linhas.append(onde)
        if a.get("presencial") and normalizar(a["presencial"]) not in ("nenhum", "nenhuma", ""):
            linhas.append(f"📍 Presencial: {a['presencial']}")
        linhas.append(f"✅ {a['motivo']}")
        if a.get("alerta"):
            linhas.append(f"⚠️ {a['alerta']}")
        linhas.append(f"[Abrir a vaga]({v.url})")
        linhas.append("")
    return "\n".join(linhas).strip()


def _relatorio(avaliadas: list[tuple[Vaga, dict]], nota_minima: int) -> Path:
    SAIDA.mkdir(parents=True, exist_ok=True)
    arquivo = SAIDA / f"{datetime.now():%Y-%m-%d}.md"
    novo = not arquivo.exists()
    with open(arquivo, "a", encoding="utf-8") as f:
        if novo:
            f.write(f"# Caçador de Vagas — {datetime.now():%d/%m/%Y}\n\nNota mínima para aviso: {nota_minima}.\n")
        f.write(f"\n## Rodada das {datetime.now():%H:%M}\n\n| Nota | Vaga | Empresa | Site | Por quê |\n|---|---|---|---|---|\n")
        for v, a in sorted(avaliadas, key=lambda x: -x[1]["nota"]):
            marca = "🎯 " if a["nota"] >= nota_minima else ""
            motivo = (a["motivo"] + (f" ⚠️ {a['alerta']}" if a.get("alerta") else "")).replace("|", "/")
            f.write(f"| {marca}{a['nota']} | [{v.titulo.replace('|', '/')}]({v.url}) | {v.empresa.replace('|', '/')} "
                    f"| {v.fonte} | {motivo} |\n")
    return arquivo


# ------------------------------------------------------------------ rodada completa
def buscar(usar_ia: bool = True, avisar: bool = True, log=print) -> dict:
    perfil = carregar_perfil()
    if not perfil_preenchido(perfil):
        log(f"⚠️ Preencha o seu perfil antes: {PERFIL}  (python main.py vagas perfil)")
        return {"erro": "perfil não preenchido"}
    log("💼 Caçador de Vagas: procurando…")
    coletadas = coletar(perfil, log)
    con = _banco()
    novas = ja_vistas(con, coletadas)
    candidatas, descartes = [], {}
    for v in novas:
        motivo = motivo_descarte(v, perfil)
        if motivo:
            descartes[motivo] = descartes.get(motivo, 0) + 1
            _gravar(con, v, descartada=motivo)
        else:
            candidatas.append(v)
    con.commit()
    termos = perfil.get("termos_pt", []) + perfil.get("termos_en", [])
    candidatas.sort(key=lambda v: (v.so_titulo, -relevancia(v, termos)))
    limite = int(perfil.get("max_avaliacoes_por_rodada", 80))
    log(f"   {len(coletadas)} coletadas · {len(novas)} novas · {len(candidatas)} passaram no filtro"
        + (f" (descartes: {', '.join(f'{k} {n}' for k, n in descartes.items())})" if descartes else ""))
    if not usar_ia:
        for v in candidatas[:30]:
            log(f"   • [{v.fonte}] {v.titulo} — {v.empresa} ({v.modalidade or '?'}) {v.url}")
        return {"coletadas": len(coletadas), "novas": len(novas), "candidatas": len(candidatas)}

    nota_minima = int(perfil.get("nota_minima", 75))
    avaliadas: list[tuple[Vaga, dict]] = []
    for i in range(0, min(len(candidatas), limite), TAMANHO_LOTE):
        lote = candidatas[i:i + TAMANHO_LOTE]
        notas = avaliar_lote(lote, perfil, log=log)
        for j, v in enumerate(lote):
            if j in notas:      # sem nota (IA fora): fica para a próxima rodada
                a = notas[j]
                avaliadas.append((v, a))
                _gravar(con, v, presencial=a["presencial"], nota=a["nota"], motivo=a["motivo"], alerta=a["alerta"],
                        avaliada_por=a["avaliada_por"], modalidade=a["modalidade"] or v.modalidade)
        con.commit()
        log(f"   🧠 {min(i + TAMANHO_LOTE, len(candidatas), limite)}/{min(len(candidatas), limite)} avaliadas")

    boas = sorted([(v, a) for v, a in avaliadas if a["nota"] >= nota_minima and a["modalidade"] != "presencial"],
                  key=lambda x: -x[1]["nota"])
    arquivo = _relatorio(avaliadas, nota_minima) if avaliadas else None
    if boas and avisar:
        from . import telegram
        for k in range(0, len(boas), 5):        # até 5 vagas por mensagem (limite do Telegram)
            telegram.enviar(mensagem_aviso(boas[k:k + 5]))
        con.executemany("UPDATE vagas SET avisada=1 WHERE chave=?", [(v.chave,) for v, _ in boas])
        con.commit()
    con.close()
    log(f"✅ {len(avaliadas)} avaliadas · {len(boas)} com nota ≥ {nota_minima}"
        + (f" (avisadas no Telegram)" if boas and avisar else "") + (f"\n   Relatório: {arquivo}" if arquivo else ""))
    return {"coletadas": len(coletadas), "novas": len(novas), "candidatas": len(candidatas),
            "avaliadas": len(avaliadas), "boas": len(boas)}


def ultimas(n: int = 10) -> str:
    if not BANCO.exists():
        return "💼 O Caçador de Vagas ainda não rodou."
    con = _banco()
    linhas = con.execute("SELECT * FROM vagas WHERE nota IS NOT NULL ORDER BY vista_em DESC, nota DESC LIMIT 200").fetchall()
    con.close()
    melhores = sorted(linhas, key=lambda r: -r["nota"])[:n]
    if not melhores:
        return "💼 Nenhuma vaga avaliada ainda."
    saida = [f"💼 **As {len(melhores)} melhores vagas recentes**", ""]
    for r in melhores:
        saida.append(f"🎯 **{r['nota']}/100 — {r['titulo']}** · {r['empresa']} · {r['fonte']}")
        saida.append(f"{r['motivo']} [Abrir]({r['url']})")
    return "\n".join(saida)


def fontes() -> str:
    perfil = carregar_perfil()
    linhas = ["Fontes do Caçador de Vagas:", "  • Gupy (Brasil, remoto/híbrido)"]
    linhas.append("  • Adzuna Brasil " + ("✅" if os.getenv("ADZUNA_APP_ID") else
                                          "(desligada: ADZUNA_APP_ID/ADZUNA_APP_KEY no .env — cadastro grátis em developer.adzuna.com)"))
    if perfil.get("aceita_vagas_do_exterior", True):
        linhas.append("  • Remotive, Remote OK, We Work Remotely, Himalayas, Working Nomads, Arbeitnow (internacionais)")
    if perfil.get("ler_alertas_por_email", True):
        from . import emails
        contas = [c["rotulo"] for c in emails.contas_configuradas() if c["senha"] and c["id"] != "EB"]
        linhas.append("  • Alertas por e-mail de LinkedIn, Indeed, Glassdoor, Catho, InfoJobs, Vagas.com — contas: "
                      + (", ".join(contas) or "nenhuma configurada"))
    return "\n".join(linhas)
