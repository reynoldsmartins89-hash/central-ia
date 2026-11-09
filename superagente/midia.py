"""O que você manda no Telegram além de texto: ÁUDIO (voz), FOTOS e DOCUMENTOS.

  • Áudio/voz → transcrito no seu PC (Whisper na placa de vídeo, segundos) e tratado como texto.
  • Foto     → lida pela IA de visão (Gemini; reserva: Gemma no seu PC) e entra no pedido.
  • Documento (PDF, Word, planilha, texto…) → o conteúdo é extraído e entra no pedido.
Os arquivos recebidos ficam em ~/.central-ia/recebidos.
"""
from __future__ import annotations

import base64
import os
import re
import urllib.request
from datetime import datetime
from pathlib import Path

from . import provedores

PASTA_RECEBIDOS = Path.home() / ".central-ia" / "recebidos"
LIMITE_DOCUMENTO = 30000
PEDIDO_FOTO = ("Descreva esta imagem em português para um assistente que vai ajudar o usuário com ela: "
               "transcreva TODO o texto visível e explique o que ela mostra, com os detalhes úteis.")


def baixar(file_id: str, nome: str) -> Path:
    """Baixa um arquivo enviado ao bot (limite do Telegram: 20 MB)."""
    from .telegram import _api
    caminho_remoto = _api("getFile", {"file_id": file_id})["result"]["file_path"]
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    PASTA_RECEBIDOS.mkdir(parents=True, exist_ok=True)
    destino = PASTA_RECEBIDOS / f"{datetime.now():%Y%m%d-%H%M%S}_{re.sub(r'[^\w.-]+', '_', nome)}"
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{token}/{caminho_remoto}", timeout=120) as r:
        destino.write_bytes(r.read())
    return destino


def _transcrever_groq(caminho: Path) -> str:
    """Transcrição pela internet (Groq Whisper, gratuito) — usada no servidor, que não tem placa de vídeo."""
    import subprocess
    import tempfile
    import uuid
    with tempfile.TemporaryDirectory() as tmp:
        mp3 = Path(tmp) / "audio.mp3"   # 16 kHz mono: pequeno e suficiente para voz
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(caminho), "-vn", "-ac", "1",
                        "-ar", "16000", "-b:a", "48k", str(mp3)], check=True, timeout=600)
        fronteira = uuid.uuid4().hex
        corpo = b"".join([
            f'--{fronteira}\r\nContent-Disposition: form-data; name="model"\r\n\r\nwhisper-large-v3-turbo\r\n'.encode(),
            f'--{fronteira}\r\nContent-Disposition: form-data; name="file"; filename="audio.mp3"\r\n'
            f"Content-Type: audio/mpeg\r\n\r\n".encode() + mp3.read_bytes() + b"\r\n",
            f"--{fronteira}--\r\n".encode()])
    req = urllib.request.Request("https://api.groq.com/openai/v1/audio/transcriptions", data=corpo, headers={
        "Authorization": f"Bearer {provedores._chave('GROQ_API_KEY')}",
        "Content-Type": f"multipart/form-data; boundary={fronteira}", "User-Agent": "central-ia"})
    import json
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8")).get("text", "").strip()


def transcrever(caminho: Path) -> str:
    from . import caixa_postal, leitor
    if caixa_postal.papel() == "nuvem":
        return _transcrever_groq(caminho)
    _, texto = leitor._transcrever(str(caminho))
    # tira as marcas de tempo/idioma "[00:03] [pt] " de cada linha
    return " ".join(re.sub(r"^\[\d+:\d+(?::\d+)?\]\s*(\[\w+\]\s*)?", "", l).strip()
                    for l in texto.splitlines() if l.strip()).strip()


def ver_imagem(caminho: Path, pedido: str = PEDIDO_FOTO) -> tuple[str, str]:
    """Devolve (quem leu, descrição). Gemini primeiro (rápido); reserva: visão local."""
    from PIL import Image
    from . import leitor
    with Image.open(caminho) as img:
        png = leitor._png(img, 1600)
    for modelo in ("gemini-3.5-flash", "gemini-flash-lite-latest"):
        try:
            r = provedores._post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
                {"contents": [{"role": "user", "parts": [
                    {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode()}},
                    {"text": pedido}]}]},
                {"x-goog-api-key": provedores._chave("GEMINI_API_KEY")})
            texto = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"]).strip()
            if texto:
                return f"gemini:{modelo}", texto
        except Exception:  # noqa: BLE001 — tenta o próximo
            continue
    return f"visão local ({leitor.MODELO_VISAO})", leitor._ver_imagem(png, pedido)


def ler_documento(caminho: Path) -> str:
    from . import leitor
    _, _, texto = leitor.ler(str(caminho))
    return (texto or "")[:LIMITE_DOCUMENTO]


def _acao(qual: str) -> None:
    """Mostra "gravando…/digitando…" no Telegram em vez de mandar mensagens de aviso."""
    try:
        from .telegram import _api
        _api("sendChatAction", {"chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(), "action": qual})
    except Exception:  # noqa: BLE001
        pass


def entrada(msg: dict, avisar=lambda _t: None) -> str | None:
    """Converte a mensagem do Telegram (texto, voz, áudio, foto ou documento) em texto para a Central."""
    legenda = (msg.get("caption") or "").strip()
    if msg.get("text"):
        return msg["text"].strip()
    if msg.get("voice") or msg.get("audio") or msg.get("video_note") or msg.get("video"):
        midia = msg.get("voice") or msg.get("audio") or msg.get("video_note") or msg["video"]
        _acao("typing")
        arquivo = baixar(midia["file_id"], midia.get("file_name") or "audio.ogg")
        texto = transcrever(arquivo)
        if not texto:
            avisar("🎙 Não consegui entender o áudio. Pode repetir?")
            return None
        avisar(f"🎙 _{texto[:400]}{'…' if len(texto) > 400 else ''}_")
        return f"{texto}\n{legenda}".strip()
    if msg.get("photo"):
        _acao("typing")
        foto = max(msg["photo"], key=lambda f: f.get("file_size", 0))
        arquivo = baixar(foto["file_id"], "foto.jpg")
        quem, descricao = ver_imagem(arquivo)
        pedido = legenda or "O que você vê nesta foto? Me ajude com ela."
        return f"{pedido}\n\n[Foto enviada pelo usuário — salva em {arquivo} — lida por {quem}:]\n{descricao}"
    if msg.get("document"):
        doc = msg["document"]
        avisar(f"📄 Lendo {doc.get('file_name', 'o documento')}…")
        arquivo = baixar(doc["file_id"], doc.get("file_name") or "documento")
        if (doc.get("mime_type") or "").startswith("image/"):
            quem, conteudo = ver_imagem(arquivo)
        elif (doc.get("mime_type") or "").startswith(("audio/", "video/")):
            conteudo = transcrever(arquivo)
        else:
            conteudo = ler_documento(arquivo)
        pedido = legenda or "Leia este documento e me diga do que se trata, com os pontos principais."
        return f"{pedido}\n\n[Documento enviado: {doc.get('file_name')} — salvo em {arquivo}]\n{conteudo}"
    return None
