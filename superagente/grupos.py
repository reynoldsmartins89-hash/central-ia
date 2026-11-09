"""Agentes de GRUPOS: leem a conversa inteira de um grupo do WhatsApp ou do Telegram e entregam um
relatório completo (assuntos, decisões, datas, links, pendências). Depois, acompanham semanalmente.

  • WhatsApp: no celular, abra o grupo → ⋮ → Mais → Exportar conversa → SEM MÍDIA → envie o
    arquivo para a Central no Telegram. O celular exporta o histórico inteiro que ele guarda.
    (Ler o WhatsApp "por fora" com programas não oficiais pode BANIR o seu número — não usamos.)
  • Telegram: pela SUA conta (login único), a Central lê o histórico completo de qualquer grupo em
    que você está — só LEITURA. Alternativa sem login: Telegram Desktop → ⋮ → Exportar histórico (JSON).

Tudo fica guardado em ~/.central-ia/grupos.db; cada relatório vai para resultados/ e para o seu Telegram.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

PASTA = Path.home() / ".central-ia"
BANCO = PASTA / "grupos.db"
SESSAO_TELEGRAM = PASTA / "telegram_conta"          # login da SUA conta (fica só no servidor)
TAMANHO_TRECHO = 50_000                              # caracteres por parte do histórico

_DATA = r"(\d{1,2})/(\d{1,2})/(\d{2,4})"
_HORA = r"(\d{1,2}):(\d{2})(?::\d{2})?(?:\s?[ap]\.?\s?m\.?)?"
ANDROID = re.compile(rf"^{_DATA},? {_HORA} - (.+?): (.*)$")
IOS = re.compile(rf"^\[{_DATA},? {_HORA}\] (.+?): (.*)$")
SISTEMA_ANDROID = re.compile(rf"^{_DATA},? {_HORA} - ")
SISTEMA_IOS = re.compile(rf"^\[{_DATA},? {_HORA}\] ")
IGNORAR = ("<mídia oculta>", "<media omitted>", "mensagem apagada", "this message was deleted",
           "você apagou esta mensagem", "imagem ocultada", "figurinha omitida", "sticker omitted")


# ------------------------------------------------------------------ banco
def _banco() -> sqlite3.Connection:
    PASTA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BANCO, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS grupos (id INTEGER PRIMARY KEY, nome TEXT UNIQUE, origem TEXT,
            ultimo_relatorio REAL DEFAULT 0, ultimo_id INTEGER DEFAULT 0, acompanhar INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS mensagens (grupo INTEGER, quando REAL, autor TEXT, texto TEXT,
            marca TEXT UNIQUE);
        CREATE INDEX IF NOT EXISTS ix_msg ON mensagens (grupo, quando);""")
    return con


def _grupo(con: sqlite3.Connection, nome: str, origem: str) -> int:
    con.execute("INSERT OR IGNORE INTO grupos (nome, origem) VALUES (?, ?)", (nome, origem))
    return con.execute("SELECT id FROM grupos WHERE nome = ?", (nome,)).fetchone()[0]


def _normalizar(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))


def achar_grupo(nome: str) -> tuple[int, str, str] | None:
    """Acha um grupo já guardado pelo nome (aceita parte do nome, sem acento)."""
    con = _banco()
    alvo = _normalizar(nome).strip()
    linhas = con.execute("SELECT id, nome, origem FROM grupos").fetchall()
    con.close()
    exatos = [l for l in linhas if _normalizar(l[1]) == alvo]
    parciais = [l for l in linhas if alvo and alvo in _normalizar(l[1])]
    return (exatos or parciais or [None])[0]


