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

🖐 EXECUTAR NO SEU PC (de qualquer lugar)
• /pc <ordem> — o Agente Executor faz de verdade: arquivos, pastas, programas,
   scripts, HDs, Drive e as ferramentas da Central. Mostra o plano → seu ✅ → executa → relatório
   ex.: /pc mova os PDFs da Área de Trabalho para Documentos/PDFs
• /continuar <ordem> — segue na MESMA tarefa anterior do Executor (ele lembra o contexto)
• Ordens em texto livre ("organize…", "repare…", "mova…") também vão para o Executor

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
• /reparos — o que o Agente Reparador recuperou (sempre em cópias)

⚙️ SISTEMA
• /status — quais IAs estão funcionando agora
• /vigia — o que o Agente Vigia está mantendo ligado
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
    if comando == "/reparos":
        from . import reparador
        telegram.enviar(reparador.relatorio(exemplos=2)[:3900])
        return
    if comando == "/vigia":
        from . import vigia
        telegram.enviar(vigia.resumo())
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

    if comando in ("/pc", "/executar", "/continuar"):
        if not resto:
            telegram.enviar("Use: /pc sua ordem — ex.: /pc liste os 10 maiores arquivos do HD E:")
            return
        _executar_no_pc(resto, continuar=comando == "/continuar")
        return
    if not comando.startswith("/"):
        from . import executor
        if executor.disponivel() and executor.classificar(texto) == "acao":
            _executar_no_pc(texto)
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


def _executar_no_pc(ordem: str, continuar: bool = False) -> None:
    """Agente Executor: plano (só lendo) → seu ✅ → execução real → relatório."""
    from . import executor
    if not executor.disponivel():
        telegram.enviar("⚠️ O Agente Executor precisa do Claude Code instalado e logado neste PC.")
        return
    telegram.enviar("🖐 Agente Executor: entendendo a ordem e conferindo o PC (só lendo, nada muda ainda)…")
    plano, sessao = executor.planejar(ordem)
    if executor.sem_acao(plano):
        telegram.enviar(plano[:3900])
        return
    if not telegram.pedir_aprovacao(f"🖐 PLANO DO EXECUTOR\n\n{plano}"[:3800]):
        telegram.enviar("❌ Cancelado. Nada foi alterado no seu PC.")
        return
    telegram.enviar("▶️ Executando… vou mandando o andamento.")
    relatorio = executor.executar(ordem, plano, sessao=executor.ultima_sessao() if continuar else sessao,
                                  progresso=telegram.enviar)
    for i in range(0, len(relatorio), 3900):
        telegram.enviar(("📋 RELATÓRIO DO EXECUTOR\n\n" if i == 0 else "") + relatorio[i:i + 3900])


JANELA_PENDENTES = 24 * 3600   # o Telegram guarda mensagens não lidas por 24 h


def _pendentes_ao_ligar(meu_chat: str) -> int:
    """Ao ligar: avisa quais ordens chegaram com o PC desligado e devolve o ponto de partida para atendê-las."""
    try:
        atualizacoes = telegram._api("getUpdates", {"timeout": 0}).get("result", [])
    except Exception:  # noqa: BLE001
        atualizacoes = []
    agora = time.time()
    pendentes = [u for u in atualizacoes if str((u.get("message") or {}).get("chat", {}).get("id")) == meu_chat
                 and (u["message"].get("text") or "").strip()
                 and agora - u["message"].get("date", 0) <= JANELA_PENDENTES]
    if not pendentes:
        telegram.enviar("🟢 Central de IA online. Mande sua tarefa! (/ajuda para exemplos)")
        return max((u["update_id"] for u in atualizacoes), default=0) + 1
    lista = "\n".join(f"{i}. {u['message']['text'][:120]}" for i, u in enumerate(pendentes, 1))
    telegram.enviar(f"🟢 Central de IA online.\n📬 Enquanto o PC estava desligado você mandou "
                    f"{len(pendentes)} ordem(ns). Vou atender agora, na ordem:\n{lista}")
    return pendentes[0]["update_id"]


def rodar() -> None:
    if not telegram.configurado():
        raise SystemExit("Telegram não configurado. Use o menu → 'Conectar o Telegram'.")
    from .vigia import travar
    if not travar("bot"):
        print("🟢 O bot já está rodando (o Vigia mantém ele ligado). Nada a fazer.")
        return
    meu_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    deslocamento = _pendentes_ao_ligar(meu_chat)   # ordens enviadas com o PC desligado (até 24 h)
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
            if time.time() - msg.get("date", time.time()) > JANELA_PENDENTES:
                continue  # velha demais: o Telegram já teria descartado
            print(f"\n📩 {msg['text']}")
            try:  # confirma ANTES de atender: se algo travar, a mesma ordem não se repete em loop
                telegram._api("getUpdates", {"offset": deslocamento, "timeout": 0, "limit": 1})
            except Exception:  # noqa: BLE001
                pass
            try:
                tratar(msg["text"].strip())
            except PlanoCancelado:
                pass
            except Exception as erro:  # noqa: BLE001 — o bot nunca deve cair
                traceback.print_exc()
                telegram.enviar(f"⚠️ Deu um erro: {str(erro)[:500]}\nTente de novo ou mande /status.")
