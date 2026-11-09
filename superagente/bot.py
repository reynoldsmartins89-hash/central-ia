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

from . import caixa_postal, memoria, provedores, telegram
from .agentes import carregar_equipe
from .orquestrador import PASTA_FLUXOS, PlanoCancelado, Superagente, formatar_plano

COMANDOS = """🧠 CENTRAL DE IA — COMANDOS DO SECRETÁRIO

💬 CONVERSAR (como no ChatGPT, mas com o SEU acervo)
• Escreva, mande ÁUDIO 🎙, FOTO 🖼 ou DOCUMENTO 📄 — a Central entende os quatro
• Ela LEMBRA a conversa: "aprofunde o item 3", "e sobre isso?" funcionam
• Perguntas e conversa → resposta rápida pelo especialista certo (com o seu acervo)
• /tarefa <pedido> — força a EQUIPE completa (plano + seu ✅), para trabalhos grandes
• /nova — começa uma conversa nova (a anterior fica arquivada)
• /lembrar <fato> — algo que TODOS os agentes devem saber sempre sobre você
• /memoria — o que está guardado · /esquecer <nº> — apaga um fato

🖐 EXECUTAR NO SEU PC (de qualquer lugar)
• /pc <ordem> — o Agente Executor faz de verdade: arquivos, pastas, programas,
   scripts, HDs, Drive e as ferramentas da Central. Mostra o plano → seu ✅ → executa → relatório
   ex.: /pc mova os PDFs da Área de Trabalho para Documentos/PDFs
• /continuar <ordem> — segue na MESMA tarefa anterior do Executor (ele lembra o contexto)
• Ordens em texto livre ("organize…", "repare…", "mova…") também vão para o Executor

✍️ PEDIR TRABALHO
• Pedidos de produção ("escreva um artigo…", "monte um estudo completo…") — o superagente
   monta a equipe e executa, após sua aprovação
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

    if caixa_postal.papel() == "nuvem" and comando in SO_NO_PC:
        raise caixa_postal.PrecisaDoPC(comando)
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
        conversar(resto, especialista=False)
        return
    if comando == "/nova":
        n = memoria.nova()
        telegram.enviar(f"🆕 Conversa nova! ({n} mensagens anteriores arquivadas.) "
                        "O que você sempre pediu para eu lembrar continua valendo (/memoria).")
        return
    if comando == "/lembrar":
        if not resto:
            telegram.enviar("Use: /lembrar <fato> — ex.: /lembrar Escrevo para empresários do agronegócio")
            return
        n = memoria.lembrar(resto)
        telegram.enviar(f"📌 Guardado (nº {n}). Todos os agentes vão levar isso em conta, sempre.")
        return
    if comando == "/memoria":
        telegram.enviar(memoria.resumo())
        return
    if comando == "/esquecer":
        removido = memoria.esquecer(int(resto)) if resto.isdigit() else None
        telegram.enviar(f"🗑 Esqueci: {removido}" if removido else "Use: /esquecer <número> (veja em /memoria)")
        return

    if comando in ("/pc", "/executar", "/continuar"):
        if not resto:
            telegram.enviar("Use: /pc sua ordem — ex.: /pc liste os 10 maiores arquivos do HD E:")
            return
        _executar_no_pc(resto, continuar=comando == "/continuar")
        return

    sa = Superagente(aprovar=_aprovar_se_preciso, ao_vivo=True, log=print)
    if comando == "/tarefa":
        if not resto:
            telegram.enviar("Use: /tarefa <pedido> — a equipe completa trabalha nele")
            return
        _tarefa_em_equipe(sa, resto)
        return
    if not comando.startswith("/"):
        from . import executor
        modo = modo_da_mensagem(texto)
        if modo == "acao" and caixa_postal.papel() == "nuvem" and executor.classificar(texto) == "acao":
            raise caixa_postal.PrecisaDoPC("ordem para o PC")
        if modo == "acao" and executor.disponivel() and executor.classificar(texto) == "acao":
            _executar_no_pc(texto)
        elif modo == "tarefa":
            _tarefa_em_equipe(sa, texto)
        else:
            conversar(texto)
        return
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

    _tarefa_em_equipe(sa, texto)


# comandos que dependem do PC (Executor e catálogo dos HDs): na nuvem, ficam guardados para o PC
SO_NO_PC = {"/pc", "/executar", "/continuar", "/buscar", "/curador", "/duplicados", "/integridade",
            "/reparos", "/leitura", "/vigia"}


# ------------------------------------------------------------------ modos: conversa, tarefa, ação
PEDIDOS_DE_PRODUCAO = ("artigo", "post", "posts", "carrossel", "roteiro", "dossiê", "dossie", "relatório",
                       "relatorio", "estudo completo", "pesquisa completa", "e-book", "ebook", "newsletter",
                       "plano de ação", "plano de negócio", "plano de estudos", "apresentação", "campanha",
                       "produza", "elabore", "redija", "monte um", "monte uma", "desenvolva um", "desenvolva uma",
                       "escreva um", "escreva uma", "crie um", "crie uma")


def modo_da_mensagem(texto: str) -> str:
    """'acao' (mexer no PC), 'tarefa' (produção em equipe) ou 'conversa' (resposta rápida)."""
    from . import executor
    t = f" {texto.lower()} "
    if executor.parece_acao(texto) and not any(p in t for p in PEDIDOS_DE_PRODUCAO):
        return "acao"
    if any(p in t for p in PEDIDOS_DE_PRODUCAO):
        return "tarefa"
    return "conversa"


def _escolher_especialista(texto: str):
    """A IA rápida lê o que cada especialista faz e escolhe UM (ou o generalista). ~1 s."""
    _, equipe = carregar_equipe()
    candidatos = {n: a for n, a in equipe.items() if a.base or n in ("generalista", "orientador_linkedin",
                                                                       "orientador_github", "poliglota")}
    lista = "\n".join(f"- {n}: {a.descricao}" for n, a in candidatos.items())
    try:
        _, escolha = provedores.chamar_com_reserva(
            ["groq:openai/gpt-oss-120b", "gemini:gemini-flash-lite-latest"],
            "Você distribui perguntas entre especialistas. Responda SOMENTE com o nome exato de UM agente da "
            "lista. Escolha um especialista só se o assunto for claramente da área dele; na dúvida, 'generalista'.",
            f"Agentes:\n{lista}\n\nMensagem do usuário:\n{texto[-1500:]}", log=lambda *_: None)
        nome = escolha.strip().strip("`*'\". ").split()[0].lower() if escolha.strip() else ""
        return candidatos.get(nome, equipe["generalista"])
    except Exception:  # noqa: BLE001
        return equipe["generalista"]


def _digitando() -> None:
    try:
        telegram._api("sendChatAction", {"chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(), "action": "typing"})
    except Exception:  # noqa: BLE001
        pass


def _enviar_longo(texto: str) -> None:
    for i in range(0, len(texto), 3800):
        telegram.enviar(texto[i:i + 3800])


def conversar(texto: str, especialista: bool = True) -> str:
    """Modo conversa: resposta direta e rápida, com memória e com o acervo do especialista certo."""
    _digitando()
    anterior = memoria.ultima_fala_sua()
    agente = _escolher_especialista(f"{anterior} {texto}") if especialista else carregar_equipe()[1]["generalista"]
    contexto = memoria.contexto()
    tarefa = (f"{contexto}\n\n## Mensagem atual do usuário\n{texto}\n\n"
              "Responda à MENSAGEM ATUAL em tom de conversa, direto ao ponto (como num chat), usando a "
              "conversa acima para entender referências. Seja completo mas enxuto; se o assunto pedir um "
              "trabalho grande (artigo, estudo completo), responda o essencial e sugira usar /tarefa.")
    memoria.adicionar("voce", texto)
    modelo, resposta = agente.executar(tarefa, log=lambda *_: None, consulta=f"{anterior} {texto}")
    memoria.adicionar("central", resposta)
    rotulo = "" if agente.nome == "generalista" else f"📚 {agente.nome} · "
    if caixa_postal.papel() == "nuvem":
        rotulo = f"☁️ {rotulo}"
    _enviar_longo(f"{rotulo}{modelo.split(':')[0]}\n\n{resposta}")
    return resposta


def _tarefa_em_equipe(sa: Superagente, texto: str) -> None:
    telegram.enviar("🧠 Recebido! O superagente está montando o plano…")
    contexto = memoria.contexto()
    memoria.adicionar("voce", texto)
    pasta = sa.executar(texto, historico=contexto)
    try:   # guarda o resultado final na memória, para você poder continuar a conversa sobre ele
        import json
        registro = json.loads((pasta / "registro.json").read_text(encoding="utf-8"))
        final = (pasta / registro["etapas"][-1]["arquivo"]).read_text(encoding="utf-8")
        memoria.adicionar("central", f"[Resultado da equipe — completo em {pasta}]\n{final}")
    except Exception:  # noqa: BLE001
        pass


def _executar_no_pc(ordem: str, continuar: bool = False) -> None:
    """Agente Executor: plano (só lendo) → seu ✅ → execução real → relatório."""
    from . import executor
    if caixa_postal.papel() == "nuvem":
        raise caixa_postal.PrecisaDoPC("ordem para o PC")
    if not executor.disponivel():
        telegram.enviar("⚠️ O Agente Executor precisa do Claude Code instalado e logado neste PC.")
        return
    telegram.enviar("🖐 Agente Executor: entendendo a ordem e conferindo o PC (só lendo, nada muda ainda)…")
    historico = memoria.historico(4000)
    memoria.adicionar("voce", ordem)
    plano, sessao = executor.planejar(f"{ordem}\n\n(Conversa recente, para entender referências:\n{historico})"
                                      if historico else ordem)
    if executor.sem_acao(plano):
        memoria.adicionar("central", plano)
        _enviar_longo(plano)
        return
    if not telegram.pedir_aprovacao(f"🖐 PLANO DO EXECUTOR\n\n{plano}"[:3800]):
        telegram.enviar("❌ Cancelado. Nada foi alterado no seu PC.")
        return
    telegram.enviar("▶️ Executando… vou mandando o andamento.")
    relatorio = executor.executar(ordem, plano, sessao=executor.ultima_sessao() if continuar else sessao,
                                  progresso=telegram.enviar)
    memoria.adicionar("central", f"[Relatório do Executor]\n{relatorio}")
    _enviar_longo(f"📋 RELATÓRIO DO EXECUTOR\n\n{relatorio}")


# Ordens com até 72 h são atendidas. ATENÇÃO: o próprio Telegram só guarda mensagens de bot por 24 h;
# para valer 72 h de verdade, o bot precisa de uma "caixa postal" sempre ligada (servidor na nuvem).
JANELA_PENDENTES = int(os.getenv("JANELA_PENDENTES_HORAS", "72")) * 3600


TIPOS_ACEITOS = ("voice", "audio", "photo", "document", "video_note", "video")


def _tem_conteudo(msg: dict) -> bool:
    return bool((msg.get("text") or "").strip() or any(msg.get(k) for k in TIPOS_ACEITOS))


def _pendentes_ao_ligar(meu_chat: str) -> int:
    """Ao ligar: avisa quais ordens chegaram com o PC desligado e devolve o ponto de partida para atendê-las."""
    papel = caixa_postal.papel()
    if papel == "nuvem":
        print("☁️ Bot da nuvem: atende quando o PC estiver desligado.")
        return 0
    try:
        if papel == "pc_com_caixa":
            for passo in (caixa_postal.baixar_memoria, caixa_postal.trazer_resultados):
                try:
                    passo()
                except Exception as erro:  # noqa: BLE001
                    print(f"(sincronização com o servidor falhou: {erro})")
            atualizacoes = caixa_postal.remoto_espiar()
        else:
            atualizacoes = telegram._api("getUpdates", {"timeout": 0}).get("result", [])
    except Exception:  # noqa: BLE001
        atualizacoes = []
    agora = time.time()
    pendentes = [u for u in atualizacoes if str((u.get("message") or {}).get("chat", {}).get("id")) == meu_chat
                 and _tem_conteudo(u["message"]) and agora - u["message"].get("date", 0) <= JANELA_PENDENTES]
    if not pendentes:
        telegram.enviar("🟢 Central de IA online. Mande sua tarefa! (/ajuda para exemplos)")
        return max((u["update_id"] for u in atualizacoes), default=0) + 1
    lista = "\n".join(f"{i}. {(u['message'].get('text') or u['message'].get('caption') or '[áudio/foto/documento]')[:120]}"
                      for i, u in enumerate(pendentes, 1))
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
    papel = caixa_postal.papel()
    ultima_busca = time.time()
    while True:
        try:
            if papel == "pc_com_caixa" and time.time() - ultima_busca > caixa_postal.PC_AUSENTE_APOS:
                caixa_postal.baixar_memoria()   # o PC dormiu: a nuvem pode ter conversado nesse meio-tempo
            r = telegram.atualizacoes(deslocamento, 50 if papel == "sozinho" else 25)
            ultima_busca = time.time()
        except KeyboardInterrupt:
            raise
        except Exception as erro:  # sem internet, etc.: espera e tenta de novo
            print(f"(sem conexão com o Telegram/servidor: {erro}; tentando de novo em 10s)")
            time.sleep(10)
            continue
        for u in r:
            deslocamento = u["update_id"] + 1
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != meu_chat:
                continue  # ignora outras pessoas
            if not _tem_conteudo(msg):
                if msg:   # figurinha, localização, contato…: avisa em vez de ficar em silêncio
                    telegram.enviar("🤔 Esse tipo de mensagem eu ainda não entendo. Mande texto, áudio, "
                                    "foto, vídeo ou documento.")
                continue
            if time.time() - msg.get("date", time.time()) > JANELA_PENDENTES:
                continue  # velha demais: o Telegram já teria descartado
            print(f"\n📩 {msg.get('text') or msg.get('caption') or '[áudio/foto/documento]'}")
            if papel == "sozinho":
                try:  # confirma ANTES de atender: se algo travar, a mesma ordem não se repete em loop
                    telegram._api("getUpdates", {"offset": deslocamento, "timeout": 0, "limit": 1})
                except Exception:  # noqa: BLE001
                    pass
            try:
                from . import midia
                texto = midia.entrada(msg, avisar=telegram.enviar)
                if not texto:
                    continue
                if msg.get("photo") or msg.get("document"):
                    # foto/documento: quem decide o que fazer é a SUA legenda, não o conteúdo do arquivo
                    if msg.get("caption") and modo_da_mensagem(msg["caption"]) == "tarefa":
                        _tarefa_em_equipe(Superagente(aprovar=_aprovar_se_preciso, ao_vivo=True, log=print), texto)
                    elif msg.get("caption") and modo_da_mensagem(msg["caption"]) == "acao":
                        _executar_no_pc(texto)
                    else:
                        conversar(texto)
                    continue
                tratar(texto)
            except PlanoCancelado:
                pass
            except caixa_postal.PrecisaDoPC:
                caixa_postal.guardar_para_o_pc(u["update_id"])
                telegram.enviar("📬 Isso precisa do seu PC (arquivos, HDs ou agente privado). Guardei a ordem: "
                                "assim que o PC ligar, ele faz e te responde aqui (vale por 72 h).")
            except Exception as erro:  # noqa: BLE001 — o bot nunca deve cair
                traceback.print_exc()
                _avisar_erro(erro)
            finally:
                if papel == "pc_com_caixa":   # a nuvem continua a conversa se o PC desligar
                    import threading
                    threading.Thread(target=caixa_postal.enviar_memoria, daemon=True).start()


def _avisar_erro(erro: Exception) -> None:
    telegram.enviar(f"⚠️ Deu um erro: {str(erro)[:500]}\nTente de novo ou mande /status.")
