"""Gerenciador de E-mails: todas as contas (Yahoo, Gmails, Hotmail, EB) lidas, classificadas
e organizadas em pastas — com um painel único em resultados/emails/.

Regras de segurança (não negociáveis):
  • NADA é apagado. O gerenciador só MOVE e-mails da Caixa de Entrada para pastas "Central/…".
  • Ler não marca como lido (BODY.PEEK): o que estava "não lido" continua "não lido".
  • Antes de mover, mostra o plano e pede o seu ✅. Tudo é registrado e pode ser desfeito
    (python main.py emails desfazer).
  • As pastas antigas (inclusive as de 10 anos atrás no Yahoo) NÃO são mexidas: só inventariadas,
    com uma SUGESTÃO de consolidação no painel.
  • A Caixa de Entrada vira a sua lista de tarefas: fica nela só o que é importante, o que pede
    resposta/ação e o que a IA não teve certeza.

Senhas: só no .env (EMAIL_<CONTA>_SENHA_APP). Use "senha de app", nunca a senha normal
(o EB, se não tiver senha de app, aceita a do webmail).

Uso:  python main.py emails paginas | testar | pastas | ler | classificar | painel | aplicar
                         | desfazer | tudo   [conta] [--limite N]
"""
from __future__ import annotations

import base64
import email
import html
import imaplib
import json
import os
import re
import sqlite3
import webbrowser
from datetime import datetime
from email import policy
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from .agentes import RAIZ

PASTA_DADOS = Path.home() / ".central-ia"
BANCO = PASTA_DADOS / "emails.db"
PASTA_SAIDA = RAIZ / "resultados" / "emails"
PASTA_RAIZ = "Central"  # todas as pastas novas ficam dentro desta, separadas das antigas
LIMITE_PADRAO = 5000    # e-mails mais recentes lidos por conta, por rodada (retoma de onde parou)
CONFIANCA_MINIMA = 0.75  # abaixo disso o e-mail fica na Caixa de Entrada para você decidir
TAMANHO_LOTE_IA = 15
BYTES_POR_EMAIL = 30000

# (id no .env, servidor IMAP padrão, rótulo)
CONTAS = [
    ("YAHOO", "imap.mail.yahoo.com", "Yahoo"),
    ("EB", "ebmail.webmail.eb.mil.br", "EB (trabalho)"),
    ("HOTMAIL", "outlook.office365.com", "Hotmail"),
    *[(f"GMAIL{n}", "imap.gmail.com", f"Gmail {n}") for n in (0, 1, 2, 3, 4, 5, 6, 7)],
]

# chave → (nome da pasta, descrição para a IA, fica na Caixa de Entrada?)
CATEGORIAS: dict[str, tuple[str, str, bool]] = {
    "importante": ("00 Importante", "escrito por uma pessoa real para você, ou assunto sério/pessoal "
                   "(família, saúde, jurídico, contratos, convites pessoais)", True),
    "trabalho": ("01 Trabalho EB", "Exército Brasileiro, quartel, serviço, documentos militares, "
                 "colegas de trabalho", False),
    "faculdade": ("02 Faculdade", "faculdades e cursos formais: FUNIB, USP, Faculdade Mar Atlântico "
                  "e outras instituições de ensino, matrícula, provas, notas, TCC", False),
    "financeiro": ("03 Financeiro e Contas", "bancos, cartões, faturas, boletos, Pix, investimentos, "
                   "impostos, contas de consumo (luz, água, telefone, internet)", False),
    "governo": ("04 Governo e Documentos", "gov.br, Receita, INSS, justiça, cartórios, detran, "
                "documentos oficiais", False),
    "compras": ("05 Compras e Entregas", "pedidos feitos, confirmações de compra, notas fiscais, "
                "rastreio e entregas (NÃO ofertas)", False),
    "seguranca": ("06 Segurança de Contas", "códigos de verificação, alertas de login, troca de senha, "
                  "avisos de segurança de contas", False),
    "cursos": ("07 Cursos e Estudos", "cursos online, plataformas de estudo, eventos, webinários, "
               "conteúdo educativo que você assinou", False),
    "notificacoes": ("08 Notificações", "avisos automáticos de redes sociais, aplicativos e serviços "
                     "(LinkedIn, Facebook, YouTube, Google, etc.)", False),
    "newsletters": ("09 Newsletters", "boletins e conteúdo editorial assinado (notícias, blogs, "
                    "Substack), sem ser oferta de venda", False),
    "promocoes": ("10 Promoções", "propaganda, ofertas, descontos, cupons, lojas, marketing", False),
    "revisar": ("", "não deu para ter certeza — você decide", True),
}

PALAVRAS_PROMO = ("oferta", "desconto", "% off", "cupom", "promoç", "promoc", "black friday", "frete grátis",
                  "frete gratis", "liquida", "aproveite", "só hoje", "imperdível", "compre", "cashback")
PALAVRAS_SEGURANCA = ("código de verificação", "codigo de verificacao", "verification code", "alerta de segurança",
                      "security alert", "novo login", "new sign-in", "redefinir senha", "reset your password",
                      "senha alterada", "acesso à sua conta")
PALAVRAS_FINANCEIRO = ("fatura", "boleto", "nubank", "itaú", "itau", "bradesco", "santander", "caixa econ",
                       "banco do brasil", "banco inter", "c6 bank", "pix", "extrato", "cartão de crédito",
                       "investimento", "tesouro direto", "imposto")
