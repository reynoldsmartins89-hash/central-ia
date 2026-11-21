"""Vigilância de conteúdo: nada entra na memória ou nas habilidades sem passar por aqui.

Adaptado do Hermes Agent, que varre os arquivos de contexto, a memória e as habilidades em busca
de injeção de instruções e de vazamento de dados. Aqui é tudo local e por padrões simples:
  • "perigoso"  → bloqueia (não grava de jeito nenhum);
  • "suspeito"  → grava só com a sua aprovação explícita, com o alerta à vista.
"""
from __future__ import annotations

import re
import unicodedata

# Caracteres invisíveis usados para esconder instruções dentro de um texto aparentemente normal
INVISIVEIS = re.compile("[​-‏‪-‮⁠-⁤﻿\U000e0000-\U000e007f]")

PERIGOSOS = [
    (r"ignor\w*\s+(todas?\s+)?(as\s+)?(instru|regras|ordens)\w*\s+(anteriores|acima)", "manda ignorar as regras"),
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules)", "manda ignorar as regras"),
    (r"(disregard|forget)\s+(all\s+)?(previous|your)\s+(instructions|rules)", "manda ignorar as regras"),
    (r"(curl|wget|invoke-webrequest|iwr)\b[^\n]*(\.env|api[_-]?key|token|senha|password)", "envia segredos para fora"),
    (r"(cat|type|get-content)\s+[^\n]*\.env\b", "lê o arquivo de chaves .env"),
    (r"rm\s+-rf\s+/(\s|$)|format\s+[a-z]:|mkfs\.|del\s+/[sq]\s+[a-z]:\\", "comando destrutivo"),
    (r"\b(sk-[a-z0-9]{20,}|AIza[0-9a-z_\-]{30,}|gsk_[a-z0-9]{20,}|ghp_[a-z0-9]{30,})", "contém uma chave de API"),
]
SUSPEITOS = [
    (r"(voc[eê]\s+agora\s+[eé]|a partir de agora voc[eê]|you\s+are\s+now)\b", "tenta trocar a identidade da Central"),
    (r"(n[aã]o\s+(conte|avise|diga)\s+(ao|para o)\s+(usu[aá]rio|reynolds))", "pede para esconder algo de você"),
    (r"(sem|nunca)\s+pedir\s+(aprova|confirma)\w*", "tenta pular a sua aprovação"),
    (r"(publique|poste|envie)\s+automaticamente", "tenta publicar sem aprovação"),
    (r"<\s*/?\s*(system|sistema)\s*>", "imita uma mensagem do sistema"),
]


def limpar(texto: str) -> str:
    """Tira caracteres invisíveis e normaliza o Unicode."""
    return unicodedata.normalize("NFC", INVISIVEIS.sub("", texto or ""))


def verificar(texto: str) -> tuple[str, list[str]]:
    """Devolve (veredito, motivos). Veredito: 'ok', 'suspeito' ou 'perigoso'."""
    motivos_perigo, motivos_suspeita = [], []
    if INVISIVEIS.search(texto or ""):
        motivos_suspeita.append("tem caracteres invisíveis")
    alvo = limpar(texto).lower()
    for padrao, motivo in PERIGOSOS:
        if re.search(padrao, alvo, re.IGNORECASE) and motivo not in motivos_perigo:
            motivos_perigo.append(motivo)
    for padrao, motivo in SUSPEITOS:
        if re.search(padrao, alvo, re.IGNORECASE) and motivo not in motivos_suspeita:
            motivos_suspeita.append(motivo)
    if motivos_perigo:
        return "perigoso", motivos_perigo + motivos_suspeita
    if motivos_suspeita:
        return "suspeito", motivos_suspeita
    return "ok", []
