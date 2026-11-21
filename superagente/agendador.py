"""Agenda da Central: tarefas recorrentes marcadas por conversa ("todo dia 07:00 | resuma…").

Adaptado do `cronjob` do Hermes Agent:
  • formatos: "em 30m", "em 2h", "a cada 2h", "todo dia 07:00", "seg,qua,sex 08:30",
    "2026-11-25 09:00" e cron de 5 campos ("0 7 * * 1-5");
  • cada execução começa do zero (sem a conversa do momento), só com o seu perfil e o caderno;
  • o resultado vai para o Telegram e fica em resultados/agenda/<nº>/;
  • se a resposta começar com [SILENCIO], nada é enviado (útil para "só me avise se…");
  • 3 falhas seguidas → a tarefa é pausada e você é avisado;
  • se o PC estava desligado na hora, roda uma vez quando ele voltar (até 24 h de atraso).
Regras de confiança: tarefa agendada NUNCA aciona o Executor (ações no PC) nem publica nada,
e não pode criar outras tarefas. Agenda: ~/.central-ia/agenda.json
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

PASTA = Path.home() / ".central-ia"
AGENDA = PASTA / "agenda.json"
RAIZ = Path(__file__).resolve().parent.parent
PASTA_SAIDA = RAIZ / "resultados" / "agenda"
LIMITE_FALHAS = 3
ATRASO_MAXIMO = 24 * 3600
SILENCIO = "[SILENCIO]"
DIAS = {"seg": 0, "ter": 1, "qua": 2, "qui": 3, "sex": 4, "sab": 5, "sáb": 5, "dom": 6}
_trava = threading.Lock()

REGRAS_AGENDADA = (
    "Esta é uma TAREFA AGENDADA da Central de IA, rodando sozinha no horário marcado pelo Reynolds. "
    "Entregue o resultado completo, direto, pronto para ler no celular. Você não pode executar ações no PC, "
    "publicar nada nem agendar outras tarefas. Se o pedido for do tipo 'só me avise se…' e não houver nada a "
    f"avisar, responda apenas {SILENCIO}."
)


# ------------------------------------------------------------------ interpretação do "quando"
def _hora(texto: str) -> tuple[int, int]:
    m = re.search(r"(\d{1,2})(?:[:h](\d{2}))?\s*(?:h\b)?", texto)
    if not m:
        raise ValueError("faltou o horário (ex.: 07:00)")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if h > 23 or mi > 59:
        raise ValueError("horário inválido")
    return h, mi


def _duracao(qtd: str, unidade: str) -> int:
    n = int(qtd)
    unidade = unidade.lower()
    if unidade.startswith("m"):
        return n * 60
    if unidade.startswith("h"):
        return n * 3600
    if unidade.startswith("d"):
        return n * 86400
    raise ValueError("unidade desconhecida (use m, h ou d)")


def _campo_cron(campo: str, minimo: int, maximo: int) -> set[int]:
    valores = set()
    for parte in campo.split(","):
        passo = 1
        if "/" in parte:
            parte, p = parte.split("/")
            passo = int(p)
        if parte in ("*", ""):
            ini, fim = minimo, maximo
        elif "-" in parte:
            ini, fim = map(int, parte.split("-"))
        else:
            ini = fim = int(parte)
        if ini < minimo or fim > maximo or ini > fim:
            raise ValueError(f"campo cron fora do intervalo: {campo}")
        valores.update(range(ini, fim + 1, passo))
    return valores


def interpretar(quando: str) -> dict:
    """Converte o texto em uma regra: {'tipo': 'uma_vez'|'intervalo'|'diario'|'semanal'|'cron', ...}."""
    q = quando.strip().lower()
    if m := re.fullmatch(r"(?:em|daqui a)\s+(\d+)\s*(m|min|minutos?|h|horas?|d|dias?)", q):
        return {"tipo": "uma_vez", "em": time.time() + _duracao(m.group(1), m.group(2))}
    if m := re.fullmatch(r"a cada\s+(\d+)\s*(m|min|minutos?|h|horas?|d|dias?)", q):
        segundos = _duracao(m.group(1), m.group(2))
        if segundos < 15 * 60:
            raise ValueError("o intervalo mínimo é de 15 minutos")
        return {"tipo": "intervalo", "segundos": segundos}
    if m := re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ t](\d{1,2}:\d{2})", q):
        return {"tipo": "uma_vez", "em": datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M").timestamp()}
    if re.fullmatch(r"[\d*/,\-]+(\s+[\d*/,\-]+){4}", q):
        campos = q.split()
        for campo, (mn, mx) in zip(campos, [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]):
            _campo_cron(campo, mn, mx)
        return {"tipo": "cron", "expressao": q}
    if re.match(r"(todo dia|todos os dias|diariamente|diário|diario)\b", q):
        h, mi = _hora(q)
        return {"tipo": "diario", "hora": h, "minuto": mi}
    if re.match(r"(dias úteis|dias uteis)\b", q):
        h, mi = _hora(q)
        return {"tipo": "semanal", "dias": [0, 1, 2, 3, 4], "hora": h, "minuto": mi}
    dias = sorted({DIAS[d[:3]] for d in re.findall(r"\b(seg|ter|qua|qui|sex|s[aá]b|dom)\w*", q)})
    if dias:
        h, mi = _hora(re.sub(r"\b(seg|ter|qua|qui|sex|s[aá]b|dom)\w*", "", q))
        return {"tipo": "semanal", "dias": dias, "hora": h, "minuto": mi}
    raise ValueError("não entendi o horário. Exemplos: 'todo dia 07:00', 'seg,qua 08:30', 'a cada 6h', "
                     "'em 30m', '2026-11-25 09:00', '0 7 * * 1-5'")


def proxima(regra: dict, depois: float) -> float | None:
    """Próximo horário (timestamp) estritamente depois de `depois`; None se não houver mais."""
    base = datetime.fromtimestamp(depois)
    tipo = regra["tipo"]
    if tipo == "uma_vez":
        return regra["em"] if regra["em"] > depois else None
    if tipo == "intervalo":
        return depois + regra["segundos"]
    if tipo in ("diario", "semanal"):
        dias = regra.get("dias", list(range(7)))
        alvo = base.replace(hour=regra["hora"], minute=regra["minuto"], second=0, microsecond=0)
        for i in range(8):
            candidato = alvo + timedelta(days=i)
            if candidato.timestamp() > depois and candidato.weekday() in dias:
                return candidato.timestamp()
        return None
    minutos, horas, dias_mes, meses, dias_sem = regra["expressao"].split()
    conj = [_campo_cron(minutos, 0, 59), _campo_cron(horas, 0, 23), _campo_cron(dias_mes, 1, 31),
            _campo_cron(meses, 1, 12), _campo_cron(dias_sem, 0, 6)]
    t = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limite = t + timedelta(days=366)
    while t < limite:
        if t.month not in conj[3] or t.day not in conj[2] or (t.weekday() + 1) % 7 not in conj[4]:
            t = (t + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if t.hour not in conj[1]:
            t = (t + timedelta(hours=1)).replace(minute=0)
            continue
        if t.minute in conj[0]:
            return t.timestamp()
        t += timedelta(minutes=1)
    return None


# ------------------------------------------------------------------ agenda
def _ler() -> list[dict]:
    try:
        return json.loads(AGENDA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _gravar(tarefas: list[dict]) -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    tmp = AGENDA.with_suffix(".tmp")
    tmp.write_text(json.dumps(tarefas, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(AGENDA)


def criar(quando: str, pedido: str, modo: str = "rapido", agora: float | None = None) -> dict:
    """modo: 'rapido' (uma IA responde) ou 'equipe' (o superagente monta a equipe, sem pedir aprovação)."""
    if not pedido.strip():
        raise ValueError("faltou o pedido")
    if modo not in ("rapido", "equipe", "lembrete"):
        raise ValueError("modo deve ser rapido, equipe ou lembrete")
    regra = interpretar(quando)
    agora = agora or time.time()
    prox = regra["em"] if regra["tipo"] == "uma_vez" else proxima(regra, agora)
    if prox is None or prox <= agora - 60:
        raise ValueError("esse horário já passou")
    with _trava:
        tarefas = _ler()
        tarefa = {"id": max((t["id"] for t in tarefas), default=0) + 1, "quando": quando, "regra": regra,
                  "pedido": pedido.strip(), "modo": modo, "proxima": prox, "ativa": True, "falhas": 0,
                  "criada": datetime.fromtimestamp(agora).strftime("%d/%m/%Y %H:%M"), "execucoes": 0}
        tarefas.append(tarefa)
        _gravar(tarefas)
    return tarefa


def listar() -> list[dict]:
    return _ler()


def remover(numero: int) -> bool:
    with _trava:
        tarefas = _ler()
        restantes = [t for t in tarefas if t["id"] != numero]
        _gravar(restantes)
    return len(restantes) != len(tarefas)


def pausar(numero: int, ativa: bool = False) -> bool:
    with _trava:
        tarefas = _ler()
        for t in tarefas:
            if t["id"] == numero:
                t["ativa"] = ativa
                if ativa:
                    t["falhas"] = 0
                    t["proxima"] = proxima(t["regra"], time.time()) or t["proxima"]
                _gravar(tarefas)
                return True
    return False


def formatar() -> str:
    tarefas = _ler()
    if not tarefas:
        return ("🗓 Nenhuma tarefa agendada.\nEx.: /agendar todo dia 07:00 | resuma as notícias de geopolítica "
                "que importam para o Brasil")
    linhas = ["🗓 Tarefas agendadas:"]
    for t in tarefas:
        prox = datetime.fromtimestamp(t["proxima"]).strftime("%d/%m %H:%M") if t.get("proxima") else "—"
        estado = "▶" if t["ativa"] else "⏸"
        linhas.append(f"{estado} {t['id']}. [{t['quando']}] {t['pedido'][:90]}\n   próxima: {prox} · "
                      f"modo {t['modo']} · {t['execucoes']} execuções")
    linhas.append("\n/desagendar <nº> apaga · /agenda pausar <nº> · /agenda retomar <nº>")
    return "\n".join(linhas)


# ------------------------------------------------------------------ execução
def _rodar_rapido(pedido: str) -> str:
    from . import memoria
    from .agentes import carregar_equipe
    generalista = carregar_equipe()[1]["generalista"]
    perfil = "\n".join(f"- {f}" for f in memoria.fatos() + memoria.caderno())
    tarefa = f"{REGRAS_AGENDADA}\n\n" + (f"## Sobre o Reynolds e o ambiente\n{perfil}\n\n" if perfil else "") + \
             f"## Pedido agendado\n{pedido}"
    _, resposta = generalista.executar(tarefa, log=lambda *_: None)
    return resposta


def _rodar_equipe(pedido: str) -> str:
    from .orquestrador import Superagente
    pasta = Superagente(aprovar=None, ao_vivo=False, log=lambda *_: None).executar(
        f"{pedido}\n\n({REGRAS_AGENDADA})")
    registro = json.loads((pasta / "registro.json").read_text(encoding="utf-8"))
    return (pasta / registro["etapas"][-1]["arquivo"]).read_text(encoding="utf-8")


def executar(tarefa: dict, enviar=None, rodar=None) -> str:
    """Roda uma tarefa agora. `rodar(pedido) -> texto` permite testar offline."""
    if rodar is None and tarefa["modo"] == "lembrete":   # lembrete simples: nenhuma IA é chamada
        rodar = lambda pedido: f"⏰ Lembrete: {pedido}"  # noqa: E731
    rodar = rodar or (_rodar_equipe if tarefa["modo"] == "equipe" else _rodar_rapido)
    resposta = rodar(tarefa["pedido"]).strip()
    pasta = PASTA_SAIDA / str(tarefa["id"])
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"{datetime.now():%Y-%m-%d_%H%M}.md").write_text(
        f"# {tarefa['pedido'][:120]}\n\n> Agendada: {tarefa['quando']}\n\n{resposta}\n", encoding="utf-8")
    if enviar and not resposta.startswith(SILENCIO):
        enviar(f"🗓 Tarefa agendada {tarefa['id']} — {tarefa['pedido'][:80]}\n\n{resposta}")
    if not resposta.startswith(SILENCIO):
        from . import diario
        diario.registrar(f"Agenda nº {tarefa['id']} ({tarefa['pedido'][:100]}): {resposta[:200]}", "agenda")
    return resposta


def tick(agora: float | None = None, enviar=None, rodar=None) -> int:
    """Roda as tarefas vencidas. Devolve quantas rodaram."""
    agora = agora or time.time()
    with _trava:
        vencidas = [t for t in _ler() if t["ativa"] and t.get("proxima") and t["proxima"] <= agora]
    rodadas = 0
    for t in vencidas:
        atrasada = agora - t["proxima"] > ATRASO_MAXIMO
        erro = None
        if not atrasada:
            try:
                executar(t, enviar, rodar)
                rodadas += 1
            except Exception as e:  # noqa: BLE001 — uma tarefa ruim não para a agenda
                erro = e
        with _trava:
            tarefas = _ler()
            for atual in tarefas:
                if atual["id"] != t["id"]:
                    continue
                if erro:
                    atual["falhas"] = atual.get("falhas", 0) + 1
                    if atual["falhas"] >= LIMITE_FALHAS:
                        atual["ativa"] = False
                        if enviar:
                            enviar(f"⏸ Pausei a tarefa agendada {atual['id']} depois de {LIMITE_FALHAS} falhas "
                                   f"seguidas ({str(erro)[:150]}). Para religar: /agenda retomar {atual['id']}")
                else:
                    atual["falhas"] = 0
                    atual["execucoes"] = atual.get("execucoes", 0) + (0 if atrasada else 1)
                atual["proxima"] = proxima(atual["regra"], agora)
                if atual["proxima"] is None:
                    atual["ativa"] = False
            _gravar(tarefas)
    return rodadas


def em_segundo_plano(enviar, intervalo: int = 60) -> threading.Thread:
    """Relógio da agenda: confere a cada minuto (ligado junto com o bot)."""
    def relogio():
        while True:
            try:
                tick(enviar=enviar)
            except Exception as erro:  # noqa: BLE001
                print(f"(agenda: {erro})")
            time.sleep(intervalo)
    t = threading.Thread(target=relogio, daemon=True, name="agenda")
    t.start()
    return t


# ------------------------------------------------------------------ pedido em linguagem natural
PISTAS = re.compile(
    r"\b(todo dia|todos os dias|diariamente|toda (manh[ãa]|tarde|noite|semana)|dias [úu]teis|"
    r"tod[oa]s? (as |os )?(segunda|ter[çc]a|quarta|quinta|sexta|s[áa]bado|domingo)s?|a cada \d+|"
    r"daqui a \d+|amanh[ãa] (de manh[ãa] |à noite |às |as )|hoje (às|as) \d|"
    r"(me )?(lembr[ae]|avis[ae])[- ]?(me )?.{0,40}\b(às|as) \d{1,2})", re.IGNORECASE)
PENDENTES_NATURAIS = PASTA / "agenda_pendente.json"

PEDIDO_EXTRAIR = """Hoje é {agora} ({dia_semana}). O Reynolds escreveu ao bot:
«{texto}»