PALAVRAS_GOVERNO = ("gov.br", "receita federal", "inss", "detran", "tribunal", "cartório", "cartorio", "e-cac")
PALAVRAS_COMPRAS = ("seu pedido", "pedido n", "pedido #", "foi enviado", "entrega", "rastreio", "rastreamento",
                    "nota fiscal", "order", "shipped", "compra aprovada", "pagamento aprovado")
REMETENTE_AUTOMATICO = ("noreply", "no-reply", "nao-responda", "naoresponda", "notification", "notificacao",
                        "mailer", "news@", "newsletter", "marketing", "info@", "updates@")


# ----------------------------------------------------------------------------- contas

def contas_configuradas(filtro: str = "") -> list[dict]:
    """Contas com endereço preenchido no .env (senha pode faltar: aí avisamos)."""
    saida = []
    for cid, servidor, rotulo in CONTAS:
        endereco = os.getenv(f"EMAIL_{cid}_ENDERECO", "").strip()
        if not endereco:
            continue
        if filtro and filtro.upper() not in (cid, endereco.upper()):
            continue
        senha = os.getenv(f"EMAIL_{cid}_SENHA_APP", "")
        if cid.startswith("GMAIL") or cid == "YAHOO":
            senha = senha.replace(" ", "")  # senhas de app vêm em blocos com espaço
        saida.append({"id": cid, "rotulo": rotulo, "endereco": endereco, "senha": senha.strip(),
                      "servidor": os.getenv(f"EMAIL_{cid}_SERVIDOR", "").strip() or servidor})
    return saida


def conectar(conta: dict) -> imaplib.IMAP4_SSL:
    if not conta["senha"]:
        raise PermissionError(f"falta EMAIL_{conta['id']}_SENHA_APP no .env")
    imap = imaplib.IMAP4_SSL(conta["servidor"], 993, timeout=90)
    imap.login(conta["endereco"], conta["senha"])
    return imap


def paginas() -> None:
    """Abre de uma vez todas as páginas para trocar senha e gerar senhas de app."""
    urls = ["https://login.yahoo.com/account/security",          # Yahoo: trocar senha + gerar senha de app
            "https://ebmail.webmail.eb.mil.br/"]                   # EB: trocar a senha no webmail
    for n in (0, 1, 2, 3, 5, 6, 7):
        urls.append(f"https://myaccount.google.com/apppasswords?authuser={n}")
    urls.append("https://account.live.com/proofs/AppPassword")   # Hotmail (pode exigir OAuth)
    for u in urls:
        webbrowser.open_new_tab(u)
    print(f"Abri {len(urls)} abas. Em cada uma: troque a senha (Yahoo/EB) e gere a senha de app;\n"
          "cole cada senha no .env, na linha EMAIL_<CONTA>_SENHA_APP. Nos Gmails, se a página disser\n"
          "que a opção não está disponível, ative antes a verificação em 2 etapas daquela conta.")


# ----------------------------------------------------------------------------- IMAP: utilidades

def utf7_codificar(texto: str) -> str:
    """Nome de pasta → UTF-7 modificado do IMAP (RFC 3501), ex.: Promoções → Promo&AOcA9Q-es."""
    saida, buffer = [], []

    def descarregar():
        if buffer:
            b = base64.b64encode("".join(buffer).encode("utf-16-be")).decode().rstrip("=").replace("/", ",")
            saida.append(f"&{b}-")
            buffer.clear()

    for ch in texto:
        if 0x20 <= ord(ch) <= 0x7E:
            descarregar()
            saida.append("&-" if ch == "&" else ch)
        else:
            buffer.append(ch)
    descarregar()
    return "".join(saida)


def utf7_decodificar(texto: str) -> str:
    def trocar(m: re.Match) -> str:
        miolo = m.group(1)
        if not miolo:
            return "&"
        b = miolo.replace(",", "/")
        b += "=" * (-len(b) % 4)
        return base64.b64decode(b).decode("utf-16-be", errors="replace")
    return re.sub(r"&([A-Za-z0-9+,]*)-", trocar, texto)


_RE_LIST = re.compile(rb'\((?P<flags>[^)]*)\) (?P<delim>"(?:\\.|[^"])*"|NIL) (?P<nome>.+)$')