def guardar(nome: str, origem: str, mensagens: list[tuple[float, str, str]], ultimo_id: int = 0) -> tuple[int, int]:
    """Guarda as mensagens sem repetir (pode importar a mesma conversa várias vezes). Devolve (novas, total)."""
    con = _banco()
    gid = _grupo(con, nome, origem)
    antes = con.execute("SELECT COUNT(*) FROM mensagens WHERE grupo = ?", (gid,)).fetchone()[0]
    vistos: dict[str, int] = {}
    linhas = []
    for quando, autor, texto in mensagens:
        base = f"{gid}|{int(quando)}|{autor}|{texto}"
        vistos[base] = vistos.get(base, 0) + 1   # mesma mensagem repetida no mesmo minuto continua valendo
        linhas.append((gid, quando, autor, texto, hashlib.sha1(f"{base}|{vistos[base]}".encode()).hexdigest()))
    con.executemany("INSERT OR IGNORE INTO mensagens VALUES (?, ?, ?, ?, ?)", linhas)
    if ultimo_id:
        con.execute("UPDATE grupos SET ultimo_id = MAX(ultimo_id, ?) WHERE id = ?", (ultimo_id, gid))
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM mensagens WHERE grupo = ?", (gid,)).fetchone()[0]
    con.close()
    return total - antes, total


# ------------------------------------------------------------------ WhatsApp (arquivo exportado pelo celular)
def _quando(dia: str, mes: str, ano: str, hora: str, minuto: str) -> float:
    ano_i = int(ano) + (2000 if len(ano) == 2 else 0)
    return datetime(ano_i, int(mes), int(dia), int(hora) % 24, int(minuto)).timestamp()


def ler_whatsapp(texto: str) -> list[tuple[float, str, str]]:
    """Lê o .txt do 'Exportar conversa' (Android ou iPhone, em português ou inglês)."""
    mensagens: list[list] = []
    for linha in texto.replace("‎", "").replace(" ", " ").replace("\r", "").split("\n"):
        m = ANDROID.match(linha) or IOS.match(linha)
        if m:
            d, mes, a, h, mi, autor, corpo = m.groups()
            try:
                mensagens.append([_quando(d, mes, a, h, mi), autor.strip(), corpo.strip()])
            except ValueError:
                continue
        elif SISTEMA_ANDROID.match(linha) or SISTEMA_IOS.match(linha):
            continue   # "Fulano entrou usando o link", "mensagens criptografadas"…
        elif mensagens and linha.strip():
            mensagens[-1][2] += "\n" + linha.rstrip()   # continuação de mensagem com várias linhas
    return [(q, a, t) for q, a, t in mensagens if t and t.strip().lower() not in IGNORAR]


def _nome_whatsapp(arquivo: Path) -> str:
    nome = re.sub(r"\.(txt|zip)$", "", arquivo.name, flags=re.I)
    nome = re.sub(r"^\d{8}-\d{6}_", "", nome)          # prefixo de data que a Central põe nos recebidos
    nome = re.sub(r"^(Conversa do WhatsApp com|WhatsApp Chat with|WhatsApp Chat -|Chat do WhatsApp com)[\s_-]*",
                  "", nome.replace("_", " "), flags=re.I)
    return nome.strip() or "Grupo do WhatsApp"


# ------------------------------------------------------------------ Telegram Desktop (exportação em JSON)
def ler_telegram_json(dados: dict) -> list[tuple[float, str, str, int]]:
    saida = []
    for m in dados.get("messages", []):
        if m.get("type") != "message":
            continue
        texto = m.get("text", "")
        if isinstance(texto, list):
            texto = "".join(p if isinstance(p, str) else p.get("text", "") for p in texto)
        if not texto.strip():
            continue
        quando = float(m.get("date_unixtime") or datetime.fromisoformat(m["date"]).timestamp())
        saida.append((quando, m.get("from") or "?", texto.strip(), int(m.get("id", 0))))
    return saida


