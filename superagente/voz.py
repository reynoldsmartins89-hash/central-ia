"""Voz da Central: quando você manda áudio, ela responde também em áudio (o jeito Jarvis).

Inspirado no chat por voz e nas notas de voz do Grok Bot, mas 100% local e gratuito:
  • a fala sai da voz do Windows em português (Microsoft Maria, já instalada; sem internet, sem custo);
  • o ffmpeg converte para o formato de mensagem de voz do Telegram (OGG/Opus);
  • o texto vai limpo (sem Markdown, sem links) e curto: a resposta completa continua no texto.
Desligar: VOZ_RESPOSTA=0 no .env.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

LIMITE_FALA = int(os.getenv("VOZ_LIMITE", "900"))     # ~1 minuto de áudio
VOZ = os.getenv("VOZ_NOME", "Microsoft Maria Desktop")
VELOCIDADE = int(os.getenv("VOZ_VELOCIDADE", "1"))     # -10 (lenta) a 10 (rápida)


def ligada() -> bool:
    return os.getenv("VOZ_RESPOSTA", "1").strip() != "0" and os.name == "nt" and bool(_ffmpeg())


def _ffmpeg() -> str | None:
    achado = shutil.which("ffmpeg")
    if achado:
        return achado
    base = Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
    for candidato in base.glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe") if base.exists() else []:
        return str(candidato)
    return None


def para_falar(texto: str, limite: int = LIMITE_FALA) -> str:
    """Tira o que não se fala (Markdown, links, emojis de enfeite) e corta numa frase inteira."""
    t = re.sub(r"```.*?```", " ", texto or "", flags=re.DOTALL)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_`#>|~]+", "", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.MULTILINE)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= limite:
        return t
    corte = t[:limite]
    fim = max(corte.rfind(". "), corte.rfind("! "), corte.rfind("? "))
    return (corte[:fim + 1] if fim > limite // 2 else corte) + " O resto está na mensagem escrita."


def gerar(texto: str, pasta: Path | None = None) -> Path:
    """Texto → arquivo .ogg (voz do Telegram). Levanta RuntimeError se não der."""
    fala = para_falar(texto)
    if not fala:
        raise RuntimeError("nada para falar")
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg não encontrado")
    pasta = pasta or Path(tempfile.gettempdir())
    nome = uuid.uuid4().hex[:10]
    txt, wav, ogg = pasta / f"fala_{nome}.txt", pasta / f"fala_{nome}.wav", pasta / f"fala_{nome}.ogg"
    txt.write_text(fala, encoding="utf-8")
    script = (
        "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"try {{ $s.SelectVoice('{VOZ}') }} catch {{}}; $s.Rate = {VELOCIDADE}; "
        f"$s.SetOutputToWaveFile('{wav}'); $s.Speak([IO.File]::ReadAllText('{txt}', [Text.Encoding]::UTF8)); $s.Dispose()")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                           timeout=180)
        if not wav.exists() or wav.stat().st_size < 1000:
            raise RuntimeError(f"a voz do Windows não gerou o áudio: {(r.stderr or '')[-200:]}")
        r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libopus", "-b:a", "32k",
                            "-ar", "48000", "-ac", "1", str(ogg)], capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or not ogg.exists():
            raise RuntimeError(f"ffmpeg falhou: {(r.stderr or '')[-200:]}")
        return ogg
    finally:
        txt.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)


def responder(texto: str) -> bool:
    """Gera e manda a voz no Telegram. Nunca derruba a conversa: devolve False se não deu."""
    if not ligada():
        return False
    from . import telegram
    try:
        ogg = gerar(texto)
    except Exception as erro:  # noqa: BLE001
        print(f"(voz: {erro})")
        return False
    try:
        return telegram.enviar_voz(ogg)
    finally:
        ogg.unlink(missing_ok=True)
