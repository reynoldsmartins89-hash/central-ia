"""Bot do Telegram: o seu ÚNICO ponto de comando.

Você escreve no Telegram → o superagente escolhe os agentes e as IAs → você
acompanha cada etapa → recebe o resultado (e a nota no Obsidian).

Só responde ao SEU chat (TELEGRAM_CHAT_ID); mensagens de outras pessoas são ignoradas.
Iniciar:  python main.py bot   (ou dois cliques em BOT-TELEGRAM.bat)
"""
from __future__ import annotations

import os
import time
import traceback

from . import provedores, telegram
from .agentes import carregar_equipe
from .orquestrador import PASTA_FLUXOS, PlanoCancelado, Superagente, formatar_plano

COMANDOS = """🧠 CENTRAL DE IA — COMANDOS DO SECRETÁRIO

✍️ PEDIR TRABALHO
• (texto livre) — o superagente monta a equipe e executa, após sua aprovação
   ex.: Analise o impacto da IA no agronegócio brasileiro para um artigo
• /conteudo <tema> — pacote editorial completo: dossiê, artigo-mãe, revisão,
   redes sociais, imagens, carrossel e roteiro de YouTube
• /rapido <pergunta> — resposta direta, sem plano nem aprovação

🗂 SECRETÁRIO (orientação, sem executar)
• /equipe <pedido> — quais agentes devem trabalhar juntos e por quê
• /agentes — a equipe completa e o que cada um faz

🗃 ARQUIVOS (Drive e HDs)
• /buscar <assunto> — onde estão seus arquivos sobre um assunto
• /curador — resumo do catálogo dos seus discos
• /duplicados — cópias idênticas encontradas e espaço recuperável
• /integridade — arquivos corrompidos ou incompletos
• /leitura — quanto do acervo já foi lido/transcrito pelo Agente Leitor

⚙️ SISTEMA
• /status — quais IAs estão funcionando agora
• /comandos — esta lista

🔒 Regra: nada é publicado, movido ou apagado sem o seu ✅ aqui no Telegram."""

AJUDA = COMANDOS


def _aprovar_se_preciso(plano) -> bool:
    if len(plano) <= 1:
        return True  # tarefa de uma etapa só: roda direto
    return telegram.pedir_aprovacao(formatar_plano(plano))


def _status() -> str:
    linhas = [f"{'✅' if ok else '⬜'} {nome}: {detalhe}" for nome, ok, detalhe in provedores.diagnostico()
              if ok or nome in ("gemini", "groq", "ollama") or nome.startswith("cli:")]
    return "Status das IAs:\n" + "\n".join(linhas)


def tratar(texto: str) -> None:
    comando, _, resto = texto.partition(" ")
    comando = comando.lower().split("@")[0]
    resto = resto.strip()

    if comando in ("/start", "/ajuda", "/help"):
        telegram.enviar(AJUDA)
        return
    if comando == "/agentes":
        _, equipe = carregar_equipe()
        telegram.enviar("👥 Equipe:\n" + "\n".join(f"• {n}: {a.descricao}" for n, a in equipe.items()))
        return
    if comando == "/status":
        telegram.enviar(_status())
        return
    if comando == "/equipe":
        if not resto:
            telegram.enviar("Use: /equipe descreva o que você quer fazer")
            return
        telegram.enviar("🗂 Analisando quais agentes deveriam trabalhar nisso…")
        plano = Superagente(log=print).planejar(resto)
        telegram.enviar(formatar_plano(plano, "🗂 Equipe recomendada")
                        + "\n\nNada foi executado. Para rodar, mande o pedido normalmente (sem /equipe).")
        return
    if comando == "/buscar":
        from . import curador
        achados = curador.buscar(resto)
        telegram.enviar(("🔎 Encontrei nos seus discos:\n" + "\n".join(
            f"• {c} ({t / 1e6:.1f} MB)" for c, t in achados)) if achados else
            "Nada encontrado no catálogo (os discos já foram catalogados? /curador)")
        return
    if comando == "/curador":
        from . import curador
        telegram.enviar("🗃 Catálogo do curador:\n" + curador.resumo())
        return
    if comando == "/duplicados":
        from . import duplicados
        telegram.enviar("🗂 Arquivos duplicados:\n" + duplicados.relatorio(5))
        return
    if comando == "/integridade":
        from . import integridade
        telegram.enviar("🩺 Integridade dos arquivos:\n" + integridade.relatorio(exemplos=3))
        return
    if comando == "/leitura":
        from . import leitor
        telegram.enviar(leitor.progresso())
        return
    if comando in ("/comandos", "/menu"):
        telegram.enviar(COMANDOS)
        return
    if comando == "/rapido":
        if not resto:
            telegram.enviar("Use: /rapido sua pergunta")
            return
        _, equipe = carregar_equipe()
        telegram.enviar("⚡ Pensando…")
        modelo, resposta = equipe["generalista"].executar(resto, log=lambda *_: None)
        telegram.enviar(f"[{modelo}]\n\n{resposta}")
        return

    sa = Superagente(aprovar=_aprovar_se_preciso, ao_vivo=True, log=print)
    if comando == "/conteudo":
        if not resto:
            telegram.enviar("Use: /conteudo tema do artigo")
            return
        telegram.enviar(f"📝 Iniciando produção editorial: {resto}")
        sa.aprovar = lambda plano: telegram.pedir_aprovacao(
            f"📋 Fluxo de produção editorial ({len(plano)} etapas):\n" + "\n".join(
                f"{i}. {p['agente']}" for i, p in enumerate(plano, 1)))
        sa.fluxo("producao-editorial", resto)
        return
    if comando.startswith("/") and (PASTA_FLUXOS / f"{comando[1:]}.toml").exists():
        sa.fluxo(comando[1:], resto)
        return

    telegram.enviar("🧠 Recebido! O superagente está montando o plano…")
    sa.executar(texto)


def rodar() -> None:
    if not telegram.configurado():
        raise SystemExit("Telegram não configurado. Use o menu → 'Conectar o Telegram'.")
    meu_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    deslocamento = telegram._ultimo_update() + 1  # ignora mensagens antigas
    telegram.enviar("🟢 Central de IA online. Mande sua tarefa! (/ajuda para exemplos)")
    print("🟢 Bot rodando. Deixe esta janela aberta (pode minimizar). Ctrl+C para parar.")
    while True:
        try:
            r = telegram._api("getUpdates", {"offset": deslocamento, "timeout": 50,
                                             "allowed_updates": ["message", "callback_query"]}, timeout=60)
        except KeyboardInterrupt:
            raise
        except Exception as erro:  # sem internet, etc.: espera e tenta de novo
            print(f"(sem conexão com o Telegram: {erro}; tentando de novo em 10s)")
            time.sleep(10)
            continue
        for u in r.get("result", []):
            deslocamento = u["update_id"] + 1
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != meu_chat or not msg.get("text"):
                continue  # ignora outras pessoas e mensagens sem texto
            print(f"\n📩 {msg['text']}")
            try:
                tratar(msg["text"].strip())
            except PlanoCancelado:
                pass
            except Exception as erro:  # noqa: BLE001 — o bot nunca deve cair
                traceback.print_exc()
                telegram.enviar(f"⚠️ Deu um erro: {str(erro)[:500]}\nTente de novo ou mande /status.")