def importar(caminho: str | Path, nome: str = "") -> tuple[str, str, int, int]:
    """Importa um arquivo exportado (WhatsApp .txt/.zip ou Telegram result.json). Devolve (grupo, origem, novas, total)."""
    caminho = Path(caminho)
    if caminho.suffix.lower() == ".zip":
        with zipfile.ZipFile(caminho) as z:
            txts = [n for n in z.namelist() if n.lower().endswith(".txt")]
            if not txts:
                raise ValueError("o .zip não tem a conversa (.txt). Exporte de novo, SEM MÍDIA.")
            texto = z.read(max(txts, key=lambda n: z.getinfo(n).file_size)).decode("utf-8", "replace")
        return _importar_whatsapp(texto, nome or _nome_whatsapp(caminho))
    if caminho.suffix.lower() == ".json":
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        msgs = ler_telegram_json(dados)
        grupo = nome or dados.get("name") or "Grupo do Telegram"
        novas, total = guardar(grupo, "telegram", [(q, a, t) for q, a, t, _ in msgs],
                               ultimo_id=max((i for *_, i in msgs), default=0))
        return grupo, "telegram", novas, total
    return _importar_whatsapp(caminho.read_text(encoding="utf-8", errors="replace"), nome or _nome_whatsapp(caminho))


def _importar_whatsapp(texto: str, grupo: str) -> tuple[str, str, int, int]:
    msgs = ler_whatsapp(texto)
    if not msgs:
        raise ValueError("não reconheci mensagens de WhatsApp neste arquivo.")
    novas, total = guardar(grupo, "whatsapp", msgs)
    return grupo, "whatsapp", novas, total


def eh_exportacao(nome_arquivo: str) -> bool:
    n = nome_arquivo.lower()
    return ("whatsapp" in n and n.endswith((".txt", ".zip"))) or n.endswith("result.json")


# ------------------------------------------------------------------ Telegram pela SUA conta (Telethon)
def telegram_conta_pronta() -> bool:
    return bool(os.getenv("TELEGRAM_API_ID", "").strip() and os.getenv("TELEGRAM_API_HASH", "").strip()
                and SESSAO_TELEGRAM.with_suffix(".session").exists())


def _cliente():
    from telethon.sync import TelegramClient
    return TelegramClient(str(SESSAO_TELEGRAM), int(os.getenv("TELEGRAM_API_ID")), os.getenv("TELEGRAM_API_HASH"))


def entrar_telegram() -> None:
    """Login único (VOCÊ digita o número e o código que o Telegram mandar). Só leitura daqui em diante."""
    if not (os.getenv("TELEGRAM_API_ID", "").strip() and os.getenv("TELEGRAM_API_HASH", "").strip()):
        raise SystemExit("Faltam TELEGRAM_API_ID e TELEGRAM_API_HASH no .env (pegue em my.telegram.org → API development tools).")
    with _cliente() as c:
        eu = c.get_me()
        print(f"✅ Conectado como {eu.first_name}. A Central agora pode LER os seus grupos (nunca envia nada por você).")
    os.chmod(SESSAO_TELEGRAM.with_suffix(".session"), 0o600)


def grupos_do_telegram() -> list[str]:
    with _cliente() as c:
        return [d.name for d in c.iter_dialogs() if d.is_group or (d.is_channel and not d.is_user)]


