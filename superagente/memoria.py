"""Memória da Central: a conversa com você e o que ela deve sempre lembrar sobre você.

  • Conversa: as últimas mensagens (suas e da Central) vão junto em cada pedido, para os
    agentes entenderem "isso", "aprofunde o item 3", "e sobre aquilo?".
    Depois de 12 h sem mensagens começa uma conversa nova (a antiga fica arquivada).
    /nova começa uma conversa nova na hora.
  • Perfil: fatos permanentes ("sou sargento do Exército", "meu público é empresário")
    guardados com /lembrar e usados por TODOS os agentes, sempre.
  • Caderno da Central: o que a própria Central aprendeu sobre o seu ambiente, projetos e
    combinados (adaptado do MEMORY.md do Hermes). Ela propõe; você aprova (/aprender).
  • Resumo acumulado: quando a conversa fica longa, o começo vira um resumo (Objetivo,
    Decisões, Pendências…) em vez de ser esquecido — a compressão de contexto do Hermes.
  • Lembranças: trechos de conversas antigas sobre o mesmo assunto (sessoes.py).
Tudo fica só no seu PC (~/.central-ia).
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

PASTA = Path.home() / ".central-ia"
ARQUIVO = PASTA / "conversa.json"
ARQUIVO_PERFIL = PASTA / "perfil.json"
ARQUIVO_CADERNO = PASTA / "memoria_central.json"
ARQUIVO_MORTO = PASTA / "conversas"
INATIVIDADE = 12 * 3600
LIMITE_HISTORICO = 12000   # caracteres de conversa enviados aos agentes (o mais recente primeiro)
LIMITE_POR_FALA = 3000
# Limites da memória curada (o Hermes usa 1.375 e 2.200; português ocupa mais caracteres)
LIMITES = {"perfil": 3000, "caderno": 4000}
GATILHO_RESUMO = 4000      # caracteres de conversa fora da janela que disparam um novo resumo
LIMITE_RESUMO = 2500
MODELOS_RESUMO = ["gemini:gemini-flash-lite-latest", "groq:openai/gpt-oss-120b", "gemini:gemini-3.5-flash",
                  "ollama:qwen3:4b"]


def _ler(arquivo: Path, padrao):
    try:
        return json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def _gravar(arquivo: Path, dados) -> None:
    PASTA.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_suffix(".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    temporario.replace(arquivo)


def _conversa() -> dict:
    conversa = _ler(ARQUIVO, {"inicio": time.time(), "atualizado": 0, "falas": []})
    if conversa["falas"] and time.time() - conversa["atualizado"] > INATIVIDADE:
        nova()
        conversa = {"inicio": time.time(), "atualizado": 0, "falas": []}
    return conversa


def adicionar(quem: str, texto: str) -> None:
    """quem: 'voce' ou 'central'."""
    conversa = _conversa()
    agora = time.time()
    conversa["falas"].append({"quem": quem, "texto": texto.strip()[:LIMITE_POR_FALA],
                              "quando": datetime.now().strftime("%d/%m %H:%M"), "ts": agora})
    conversa["atualizado"] = agora
    _gravar(ARQUIVO, conversa)
    try:   # a lembrança de longo prazo nunca pode travar a conversa
        from . import sessoes
        sessoes.registrar(sessao_atual(conversa), quem, texto.strip(), agora)
    except Exception:  # noqa: BLE001
        pass


def sessao_atual(conversa: dict | None = None) -> str:
    return f"conversa-{int((conversa or _conversa()).get('inicio', 0))}"


def nova() -> int:
    """Arquiva a conversa atual e começa outra. Devolve quantas falas foram arquivadas."""
    conversa = _ler(ARQUIVO, {"falas": []})
    if conversa.get("falas"):
        ARQUIVO_MORTO.mkdir(parents=True, exist_ok=True)
        _gravar(ARQUIVO_MORTO / f"{datetime.now():%Y-%m-%d_%H%M%S}.json", conversa)
    _gravar(ARQUIVO, {"inicio": time.time(), "atualizado": 0, "falas": []})
    return len(conversa.get("falas", []))


def historico(limite: int = LIMITE_HISTORICO) -> str:
    falas, total = [], 0
    for fala in reversed(_conversa()["falas"]):
        linha = f"[{fala['quando']}] {'Você' if fala['quem'] == 'voce' else 'Central'}: {fala['texto']}"
        if total + len(linha) > limite:
            break
        falas.append(linha)
        total += len(linha)
    return "\n\n".join(reversed(falas))


def ultima_fala_central() -> str:
    for fala in reversed(_conversa()["falas"]):
        if fala["quem"] == "central":
            return fala["texto"]
    return ""


def ultima_fala_sua() -> str:
    for fala in reversed(_conversa()["falas"]):
        if fala["quem"] == "voce":
            return fala["texto"]
    return ""


# ------------------------------------------------------------------ perfil permanente
def fatos() -> list[str]:
    return _ler(ARQUIVO_PERFIL, [])


def lembrar(fato: str) -> int:
    lista = fatos()
    lista.append(fato.strip())
    _gravar(ARQUIVO_PERFIL, lista)
    return len(lista)


def esquecer(numero: int) -> str | None:
    lista = fatos()
    if 1 <= numero <= len(lista):
        removido = lista.pop(numero - 1)
        _gravar(ARQUIVO_PERFIL, lista)
        return removido
    return None


def contexto(consulta: str = "") -> str:
    """Bloco que acompanha cada pedido: quem é você, o caderno da Central, o resumo do início da
    conversa, a conversa recente e (se houver `consulta`) lembranças de conversas antigas."""
    partes = []
    if fatos():
        partes.append("## O que você sabe sobre o usuário (sempre válido)\n" + "\n".join(f"- {f}" for f in fatos()))
    if caderno():
        partes.append("## Caderno da Central (o que você já aprendeu sobre o ambiente e os combinados)\n"
                      + "\n".join(f"- {f}" for f in caderno()))
    try:
        compactar()
    except Exception:  # noqa: BLE001 — sem resumo novo, segue com o que tem
        pass
    try:
        from . import diario
        dia = diario.contexto()
        if dia:
            partes.append(dia)
    except Exception:  # noqa: BLE001
        pass
    resumo_antigo = _conversa().get("resumo", "")
    if resumo_antigo:
        partes.append("## Resumo do começo desta conversa\n" + resumo_antigo)
    conversa = historico()
    if conversa:
        partes.append("## Conversa até aqui (use para entender referências como 'isso', 'o item 3')\n" + conversa)
    if consulta:
        try:
            from . import sessoes
            antigas = sessoes.lembrancas(consulta, ignorar_sessao=sessao_atual())
            if antigas:
                partes.append(antigas)
        except Exception:  # noqa: BLE001
            pass
    return "\n\n".join(partes)


# ------------------------------------------------------------------ memória curada (perfil + caderno)
def caderno() -> list[str]:
    return _ler(ARQUIVO_CADERNO, [])


def _arquivo_do_alvo(alvo: str) -> Path:
    if alvo not in LIMITES:
        raise ValueError(f"alvo de memória desconhecido: {alvo} (use perfil ou caderno)")
    return ARQUIVO_PERFIL if alvo == "perfil" else ARQUIVO_CADERNO


def escrever(alvo: str, acao: str, texto: str = "", antigo: str = "") -> tuple[bool, str]:
    """Escrita controlada na memória curada, como a ferramenta `memory` do Hermes.
    acao: 'add' (acrescenta), 'patch' (troca o trecho `antigo` por `texto`), 'remove' (apaga o item
    que contém `antigo`). Recusa duplicatas, estouro de limite e conteúdo perigoso."""
    from . import seguranca
    arquivo = _arquivo_do_alvo(alvo)
    lista = _ler(arquivo, [])
    texto = seguranca.limpar(texto).strip()
    if acao in ("add", "patch"):
        veredito, motivos = seguranca.verificar(texto)
        if veredito == "perigoso":
            return False, "bloqueado pela segurança: " + "; ".join(motivos)
    if acao == "add":
        if not texto:
            return False, "nada para guardar"
        if any(texto.lower() == f.lower() for f in lista):
            return False, "isso já está guardado"
        lista.append(texto)
    elif acao in ("patch", "remove"):
        donos = [i for i, f in enumerate(lista) if antigo and antigo in f]
        if len(donos) != 1:
            return False, ("trecho não encontrado" if not donos
                           else "trecho aparece em mais de um item; seja mais específico")
        if acao == "patch":
            lista[donos[0]] = lista[donos[0]].replace(antigo, texto)
        else:
            lista.pop(donos[0])
    else:
        return False, f"ação desconhecida: {acao}"
    total = sum(len(f) for f in lista)
    if acao != "remove" and total > LIMITES[alvo]:
        return False, (f"o {alvo} ficaria com {total} caracteres (limite {LIMITES[alvo]}): "
                       "resuma ou apague itens antes")
    _gravar(arquivo, lista)
    return True, "ok"


# ------------------------------------------------------------------ compressão (resumo acumulado)
PEDIDO_RESUMO = """Resuma a parte antiga de uma conversa entre o Reynolds e a Central de IA, para a Central não
esquecer o essencial. Use EXATAMENTE estas seções curtas, em português, só com o que aparece no texto:
**Objetivo** · **Restrições e preferências** · **Feito até aqui** · **Decisões** · **Pendências** · **Contexto crítico**
(nomes, números, caminhos de arquivo). Máximo de {limite} caracteres. Sem introdução.

