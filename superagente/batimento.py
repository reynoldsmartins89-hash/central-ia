"""Batimento: a Central toma a iniciativa e só fala quando há algo que valha a pena.

Adaptado da verificação proativa do OpenClaw. Lá, a cada 30 minutos (dentro de um horário ativo e
só quando a conversa está parada), o agente relê uma lista do que vigiar e responde NO_REPLY quando
não há nada. Aqui é igual, mais econômico e mais previsível:
  1. COLETORES leem o estado real (resgate do HD, assinaturas em descanso, agenda pausada,
     aprendizado esperando seu ✅) e produzem SINAIS — sem chamar IA nenhuma;
  2. cada sinal tem uma chave; sinal já avisado não é repetido (fica anotado por 3 dias);
  3. só quando há sinal NOVO, uma IA gratuita lê a sua lista (agentes/BATIMENTO.md), o diário
     de hoje e os sinais, e escreve UMA mensagem curta — ou [NADA], e então você não recebe nada;
  4. uma vez por noite, relê o diário do dia e propõe o que vale guardar para sempre (com ✅/❌).
Nunca executa ações: só observa e avisa. Estado: ~/.central-ia/batimento.json
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PASTA = Path.home() / ".central-ia"
ESTADO = PASTA / "batimento.json"
RAIZ = Path(__file__).resolve().parent.parent
LISTA = RAIZ / "agentes" / "BATIMENTO.md"
PASTA_RESGATE = Path(os.getenv("RESGATE_HD_PASTA", r"E:\Resgate-HD"))
INTERVALO = int(os.getenv("BATIMENTO_MINUTOS", "30")) * 60
HORARIO = os.getenv("BATIMENTO_HORARIO", "07-22")     # só avisa entre 7h e 22h
OCIOSO = 5 * 60                                       # espera a conversa ficar parada 5 min
GUARDA_AVISO = 3 * 86400                              # não repete o mesmo aviso por 3 dias
HORA_REVISAO_DO_DIA = 21
NADA = "[NADA]"
MODELOS = ["gemini:gemini-flash-lite-latest", "groq:openai/gpt-oss-120b", "gemini:gemini-3.5-flash",
           "ollama:qwen3:4b"]   # batimento é frequente: só IAs gratuitas, para não gastar as assinaturas
_trava = threading.Lock()


@dataclass
class Sinal:
    chave: str       # identifica o fato; a mesma chave nunca é avisada duas vezes em 3 dias
    texto: str
    urgente: bool = False


# ------------------------------------------------------------------ coletores (sem IA)
def _resgate_hd() -> list[Sinal]:
    mapa = PASTA_RESGATE / "DISCO1.img.mapa.json"
    if not mapa.exists():
        return []
    sinais = []
    relatorio = PASTA_RESGATE / "DISCO1-relatorio.txt"
    texto = relatorio.read_text(encoding="utf-8", errors="replace") if relatorio.exists() else ""
    if re.search(r"Clone terminado:\s*sim", texto, re.IGNORECASE):
        sinais.append(Sinal("resgate-concluido", "O resgate do HD de 1 TB (DISCO1) TERMINOU: clone e extração "
                            f"finais prontos em {PASTA_RESGATE}\\DISCO1. Lembrar o Reynolds do teste do Hermes "
                            "isolado na Oracle (ele pediu).", urgente=True))
        return sinais
    parado = time.time() - mapa.stat().st_mtime
    if parado > 3 * 3600:
        sinais.append(Sinal(f"resgate-parado-{int(mapa.stat().st_mtime)}",
                            f"O resgate do HD de 1 TB não avança há {parado / 3600:.0f} h (o mapa não muda). "
                            "Pode ser o HD desligado, o PC dormindo ou o cabo USB.", urgente=True))
    try:
        dados = json.loads(mapa.read_text(encoding="utf-8"))
        aguardando = re.search(r"aguardando o clone:\s*(\d+)", texto)
        fase = "2ª passada" if dados.get("pulados") or dados.get("pulados_prox") else "passada final"
        if aguardando:   # um aviso a cada 10 mil arquivos liberados, não a cada batida
            n = int(aguardando.group(1))
            sinais.append(Sinal(f"resgate-marco-{fase}-{n // 10000}", f"Resgate do HD em andamento ({fase}); "
                                f"{n} arquivos ainda aguardam o clone."))
    except (OSError, ValueError):
        pass
    return sinais


def _assinaturas() -> list[Sinal]:
    from . import provedores
    sinais = []
    for chave, ate in provedores._descansos().items():
        sinais.append(Sinal(f"descanso-{chave}-{int(ate) // 3600}",
                            f"A assinatura {chave} bateu no limite ou caiu; descansa até {datetime.fromtimestamp(ate):%H:%M}. "
                            "As IAs gratuitas estão cobrindo enquanto isso."))
    return sinais


def _agenda() -> list[Sinal]:
    from . import agendador
    return [Sinal(f"agenda-pausada-{t['id']}-{t.get('falhas', 0)}",
                  f"A tarefa agendada nº {t['id']} («{t['pedido'][:80]}») foi pausada depois de falhas seguidas.")
            for t in agendador.listar() if not t.get("ativa") and t.get("falhas", 0) >= agendador.LIMITE_FALHAS]


def _aprendizado() -> list[Sinal]:
    from . import aprendizado
    antigas = [p for p in aprendizado.pendentes()
               if time.time() - _data(p.get("criado", "")) > 86400]
    if not antigas:
        return []
    return [Sinal(f"aprendizado-esperando-{len(antigas)}",
                  f"Há {len(antigas)} proposta(s) de aprendizado esperando o seu ✅ há mais de um dia "
                  "(é só perguntar 'o que você aprendeu?').")]


def _data(texto: str) -> float:
    try:
        return datetime.strptime(texto, "%d/%m/%Y %H:%M").timestamp()
    except ValueError:
        return time.time()


COLETORES = [_resgate_hd, _assinaturas, _agenda, _aprendizado]


def coletar(coletores=None) -> list[Sinal]:
    sinais = []
    for coletor in coletores or COLETORES:
        try:
            sinais += coletor()
        except Exception as erro:  # noqa: BLE001 — um coletor quebrado não para os outros
            print(f"(batimento: {coletor.__name__} falhou: {erro})")
    return sinais


# ------------------------------------------------------------------ estado e regras de horário
def _ler() -> dict:
    try:
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _gravar(estado: dict) -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


def dentro_do_horario(agora: datetime | None = None, horario: str = HORARIO) -> bool:
    agora = agora or datetime.now()
    inicio, _, fim = horario.partition("-")
    return int(inicio) <= agora.hour < int(fim or 24)


def conversa_parada(minimo: int = OCIOSO) -> bool:
    try:
        from . import memoria
        return time.time() - memoria._conversa().get("atualizado", 0) > minimo
    except Exception:  # noqa: BLE001
        return True


def novos(sinais: list[Sinal], estado: dict, agora: float | None = None) -> list[Sinal]:
    agora = agora or time.time()
    avisados = {k: v for k, v in estado.get("avisados", {}).items() if agora - v < GUARDA_AVISO}
    return [s for s in sinais if s.chave not in avisados]


# ------------------------------------------------------------------ a batida
PEDIDO = """Você é a Central de IA do Reynolds fazendo a VERIFICAÇÃO PROATIVA (ninguém pediu nada agora).
Leia a lista de vigia dele, o diário de hoje e os SINAIS NOVOS. Escreva UMA mensagem curta para o Telegram
(no máximo 6 linhas, português direto, sem saudação longa) só com o que ele precisa saber ou decidir.
Se nada disso merecer incomodá-lo agora, responda apenas {nada}.
Você não executa ações: no máximo sugere ("quer que eu…?").

