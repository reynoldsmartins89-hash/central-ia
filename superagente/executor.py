"""Agente Executor: as MÃOS da Central. Faz ações REAIS no seu PC, a partir do Telegram.

Por baixo usa o Claude Code (sua assinatura Claude), que sabe abrir/criar/mover arquivos,
rodar programas e scripts e usar todas as ferramentas da Central (Leitor, Curador, Reparador…).

Segurança, em 3 camadas:
  1. PLANO PRIMEIRO — o Executor investiga SÓ LENDO e devolve o plano; nada muda ainda.
  2. SEU ✅ NO TELEGRAM — só depois da sua aprovação ele executa (e só o seu chat é aceito).
  3. MODO AUTOMÁTICO COM REVISÃO — cada comando passa por um revisor de segurança do Claude Code,
     que bloqueia ações perigosas; e as regras abaixo proíbem apagar de vez, mexer em senhas etc.
Ao final você recebe um relatório do que foi feito e onde.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from .provedores import _achar_cli

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
PASTA = Path.home() / ".central-ia"
REGISTRO = PASTA / "executor.jsonl"
SESSAO = PASTA / "executor_sessao.txt"
TEMPO_MAXIMO = float(os.getenv("EXECUTOR_TEMPO_MAXIMO", 3600))
INTERVALO_PROGRESSO = 45   # segundos entre mensagens de andamento no Telegram

REGRAS = """Você é o AGENTE EXECUTOR da Central de IA do usuário (Windows, PC pessoal dele).
O usuário NÃO é programador e manda ordens pelo Telegram, muitas vezes do celular. Você EXECUTA de verdade.

Ferramentas próprias da Central (rode a partir de C:\\Users\\reyno\\Documents\\central-ia com `python main.py ...`):
leitor (ler), curador, duplicados, integridade, reparar, drive, indexar, vigia, perguntar/executar (agentes de texto).
Os comandos estão em COMANDOS.md. Prefira essas ferramentas a soluções improvisadas.
Discos e pastas conhecidos: E:\\ (HD externo), G:\\Meu Drive (Google Drive), Documentos, Área de Trabalho,
Obsidian em C:\\Users\\reyno\\Documents\\Obsidian Vault.

REGRAS INVIOLÁVEIS:
- Faça SOMENTE o que o pedido aprovado diz. Se surgir algo fora do combinado, pare e explique no relatório.
- NUNCA apague definitivamente: em vez de apagar, mova para uma pasta "PARA APAGAR - REVISAR" no mesmo disco
  ou para a Lixeira. Nunca esvazie a Lixeira.
- NUNCA mexa em senhas, chaves, tokens, .env, contas bancárias, configurações de segurança do Windows,
  antivírus ou firewall; nunca instale programas de fontes desconhecidas.
- NUNCA envie mensagens, e-mails, posts ou publique nada em nome do usuário; nunca faça compras.
- Antes de mudar ou mover arquivos importantes, confira que eles existem e anote de onde para onde.
- Comandos demorados (mais de 10 min): inicie em segundo plano e informe como acompanhar.
- Responda SEMPRE em português simples, sem jargão."""

PEDIDO_PLANO = """Ordem do usuário (recebida pelo Telegram):
«{pedido}»

Investigue SOMENTE LENDO (não altere nada) e responda com um PLANO curto em português simples:
1. O que você entendeu do pedido (1 linha).
2. Os passos que vai executar (no máximo 8), dizendo QUAIS arquivos/pastas/programas serão afetados.
3. Riscos ou o que não dá para fazer (se houver) e se algo pode ser desfeito.
Se o pedido for só uma pergunta que não exige ação, responda a pergunta e escreva no fim: SEM AÇÃO."""

PEDIDO_EXECUCAO = """Ordem do usuário: «{pedido}»

Plano APROVADO pelo usuário:
{plano}