def puxar_telegram(nome: str, log=print) -> tuple[str, int, int]:
    """Traz do Telegram o histórico do grupo (completo na 1ª vez; depois só o que é novo)."""
    alvo = _normalizar(nome)
    with _cliente() as c:
        dialogos = [d for d in c.iter_dialogs() if d.is_group or (d.is_channel and not d.is_user)]
        achado = ([d for d in dialogos if _normalizar(d.name) == alvo]
                  or [d for d in dialogos if alvo in _normalizar(d.name)])
        if not achado:
            raise ValueError(f"não achei um grupo com '{nome}' no seu Telegram.")
        dialogo = achado[0]
        con = _banco()
        linha = con.execute("SELECT ultimo_id FROM grupos WHERE nome = ?", (dialogo.name,)).fetchone()
        con.close()
        desde = linha[0] if linha else 0
        log(f"📥 Lendo '{dialogo.name}' {'desde o início' if not desde else 'desde a última leitura'}…")
        msgs, ultimo = [], desde
        for m in c.iter_messages(dialogo.entity, min_id=desde, reverse=True):
            ultimo = max(ultimo, m.id)
            if not (m.message or "").strip():
                continue
            autor = "?"
            if m.sender is not None:
                autor = (getattr(m.sender, "first_name", None) or getattr(m.sender, "title", None) or "?")
                if getattr(m.sender, "last_name", None):
                    autor += f" {m.sender.last_name}"
            msgs.append((m.date.timestamp(), autor, m.message.strip()))
            if len(msgs) % 2000 == 0:
                log(f"   {len(msgs):,} mensagens lidas…")
    novas, total = guardar(dialogo.name, "telegram", msgs, ultimo_id=ultimo)
    return dialogo.name, novas, total


# ------------------------------------------------------------------ relatório
def _formatar(linhas: list[tuple[float, str, str]]) -> str:
    return "\n".join(f"[{datetime.fromtimestamp(q):%d/%m/%y %H:%M}] {a}: {t}" for q, a, t in linhas)


def _trechos(texto: str, tamanho: int = TAMANHO_TRECHO) -> list[str]:
    partes, atual = [], []
    total = 0
    for linha in texto.split("\n"):
        if total + len(linha) > tamanho and atual:
            partes.append("\n".join(atual))
            atual, total = [], 0
        atual.append(linha)
        total += len(linha) + 1
    if atual:
        partes.append("\n".join(atual))
    return partes


def relatorio(nome: str, assunto: str = "", tudo: bool = False, log=print) -> tuple[str, Path]:
    """Lê as mensagens (todas na 1ª vez; depois, as novas desde o último relatório) e escreve o relatório."""
    from .agentes import carregar_equipe
    from .orquestrador import PASTA_RESULTADOS, _slug
    achado = achar_grupo(nome)
    if not achado:
        raise ValueError(f"não tenho o grupo '{nome}'. Mande a conversa exportada (WhatsApp) ou peça pelo Telegram.")
    gid, grupo, origem = achado
    con = _banco()
    ultimo = 0 if tudo else con.execute("SELECT ultimo_relatorio FROM grupos WHERE id = ?", (gid,)).fetchone()[0]
    linhas = con.execute("SELECT quando, autor, texto FROM mensagens WHERE grupo = ? AND quando > ? ORDER BY quando",
                         (gid, ultimo)).fetchall()
    con.close()
    if not linhas:
        raise ValueError(f"nenhuma mensagem nova no grupo '{grupo}' desde o último relatório.")
    agente = carregar_equipe()[1]["analista_whatsapp" if origem == "whatsapp" else "analista_telegram"]
    periodo = f"{datetime.fromtimestamp(linhas[0][0]):%d/%m/%Y} a {datetime.fromtimestamp(linhas[-1][0]):%d/%m/%Y}"
    foco = f"\nASSUNTO pedido pelo usuário: {assunto}" if assunto else ""
    partes = _trechos(_formatar(linhas))
    log(f"📖 {grupo}: {len(linhas):,} mensagens ({periodo}) em {len(partes)} parte(s)")
    if len(partes) == 1:
        resumos = [partes[0]]
        tarefa = f"Relatório final do grupo '{grupo}' ({origem}), período {periodo}.{foco}\n\nConversa completa:\n"
    else:
        resumos = []
        for i, parte in enumerate(partes, 1):
            log(f"   parte {i}/{len(partes)}…")
            _, r = agente.executar(f"Etapa parcial {i}/{len(partes)} do grupo '{grupo}'.{foco}\n\nTrecho:\n{parte}",
                                   log=lambda *_: None)
            resumos.append(f"### Parte {i}/{len(partes)}\n{r}")
        tarefa = (f"Relatório final do grupo '{grupo}' ({origem}), período {periodo}, {len(linhas):,} mensagens."
                  f"{foco}\n\nResumos parciais, em ordem cronológica:\n")
    modelo, texto = agente.executar(tarefa + "\n\n".join(resumos), log=lambda *_: None)
    pasta = PASTA_RESULTADOS / f"{datetime.now():%Y-%m-%d_%H%M%S}_grupo-{_slug(grupo)}"
    pasta.mkdir(parents=True, exist_ok=True)
    cabecalho = (f"# Relatório do grupo: {grupo}\n\n*{origem} · {periodo} · {len(linhas):,} mensagens"
                 f"{' · assunto: ' + assunto if assunto else ''} · {modelo}*\n\n")
    (pasta / "relatorio.md").write_text(cabecalho + texto, encoding="utf-8")
    con = _banco()
    con.execute("UPDATE grupos SET ultimo_relatorio = ? WHERE id = ?", (linhas[-1][0], gid))
    con.commit()
    con.close()
    return cabecalho + texto, pasta


