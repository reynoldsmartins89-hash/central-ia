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


def configurado() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip())


def _api(metodo: str, dados: dict | None = None, timeout: float = 40) -> dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    url = f"https://api.telegram.org/bot{token}/{metodo}"
    corpo = json.dumps(dados or {}).encode("utf-8")
    req = urllib.request.Request(url, data=corpo, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def enviar(texto: str, botoes: list[tuple[str, str]] | None = None) -> None:
    """Envia uma mensagem. Falhas do Telegram nunca interrompem o trabalho."""
    if not configurado():
        return
    dados = {"chat_id": os.getenv("TELEGRAM_CHAT_ID").strip(), "text": texto[:LIMITE_TEXTO]}
    if botoes:
        dados["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in botoes]]}
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
        urllib.request.urlopen(req, timeout=120).read()
    except Exception as erro:  # noqa: BLE001
        print(f"   (aviso: não consegui enviar o arquivo ao Telegram: {erro})")


def _ultimo_update() -> int:
    r = _api("getUpdates", {"timeout": 0})
    return max((u["update_id"] for u in r.get("result", [])), default=0)


def pedir_aprovacao(texto: str, espera_max: int = 3600) -> bool:
    """Envia o texto com botões Aprovar/Rejeitar e espera a sua resposta no celular."""
    codigo = str(int(time.time()))
    deslocamento = _ultimo_update() + 1
    enviar(texto + "\n\nAprovar a execução?",
           [("✅ Aprovar", f"sim:{codigo}"), ("❌ Rejeitar", f"nao:{codigo}")])
    print("📱 Aguardando sua aprovação no Telegram…")
    limite = time.time() + espera_max
    while time.time() < limite:
        r = _api("getUpdates", {"offset": deslocamento, "timeout": 30,
                                "allowed_updates": ["message", "callback_query"]}, timeout=40)
        for u in r.get("result", []):
            deslocamento = u["update_id"] + 1
            if "message" in u:
                enviar("⏳ Ainda estou esperando você aprovar ou rejeitar o plano acima.")
                continue
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