Execute o plano agora. No final, escreva um RELATÓRIO curto em português simples:
✅ o que foi feito (com os caminhos), ⚠ o que não deu certo e por quê, e ↩ como desfazer, se for o caso."""


def disponivel() -> bool:
    return _achar_cli("claude") is not None


def _registrar(evento: dict) -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    with open(REGISTRO, "a", encoding="utf-8") as f:
        f.write(json.dumps({"quando": datetime.now().isoformat(timespec="seconds"), **evento},
                           ensure_ascii=False) + "\n")


def _diretorios_extras() -> list[str]:
    candidatos = [Path.home() / "Documents", Path.home() / "Desktop", Path.home() / "Downloads",
                  Path("E:/"), Path("G:/Meu Drive")]
    return [str(c) for c in candidatos if c.exists()]


def _descrever(ferramenta: str, entrada: dict) -> str:
    if ferramenta in ("Bash", "PowerShell"):
        return entrada.get("description") or entrada.get("command", "")[:120]
    for chave in ("file_path", "path", "pattern"):
        if entrada.get(chave):
            return f"{ferramenta}: {str(entrada[chave])[-90:]}"
    return ferramenta


def _rodar_claude(prompt: str, modo: str, progresso=None, retomar: str | None = None) -> tuple[str, str | None]:
    """Roda o Claude Code sem janela; devolve (texto final, id da sessão). `progresso(str)` recebe o andamento."""
    executavel = _achar_cli("claude")
    if not executavel:
        raise RuntimeError("Claude Code não está instalado (~/.central-ia/ferramentas/claude.cmd)")
    comando = [executavel, "-p", "--permission-mode", modo, "--output-format", "stream-json", "--verbose",
               "--append-system-prompt", REGRAS]
    for pasta in _diretorios_extras():
        comando += ["--add-dir", pasta]
    if retomar:
        comando += ["--resume", retomar]
    processo = subprocess.Popen(comando, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", cwd=RAIZ_PROJETO,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    processo.stdin.write(prompt)
    processo.stdin.close()
    cronometro = threading.Timer(TEMPO_MAXIMO, lambda: subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(processo.pid)], capture_output=True))
    cronometro.start()
    resultado, sessao, passos, ultimo_aviso, bruto = "", None, [], time.time(), []
    try:
        for linha in processo.stdout:
            try:
                evento = json.loads(linha)
            except json.JSONDecodeError:
                bruto.append(linha.strip())
                continue
            sessao = evento.get("session_id") or sessao
            if evento.get("type") == "assistant":
                for parte in evento["message"].get("content", []):
                    if parte.get("type") == "tool_use":
                        passos.append(_descrever(parte["name"], parte.get("input", {})))
            elif evento.get("type") == "result":
                resultado = evento.get("result") or ""
                negados = evento.get("permission_denials") or []
                if negados:
                    resultado += f"\n\n🛡 {len(negados)} ação(ões) bloqueada(s) pelo revisor de segurança."
            if progresso and passos and time.time() - ultimo_aviso > INTERVALO_PROGRESSO:
                ultimo_aviso = time.time()
                progresso("⚙️ " + " → ".join(passos[-3:])[:600])
        processo.wait()
    finally:
        cronometro.cancel()
    if not resultado.strip():
        detalhe = " | ".join(bruto[-3:])[:300] or f"código {processo.returncode}"
        raise RuntimeError(f"o Claude Code não terminou a tarefa ({detalhe})")
    return resultado.strip(), sessao


def planejar(pedido: str) -> tuple[str, str | None]:
    """Investiga sem alterar nada e devolve (plano, sessão)."""
    return _rodar_claude(PEDIDO_PLANO.format(pedido=pedido), "plan")


def executar(pedido: str, plano: str, sessao: str | None = None, progresso=None) -> str:
    _registrar({"etapa": "inicio", "pedido": pedido, "plano": plano})
    relatorio, sessao_nova = _rodar_claude(PEDIDO_EXECUCAO.format(pedido=pedido, plano=plano), "auto",
                                           progresso=progresso, retomar=sessao)
    if sessao_nova:
        SESSAO.write_text(sessao_nova, encoding="utf-8")
    _registrar({"etapa": "fim", "pedido": pedido, "relatorio": relatorio})
    return relatorio


def ultima_sessao() -> str | None:
    try:
        return SESSAO.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def sem_acao(plano: str) -> bool:
    return plano.rstrip().upper().endswith("SEM AÇÃO") or plano.rstrip().upper().endswith("SEM ACAO")


# ------------------------------------------------------------------ roteador: ação no PC × conteúdo
PALAVRAS_DE_ACAO = ("abra", "abrir", "execute", "executar", "rode ", "rodar", "instale", "instalar", "mova", "mover",
                    "copie", "copiar", "renomeie", "renomear", "apague", "apagar", "exclua", "excluir", "delete",
                    "organize", "organizar", "separe", "separar", "repare", "reparar", "conserte", "consertar",
                    "baixe", "baixar", "crie a pasta", "crie uma pasta", "compacte", "descompacte", "extraia",
                    "converta", "converter", "transcreva", "transcrever", "leia a pasta", "atualize", "atualizar",
                    "verifique o", "verifique a", "verifique se", "limpe", "limpar", "faça backup", "backup",
                    "no meu pc", "no meu computador", "no hd", "no drive", "na pasta", "o arquivo", "os arquivos")


def parece_acao(texto: str) -> bool:
    """Atalho barato antes de perguntar à IA: o pedido fala em agir no PC/arquivos?"""
    t = f" {texto.lower()} "
    return any(p in t for p in PALAVRAS_DE_ACAO)


def classificar(texto: str) -> str:
    """'acao' (mexer no PC/arquivos/programas) ou 'conteudo' (texto, análise, resposta)."""
    if not parece_acao(texto):
        return "conteudo"
    from .provedores import chamar_com_reserva
    try:
        _, resposta = chamar_com_reserva(
            ["groq:openai/gpt-oss-120b", "gemini:gemini-3.5-flash", "ollama:qwen3:4b"],
            "Classifique a mensagem. Responda só com uma palavra: ACAO se o usuário quer que algo seja FEITO "
            "no computador dele (arquivos, pastas, programas, discos, Drive, scripts, ferramentas da Central); "
            "CONTEUDO se quer um texto, análise, explicação, pesquisa ou resposta.", texto, log=lambda *_: None)
        return "acao" if "ACAO" in resposta.upper().replace("Ç", "C").replace("Ã", "A") else "conteudo"
    except Exception:  # noqa: BLE001 — sem IA para classificar: na dúvida, o Executor planeja (nada muda sem ✅)
        return "acao"