{anterior}## Trecho antigo da conversa
{trecho}"""


def _resumir_com_ia(trecho: str, anterior: str) -> str:
    from . import provedores
    pedido = PEDIDO_RESUMO.format(limite=LIMITE_RESUMO, trecho=trecho,
                                  anterior=f"## Resumo anterior (atualize-o)\n{anterior}\n\n" if anterior else "")
    _, texto = provedores.chamar_com_reserva(MODELOS_RESUMO, "Você resume conversas com fidelidade.", pedido,
                                             log=lambda *_: None)
    return texto.strip()


def _resumo_simples(trecho: str, anterior: str) -> str:
    """Plano B sem IA: guarda o começo de cada fala sua (o que você pediu não se perde)."""
    pedidos = [linha.split(": ", 1)[-1][:160] for linha in trecho.splitlines() if "] Você: " in linha]
    return (anterior + "\n" if anterior else "") + "\n".join(f"- Você pediu: {p}" for p in pedidos)


def compactar(resumir=None, limite: int = LIMITE_HISTORICO) -> bool:
    """Se a parte da conversa que já saiu da janela passou de GATILHO_RESUMO caracteres, resume-a.
    Devolve True se gerou um resumo novo."""
    conversa = _conversa()
    falas = conversa["falas"]
    total, inicio_janela = 0, len(falas)
    for i in range(len(falas) - 1, -1, -1):
        total += len(falas[i]["texto"]) + 20
        if total > limite:
            break
        inicio_janela = i
    ate = conversa.get("resumido_ate", 0)
    fora = [f for f in falas[:inicio_janela] if f.get("ts", 0) > ate]
    if sum(len(f["texto"]) for f in fora) < GATILHO_RESUMO:
        return False
    trecho = "\n".join(f"[{f['quando']}] {'Você' if f['quem'] == 'voce' else 'Central'}: {f['texto']}"
                        for f in fora)
    anterior = conversa.get("resumo", "")
    try:
        novo = (resumir or _resumir_com_ia)(trecho, anterior)
    except Exception:  # noqa: BLE001 — nenhuma IA respondeu
        novo = _resumo_simples(trecho, anterior)
    conversa["resumo"] = (novo or _resumo_simples(trecho, anterior))[-LIMITE_RESUMO * 2:]
    conversa["resumido_ate"] = fora[-1].get("ts", time.time())
    _gravar(ARQUIVO, conversa)
    try:   # a "gravação antes de comprimir" do OpenClaw: o resumo também fica no diário do dia
        from . import diario
        diario.registrar(f"Resumo da conversa: {conversa['resumo']}", "conversa")
    except Exception:  # noqa: BLE001
        pass
    return True


def resumo() -> str:
    conversa = _conversa()
    linhas = [f"🧠 Memória da Central",
              f"• Conversa atual: {len(conversa['falas'])} mensagens"
              + (f" (desde {datetime.fromtimestamp(conversa['inicio']):%d/%m %H:%M})" if conversa["falas"] else ""),
              "• Começa sozinha uma conversa nova após 12 h sem mensagens (ou mande /nova)"]
    if conversa.get("resumo"):
        linhas.append("• O começo da conversa já foi resumido (compressão automática)")
    if caderno():
        linhas.append(f"\n📓 Caderno da Central ({sum(map(len, caderno()))}/{LIMITES['caderno']} caracteres):")
        linhas += [f"- {f}" for f in caderno()]
    if fatos():
        linhas.append("\n📌 O que eu sempre lembro sobre você:")
        linhas += [f"{i}. {f}" for i, f in enumerate(fatos(), 1)]
        linhas.append("(para apagar um: /esquecer <número>)")
    else:
        linhas.append("\n📌 Nada fixo ainda. Ex.: /lembrar Meu público são empresários do agronegócio")
    return "\n".join(linhas)


def _momento(fala: dict) -> float:
    if fala.get("ts"):
        return float(fala["ts"])
    try:   # falas antigas só têm "dd/mm HH:MM"
        return datetime.strptime(f"{datetime.now().year}/{fala['quando']}", "%Y/%d/%m %H:%M").timestamp()
    except (KeyError, ValueError):
        return 0.0


def juntar_conversas(a: dict, b: dict) -> dict:
    """Une duas versões da conversa (PC e nuvem) sem perder nenhuma fala, em ordem de tempo."""
    vistas, falas = set(), []
    for fala in a.get("falas", []) + b.get("falas", []):
        chave = (fala.get("quem"), fala.get("quando"), fala.get("texto", "")[:300])
        if chave not in vistas:
            vistas.add(chave)
            falas.append(fala)
    falas.sort(key=_momento)
    unida = {"inicio": min(a.get("inicio", time.time()), b.get("inicio", time.time())),
             "atualizado": max(a.get("atualizado", 0), b.get("atualizado", 0)), "falas": falas}
    mais_resumida = max((a, b), key=lambda c: c.get("resumido_ate", 0))
    if mais_resumida.get("resumo"):   # o resumo mais avançado vence (nenhum dos dois apaga falas)
        unida["resumo"], unida["resumido_ate"] = mais_resumida["resumo"], mais_resumida["resumido_ate"]
    return unida


def juntar_perfis(a: list, b: list) -> list:
    return a + [f for f in b if f not in a]
