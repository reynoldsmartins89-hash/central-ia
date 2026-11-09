"""Conectores para os provedores de IA, usando apenas a biblioteca padrão.

Cada modelo é escrito como "provedor:modelo", por exemplo:
    "ollama:qwen3:4b"   "gemini:gemini-2.5-flash"   "groq:llama-3.3-70b-versatile"

`chamar_com_reserva` tenta cada modelo da lista até um funcionar.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


class ErroProvedor(Exception):
    """Falha ao chamar um provedor (sem chave, fora do ar, cota esgotada...)."""


# Provedores que seguem o formato da API da OpenAI (/chat/completions).
OPENAI_COMPATIVEIS: dict[str, tuple[str, str]] = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),  # Grok
    "moonshot": ("https://api.moonshot.ai/v1", "MOONSHOT_API_KEY"),  # Kimi
    "qwen": ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY"),
    "zai": ("https://api.z.ai/api/paas/v4", "ZAI_API_KEY"),
    "perplexity": ("https://api.perplexity.ai", "PERPLEXITY_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}

CHAVES = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
          **{p: chave for p, (_, chave) in OPENAI_COMPATIVEIS.items()}}

TIMEOUT = float(os.getenv("CENTRAL_IA_TIMEOUT", "300"))


def _chave(nome_env: str) -> str:
    valor = os.getenv(nome_env, "").strip()
    if not valor:
        raise ErroProvedor(f"variável {nome_env} não configurada no .env")
    return valor


ESPERAS_RETENTATIVA = (5, 15)  # segundos entre novas tentativas quando o provedor está ocupado


def _post(url: str, corpo: dict, cabecalhos: dict) -> dict:
    requisicao = urllib.request.Request(
        url,
        data=json.dumps(corpo).encode("utf-8"),
        headers={"Content-Type": "application/json", **cabecalhos},
        method="POST",
    )
    for tentativa in range(len(ESPERAS_RETENTATIVA) + 1):
        try:
            with urllib.request.urlopen(requisicao, timeout=TIMEOUT) as resposta:
                return json.loads(resposta.read().decode("utf-8"))
        except urllib.error.HTTPError as erro:
            # 429 = limite por minuto; 500/503 = provedor sobrecarregado → espera e tenta de novo
            if erro.code in (429, 500, 503) and tentativa < len(ESPERAS_RETENTATIVA):
                time.sleep(ESPERAS_RETENTATIVA[tentativa])
                continue
            detalhe = erro.read().decode("utf-8", errors="ignore")[:300]
            raise ErroProvedor(f"HTTP {erro.code}: {detalhe}") from erro
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            raise ErroProvedor(f"sem conexão: {erro}") from erro
    raise ErroProvedor("provedor não respondeu")


def _ollama(modelo: str, sistema: str, mensagem: str) -> str:
    url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/api/chat"
    r = _post(url, {
        "model": modelo,
        "stream": False,
        "messages": [{"role": "system", "content": sistema},
                     {"role": "user", "content": mensagem}],
    }, {})
    return r["message"]["content"]


def _gemini(modelo: str, sistema: str, mensagem: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
    r = _post(url, {
        "system_instruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": [{"text": mensagem}]}],
    }, {"x-goog-api-key": _chave("GEMINI_API_KEY")})
    try:
        return "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError) as erro:
        raise ErroProvedor(f"resposta inesperada do Gemini: {str(r)[:300]}") from erro


def _anthropic(modelo: str, sistema: str, mensagem: str) -> str:
    r = _post("https://api.anthropic.com/v1/messages", {
        "model": modelo,
        "max_tokens": 8000,
        "system": sistema,
        "messages": [{"role": "user", "content": mensagem}],
    }, {"x-api-key": _chave("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01"})
    return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")


def _openai_compativel(provedor: str, modelo: str, sistema: str, mensagem: str) -> str:
    base, nome_env = OPENAI_COMPATIVEIS[provedor]
    r = _post(base + "/chat/completions", {
        "model": modelo,
        "messages": [{"role": "system", "content": sistema},
                     {"role": "user", "content": mensagem}],
    }, {"Authorization": f"Bearer {_chave(nome_env)}"})
    return r["choices"][0]["message"]["content"]


def _eco(modelo: str, sistema: str, mensagem: str) -> str:
    """Provedor falso para testes offline: devolve um texto previsível."""
    habilidade = sistema.splitlines()[0] if sistema else ""
    return f"[eco:{modelo}] {habilidade}\n\n{mensagem[-400:]}"


def chamar(especificacao: str, sistema: str, mensagem: str) -> str:
    """Chama um único modelo no formato "provedor:modelo"."""
    provedor, _, modelo = especificacao.partition(":")
    if not modelo:
        raise ErroProvedor(f"formato inválido '{especificacao}' (use provedor:modelo)")
    if provedor == "ollama":
        return _ollama(modelo, sistema, mensagem)
    if provedor == "gemini":
        return _gemini(modelo, sistema, mensagem)
    if provedor == "anthropic":
        return _anthropic(modelo, sistema, mensagem)
    if provedor in OPENAI_COMPATIVEIS:
        return _openai_compativel(provedor, modelo, sistema, mensagem)
    if provedor == "eco":
        return _eco(modelo, sistema, mensagem)
    raise ErroProvedor(f"provedor desconhecido: {provedor}")


def chamar_com_reserva(modelos: list[str], sistema: str, mensagem: str,
                       log=print) -> tuple[str, str]:
    """Tenta cada modelo em ordem. Retorna (modelo_usado, resposta)."""
    erros = []
    for especificacao in modelos:
        try:
            resposta = chamar(especificacao, sistema, mensagem)
            if resposta.strip():
                return especificacao, resposta
            erros.append(f"{especificacao}: resposta vazia")
        except ErroProvedor as erro:
            erros.append(f"{especificacao}: {erro}")
            log(f"   ↪ {especificacao} indisponível ({str(erro)[:90]}); tentando o próximo…")
    raise ErroProvedor("nenhum modelo respondeu:\n  - " + "\n  - ".join(erros))


def diagnostico() -> list[tuple[str, bool, str]]:
    """Verifica quais provedores estão prontos para uso."""
    resultado = []
    try:
        url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=3) as r:
            modelos = [m["name"] for m in json.loads(r.read()).get("models", [])]
        resultado.append(("ollama", True, "modelos: " + (", ".join(modelos) or "nenhum baixado")))
    except Exception:
        resultado.append(("ollama", False, "não está rodando (instale em ollama.com)"))
    for provedor, nome_env in CHAVES.items():
        ok = bool(os.getenv(nome_env, "").strip())
        resultado.append((provedor, ok, f"{nome_env} {'configurada' if ok else 'ausente'}"))
    return resultado