def analisar_list(linha) -> tuple[list[str], str, str] | None:
    """Uma linha da resposta LIST → (flags, delimitador, nome_imap)."""
    if isinstance(linha, tuple):  # nome veio como literal
        cabeca, literal = linha
        m = _RE_LIST.match(cabeca.rstrip() + b" X")
        if not m:
            return None
        nome = literal.decode("utf-8", "replace")
    else:
        m = _RE_LIST.match(linha)
        if not m:
            return None
        nome = m.group("nome").decode("utf-8", "replace").strip()
        if nome.startswith('"') and nome.endswith('"'):
            nome = nome[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    flags = m.group("flags").decode().split()
    delim = m.group("delim").decode()
    delim = "" if delim == "NIL" else delim.strip('"').replace("\\\\", "\\")
    return flags, delim, nome


def _citar(nome_imap: str) -> str:
    return '"' + nome_imap.replace("\\", "\\\\").replace('"', '\\"') + '"'


def listar_pastas(imap) -> tuple[str, list[dict]]:
    tipo, dados = imap.list()
    if tipo != "OK":
        raise RuntimeError("o servidor não listou as pastas")
    pastas, delim = [], "/"
    for linha in dados:
        if linha is None:
            continue
        r = analisar_list(linha)
        if not r:
            continue
        flags, d, nome = r
        delim = d or delim
        pastas.append({"imap": nome, "nome": utf7_decodificar(nome), "flags": flags,
                       "selecionavel": "\\Noselect" not in flags and "\\NonExistent" not in flags})
    return delim, pastas


def contar_pasta(imap, nome_imap: str) -> tuple[int, int]:
    tipo, dados = imap.status(_citar(nome_imap), "(MESSAGES UNSEEN)")
    if tipo != "OK" or not dados or not dados[0]:
        return 0, 0
    texto = dados[0].decode("utf-8", "replace")
    msgs = re.search(r"MESSAGES (\d+)", texto)
    nao = re.search(r"UNSEEN (\d+)", texto)
    return int(msgs.group(1)) if msgs else 0, int(nao.group(1)) if nao else 0


# ----------------------------------------------------------------------------- banco

def _banco() -> sqlite3.Connection:
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BANCO)
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE IF NOT EXISTS emails (
            conta TEXT, pasta TEXT, uidvalidity TEXT, uid INTEGER,
            message_id TEXT, de_nome TEXT, de_email TEXT, para TEXT, assunto TEXT, data TEXT,
            lido INTEGER, sinais TEXT, trecho TEXT,
            categoria TEXT, confianca REAL, acao INTEGER, resumo TEXT, classificado_por TEXT,
            movido_para TEXT, movido_em TEXT,
            PRIMARY KEY (conta, pasta, uidvalidity, uid));
        CREATE TABLE IF NOT EXISTS pastas (
            conta TEXT, nome TEXT, imap TEXT, mensagens INTEGER, nao_lidas INTEGER, visto_em TEXT,
            PRIMARY KEY (conta, imap));
        CREATE TABLE IF NOT EXISTS sugestoes (conta TEXT PRIMARY KEY, texto TEXT, feito_em TEXT);
    """)
    return con


# ----------------------------------------------------------------------------- leitura

def _texto_do_email(msg) -> str:
    plano, html_txt = "", ""
    for parte in msg.walk():
        if parte.is_multipart() or parte.get_content_disposition() == "attachment":
            continue
        tipo = parte.get_content_type()
        if tipo not in ("text/plain", "text/html"):
            continue
        try:
            conteudo = parte.get_content()
        except Exception:  # noqa: BLE001 — charset estranho ou corte no meio
            bruto = parte.get_payload(decode=True) or b""
            conteudo = bruto.decode(parte.get_content_charset() or "utf-8", "replace")
        if tipo == "text/plain" and not plano:
            plano = conteudo
        elif tipo == "text/html" and not html_txt:
            html_txt = conteudo
    if not plano and html_txt:
        semtags = re.sub(r"(?is)<(script|style).*?</\1>", " ", html_txt)
        plano = html.unescape(re.sub(r"<[^>]+>", " ", semtags))
    return re.sub(r"\s+", " ", plano).strip()


def _cabecalho(msg, nome: str) -> str:
    try:
        valor = msg.get(nome)
        return str(valor) if valor is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def analisar_mensagem(bruto: bytes) -> dict:
    """Bytes (podem estar cortados) → campos que interessam para classificar."""
    msg = email.message_from_bytes(bruto, policy=policy.default)
    nome, endereco = parseaddr(_cabecalho(msg, "From"))
    data = ""
    try:
        data = parsedate_to_datetime(_cabecalho(msg, "Date")).isoformat()
    except Exception:  # noqa: BLE001
        pass
    sinais = []
    if _cabecalho(msg, "List-Unsubscribe"):
        sinais.append("descadastrar")
    if _cabecalho(msg, "List-Id"):
        sinais.append("lista")
    if _cabecalho(msg, "Precedence").lower() in ("bulk", "list", "junk"):
        sinais.append("envio-em-massa")
    if _cabecalho(msg, "Auto-Submitted").lower() not in ("", "no"):
        sinais.append("automatico")
    if any(p in endereco.lower() for p in REMETENTE_AUTOMATICO):
        sinais.append("remetente-automatico")
    return {"message_id": _cabecalho(msg, "Message-ID").strip(), "de_nome": nome, "de_email": endereco.lower(),
            "para": _cabecalho(msg, "To")[:300], "assunto": _cabecalho(msg, "Subject")[:300], "data": data,
            "sinais": ",".join(sinais), "trecho": _texto_do_email(msg)[:1500]}


def _respostas_fetch(dados) -> list[tuple[bytes, bytes]]:
    """Resposta do UID FETCH → [(metadados, corpo)], juntando FLAGS que vêm depois do literal."""
    saida: list[list] = []
    for item in dados:
        if isinstance(item, tuple):
            saida.append([item[0], item[1]])
        elif isinstance(item, bytes) and saida:
            saida[-1][0] += item
    return [(m, c) for m, c in saida]


def ler(conta: dict, pasta: str = "INBOX", limite: int = LIMITE_PADRAO, log=print) -> int:
    """Lê (sem marcar como lido) os e-mails mais recentes da pasta e guarda no banco."""
    con = _banco()
    imap = conectar(conta)
    try:
        tipo, _ = imap.select(_citar(utf7_codificar(pasta)), readonly=True)
        if tipo != "OK":
            raise RuntimeError(f"não abri a pasta {pasta}")
        uidvalidity = (imap.response("UIDVALIDITY")[1] or [b"0"])[0].decode()
        tipo, dados = imap.uid("SEARCH", None, "ALL")
        uids = [int(u) for u in (dados[0] or b"").split()]
        uids = sorted(uids)[-limite:] if limite else sorted(uids)
        ja = {r[0] for r in con.execute("SELECT uid FROM emails WHERE conta=? AND pasta=? AND uidvalidity=?",
                                        (conta["id"], pasta, uidvalidity))}
        novos = [u for u in uids if u not in ja]
        log(f"📥 {conta['rotulo']}: {len(uids)} e-mails em {pasta}; {len(novos)} ainda não lidos pela Central")
        for i in range(0, len(novos), 50):
            lote = novos[i:i + 50]
            tipo, dados = imap.uid("FETCH", ",".join(map(str, lote)),
                                   f"(UID FLAGS BODY.PEEK[]<0.{BYTES_POR_EMAIL}>)")
            if tipo != "OK":
                log(f"   ⚠️ lote {i} falhou; sigo para o próximo")
                continue
            for meta, corpo in _respostas_fetch(dados):
                m_uid = re.search(rb"UID (\d+)", meta)
                if not m_uid:
                    continue
                m_flags = re.search(rb"FLAGS \(([^)]*)\)", meta)
                lido = int(bool(m_flags and b"\\Seen" in m_flags.group(1)))
                try:
                    campos = analisar_mensagem(corpo)
                except Exception as erro:  # noqa: BLE001
                    campos = {"message_id": "", "de_nome": "", "de_email": "", "para": "", "data": "",
                              "assunto": f"(não consegui ler: {erro})", "sinais": "", "trecho": ""}
                con.execute("""INSERT OR IGNORE INTO emails (conta, pasta, uidvalidity, uid, message_id, de_nome,
                               de_email, para, assunto, data, lido, sinais, trecho)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (conta["id"], pasta, uidvalidity, int(m_uid.group(1)), campos["message_id"],
                             campos["de_nome"], campos["de_email"], campos["para"], campos["assunto"],
                             campos["data"], lido, campos["sinais"], campos["trecho"]))
            con.commit()
            log(f"   … {min(i + 50, len(novos))}/{len(novos)}")
        return len(novos)
    finally:
        _sair(imap)