Se for um pedido para fazer algo NUM HORÁRIO ou DE TEMPOS EM TEMPOS, converta para JSON:
{{"agendar": true, "quando": "<um destes formatos>", "pedido": "<o que fazer, sem o horário>",
  "modo": "lembrete|rapido|equipe"}}
Formatos aceitos para "quando" (escolha o formato e preencha com os dias e o horário QUE ELE DISSE):
  "em 30m" · "em 2h" · "a cada 6h" · "todo dia 07:00" · "seg,qua,sex 08:30" · "dias úteis 07:00" ·
  "AAAA-MM-DD HH:MM" (para uma vez só, ex.: amanhã às 9h → a data de amanhã 09:00)
modo: "lembrete" = só lembrar de algo (sem IA); "rapido" = uma IA pesquisa/escreve; "equipe" = trabalho grande.
Se NÃO for um pedido de agendamento (ex.: pergunta comum que só cita um horário), responda {{"agendar": false}}.
Responda SOMENTE o JSON."""
DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def parece_agendamento(texto: str) -> bool:
    return bool(PISTAS.search(texto or ""))


DIA_NO_TEXTO = {0: r"segunda|seg\b", 1: r"ter[çc]a|ter\b", 2: r"quarta|qua\b", 3: r"quinta|qui\b",
                4: r"sexta|sex\b", 5: r"s[áa]bado|s[áa]b\b", 6: r"domingo|dom\b"}


def coerente(texto: str, quando: str) -> bool:
    """Confere se a IA não inventou: o horário e os dias entendidos precisam aparecer no que você escreveu."""
    regra = interpretar(quando)
    t = (texto or "").lower()
    if regra["tipo"] == "semanal" and not re.search(r"dias [úu]teis|segunda a sexta", t):
        if any(not re.search(DIA_NO_TEXTO[d], t) for d in regra["dias"]):
            return False
    hora = regra.get("hora")
    if regra["tipo"] == "uma_vez" and re.search(r"\d{2}:\d{2}$", quando) and not re.search(r"\b(em|daqui a) \d", t):
        hora = int(quando[-5:-3])
    if hora is not None:
        numeros = {int(n) for n in re.findall(r"\b(\d{1,2})(?=\s*(?:h|:|hs|horas)|\b)", t)}
        tarde = bool(re.search(r"\b(da tarde|da noite|pm)\b", t))
        candidatos = numeros | ({n + 12 for n in numeros if n < 12} if tarde else set())
        if numeros and hora not in candidatos:
            return False
        minuto = regra.get("minuto", int(quando[-2:]) if regra["tipo"] == "uma_vez" and quando[-3:-2] == ":" else 0)
        ditos = {int(m) for m in re.findall(r"\b\d{1,2}\s*[:h]\s*(\d{2})\b", t)} | ({30} if "e meia" in t else set())
        if minuto and minuto not in ditos:
            return False
    return True


def extrair(texto: str, chamar=None, agora: datetime | None = None) -> dict | None:
    """Usa uma IA barata para transformar a frase em {quando, pedido, modo}. None se não for agendamento.
    Se o entendido não bater com o texto (horário ou dias), tenta mais uma vez; depois desiste."""
    agora = agora or datetime.now()
    pedido = PEDIDO_EXTRAIR.format(agora=agora.strftime("%Y-%m-%d %H:%M"), dia_semana=DIAS_SEMANA[agora.weekday()],
                                   texto=texto[:1500])
    for tentativa in range(2):
        if tentativa:
            pedido += ("\n\nATENÇÃO: a resposta anterior usou dias ou horário que ele NÃO disse. Use exatamente "
                       "os dias e o horário da mensagem dele; os formatos acima são só exemplos.")
        if chamar is None:
            from . import memoria, provedores
            _, resposta = provedores.chamar_com_reserva(memoria.MODELOS_RESUMO, "Você converte pedidos em JSON.",
                                                        pedido, log=lambda *_: None)
        else:
            resposta = chamar(pedido)
        inicio, fim = resposta.find("{"), resposta.rfind("}")
        if inicio < 0:
            return None
        dados = json.loads(resposta[inicio:fim + 1])
        if not dados.get("agendar") or not str(dados.get("pedido") or "").strip():
            return None
        quando = str(dados.get("quando") or "")
        interpretar(quando)   # ValueError se o formato vier errado
        if coerente(texto, quando):
            modo = dados.get("modo") if dados.get("modo") in ("lembrete", "rapido", "equipe") else "rapido"
            return {"quando": quando, "pedido": dados["pedido"].strip(), "modo": modo}
    return None


def propor_natural(texto: str, enviar, chamar=None) -> bool:
    """Se a mensagem pede algo com horário, mostra o entendido com ✅/❌. Devolve True se tratou a mensagem."""
    if not parece_agendamento(texto):
        return False
    try:
        dados = extrair(texto, chamar)
    except Exception:  # noqa: BLE001 — não entendi: a mensagem segue o caminho normal
        return False
    if not dados:
        return False
    codigo = str(int(time.time() * 1000))
    pendentes = {k: v for k, v in _ler_pendentes().items() if time.time() - v.get("ts", 0) < 7 * 86400}
    pendentes[codigo] = {**dados, "ts": time.time()}
    _gravar_pendentes(pendentes)
    prox = proxima(interpretar(dados["quando"]), time.time())
    quando_txt = datetime.fromtimestamp(prox).strftime("%d/%m %H:%M") if prox else "?"
    tipo = {"lembrete": "lembrete", "rapido": "uma IA prepara", "equipe": "a equipe completa prepara"}[dados["modo"]]
    enviar(f"🗓 Entendi assim:\n• O quê: {dados['pedido']}\n• Quando: {dados['quando']} (primeira vez {quando_txt})"
           f"\n• Como: {tipo}\n\nAgendo?", [("✅ Agendar", f"agd:sim:{codigo}"), ("❌ Não", f"agd:nao:{codigo}")])
    return True


def _ler_pendentes() -> dict:
    try:
        return json.loads(PENDENTES_NATURAIS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _gravar_pendentes(dados: dict) -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    PENDENTES_NATURAIS.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


def confirmar_natural(codigo: str, sim: bool) -> str:
    pendentes = _ler_pendentes()
    dados = pendentes.pop(codigo, None)
    _gravar_pendentes(pendentes)
    if not dados:
        return "Esse agendamento já tinha sido decidido."
    if not sim:
        return "Ok, não agendei nada."
    try:
        t = criar(dados["quando"], dados["pedido"], dados["modo"])
    except ValueError as erro:
        return f"⚠ Não consegui agendar: {erro}"
    return (f"🗓 Agendado (nº {t['id']}). Próxima vez: {datetime.fromtimestamp(t['proxima']):%d/%m/%Y %H:%M}. "
            "Para ver ou apagar, é só me pedir: \"o que está agendado?\"")


def comando_agendar(resto: str) -> str:
    """'/agendar <quando> | <pedido>' ou '/agendar equipe <quando> | <pedido>'."""
    modo = "rapido"
    if resto.lower().startswith("equipe "):
        modo, resto = "equipe", resto[7:]
    if "|" not in resto:
        return ("Use: /agendar <quando> | <pedido>\nEx.: /agendar todo dia 07:00 | 3 notícias de geopolítica "
                "que importam para o Brasil\nPara a equipe completa: /agendar equipe seg 08:00 | …")
    quando, pedido = (p.strip() for p in resto.split("|", 1))
    try:
        t = criar(quando, pedido, modo)
    except ValueError as erro:
        return f"⚠ {erro}"
    return (f"🗓 Agendado (nº {t['id']}): {t['pedido'][:100]}\nPróxima vez: "
            f"{datetime.fromtimestamp(t['proxima']):%d/%m/%Y %H:%M} · modo {modo}\n"
            "Nunca aciona o Executor nem publica nada. Ver todas: /agenda")


def comando_agenda(resto: str) -> str:
    partes = resto.split()
    if len(partes) == 2 and partes[1].isdigit() and partes[0] in ("pausar", "retomar"):
        ok = pausar(int(partes[1]), ativa=partes[0] == "retomar")
        return ("✅ Feito.\n\n" + formatar()) if ok else "Tarefa não encontrada."
    return formatar()