def listar() -> str:
    con = _banco()
    linhas = con.execute("""SELECT g.nome, g.origem, COUNT(m.marca), MIN(m.quando), MAX(m.quando), g.acompanhar
                            FROM grupos g LEFT JOIN mensagens m ON m.grupo = g.id GROUP BY g.id ORDER BY g.nome""").fetchall()
    con.close()
    if not linhas:
        return "Nenhum grupo ainda. Mande a conversa exportada do WhatsApp, ou peça um grupo do Telegram."
    return "👥 Grupos guardados:\n" + "\n".join(
        f"• {n} ({o}) — {q:,} msgs, {datetime.fromtimestamp(a):%d/%m/%y} a {datetime.fromtimestamp(b):%d/%m/%y}"
        f"{' · 🔁 semanal' if s else ''}" for n, o, q, a, b, s in linhas if q)


def acompanhar(nome: str, sim: bool = True) -> str:
    achado = achar_grupo(nome)
    if not achado:
        raise ValueError(f"não tenho o grupo '{nome}'.")
    con = _banco()
    con.execute("UPDATE grupos SET acompanhar = ? WHERE id = ?", (int(sim), achado[0]))
    con.commit()
    con.close()
    return achado[1]


# ------------------------------------------------------------------ trabalho completo (roda em segundo plano)
def trabalhar(acao: str, alvo: str, assunto: str = "", tudo: bool = False) -> None:
    """Executa e responde no Telegram. acao: 'importar' (arquivo), 'telegram' (nome do grupo) ou 'relatorio'."""
    from . import telegram
    try:
        if acao == "importar":
            grupo, origem, novas, total = importar(alvo)
            telegram.enviar(f"📥 {origem.title()} — '{grupo}': {novas:,} mensagens novas ({total:,} no total). "
                            "Lendo tudo para o relatório…")
        elif acao == "telegram":
            if not telegram_conta_pronta():
                telegram.enviar("🔐 Para ler grupos do Telegram, a Central precisa entrar na SUA conta uma vez "
                                "(só leitura). Peça ao Claude: 'conectar minha conta do Telegram'. Enquanto isso, "
                                "dá para mandar a exportação do Telegram Desktop (JSON).")
                return
            grupo, novas, total = puxar_telegram(alvo, log=print)
            telegram.enviar(f"📥 Telegram — '{grupo}': {novas:,} mensagens novas ({total:,} no total). Lendo…")
        else:
            grupo = alvo
        texto, pasta = relatorio(grupo, assunto, tudo=tudo)
        for i in range(0, len(texto), 3800):
            telegram.enviar(texto[i:i + 3800])
        telegram.enviar_arquivo(pasta / "relatorio.md", f"📄 Relatório completo — {grupo}")
    except Exception as erro:  # noqa: BLE001
        telegram.enviar(f"⚠️ Grupo: {erro}")