def _sair(imap) -> None:
    try:
        imap.logout()
    except Exception:  # noqa: BLE001
        pass


def inventariar_pastas(conta: dict, log=print) -> list[dict]:
    con = _banco()
    imap = conectar(conta)
    try:
        _, pastas = listar_pastas(imap)
        agora = datetime.now().isoformat(timespec="seconds")
        con.execute("DELETE FROM pastas WHERE conta=?", (conta["id"],))
        for p in pastas:
            if not p["selecionavel"]:
                continue
            try:
                p["mensagens"], p["nao_lidas"] = contar_pasta(imap, p["imap"])
            except Exception:  # noqa: BLE001
                p["mensagens"], p["nao_lidas"] = -1, -1
            con.execute("INSERT OR REPLACE INTO pastas VALUES (?,?,?,?,?,?)",
                        (conta["id"], p["nome"], p["imap"], p["mensagens"], p["nao_lidas"], agora))
        con.commit()
        total = sum(max(p.get("mensagens", 0), 0) for p in pastas)
        log(f"🗂 {conta['rotulo']}: {len(pastas)} pastas, {total} e-mails no total")
        return pastas
    finally:
        _sair(imap)


# ----------------------------------------------------------------------------- classificação

def _palavras_faculdade() -> list[str]:
    return [p.strip().lower() for p in os.getenv("EMAIL_FACULDADE", "funib,usp.br,mar atlântico,"
                                                 "mar atlantico,maratlantico").split(",") if p.strip()]


def classificar_por_regras(e: dict, conta_id: str = "") -> tuple[str, float, str]:
    """Classificação de reserva (sem IA). Retorna (categoria, confiança, motivo)."""
    remetente = f"{e.get('de_nome', '')} {e.get('de_email', '')}".lower()
    texto = f"{remetente} {e.get('assunto', '')}".lower()
    sinais = e.get("sinais", "")
    massa = any(s in sinais for s in ("descadastrar", "lista", "envio-em-massa"))
    if any(p in texto for p in _palavras_faculdade()):
        return "faculdade", 0.85, "instituição de ensino no remetente/assunto"
    if e.get("de_email", "").endswith("eb.mil.br") and conta_id != "EB":
        return "trabalho", 0.85, "remetente do EB"
    if any(p in texto for p in PALAVRAS_SEGURANCA):
        return "seguranca", 0.8, "aviso de segurança de conta"
    if any(p in texto for p in PALAVRAS_GOVERNO):
        return "governo", 0.75, "órgão público"
    if massa and any(p in texto for p in PALAVRAS_PROMO):
        return "promocoes", 0.85, "envio em massa com linguagem de oferta"
    if any(p in texto for p in PALAVRAS_FINANCEIRO):
        return "financeiro", 0.7, "banco/fatura"
    if any(p in texto for p in PALAVRAS_COMPRAS):
        return "compras", 0.7, "pedido/entrega"
    if massa:
        return "newsletters", 0.6, "envio em massa"
    if "remetente-automatico" in sinais or "automatico" in sinais:
        return "notificacoes", 0.6, "remetente automático"
    return "revisar", 0.3, "sem sinais claros"


