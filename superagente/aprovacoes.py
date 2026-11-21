"""Regras de aprovação do Executor: "só desta vez", "sempre permitir" e "perguntar sempre".

Adaptado das regras do Grok Bot (Allow once · Always allow · Deny, com até 20 regras "permitir
automaticamente" e 20 "perguntar antes", e "perguntar antes" sempre vence). Aqui:
  • ♾ Sempre permitir guarda a ORDEM; ordens praticamente iguais depois são aprovadas sozinhas;
  • há uma lista fixa de "perguntar sempre" (apagar, enviar, publicar, pagar, instalar, desligar…)
    que nenhuma regra fura — nem que o pedido seja igual a um já permitido;
  • o PLANO também é conferido: se o Executor planejar algo da lista fixa, a aprovação volta a ser sua;
  • você pode acrescentar as suas próprias regras de "perguntar antes" (até 20).
Regras: ~/.central-ia/regras_aprovacao.json
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path

ARQUIVO = Path.home() / ".central-ia" / "regras_aprovacao.json"
MAXIMO = 20
SEMELHANCA = 0.8
PERGUNTAR_SEMPRE = [
    (r"\b(apag|delet|exclu|remov|limp[ae]|esvazi)\w*", "apaga ou remove algo"),
    (r"\bformat\w*", "formata um disco"),
    (r"\b(envi|mand)\w*\b.{0,30}\b(e-?mails?|mensage\w*|whatsapp)\b", "envia mensagem em seu nome"),
    (r"\b(public|post)\w*", "publica algo"),
    (r"\b(pag[aoe]\w*|transfer\w*|pix\b|compr[aeo]\w*|assin[ae]\w* (plano|servi))", "mexe com dinheiro"),
    (r"\b(desinstal|instal)\w*", "instala ou desinstala programa"),
    (r"\b(reinici|deslig|hibern)\w*", "reinicia ou desliga o PC"),
    (r"\b(senha|password|credencia|token|\.env)\b", "mexe com senhas ou chaves"),
    (r"\b(regedit|registro do windows|firewall|antiv[ií]rus|defender)\b", "mexe na segurança do sistema"),
    (r"\b(sobrescrev|substitu)\w*", "sobrescreve arquivos"),
    (r"\b(rm\s+-|del\s+/|rmdir|remove-item|move-item|robocopy\b.*\/mir)", "comando que apaga ou move em massa"),
]
PALAVRAS_VAZIAS = set("a o as os de da do das dos e em um uma no na nos nas para pra com por que me meu minha".split())


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def _palavras(texto: str) -> set[str]:
    return {p for p in re.findall(r"[a-z0-9:\\/._-]{2,}", _normalizar(texto)) if p not in PALAVRAS_VAZIAS}


def _ler() -> dict:
    try:
        return json.loads(ARQUIVO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"permitir": [], "perguntar": []}


def _gravar(regras: dict) -> None:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO.write_text(json.dumps(regras, ensure_ascii=False, indent=1), encoding="utf-8")


def motivos_para_perguntar(texto: str, regras: dict | None = None) -> list[str]:
    """Por que este texto (ordem ou plano) exige a sua aprovação. Vazio = nada sensível."""
    alvo = _normalizar(texto)
    motivos = [m for p, m in PERGUNTAR_SEMPRE if re.search(p, alvo)]
    for r in (regras or _ler()).get("perguntar", []):
        if _normalizar(r["texto"]) in alvo:
            motivos.append(f"sua regra: perguntar antes de «{r['texto']}»")
    return list(dict.fromkeys(motivos))


def _parecida(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def decidir(ordem: str, plano: str = "") -> tuple[bool, str]:
    """(aprovada_sozinha, explicação). Só aprova sozinha se houver regra 'sempre' e nada sensível."""
    regras = _ler()
    sensivel = motivos_para_perguntar(f"{ordem}\n{plano}", regras)
    if sensivel:
        return False, "precisa do seu OK: " + "; ".join(sensivel)
    palavras = _palavras(ordem)
    melhor = max(regras.get("permitir", []), key=lambda r: _parecida(palavras, set(r["palavras"])), default=None)
    if melhor and _parecida(palavras, set(melhor["palavras"])) >= SEMELHANCA:
        melhor["usos"] = melhor.get("usos", 0) + 1
        _gravar(regras)
        return True, f"aprovada pela sua regra «{melhor['texto'][:80]}»"
    return False, "ainda sem regra"


def permitir_sempre(ordem: str) -> str:
    regras = _ler()
    sensivel = motivos_para_perguntar(ordem, regras)
    if sensivel:
        return ("Essa ordem " + "; ".join(sensivel) + ", então continuo perguntando sempre (regra fixa de "
                "segurança). Desta vez foi aprovada.")
    lista = regras.setdefault("permitir", [])
    if len(lista) >= MAXIMO:
        return f"Já há {MAXIMO} regras 'sempre permitir'. Apague uma (/regras) para criar outra. Desta vez foi aprovada."
    lista.append({"texto": ordem.strip()[:300], "palavras": sorted(_palavras(ordem)),
                  "criada": time.strftime("%d/%m/%Y %H:%M"), "usos": 0})
    _gravar(regras)
    return "♾ Regra criada: da próxima vez, uma ordem igual a esta roda sem perguntar (exceto se o plano for sensível)."


def perguntar_antes(trecho: str) -> str:
    regras = _ler()
    lista = regras.setdefault("perguntar", [])
    if len(lista) >= MAXIMO:
        return f"Já há {MAXIMO} regras 'perguntar antes'."
    lista.append({"texto": trecho.strip()[:120], "criada": time.strftime("%d/%m/%Y %H:%M")})
    _gravar(regras)
    return f"🛑 Combinado: sempre que aparecer «{trecho.strip()[:120]}», eu pergunto antes."


def remover(tipo: str, numero: int) -> bool:
    regras = _ler()
    lista = regras.get(tipo, [])
    if not 1 <= numero <= len(lista):
        return False
    lista.pop(numero - 1)
    _gravar(regras)
    return True


def formatar() -> str:
    regras = _ler()
    linhas = ["🛡 Regras de aprovação do Executor", "", "♾ Sempre permitir (ordens que rodam sozinhas):"]
    linhas += [f"  {i}. {r['texto'][:100]} ({r.get('usos', 0)} usos)" for i, r in enumerate(regras.get("permitir", []), 1)] \
        or ["  (nenhuma)"]
    linhas += ["", "🛑 Perguntar antes (suas regras):"]
    linhas += [f"  {i}. {r['texto']}" for i, r in enumerate(regras.get("perguntar", []), 1)] or ["  (nenhuma)"]
    linhas += ["", "🔒 Sempre pergunto, sem exceção: " + ", ".join(m for _, m in PERGUNTAR_SEMPRE) + ".",
               "", "Apagar: /regras apagar permitir <nº> · /regras apagar perguntar <nº>",
               "Nova: /regras perguntar <trecho> (ex.: /regras perguntar HD E:)"]
    return "\n".join(linhas)


def comando(resto: str) -> str:
    partes = resto.split()
    if len(partes) == 3 and partes[0] == "apagar" and partes[1] in ("permitir", "perguntar") and partes[2].isdigit():
        return "🗑 Regra apagada." if remover(partes[1], int(partes[2])) else "Regra não encontrada."
    if partes and partes[0] == "perguntar" and len(partes) > 1:
        return perguntar_antes(resto.split(None, 1)[1])
    return formatar()
