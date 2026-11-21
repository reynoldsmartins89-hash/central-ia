"""Ciclo de aprendizado: a Central fica melhor a cada uso — sempre com o seu OK.

Adaptado do "learning loop" do Hermes Agent. Lá, depois de algumas conversas, um revisor em segundo
plano relê o que aconteceu e (1) anota fatos na memória e (2) cria ou corrige habilidades (SKILL.md).
Aqui é igual, com duas diferenças de confiança:
  • nada é gravado direto: tudo vira PROPOSTA pendente e você aprova ou rejeita (/aprender);
  • toda proposta passa pela vigilância (seguranca.py); a perigosa é descartada na hora.

Quando revisa: a cada INTERVALO mensagens suas, e sempre depois de um trabalho em equipe ou do Executor.
Pendências: ~/.central-ia/aprendizado_pendente.json
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from . import habilidades, memoria, seguranca

PASTA = Path.home() / ".central-ia"
PENDENTES = PASTA / "aprendizado_pendente.json"
ESTADO = PASTA / "aprendizado_estado.json"
INTERVALO = 6               # mensagens suas entre uma revisão e outra
LIMITE_TRECHO = 14000
LIMITE_CORPO_HABILIDADE = 24000   # mesmo teto do lint "oversized-body" do Hermes
MODELOS = ["gemini:gemini-3.5-flash", "groq:openai/gpt-oss-120b", "gemini:gemini-flash-lite-latest",
           "ollama:qwen3:4b"]
NOME_VALIDO = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_trava = threading.Lock()

PEDIDO = """Você é o REVISOR DE APRENDIZADO da Central de IA do Reynolds. Releia o trecho abaixo e decida se
vale guardar algo para o futuro. Seja exigente: quase sempre a resposta certa é NÃO guardar nada.

Guarde na MEMÓRIA só fatos duráveis que mudam como a Central deve agir:
  • alvo "perfil": quem o Reynolds é, preferências, objetivos, jeito de trabalhar;
  • alvo "caderno": fatos do ambiente e combinados (caminhos, ferramentas, projetos, regras do sistema).
Nunca guarde: o que já está na memória, coisas passageiras do dia, opiniões da Central, segredos ou chaves.

Proponha uma HABILIDADE (SKILL.md) só quando apareceu um PROCEDIMENTO reutilizável, de vários passos, que
deu certo (ou um erro com a correção descoberta) e que a Central vai precisar de novo.
  • se já existe uma habilidade do mesmo assunto no índice, prefira "corrigir" (old/new) a criar outra;
  • nome em minúsculas-com-hífen; categoria = uma das pastas do índice (ou uma nova, se nenhuma servir);
  • corpo em Markdown, em português, com: Quando usar · Passo a passo · Armadilhas · Como conferir.

## Memória atual
{memoria_atual}

## Índice de habilidades existentes (caminho: descrição)
{indice}

## Trecho para revisar ({origem})
{trecho}

Responda SOMENTE com JSON:
{{"memoria": [{{"alvo": "perfil|caderno", "texto": "<fato em 1 frase>", "porque": "<motivo>"}}],
 "habilidades": [{{"acao": "criar", "categoria": "<pasta>", "nome": "<nome>", "descricao": "<1 frase: quando usar>",
                  "corpo": "<markdown>", "porque": "<motivo>"}},
                 {{"acao": "corrigir", "caminho": "<categoria/nome>", "antigo": "<trecho exato que existe>",
                  "novo": "<trecho novo>", "porque": "<motivo>"}}]}}
