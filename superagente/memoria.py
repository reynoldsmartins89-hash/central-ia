"""Memória da Central: a conversa com você e o que ela deve sempre lembrar sobre você.

  • Conversa: as últimas mensagens (suas e da Central) vão junto em cada pedido, para os
    agentes entenderem "isso", "aprofunde o item 3", "e sobre aquilo?".
    Depois de 12 h sem mensagens começa uma conversa nova (a antiga fica arquivada).
    /nova começa uma conversa nova na hora.
  • Perfil: fatos permanentes ("sou sargento do Exército", "meu público é empresário")
    guardados com /lembrar e usados por TODOS os agentes, sempre.
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
ARQUIVO_MORTO = PASTA / "conversas"
INATIVIDADE = 12 * 3600
LIMITE_HISTORICO = 12000   # caracteres de conversa enviados aos agentes (o mais recente primeiro)
LIMITE_POR_FALA = 3000


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
    conversa["falas"].append({"quem": quem, "texto": texto.strip()[:LIMITE_POR_FALA],
                              "quando": datetime.now().strftime("%d/%m %H:%M")})
    conversa["atualizado"] = time.time()
    _gravar(ARQUIVO, conversa)


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


def contexto() -> str:
    """Bloco que acompanha cada pedido: quem é você + a conversa até aqui."""
    partes = []
    if fatos():
        partes.append("## O que você sabe sobre o usuário (sempre válido)\n" + "\n".join(f"- {f}" for f in fatos()))
    conversa = historico()
    if conversa:
        partes.append("## Conversa até aqui (use para entender referências como 'isso', 'o item 3')\n" + conversa)
    return "\n\n".join(partes)


def resumo() -> str:
    conversa = _conversa()
    linhas = [f"🧠 Memória da Central",
              f"• Conversa atual: {len(conversa['falas'])} mensagens"
              + (f" (desde {datetime.fromtimestamp(conversa['inicio']):%d/%m %H:%M})" if conversa["falas"] else ""),
              "• Começa sozinha uma conversa nova após 12 h sem mensagens (ou mande /nova)"]
    if fatos():
        linhas.append("\n📌 O que eu sempre lembro sobre você:")
        linhas += [f"{i}. {f}" for i, f in enumerate(fatos(), 1)]
        linhas.append("(para apagar um: /esquecer <número>)")
    else:
        linhas.append("\n📌 Nada fixo ainda. Ex.: /lembrar Meu público são empresários do agronegócio")
    return "\n".join(linhas)