def em_segundo_plano(acao: str, alvo: str, assunto: str = "", tudo: bool = False) -> None:
    """Dispara o trabalho sem travar o bot. Com servidor na nuvem, roda LÁ (onde ficam os grupos)."""
    from . import caixa_postal
    args = ["grupos", acao, alvo] + (["--assunto", assunto] if assunto else []) + (["--tudo"] if tudo else [])
    if caixa_postal.papel() == "pc_com_caixa":
        if acao == "importar":   # o arquivo vai para o servidor
            remoto = f".central-ia/recebidos/{Path(alvo).name}"
            caixa_postal._ssh("mkdir -p ~/.central-ia/recebidos ~/.central-ia/logs")
            caixa_postal._scp(str(alvo), f"{os.getenv('CAIXA_POSTAL').strip()}:{remoto}", timeout=600)
            args[2] = f"/home/ubuntu/{remoto}"
        import shlex
        caixa_postal._ssh("cd ~/central-ia && nohup .venv/bin/python main.py " + " ".join(shlex.quote(a) for a in args)
                          + " >> ~/.central-ia/logs/grupos.log 2>&1 < /dev/null &")
        return
    raiz = Path(__file__).resolve().parent.parent
    subprocess.Popen([sys.executable, str(raiz / "main.py"), *args], cwd=raiz,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def semanal() -> None:
    """Rotina semanal (servidor): grupos marcados com 🔁 recebem o relatório da semana."""
    from . import telegram
    con = _banco()
    marcados = con.execute("SELECT nome, origem FROM grupos WHERE acompanhar = 1").fetchall()
    con.close()
    for nome, origem in marcados:
        if origem == "telegram" and telegram_conta_pronta():
            trabalhar("telegram", nome)
        elif origem == "whatsapp":
            telegram.enviar(f"🔁 Relatório semanal do grupo de WhatsApp '{nome}': exporte a conversa de novo "
                            "(⋮ → Mais → Exportar conversa → SEM MÍDIA) e mande aqui. Eu pego só o que é novo.")
        else:
            trabalhar("relatorio", nome)


def pedido(nome: str, assunto: str = "", tudo: bool = False) -> None:
    """Pedido pelo Telegram: decide sozinho se o grupo é do WhatsApp (já guardado) ou do Telegram (pela sua conta)."""
    from . import telegram
    achado = achar_grupo(nome)
    if achado and achado[2] == "whatsapp":
        trabalhar("relatorio", achado[1], assunto, tudo)
    elif telegram_conta_pronta():
        trabalhar("telegram", nome, assunto, tudo)
    elif achado:
        trabalhar("relatorio", achado[1], assunto, tudo)
    else:
        telegram.enviar(
            f"🤔 Ainda não tenho o grupo '{nome}'.\n\n• Se for do WHATSAPP: no celular, abra o grupo → ⋮ → Mais → "
            "Exportar conversa → SEM MÍDIA → escolha o Telegram → mande para a Central (pode pôr o assunto na legenda).\n"
            "• Se for do TELEGRAM: a Central precisa entrar na sua conta uma vez (só leitura) — peça ao Claude "
            "'conectar minha conta do Telegram'.")


def comando(*args: str) -> str:
    """Roda um comando rápido de grupos onde os grupos ficam (no servidor, se houver) e devolve o texto."""
    from . import caixa_postal
    import shlex
    if caixa_postal.papel() == "pc_com_caixa":
        return caixa_postal._ssh("cd ~/central-ia && .venv/bin/python main.py grupos "
                                 + " ".join(shlex.quote(a) for a in args), timeout=120).strip()
    raiz = Path(__file__).resolve().parent.parent
    r = subprocess.run([sys.executable, str(raiz / "main.py"), "grupos", *args], cwd=raiz, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=120)
    return (r.stdout or r.stderr).strip()