SISTEMA_IA = """Você é o secretário de e-mails de Reynolds (militar do Exército Brasileiro, estudante de
várias faculdades — FUNIB, USP, Faculdade Mar Atlântico e outras — e produtor de conteúdo).
Classifique CADA e-mail com rigor, lendo remetente, assunto e trecho. Categorias permitidas:
{categorias}
Regras:
- "importante" é para mensagens escritas por pessoas reais para ele, ou assuntos sérios/pessoais.
- E-mail de faculdade/EB/banco/governo continua nessas categorias mesmo que peça ação (use acao=true).
- acao=true quando ele precisa responder, pagar, assinar, enviar algo, cumprir prazo ou comparecer.
- Na dúvida real, use "revisar" com confiança baixa. Nunca invente.
- confianca entre 0 e 1. resumo: 1 frase curta em português dizendo o que é e o que pede.
Responda SOMENTE com JSON: [{{"id": <id>, "categoria": "...", "confianca": 0.9, "acao": false, "resumo": "..."}}]"""


def _modelos() -> list[str]:
    padrao = "gemini:gemini-3.5-flash,groq:openai/gpt-oss-120b,gemini:gemini-flash-lite-latest,ollama:qwen3:4b"
    return [m.strip() for m in os.getenv("EMAIL_IA_MODELOS", padrao).split(",") if m.strip()]


def _classificar_lote_ia(lote: list[dict], conta: dict, modelos: list[str], log=print) -> dict[int, dict]:
    from .orquestrador import _extrair_json
    from .provedores import ErroProvedor, chamar_com_reserva
    categorias = "\n".join(f"- {k}: {v[1]}" for k, v in CATEGORIAS.items())
    linhas = []
    for i, e in enumerate(lote):
        linhas.append(json.dumps({"id": i, "conta": conta["rotulo"], "de": f"{e['de_nome']} <{e['de_email']}>",
                                  "assunto": e["assunto"], "sinais": e["sinais"],
                                  "trecho": (e["trecho"] or "")[:700]}, ensure_ascii=False))
    try:
        modelo, resposta = chamar_com_reserva(modelos, SISTEMA_IA.format(categorias=categorias),
                                              "E-mails (um JSON por linha):\n" + "\n".join(linhas), log=log)
        itens = _extrair_json(resposta)
    except (ErroProvedor, ValueError) as erro:
        log(f"   ⚠️ IA indisponível neste lote ({str(erro)[:80]}); uso as regras")
        return {}
    saida = {}
    for item in itens if isinstance(itens, list) else []:
        try:
            idx = int(item["id"])
            cat = str(item.get("categoria", "revisar")).strip().lower()
            if cat not in CATEGORIAS or not 0 <= idx < len(lote):
                continue
            saida[idx] = {"categoria": cat, "confianca": max(0.0, min(1.0, float(item.get("confianca", 0.5)))),
                          "acao": int(bool(item.get("acao"))), "resumo": str(item.get("resumo", ""))[:300],
                          "por": modelo}
        except (KeyError, TypeError, ValueError):
            continue
    return saida


def classificar(conta: dict, usar_ia: bool = True, log=print) -> int:
    """Classifica tudo o que foi lido e ainda não tem categoria."""
    con = _banco()
    pendentes = [dict(r) for r in con.execute(
        "SELECT rowid AS rid, * FROM emails WHERE conta=? AND categoria IS NULL ORDER BY data DESC",
        (conta["id"],))]
    if not pendentes:
        return 0
    log(f"🧠 {conta['rotulo']}: classificando {len(pendentes)} e-mails")
    modelos = _modelos()
    for i in range(0, len(pendentes), TAMANHO_LOTE_IA):
        lote = pendentes[i:i + TAMANHO_LOTE_IA]
        da_ia = _classificar_lote_ia(lote, conta, modelos, log) if usar_ia else {}
        for j, e in enumerate(lote):
            if j in da_ia:
                r = da_ia[j]
            else:
                cat, conf, motivo = classificar_por_regras(e, conta["id"])
                r = {"categoria": cat, "confianca": conf, "acao": 0, "resumo": motivo, "por": "regras"}
            con.execute("UPDATE emails SET categoria=?, confianca=?, acao=?, resumo=?, classificado_por=? "
                        "WHERE rowid=?", (r["categoria"], r["confianca"], r["acao"], r["resumo"], r["por"], e["rid"]))
        con.commit()
        log(f"   … {min(i + TAMANHO_LOTE_IA, len(pendentes))}/{len(pendentes)}")
    return len(pendentes)


def destino(e: dict) -> str:
    """Pasta para onde o e-mail vai, ou "" se fica na Caixa de Entrada."""
    if e.get("movido_para") or e.get("pasta") != "INBOX":
        return ""
    cat = e.get("categoria") or "revisar"
    nome, _, fica = CATEGORIAS.get(cat, CATEGORIAS["revisar"])
    if fica or e.get("acao") or (e.get("confianca") or 0) < CONFIANCA_MINIMA:
        return ""
    return nome


