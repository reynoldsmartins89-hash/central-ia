"""Produção de imagens, em camadas (a primeira que funcionar é usada):

1. Gemini (Nano Banana) via API — melhor qualidade; exige faturamento ativado
   no Google AI Studio (a camada gratuita NÃO gera imagens). Ative com IMAGENS_GEMINI=1.
2. Pexels — fotos reais, gratuitas para uso comercial. Chave grátis em
   https://www.pexels.com/api/ → PEXELS_API_KEY no .env.
3. Pollinations — geração por IA gratuita, sem chave. Qualidade de RASCUNHO
   (baixa resolução, marca d'água): serve para visualizar a ideia, não para publicar.

O agente diretor_de_arte decide QUAIS imagens; este módulo as PRODUZ.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .provedores import _post, ErroProvedor

DIMENSOES = {"16:9": (1600, 900), "1.91:1": (1200, 627), "4:5": (1080, 1350), "1:1": (1080, 1080)}
CABECALHO = {"User-Agent": "central-ia/0.1"}


def extrair_lista(texto: str) -> list[dict]:
    blocos = re.findall(r"```json\s*(.*?)```", texto, re.S)
    for bloco in reversed(blocos):
        try:
            dados = json.loads(bloco)
            if isinstance(dados, list):
                return [d for d in dados if isinstance(d, dict) and d.get("nome")]
        except json.JSONDecodeError:
            continue
    return []


def _gemini(item: dict) -> bytes:
    if os.getenv("IMAGENS_GEMINI", "").strip() != "1":
        raise ErroProvedor("Gemini imagens desativado (IMAGENS_GEMINI=1 após ativar faturamento)")
    modelo = os.getenv("GEMINI_MODELO_IMAGEM", "gemini-3.1-flash-image")
    r = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent", {
        "contents": [{"parts": [{"text": f"{item['prompt_en']}. Aspect ratio {item.get('formato', '16:9')}."}]}],
        "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]},
    }, {"x-goog-api-key": os.getenv("GEMINI_API_KEY", "")})
    for parte in r["candidates"][0]["content"]["parts"]:
        if "inlineData" in parte:
            return base64.b64decode(parte["inlineData"]["data"])
    raise ErroProvedor("Gemini não devolveu imagem")


def _pexels(item: dict) -> bytes:
    chave = os.getenv("PEXELS_API_KEY", "").strip()
    if not chave:
        raise ErroProvedor("PEXELS_API_KEY ausente")
    orientacao = "portrait" if item.get("formato") == "4:5" else "landscape"
    url = ("https://api.pexels.com/v1/search?per_page=1&orientation=" + orientacao
           + "&query=" + urllib.parse.quote(item.get("busca_en") or item["prompt_en"][:60]))
    req = urllib.request.Request(url, headers={"Authorization": chave, **CABECALHO})
    fotos = json.loads(urllib.request.urlopen(req, timeout=30).read()).get("photos", [])
    if not fotos:
        raise ErroProvedor("nenhuma foto encontrada")
    item["credito"] = f"Foto: {fotos[0]['photographer']} / Pexels ({fotos[0]['url']})"
    req = urllib.request.Request(fotos[0]["src"]["large2x"], headers=CABECALHO)
    return urllib.request.urlopen(req, timeout=60).read()


def _pollinations(item: dict) -> bytes:
    largura, altura = DIMENSOES.get(item.get("formato", "16:9"), (1600, 900))
    url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(item['prompt_en'])}"
           f"?width={largura}&height={altura}&nologo=true")
    item["credito"] = "RASCUNHO gerado por IA (Pollinations) — não publicar sem substituir"
    return urllib.request.urlopen(urllib.request.Request(url, headers=CABECALHO), timeout=180).read()


FONTES = (("gemini", _gemini), ("pexels", _pexels), ("pollinations", _pollinations))


def produzir(texto_diretor: str, pasta: Path, log=print) -> list[dict]:
    """Lê o JSON do diretor de arte e salva as imagens em <pasta>/imagens/."""
    lista = extrair_lista(texto_diretor)
    if not lista:
        log("   ⚠ diretor de arte não entregou a lista JSON de imagens")
        return []
    destino = pasta / "imagens"
    destino.mkdir(exist_ok=True)
    for item in lista:
        for fonte, funcao in FONTES:
            try:
                dados = funcao(item)
                extensao = ".png" if dados[:4] == b"\x89PNG" else ".jpg"
                arquivo = destino / f"{item['nome']}{extensao}"
                arquivo.write_bytes(dados)
                item.update(arquivo=arquivo.name, fonte=fonte)
                log(f"   🖼 {item['nome']} ← {fonte}")
                break
            except Exception as erro:  # noqa: BLE001 — tenta a próxima fonte
                item.setdefault("erros", []).append(f"{fonte}: {str(erro)[:80]}")
    (destino / "imagens.json").write_text(json.dumps(lista, ensure_ascii=False, indent=2), encoding="utf-8")
    return lista