Listas vazias quando não houver nada que valha a pena."""


# ------------------------------------------------------------------ armazenamento
def _ler(arquivo: Path, padrao):
    try:
        return json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def _gravar(arquivo: Path, dados) -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    tmp = arquivo.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(arquivo)


def pendentes() -> list[dict]:
    return _ler(PENDENTES, [])


def _novo_id(lista: list[dict]) -> int:
    return max((p["id"] for p in lista), default=0) + 1


def _extrair_json(texto: str):
    texto = re.sub(r"^```(?:json)?|```$", "", (texto or "").strip(), flags=re.MULTILINE).strip()
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim <= inicio:
        raise ValueError("resposta sem JSON")
    return json.loads(texto[inicio:fim + 1])


# ------------------------------------------------------------------ validação das propostas
def _validar_memoria(m: dict) -> str | None:
    if m.get("alvo") not in memoria.LIMITES:
        return "alvo inválido"
    texto = str(m.get("texto") or "").strip()
    if len(texto) < 8:
        return "texto curto demais"
    atuais = memoria.fatos() if m["alvo"] == "perfil" else memoria.caderno()
    if any(texto.lower() == f.lower() for f in atuais):
        return "já está na memória"
    return None


def _validar_habilidade(h: dict) -> str | None:
    if h.get("acao") == "criar":
        if not NOME_VALIDO.fullmatch(str(h.get("nome") or "")):
            return "nome fora do padrão minúsculas-com-hífen"
        if not NOME_VALIDO.fullmatch(str(h.get("categoria") or "")):
            return "categoria fora do padrão"
        if len(str(h.get("descricao") or "")) < 20:
            return "descrição curta demais"
        corpo = str(h.get("corpo") or "")
        if len(corpo) < 200:
            return "corpo curto demais para ser um procedimento"
        if len(corpo) > LIMITE_CORPO_HABILIDADE:
            return "corpo grande demais"
        if (habilidades.PASTA_HABILIDADES / h["categoria"] / h["nome"]).exists():
            return "já existe uma habilidade com esse nome"
        return None
    if h.get("acao") == "corrigir":
        arquivo = habilidades.PASTA_HABILIDADES / str(h.get("caminho") or "") / "SKILL.md"
        if not arquivo.is_file():
            return "habilidade a corrigir não existe"
        antigo = str(h.get("antigo") or "")
        if not antigo or arquivo.read_text(encoding="utf-8").count(antigo) != 1:
            return "trecho antigo não aparece exatamente uma vez"
        if not str(h.get("novo") or "").strip():
            return "trecho novo vazio"
        return None
    return "ação desconhecida"


def _texto_para_vigiar(p: dict) -> str:
    return "\n".join(str(p.get(c) or "") for c in ("texto", "descricao", "corpo", "novo"))


def propor(dados: dict, origem: str) -> list[dict]:
    """Transforma a resposta do revisor em propostas pendentes (só as válidas e não perigosas)."""
    with _trava:
        lista = pendentes()
        novas = []
        candidatas = [{"tipo": "memoria", **m} for m in dados.get("memoria") or [] if isinstance(m, dict)]
        candidatas += [{"tipo": "habilidade", **h} for h in dados.get("habilidades") or [] if isinstance(h, dict)]
        for c in candidatas:
            erro = _validar_memoria(c) if c["tipo"] == "memoria" else _validar_habilidade(c)
            if erro:
                continue
            veredito, motivos = seguranca.verificar(_texto_para_vigiar(c))
            if veredito == "perigoso":
                continue
            if any(_texto_para_vigiar(p) == _texto_para_vigiar(c) for p in lista):
                continue
            c.update(id=_novo_id(lista), origem=origem, criado=time.strftime("%d/%m/%Y %H:%M"),
                     veredito=veredito, motivos=motivos)
            lista.append(c)
            novas.append(c)
        if novas:
            _gravar(PENDENTES, lista)
        return novas


# ------------------------------------------------------------------ revisão
def revisar(trecho: str, origem: str = "conversa", chamar=None) -> list[dict]:
    """Chama o revisor e registra as propostas. `chamar(sistema, mensagem) -> texto` permite testar offline."""
    if not trecho.strip():
        return []
    memoria_atual = "\n".join([*(f"- [perfil] {f}" for f in memoria.fatos()),
                               *(f"- [caderno] {f}" for f in memoria.caderno())]) or "(vazia)"
    pedido = PEDIDO.format(memoria_atual=memoria_atual, indice=habilidades.indice(), origem=origem,
                           trecho=trecho[-LIMITE_TRECHO:])
    sistema = "Você revisa conversas e só guarda o que é durável e útil. Responde apenas JSON."
    if chamar is None:
        from . import provedores
        _, resposta = provedores.chamar_com_reserva(MODELOS, sistema, pedido, log=lambda *_: None)
    else:
        resposta = chamar(sistema, pedido)
    return propor(_extrair_json(resposta), origem)


def talvez_revisar(motivo: str = "conversa", forcar: bool = False, avisar=None) -> threading.Thread | None:
    """Conta as mensagens e, quando é hora, revisa em segundo plano (nunca atrasa a sua resposta)."""
    estado = _ler(ESTADO, {"desde_ultima": 0})
    estado["desde_ultima"] = estado.get("desde_ultima", 0) + (1 if motivo == "conversa" else 0)
    if not forcar and motivo == "conversa" and estado["desde_ultima"] < INTERVALO:
        _gravar(ESTADO, estado)
        return None
    estado["desde_ultima"] = 0
    _gravar(ESTADO, estado)
    trecho = memoria.historico(LIMITE_TRECHO)

    def trabalhar():
        try:
            novas = revisar(trecho, motivo)
        except Exception as erro:  # noqa: BLE001 — aprender é bônus; nunca derruba o bot
            print(f"(revisão de aprendizado falhou: {erro})")
            return
        if novas and avisar:
            avisar_com_botoes(novas, avisar)

    t = threading.Thread(target=trabalhar, daemon=True)
    t.start()
    return t


# ------------------------------------------------------------------ decisão humana
def _formatar(p: dict) -> str:
    alerta = f"\n   ⚠ suspeito: {'; '.join(p['motivos'])}" if p.get("veredito") == "suspeito" else ""
    if p["tipo"] == "memoria":
        return f"{p['id']}. 🧠 Memória ({p['alvo']}): {p['texto']}\n   ↳ {p.get('porque', '')}{alerta}"
    if p["acao"] == "criar":
        return (f"{p['id']}. 📘 Nova habilidade {p['categoria']}/{p['nome']}: {p['descricao']}\n"
                f"   ↳ {p.get('porque', '')}{alerta}")
    return (f"{p['id']}. 🔧 Corrigir {p['caminho']}: «{str(p['antigo'])[:120]}» → «{str(p['novo'])[:120]}»\n"
            f"   ↳ {p.get('porque', '')}{alerta}")


def avisar_com_botoes(novas: list[dict], enviar) -> None:
    """Uma mensagem por proposta, com ✅/❌: você só toca, não precisa digitar nada."""
    enviar(f"💡 Aprendi {len(novas)} coisa(s) nesta conversa. Toque em ✅ para eu guardar ou ❌ para descartar "
           "(nada muda sem o seu toque).")
    for p in novas:
        texto = _formatar(p)
        if p["tipo"] == "habilidade" and p.get("acao") == "criar":
            texto += f"\n\nPrévia:\n{str(p['corpo'])[:700]}"
        enviar(texto, [("✅ Guardar", f"apr:sim:{p['id']}"), ("❌ Descartar", f"apr:nao:{p['id']}")])


def listar() -> str:
    lista = pendentes()
    if not lista:
        return "💡 Nenhuma proposta de aprendizado pendente."
    return ("💡 Propostas de aprendizado (nada foi gravado ainda):\n\n" + "\n\n".join(map(_formatar, lista))
            + "\n\nPara decidir: /aprender sim <nº> · /aprender nao <nº> · /aprender ver <nº>"
            " (vale 'todos' no lugar do número)")


def ver(numero: int) -> str:
    p = next((p for p in pendentes() if p["id"] == numero), None)
    if not p:
        return f"Não há proposta nº {numero}."
    if p["tipo"] == "habilidade" and p["acao"] == "criar":
        return f"{_formatar(p)}\n\n---\n{p['corpo']}"
    return _formatar(p)


def _aplicar(p: dict) -> str:
    if p["tipo"] == "memoria":
        ok, msg = memoria.escrever(p["alvo"], "add", p["texto"])
        if not ok:
            raise ValueError(msg)
        return f"🧠 guardado no {p['alvo']}"
    raiz = habilidades.PASTA_HABILIDADES
    if p["acao"] == "criar":
        erro = _validar_habilidade(p)
        if erro:
            raise ValueError(erro)
        categoria = raiz / p["categoria"]
        if not (categoria / "DESCRIPTION.md").exists():
            categoria.mkdir(parents=True, exist_ok=True)
            (categoria / "DESCRIPTION.md").write_text(
                f"# {p['categoria'].replace('-', ' ').title()}\n\nHabilidades criadas pelo ciclo de aprendizado "
                "da Central e aprovadas pelo Reynolds.\n", encoding="utf-8")
        pasta = categoria / p["nome"]
        pasta.mkdir()
        descricao = str(p["descricao"]).replace("\n", " ").replace('"', "'")
        (pasta / "SKILL.md").write_text(
            f'---\nname: {p["nome"]}\ndescription: "{descricao}"\norigem: aprendizado\n'
            f'criada: {time.strftime("%Y-%m-%d")}\n---\n\n{p["corpo"].strip()}\n', encoding="utf-8")
        return f"📘 habilidade criada em habilidades/{p['categoria']}/{p['nome']}"
    erro = _validar_habilidade(p)
    if erro:
        raise ValueError(erro)
    arquivo = raiz / p["caminho"] / "SKILL.md"
    arquivo.write_text(arquivo.read_text(encoding="utf-8").replace(p["antigo"], p["novo"], 1), encoding="utf-8")
    return f"🔧 habilidade {p['caminho']} corrigida"


def decidir(alvo: str, aprovar: bool) -> str:
    """alvo: número da proposta ou 'todos'."""
    with _trava:
        lista = pendentes()
        escolhidas = lista if alvo == "todos" else [p for p in lista if str(p["id"]) == str(alvo)]
        if not escolhidas:
            return f"Não há proposta {alvo}."
        linhas = []
        for p in escolhidas:
            if aprovar:
                try:
                    feito = _aplicar(p)
                    linhas.append(f"{p['id']}. ✅ {feito}")
                    from . import diario
                    diario.registrar(f"Aprendido com o seu OK: {feito} — {_texto_para_vigiar(p)[:150]}",
                                     "aprendizado")
                except (ValueError, OSError) as erro:
                    linhas.append(f"{p['id']}. ⚠ não apliquei: {erro}")
            else:
                linhas.append(f"{p['id']}. 🗑 descartada")
        ids = {p["id"] for p in escolhidas}
        _gravar(PENDENTES, [p for p in lista if p["id"] not in ids])
    return "\n".join(linhas)


def comando(resto: str) -> str:
    """Interpreta '/aprender [sim|nao|ver] [nº|todos]'."""
    partes = resto.split()
    if not partes:
        return listar()
    acao, alvo = partes[0].lower(), (partes[1] if len(partes) > 1 else "")
    if acao in ("sim", "aprovar", "s") and alvo:
        return decidir(alvo, True)
    if acao in ("nao", "não", "rejeitar", "n") and alvo:
        return decidir(alvo, False)
    if acao == "ver" and alvo.isdigit():
        return ver(int(alvo))
    return "Use: /aprender · /aprender sim <nº|todos> · /aprender nao <nº|todos> · /aprender ver <nº>"