def sugerir_consolidacao(conta: dict, log=print) -> str:
    """Pede à IA uma proposta para reduzir as pastas antigas (só NOMES e contagens vão para a IA)."""
    from .provedores import ErroProvedor, chamar_com_reserva
    con = _banco()
    pastas = [dict(r) for r in con.execute("SELECT nome, mensagens FROM pastas WHERE conta=? ORDER BY nome",
                                           (conta["id"],))]
    if len(pastas) < 8:
        return ""
    alvo = "\n".join(f"- Central/{v[0]}" for v in CATEGORIAS.values() if v[0])
    lista = "\n".join(f"{p['nome']} ({p['mensagens']})" for p in pastas)
    pedido = (f"Conta {conta['rotulo']}. Estas são as pastas existentes (nome e nº de e-mails):\n{lista}\n\n"
              f"Estrutura nova proposta:\n{alvo}\n\nProponha como consolidar as pastas antigas: para cada pasta, "
              "diga se MANTÉM (arquivo histórico com valor próprio), JUNTA em qual pasta nova, ou ARQUIVA "
              "(vai para Central/Arquivo Antigo). Pastas do sistema (Caixa de Entrada, Enviados, Rascunhos, "
              "Lixeira, Spam, [Gmail]/…) sempre MANTÉM. Seja criterioso e conservador. Responda em tabela "
              "markdown: Pasta | Decisão | Destino | Por quê. No fim, 3 linhas de resumo.")
    try:
        _, texto = chamar_com_reserva(_modelos(), "Você organiza caixas de e-mail com cuidado.", pedido, log=log)
    except ErroProvedor as erro:
        log(f"   ⚠️ sem sugestão de pastas agora ({str(erro)[:80]})")
        return ""
    con.execute("INSERT OR REPLACE INTO sugestoes VALUES (?,?,?)",
                (conta["id"], texto, datetime.now().isoformat(timespec="seconds")))
    con.commit()
    return texto


# ----------------------------------------------------------------------------- mover / desfazer

def plano(conta_id: str = "") -> list[dict]:
    con = _banco()
    sql = "SELECT * FROM emails WHERE categoria IS NOT NULL AND movido_para IS NULL AND pasta='INBOX'"
    linhas = [dict(r) for r in con.execute(sql + (" AND conta=?" if conta_id else ""),
                                           ((conta_id,) if conta_id else ()))]
    for e in linhas:
        e["destino"] = destino(e)
    return [e for e in linhas if e["destino"]]


def _garantir_pasta(imap, delim: str, existentes: set[str], nome: str) -> str:
    caminho = f"{PASTA_RAIZ}{delim}{nome}"
    imap_nome = utf7_codificar(caminho)
    if imap_nome not in existentes:
        if utf7_codificar(PASTA_RAIZ) not in existentes:
            imap.create(_citar(utf7_codificar(PASTA_RAIZ)))
            existentes.add(utf7_codificar(PASTA_RAIZ))
        tipo, dados = imap.create(_citar(imap_nome))
        if tipo != "OK":
            raise RuntimeError(f"não consegui criar a pasta {caminho}: {dados}")
        imap.subscribe(_citar(imap_nome))
        existentes.add(imap_nome)
    return imap_nome


def aplicar(conta: dict, log=print) -> int:
    """MOVE (nunca apaga) os e-mails do plano. Exige o comando MOVE do servidor."""
    itens = plano(conta["id"])
    if not itens:
        return 0
    con = _banco()
    imap = conectar(conta)
    movidos = 0
    try:
        tipo, dados = imap.capability()
        capacidades = (dados[0] or b"").decode().upper().split() if tipo == "OK" else []
        if "MOVE" not in capacidades:
            log(f"⚠️ {conta['rotulo']}: o servidor não tem o comando MOVE; não movo nada (evito cópias duplicadas)")
            return 0
        delim, pastas = listar_pastas(imap)
        existentes = {p["imap"] for p in pastas}
        imap.select("INBOX")
        uidvalidity = (imap.response("UIDVALIDITY")[1] or [b"0"])[0].decode()
        por_destino: dict[str, list[dict]] = {}
        for e in itens:
            if e["uidvalidity"] != uidvalidity:
                continue  # a caixa mudou desde a leitura: releia antes
            por_destino.setdefault(e["destino"], []).append(e)
        for nome, lista in por_destino.items():
            alvo = _garantir_pasta(imap, delim, existentes, nome)
            for i in range(0, len(lista), 100):
                lote = lista[i:i + 100]
                tipo, dados = imap.uid("MOVE", ",".join(str(e["uid"]) for e in lote), _citar(alvo))
                if tipo != "OK":
                    log(f"   ⚠️ falhou mover para {nome}: {dados}")
                    continue
                agora = datetime.now().isoformat(timespec="seconds")
                con.executemany("UPDATE emails SET movido_para=?, movido_em=? WHERE conta=? AND pasta='INBOX' "
                                "AND uidvalidity=? AND uid=?",
                                [(f"{PASTA_RAIZ}{delim}{nome}", agora, conta["id"], e["uidvalidity"], e["uid"])
                                 for e in lote])
                con.commit()
                movidos += len(lote)
            log(f"   📁 {nome}: {len(lista)}")
        return movidos
    finally:
        _sair(imap)


def desfazer(conta: dict, log=print) -> int:
    """Devolve à Caixa de Entrada tudo o que a Central moveu (procura pelo Message-ID)."""
    con = _banco()
    itens = [dict(r) for r in con.execute("SELECT * FROM emails WHERE conta=? AND movido_para IS NOT NULL",
                                          (conta["id"],))]
    if not itens:
        return 0
    imap = conectar(conta)
    voltaram = 0
    try:
        por_pasta: dict[str, list[dict]] = {}
        for e in itens:
            por_pasta.setdefault(e["movido_para"], []).append(e)
        for pasta, lista in por_pasta.items():
            if imap.select(_citar(utf7_codificar(pasta)))[0] != "OK":
                continue
            for e in lista:
                if not e["message_id"]:
                    continue
                tipo, dados = imap.uid("SEARCH", None, "HEADER", "Message-ID", _citar(e["message_id"]))
                uids = (dados[0] or b"").split() if tipo == "OK" else []
                if uids and imap.uid("MOVE", b",".join(uids).decode(), "INBOX")[0] == "OK":
                    con.execute("UPDATE emails SET movido_para=NULL, movido_em=NULL WHERE rowid IN "
                                "(SELECT rowid FROM emails WHERE conta=? AND message_id=?)",
                                (conta["id"], e["message_id"]))
                    voltaram += 1
            con.commit()
        log(f"↩️ {conta['rotulo']}: {voltaram} e-mails de volta à Caixa de Entrada")
        return voltaram
    finally:
        _sair(imap)


