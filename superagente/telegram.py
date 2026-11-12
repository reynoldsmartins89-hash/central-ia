"""Avisos e aprovação pelo Telegram (gratuito).

Configuração (.env):
    TELEGRAM_BOT_TOKEN=  token que o @BotFather entrega
    TELEGRAM_CHAT_ID=    seu número de chat (python main.py telegram descobre sozinho)

Sem essas variáveis, tudo funciona normalmente sem Telegram.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

LIMITE_TEXTO = 3800  # o Telegram aceita até 4096 caracteres por mensagem
ADIADAS: list[dict] = []   # mensagens que chegaram enquanto um plano esperava aprovação: o bot atende em seguida


def configurado() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip())


def _api(metodo: str, dados: dict | None = None, timeout: float = 40) -> dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    url = f"https://api.telegram.org/bot{token}/{metodo}"
    corpo = json.dumps(dados or {}).encode("utf-8")
    req = urllib.request.Request(url, data=corpo, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def formatar_html(texto: str) -> str:
    """Markdown das IAs → formatação do Telegram (negrito, itálico, links, listas), sem símbolos crus."""
    import html
    import re
    blocos, saida = re.split(r"```(?:\w+)?\n?(.*?)```", texto, flags=re.S), []
    for i, bloco in enumerate(blocos):
        if i % 2:   # bloco de código
            saida.append(f"<pre>{html.escape(bloco.strip())}</pre>")
            continue
        linhas = []
        for linha in bloco.split("\n"):
            bruta = linha.strip()
            if re.fullmatch(r"\|?[\s:|-]+\|?", bruta) and "-" in bruta:
                continue                                  # linha separadora de tabela
            if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", bruta):
                linhas.append("")                         # régua horizontal
                continue
            if bruta.startswith("|") and bruta.endswith("|"):
                celulas = [c.strip() for c in bruta.strip("|").split("|")]
                linha = "• " + " — ".join(c for c in celulas if c)
            linha = html.escape(linha, quote=False)
            titulo = re.match(r"\s*#{1,6}\s+(.*)", linha)
            if titulo:
                linha = f"<b>{titulo.group(1).strip('* ')}</b>"
            linha = re.sub(r"^(\s*)[-*]\s+", r"\1• ", linha)
            linha = re.sub(r"^(\s*)&gt;\s?", r"\1▎ ", linha)
            linhas.append(linha)
        parte = "\n".join(linhas)
        parte = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", parte)
        parte = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", parte, flags=re.S)
        parte = re.sub(r"__(.+?)__", r"<b>\1</b>", parte, flags=re.S)
        parte = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", parte)
        parte = re.sub(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])", r"<i>\1</i>", parte)
        parte = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', parte)
        saida.append(parte)
    return re.sub(r"\n{3,}", "\n\n", "".join(saida)).strip()


def enviar(texto: str, botoes: list[tuple[str, str]] | None = None) -> None:
    """Envia uma mensagem, já formatada. Falhas do Telegram nunca interrompem o trabalho."""
    if not configurado():
        return
    dados = {"chat_id": os.getenv("TELEGRAM_CHAT_ID").strip(), "text": formatar_html(texto[:LIMITE_TEXTO]),
             "parse_mode": "HTML", "link_preview_options": {"is_disabled": True}}
    if botoes:
        dados["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in botoes]]}
    try:
        _api("sendMessage", dados)
    except Exception:  # noqa: BLE001 — formatação recusada? manda como texto simples
        dados.pop("parse_mode")
        dados["text"] = texto[:LIMITE_TEXTO]
        try:
            _api("sendMessage", dados)
        except Exception as erro:  # noqa: BLE001
            print(f"   (aviso: não consegui enviar ao Telegram: {erro})")


def enviar_arquivo(caminho, legenda: str = "") -> None:
    """Envia um arquivo (ex.: o resultado .md ou uma imagem) como documento."""
    if not configurado():
        return
    import uuid
    from pathlib import Path
    caminho = Path(caminho)
    fronteira = uuid.uuid4().hex
    partes = []
    for nome, valor in (("chat_id", os.getenv("TELEGRAM_CHAT_ID").strip()), ("caption", legenda[:1000])):
        partes.append(f'--{fronteira}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n{valor}\r\n'.encode())
    partes.append(
        f'--{fronteira}\r\nContent-Disposition: form-data; name="document"; filename="{caminho.name}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n".encode() + caminho.read_bytes() + b"\r\n")
    partes.append(f"--{fronteira}--\r\n".encode())
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument", data=b"".join(partes),
        headers={"Content-Type": f"multipart/form-data; boundary={fronteira}"})
    try:
        urllib.request.urlopen(req, timeout=900).read()   # arquivos grandes (livros) levam mais tempo
    except Exception as erro:  # noqa: BLE001
        print(f"   (aviso: não consegui enviar o arquivo ao Telegram: {erro})")


def _ultimo_update() -> int:
    from . import caixa_postal
    if caixa_postal.papel() != "sozinho":
        return 0   # com a caixa postal, cada mensagem é entregue uma única vez
    r = _api("getUpdates", {"timeout": 0})
    return max((u["update_id"] for u in r.get("result", [])), default=0)


def atualizacoes(deslocamento: int = 0, espera: int = 30) -> list[dict]:
    """Novas mensagens/cliques. Vêm da caixa postal (servidor) quando ela existe; senão, do Telegram."""
    from . import caixa_postal
    papel = caixa_postal.papel()
    if papel == "nuvem":
        return caixa_postal.pegar("nuvem", espera)
    if papel == "pc_com_caixa":
        return caixa_postal.remoto_pegar(espera)
    return _api("getUpdates", {"offset": deslocamento, "timeout": espera,
                               "allowed_updates": ["message", "callback_query"]}, timeout=espera + 10).get("result", [])


def pedir_aprovacao(texto: str, espera_max: int = 3600) -> bool:
    """Envia o texto com botões Aprovar/Rejeitar e espera a sua resposta no celular."""
    codigo = str(int(time.time()))
    deslocamento = _ultimo_update() + 1
    enviar(texto + "\n\nAprovar a execução?",
           [("✅ Aprovar", f"sim:{codigo}"), ("❌ Rejeitar", f"nao:{codigo}")])
    print("📱 Aguardando sua aprovação no Telegram…")
    limite = time.time() + espera_max
    while time.time() < limite:
        for u in atualizacoes(deslocamento, 30):
            deslocamento = u["update_id"] + 1
            if "message" in u:
                # você seguiu conversando em vez de tocar no botão: o plano é cancelado e a sua nova
                # mensagem é atendida normalmente (nunca mais "ainda estou esperando…" em série)
                ADIADAS.append(u)
                enviar("↩️ Deixei o plano acima de lado (nada foi feito) para atender a sua nova mensagem.")
                return False
            cb = u.get("callback_query") or {}
            resposta, _, cod = (cb.get("data") or "").partition(":")
            if cod != codigo or str(cb.get("message", {}).get("chat", {}).get("id")) != os.getenv("TELEGRAM_CHAT_ID").strip():
                continue
            _api("answerCallbackQuery", {"callback_query_id": cb["id"],
                                         "text": "Aprovado!" if resposta == "sim" else "Rejeitado."})
            return resposta == "sim"
    enviar("⏱ Tempo de aprovação esgotado; plano cancelado.")
    return False


def descobrir_chat_id() -> str | None:
    """Lê a última mensagem enviada ao bot e devolve o chat_id de quem enviou."""
    r = _api("getUpdates", {"timeout": 0})
    for u in reversed(r.get("result", [])):
        chat = (u.get("message") or {}).get("chat")
        if chat:
            return str(chat["id"])
    return None
