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
    if modo not in ("rapido", "equipe"):
        raise ValueError("modo deve ser rapido ou equipe")
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
    rodar = rodar or (_rodar_equipe if tarefa["modo"] == "equipe" else _rodar_rapido)
    resposta = rodar(tarefa["pedido"]).strip()
    pasta = PASTA_SAIDA / str(tarefa["id"])
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / f"{datetime.now():%Y-%m-%d_%H%M}.md").write_text(
        f"# {tarefa['pedido'][:120]}\n\n> Agendada: {tarefa['quando']}\n\n{resposta}\n", encoding="utf-8")
    if enviar and not resposta.startswith(SILENCIO):
        enviar(f"🗓 Tarefa agendada {tarefa['id']} — {tarefa['pedido'][:80]}\n\n{resposta}")
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