# ----------------------------------------------------------------------------- painel

def _h(t) -> str:
    return html.escape(str(t or ""))


def painel(abrir: bool = True) -> Path:
    con = _banco()
    rotulos = {cid: rotulo for cid, _, rotulo in CONTAS}
    emails = [dict(r) for r in con.execute("SELECT * FROM emails WHERE categoria IS NOT NULL ORDER BY data DESC")]
    for e in emails:
        e["destino"] = destino(e)
    contas = sorted({e["conta"] for e in emails} | {r[0] for r in con.execute("SELECT conta FROM pastas")})

    def tabela(lista: list[dict], titulo: str, vazio: str) -> str:
        if not lista:
            return f"<h2>{titulo}</h2><p class=vazio>{vazio}</p>"
        linhas = "".join(
            f"<tr class='{'' if e['lido'] else 'naolido'}'><td>{_h(rotulos.get(e['conta'], e['conta']))}</td>"
            f"<td>{_h((e['data'] or '')[:10])}</td><td>{_h(e['de_nome'] or e['de_email'])}"
            f"<br><small>{_h(e['de_email'])}</small></td><td><b>{_h(e['assunto'])}</b><br>"
            f"<small>{_h(e['resumo'])}</small></td><td>{_h(e['categoria'])}</td></tr>" for e in lista[:400])
        mais = f"<p class=vazio>… e mais {len(lista) - 400}</p>" if len(lista) > 400 else ""
        return (f"<h2>{titulo} <span class=n>{len(lista)}</span></h2><table><tr><th>Conta</th><th>Data</th>"
                f"<th>De</th><th>Assunto / resumo</th><th>Categoria</th></tr>{linhas}</table>{mais}")

    cartoes = []
    for c in contas:
        dele = [e for e in emails if e["conta"] == c]
        cont: dict[str, int] = {}
        for e in dele:
            cont[e["categoria"]] = cont.get(e["categoria"], 0) + 1
        itens = "".join(f"<li>{_h(CATEGORIAS[k][0] or 'Revisar (fica na entrada)')}: <b>{v}</b></li>"
                        for k, v in sorted(cont.items(), key=lambda kv: list(CATEGORIAS).index(kv[0])
                                           if kv[0] in CATEGORIAS else 99))
        mover = sum(1 for e in dele if e["destino"])
        movidos = sum(1 for e in dele if e["movido_para"])
        cartoes.append(f"<div class=cartao><h3>{_h(rotulos.get(c, c))}</h3><p>{len(dele)} analisados · "
                       f"<b>{mover}</b> a mover · {movidos} já movidos</p><ul>{itens}</ul></div>")

    acao = [e for e in emails if e["acao"] and not e["movido_para"]]
    importantes = [e for e in emails if e["categoria"] == "importante" and not e["acao"]]
    revisar = [e for e in emails if e["pasta"] == "INBOX" and not e["destino"] and not e["movido_para"]
               and e["categoria"] not in ("importante",) and not e["acao"]]

    pastas_html = []
    for c in contas:
        ps = [dict(r) for r in con.execute("SELECT * FROM pastas WHERE conta=? ORDER BY nome", (c,))]
        if not ps:
            continue
        linhas = "".join(f"<tr><td>{_h(p['nome'])}</td><td>{p['mensagens']}</td><td>{p['nao_lidas']}</td></tr>"
                         for p in ps)
        sug = con.execute("SELECT texto FROM sugestoes WHERE conta=?", (c,)).fetchone()
        sug_html = f"<h4>Sugestão de consolidação (nada foi mexido)</h4><pre>{_h(sug[0])}</pre>" if sug else ""
        pastas_html.append(f"<details><summary>{_h(rotulos.get(c, c))}: {len(ps)} pastas</summary>"
                           f"<table><tr><th>Pasta</th><th>E-mails</th><th>Não lidos</th></tr>{linhas}</table>"
                           f"{sug_html}</details>")

    agora = datetime.now()
    pagina = f"""<!doctype html><html lang=pt-BR><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Painel de E-mails</title><style>
:root{{--fundo:#f6f5f2;--card:#fff;--texto:#1d1d1b;--suave:#6b6b66;--linha:#e3e1db;--destaque:#b4531f}}
@media (prefers-color-scheme:dark){{:root{{--fundo:#161615;--card:#20201e;--texto:#ecebe7;--suave:#a3a29c;
--linha:#34332f;--destaque:#f08a4b}}}}
body{{background:var(--fundo);color:var(--texto);font:15px/1.45 system-ui,sans-serif;margin:0;padding:24px 16px}}
main{{max-width:1150px;margin:auto}}h1{{margin:0 0 4px}}.sub{{color:var(--suave);margin:0 0 20px}}
.cartoes{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}}
.cartao,details{{background:var(--card);border:1px solid var(--linha);border-radius:10px;padding:12px 14px}}
.cartao h3{{margin:0}}.cartao ul{{margin:6px 0 0;padding-left:18px;color:var(--suave)}}
h2{{margin:30px 0 10px}}.n{{background:var(--destaque);color:#fff;border-radius:99px;padding:1px 10px;font-size:14px}}
table{{width:100%;border-collapse:collapse;background:var(--card);font-size:14px}}
td,th{{border-bottom:1px solid var(--linha);padding:7px;text-align:left;vertical-align:top}}
small{{color:var(--suave)}}tr.naolido td:first-child{{border-left:3px solid var(--destaque)}}
.vazio{{color:var(--suave)}}details{{margin:8px 0}}pre{{white-space:pre-wrap}}
.overflow{{overflow-x:auto}}</style></head><body><main>
<h1>📬 Painel de E-mails</h1><p class=sub>Atualizado em {agora:%d/%m/%Y %H:%M}. Nada foi apagado.
Barra laranja = ainda não lido. Para mover o que está indicado: <code>python main.py emails aplicar</code></p>
<div class=cartoes>{''.join(cartoes) or '<p>Nenhuma conta lida ainda.</p>'}</div>
<div class=overflow>{tabela(acao, "✋ Pedem resposta ou ação (ficam na Caixa de Entrada)", "Nada pendente.")}</div>
<div class=overflow>{tabela(importantes, "⭐ Importantes", "Nenhum.")}</div>
<div class=overflow>{tabela(revisar, "🤔 Para você decidir (a IA não teve certeza)", "Nenhum.")}</div>
<h2>🗂 Pastas que já existem</h2>{''.join(pastas_html) or '<p class=vazio>Rode: python main.py emails pastas</p>'}
</main></body></html>"""
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    arquivo = PASTA_SAIDA / f"painel-{agora:%Y-%m-%d}.html"
    arquivo.write_text(pagina, encoding="utf-8")
    if abrir:
        webbrowser.open(arquivo.as_uri())
    return arquivo