## Lista de vigia do Reynolds
{lista}

{diario}

## Sinais novos
{sinais}"""


def bater(agora: datetime | None = None, enviar=None, chamar=None, coletores=None, forcar: bool = False) -> str:
    """Uma batida. Devolve o que foi enviado ('' se nada). `chamar(pedido) -> texto` permite testar offline."""
    agora = agora or datetime.now()
    with _trava:
        estado = _ler()
        if not forcar and not dentro_do_horario(agora):
            return ""
        sinais = coletar(coletores)
        frescos = novos(sinais, estado, agora.timestamp())
        urgentes = [s for s in frescos if s.urgente]
        if not frescos or (not forcar and not urgentes and not conversa_parada()):
            return ""
        from . import diario
        lista = LISTA.read_text(encoding="utf-8") if LISTA.exists() else "(sem lista)"
        pedido = PEDIDO.format(nada=NADA, lista=lista, diario=diario.contexto(agora.date()),
                               sinais="\n".join(f"- {'[URGENTE] ' if s.urgente else ''}{s.texto}" for s in frescos))
        if chamar is None:
            from . import provedores
            _, resposta = provedores.chamar_com_reserva(MODELOS, "Você avisa só o que importa.", pedido,
                                                        log=lambda *_: None)
        else:
            resposta = chamar(pedido)
        resposta = resposta.strip()
        avisados = estado.setdefault("avisados", {})
        for s in frescos:   # avisado ou julgado irrelevante: não volta a incomodar por 3 dias
            avisados[s.chave] = agora.timestamp()
        estado["ultima"] = agora.timestamp()
        _gravar(estado)
    if not resposta or resposta.startswith(NADA) or NADA in resposta[:40]:
        return ""
    if enviar:
        enviar(f"🔔 {resposta}")
    diario.registrar(f"Aviso proativo: {resposta}", "batimento", agora)
    return resposta


def revisar_o_dia(agora: datetime | None = None, enviar=None, chamar=None) -> int:
    """Uma vez por noite: o que o diário de hoje tem de durável vira proposta (o 'dreaming' do OpenClaw)."""
    agora = agora or datetime.now()
    with _trava:
        estado = _ler()
        if agora.hour < HORA_REVISAO_DO_DIA or estado.get("dia_revisado") == agora.date().isoformat():
            return 0
        estado["dia_revisado"] = agora.date().isoformat()
        _gravar(estado)
    from . import aprendizado, diario
    texto = diario.ler(agora.date())
    if texto.count("\n- ") < 2:
        return 0
    novas = aprendizado.revisar(texto, "diário do dia", chamar=chamar)
    if novas and enviar:
        aprendizado.avisar_com_botoes(novas, enviar)
    return len(novas)


def em_segundo_plano(enviar, intervalo: int = INTERVALO) -> threading.Thread:
    def coracao():
        time.sleep(120)   # deixa o bot terminar de ligar
        while True:
            try:
                from . import sessoes
                sessoes.importar_claude_code()   # o que foi feito no app do Claude entra na lembrança
            except Exception as erro:  # noqa: BLE001
                print(f"(lembranças do Claude: {erro})")
            try:
                bater(enviar=enviar)
                revisar_o_dia(enviar=enviar)
            except Exception as erro:  # noqa: BLE001
                print(f"(batimento: {erro})")
            time.sleep(intervalo)
    t = threading.Thread(target=coracao, daemon=True, name="batimento")
    t.start()
    return t


def estado_legivel() -> str:
    estado = _ler()
    ultima = estado.get("ultima")
    sinais = coletar()
    frescos = novos(sinais, estado)
    return "\n".join([
        f"💓 Batimento a cada {INTERVALO // 60} min, das {HORARIO.replace('-', 'h às ')}h.",
        f"Última batida com aviso: {datetime.fromtimestamp(ultima):%d/%m %H:%M}" if ultima else "Ainda não avisou nada.",
        f"Sinais agora: {len(sinais)} ({len(frescos)} novos)",
        *(f"- {'🔴 ' if s.urgente else ''}{s.texto}" for s in sinais),
    ])
