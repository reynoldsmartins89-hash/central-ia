"""Conectores para os provedores de IA, usando apenas a biblioteca padrão.

Cada modelo é escrito como "provedor:modelo", por exemplo:
    "ollama:qwen3:4b"   "gemini:gemini-2.5-flash"   "groq:llama-3.3-70b-versatile"

`chamar_com_reserva` tenta cada modelo da lista até um funcionar.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
from pathlib import Path
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
        # User-Agent próprio: alguns provedores (ex.: Groq/Cloudflare) bloqueiam o padrão "Python-urllib"
        headers={"Content-Type": "application/json", "User-Agent": "central-ia/0.1", **cabecalhos},
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


# O plano gratuito do Groq recusa pedidos grandes (HTTP 413, ~8 mil tokens por minuto no gpt-oss-120b).
# Pedidos acima deste tamanho pulam direto para o próximo modelo, sem gastar uma chamada à toa.
GROQ_LIMITE_CARACTERES = int(os.getenv("GROQ_LIMITE_CARACTERES", "18000"))


def _openai_compativel(provedor: str, modelo: str, sistema: str, mensagem: str) -> str:
    if provedor == "groq" and len(sistema) + len(mensagem) > GROQ_LIMITE_CARACTERES:
        raise ErroProvedor(f"pedido grande demais para o plano gratuito do Groq "
                           f"({len(sistema) + len(mensagem)} caracteres)")
    base, nome_env = OPENAI_COMPATIVEIS[provedor]
    r = _post(base + "/chat/completions", {
        "model": modelo,
        "messages": [{"role": "system", "content": sistema},
                     {"role": "user", "content": mensagem}],
    }, {"Authorization": f"Bearer {_chave(nome_env)}"})
    return r["choices"][0]["message"]["content"]


PASTA_CLI = Path(__file__).resolve().parent.parent / "resultados" / "_cli"
TIMEOUT_CLI = float(os.getenv("CENTRAL_IA_TIMEOUT_CLI", "600"))


def _achar_cli(nome: str) -> str | None:
    """Procura em ~/.central-ia/ferramentas (instalação própria), no PATH e na pasta global do npm.
    A pasta própria existe porque o Python do "Python install manager" pode não enxergar AppData."""
    pastas = [Path.home() / ".central-ia" / "ferramentas", Path(os.getenv("APPDATA", "")) / "npm"]
    return next((c for p in pastas if (c := shutil.which(nome, path=str(p)))), None) or shutil.which(nome)


def _cli(ferramenta: str, sistema: str, mensagem: str) -> str:
    """Usa as ferramentas OFICIAIS de terminal, logadas com as SUAS assinaturas:
        cli:claude  → Claude Code (plano Claude Pro/Max)
        cli:codex   → Codex CLI   (ChatGPT Plus)
        cli:gemini  → Gemini CLI  (conta Google)
    Opcional: modelo após "/", ex.: "cli:claude/opus", "cli:gemini/gemini-3.5-pro".
    O texto vai pela entrada padrão (sem limite de tamanho da linha de comando).
    """
    nome, _, modelo = ferramenta.partition("/")
    executavel = _achar_cli(nome)
    if not executavel:
        raise ErroProvedor(f"{nome} não está instalado")
    PASTA_CLI.mkdir(parents=True, exist_ok=True)
    prompt = f"{sistema}\n\n---\n\n{mensagem}\n\nResponda apenas com o conteúdo pedido, sem usar ferramentas nem criar arquivos."
    import uuid
    saida_codex = PASTA_CLI / f"codex_resposta_{uuid.uuid4().hex[:10]}.txt"   # um por chamada: roda em paralelo
    if nome == "claude":
        comando = [executavel, "-p", "--output-format", "text"] + (["--model", modelo] if modelo else [])
    elif nome == "codex":
        saida_codex.unlink(missing_ok=True)
        comando = [executavel, "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                   "--output-last-message", str(saida_codex)] + (["-m", modelo] if modelo else []) + ["-"]
    elif nome == "gemini":
        comando = [executavel, "--skip-trust"] + (["-m", modelo] if modelo else [])
    else:
        raise ErroProvedor(f"ferramenta de terminal desconhecida: {nome}")
    ambiente = dict(os.environ)
    if nome == "gemini":
        # Sem a chave gratuita no ambiente, o Gemini CLI usa o login Google (plano Google AI Pro).
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            ambiente.pop(var, None)
    processo = subprocess.Popen(comando, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", cwd=PASTA_CLI, env=ambiente)
    try:
        saida, erros = processo.communicate(prompt, timeout=TIMEOUT_CLI)
    except subprocess.TimeoutExpired as erro:
        # .cmd abre um node por baixo: é preciso encerrar a árvore inteira de processos
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(processo.pid)], capture_output=True)
        processo.communicate()
        raise ErroProvedor(f"{nome} não respondeu em {TIMEOUT_CLI:.0f}s (precisa de login?)") from erro
    r = subprocess.CompletedProcess(comando, processo.returncode, saida, erros)
    texto = saida_codex.read_text(encoding="utf-8") if nome == "codex" and saida_codex.exists() else r.stdout
    saida_codex.unlink(missing_ok=True)
    if r.returncode != 0 or not texto.strip():
        detalhe = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
        raise ErroProvedor(f"{nome} falhou (está logado?): {' | '.join(detalhe)[:250]}")
    return texto.strip()


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
    if provedor == "cli":
        return _cli(modelo, sistema, mensagem)
    raise ErroProvedor(f"provedor desconhecido: {provedor}")


# ------------------------------------------------------------------ descanso das assinaturas
# Quando uma assinatura bate no limite (ou o serviço está fora), ela "descansa" um tempo e as
# próximas chamadas vão direto para a seguinte da lista, sem esperar minutos por uma falha certa.
# O arquivo é compartilhado entre o bot, a agenda e o terminal.
ARQUIVO_DESCANSO = Path.home() / ".central-ia" / "descanso.json"
DESCANSO_LIMITE = 45 * 60      # bateu na cota
DESCANSO_FALHA = 10 * 60       # serviço fora do ar / sem resposta
PISTAS_DE_COTA = ("limit", "quota", "rate", "usage", "429", "exceeded", "too many", "esgot")


def _chave_descanso(especificacao: str) -> str:
    provedor, _, modelo = especificacao.partition(":")
    return f"cli:{modelo.split('/')[0]}" if provedor == "cli" else especificacao


def _descansos() -> dict:
    try:
        import json
        dados = json.loads(ARQUIVO_DESCANSO.read_text(encoding="utf-8"))
        return {k: v for k, v in dados.items() if v > time.time()}
    except (OSError, ValueError):
        return {}


def descansando(especificacao: str) -> bool:
    return _chave_descanso(especificacao) in _descansos()


def por_para_descansar(especificacao: str, erro: str) -> int:
    """Marca a assinatura como em descanso. Só vale para ferramentas de terminal (cli:)."""
    if not especificacao.startswith("cli:"):
        return 0
    import json
    segundos = DESCANSO_LIMITE if any(p in erro.lower() for p in PISTAS_DE_COTA) else DESCANSO_FALHA
    dados = _descansos()
    dados[_chave_descanso(especificacao)] = time.time() + segundos
    try:
        ARQUIVO_DESCANSO.parent.mkdir(parents=True, exist_ok=True)
        ARQUIVO_DESCANSO.write_text(json.dumps(dados), encoding="utf-8")
    except OSError:
        pass
    return segundos


def chamar_com_reserva(modelos: list[str], sistema: str, mensagem: str,
                       log=print) -> tuple[str, str]:
    """Tenta cada modelo em ordem. Retorna (modelo_usado, resposta).
    Pula as assinaturas em descanso e, dentro do servidor MCP (CENTRAL_SEM_CLI), as de terminal."""
    erros = []
    sem_cli = os.getenv("CENTRAL_SEM_CLI", "").strip() == "1"
    candidatos = [m for m in modelos if not (sem_cli and m.startswith("cli:")) and not descansando(m)]
    if not candidatos:   # tudo descansando: tenta assim mesmo, na ordem original
        candidatos = list(modelos)
    for especificacao in candidatos:
        try:
            resposta = chamar(especificacao, sistema, mensagem)
            if resposta.strip():
                return especificacao, resposta
            erros.append(f"{especificacao}: resposta vazia")
        except ErroProvedor as erro:
            erros.append(f"{especificacao}: {erro}")
            pausa = por_para_descansar(especificacao, str(erro))
            log(f"   ↪ {especificacao} indisponível ({str(erro)[:90]}); tentando o próximo…"
                + (f" (descansa {pausa // 60} min)" if pausa else ""))
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
    for ferramenta in ("claude", "codex", "gemini"):
        ok = bool(_achar_cli(ferramenta))
        resultado.append((f"cli:{ferramenta}", ok, "instalado (assinatura)" if ok else "não instalado"))
    for provedor, nome_env in CHAVES.items():
        ok = bool(os.getenv(nome_env, "").strip())
        resultado.append((provedor, ok, f"{nome_env} {'configurada' if ok else 'ausente'}"))
    return resultado