# ----------------------------------------------------------------------------- linha de comando

def _por_conta(contas: list[dict], funcao, log=print) -> None:
    for c in contas:
        try:
            funcao(c)
        except PermissionError as erro:
            log(f"⬜ {c['rotulo']}: {erro}")
        except imaplib.IMAP4.error as erro:
            log(f"❌ {c['rotulo']}: o servidor recusou o login ({str(erro)[:120]}). "
                "Confira a senha de app no .env.")
        except Exception as erro:  # noqa: BLE001
            log(f"❌ {c['rotulo']}: {type(erro).__name__}: {str(erro)[:160]}")


def resumo_plano(itens: list[dict]) -> str:
    rotulos = {cid: rotulo for cid, _, rotulo in CONTAS}
    cont: dict[tuple[str, str], int] = {}
    for e in itens:
        cont[(e["conta"], e["destino"])] = cont.get((e["conta"], e["destino"]), 0) + 1
    return "\n".join(f"  {rotulos.get(c, c):<14} → {PASTA_RAIZ}/{d}: {n}" for (c, d), n in sorted(cont.items()))


def cli(acao: str, conta: str = "", limite: int | None = None, pasta: str = "INBOX", sim: bool = False,
        log=print) -> int:
    if acao == "paginas":
        paginas()
        return 0
    contas = contas_configuradas(conta)
    if not contas:
        log("Nenhuma conta com EMAIL_<CONTA>_ENDERECO preenchido no .env.")
        return 1
    if acao == "testar":
        def testar(c):
            _sair(conectar(c))
            log(f"✅ {c['rotulo']} ({c['endereco']}): conectado")
        _por_conta(contas, testar, log)
    elif acao == "pastas":
        def pastas_e_sugestao(c):
            inventariar_pastas(c, log)
            sugerir_consolidacao(c, log)
        _por_conta(contas, pastas_e_sugestao, log)
    elif acao == "ler":
        _por_conta(contas, lambda c: ler(c, pasta, limite or LIMITE_PADRAO, log), log)
    elif acao == "classificar":
        _por_conta(contas, lambda c: classificar(c, log=log), log)
    elif acao == "tudo":
        def tudo(c):
            inventariar_pastas(c, log)
            ler(c, pasta, limite or LIMITE_PADRAO, log)
            classificar(c, log=log)
            sugerir_consolidacao(c, log)
        _por_conta(contas, tudo, log)
    elif acao == "aplicar":
        itens = [e for c in contas for e in plano(c["id"])]
        if not itens:
            log("Nada a mover. (Rode antes: python main.py emails tudo)")
            return 0
        log(f"Plano: mover {len(itens)} e-mails da Caixa de Entrada (nada é apagado):\n{resumo_plano(itens)}")
        if not sim and input("\nConfirmar? [s/N] ").strip().lower() not in ("s", "sim"):
            log("Cancelado. Nada foi movido.")
            return 0
        _por_conta(contas, lambda c: log(f"✅ {c['rotulo']}: {aplicar(c, log)} movidos"), log)
    elif acao == "desfazer":
        if not sim and input("Devolver à Caixa de Entrada tudo o que a Central moveu? [s/N] ").strip().lower() \
                not in ("s", "sim"):
            return 0
        _por_conta(contas, lambda c: desfazer(c, log), log)
    if acao in ("tudo", "classificar", "pastas", "painel", "aplicar"):
        log(f"📊 Painel: {painel()}")
    return 0
