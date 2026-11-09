"""Biblioteca dos agentes de conhecimento: índice de busca local sobre os seus arquivos.

Cada agente com `base = "<pasta>"` no agentes.toml ganha um índice SQLite FTS5
(busca por palavras, já embutida no Python — grátis, local e rápida), guardado em
~/.central-ia/indices/<agente>.db. Os arquivos originais nunca são alterados.

Indexar:   python main.py indexar geopolitica     (ou: python main.py indexar todos)
A indexação é incremental: rodar de novo só processa arquivos novos ou alterados.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

PASTA_INDICES = Path.home() / ".central-ia" / "indices"
EXTENSOES = {".md", ".txt"}
TAMANHO_TRECHO = 1500          # caracteres por trecho indexado
LIMITE_ARQUIVO = 8_000_000     # ignora arquivos de texto gigantes (provavelmente imagens embutidas)

PALAVRAS_VAZIAS = set("""
a o as os um uma uns umas de do da dos das no na nos nas em por para com sem sob sobre entre
e ou mas que se como quando onde qual quais quem cujo isso isto esse essa este esta aquele aquela
ao aos à às pelo pela pelos pelas seu sua seus suas meu minha nosso nossa ser estar ter haver
foi era são é está fazer faça explique escreva crie analise sobre muito mais menos já também
the of and to in for on with what how why is are
""".split())


def _caminho_indice(agente: str) -> Path:
    PASTA_INDICES.mkdir(parents=True, exist_ok=True)
    return PASTA_INDICES / f"{agente}.db"


def _conectar(agente: str) -> sqlite3.Connection:
    # timeout + WAL: a indexação (que escreve) não pode travar as consultas do bot (que só leem)
    con = sqlite3.connect(_caminho_indice(agente), timeout=60)
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass   # índice ocupado agora; vale da próxima vez
    con.executescript("""
        CREATE TABLE IF NOT EXISTS arquivos (caminho TEXT PRIMARY KEY, modificado REAL);
        CREATE VIRTUAL TABLE IF NOT EXISTS trechos USING fts5(
            texto, titulo UNINDEXED, caminho UNINDEXED,
            tokenize = 'unicode61 remove_diacritics 2');
    """)
    return con


def _limpar(texto: str) -> str:
    texto = re.sub(r"!\[[^\]]*\]\(data:[^)]*\)", " ", texto)       # imagens embutidas em base64
    texto = re.sub(r"\S{120,}", " ", texto)                         # blocos sem espaço (base64, hashes)
    texto = re.sub(r"[ \t]+", " ", texto)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def _trechos(texto: str):
    paragrafos, atual = re.split(r"\n\s*\n", texto), ""
    for p in paragrafos:
        if len(atual) + len(p) > TAMANHO_TRECHO and atual:
            yield atual.strip()
            atual = ""
        atual += p + "\n\n"
        while len(atual) > TAMANHO_TRECHO * 2:          # parágrafo enorme: corta
            yield atual[:TAMANHO_TRECHO].strip()
            atual = atual[TAMANHO_TRECHO:]
    if atual.strip():
        yield atual.strip()


def indexar(agente: str, pasta: str | Path, log=print) -> dict:
    pasta = Path(pasta)
    if not pasta.is_dir():
        raise FileNotFoundError(f"pasta da biblioteca não encontrada: {pasta}")
    con = _conectar(agente)
    conhecidos = dict(con.execute("SELECT caminho, modificado FROM arquivos"))
    vistos, novos, ignorados, inicio = set(), 0, 0, time.time()
    for n, arquivo in enumerate((p for p in pasta.rglob("*") if p.suffix.lower() in EXTENSOES), 1):
        chave = str(arquivo.relative_to(pasta))
        vistos.add(chave)
        try:
            info = arquivo.stat()
            if conhecidos.get(chave) == info.st_mtime:
                continue
            if info.st_size > LIMITE_ARQUIVO:
                ignorados += 1
                continue
            texto = _limpar(arquivo.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            ignorados += 1
            continue
        con.execute("DELETE FROM trechos WHERE caminho = ?", (chave,))
        titulo = arquivo.stem[:200]
        con.executemany("INSERT INTO trechos (texto, titulo, caminho) VALUES (?, ?, ?)",
                        ((t, titulo, chave) for t in _trechos(texto) if len(t) > 80))
        con.execute("INSERT OR REPLACE INTO arquivos VALUES (?, ?)", (chave, info.st_mtime))
        novos += 1
        if novos % 200 == 0:
            con.commit()
            log(f"   {agente}: {n} arquivos lidos, {novos} indexados ({time.time() - inicio:.0f}s)")
    removidos = set(conhecidos) - vistos
    for chave in removidos:
        con.execute("DELETE FROM trechos WHERE caminho = ?", (chave,))
        con.execute("DELETE FROM arquivos WHERE caminho = ?", (chave,))
    con.commit()
    total = con.execute("SELECT count(*) FROM arquivos").fetchone()[0]
    con.close()
    resumo = {"agente": agente, "arquivos": total, "novos": novos, "ignorados": ignorados,
              "removidos": len(removidos), "segundos": round(time.time() - inicio)}
    log(f"✅ {agente}: {total} arquivos no índice ({novos} novos, {ignorados} ignorados)")
    return resumo


def indexado(agente: str) -> bool:
    return _caminho_indice(agente).exists()


def buscar(agente: str, pergunta: str, limite: int = 8, max_caracteres: int = 12_000) -> list[dict]:
    """Retorna os trechos mais relevantes da biblioteca do agente para a pergunta."""
    if not indexado(agente):
        return []
    palavras = {p for p in re.findall(r"\w{3,}", pergunta.lower()) if p not in PALAVRAS_VAZIAS}
    if not palavras:
        return []
    consulta = " OR ".join(f'"{p}"' for p in sorted(palavras, key=len, reverse=True)[:20])
    con = _conectar(agente)
    try:
        linhas = con.execute(
            "SELECT texto, titulo, caminho FROM trechos WHERE trechos MATCH ? ORDER BY bm25(trechos) LIMIT ?",
            (consulta, limite * 3)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    resultado, usados, por_arquivo = [], 0, {}
    for texto, titulo, caminho in linhas:          # no máximo 2 trechos por arquivo, para variar as fontes
        if por_arquivo.get(caminho, 0) >= 2 or usados + len(texto) > max_caracteres:
            continue
        por_arquivo[caminho] = por_arquivo.get(caminho, 0) + 1
        usados += len(texto)
        resultado.append({"texto": texto, "titulo": titulo, "caminho": caminho})
        if len(resultado) >= limite:
            break
    return resultado


def relevancia(agente: str, texto: str) -> int:
    """Quantos trechos da biblioteca tratam do assunto (sinal para o superagente escolher agentes).

    Conta trechos que contêm pelo menos DUAS palavras-chave do pedido (evita falsos positivos
    de palavras soltas). Com uma única palavra-chave, basta ela.
    """
    if not indexado(agente):
        return 0
    palavras = sorted({p for p in re.findall(r"\w{4,}", texto.lower()) if p not in PALAVRAS_VAZIAS},
                      key=len, reverse=True)[:8]
    if not palavras:
        return 0
    if len(palavras) == 1:
        consulta = f'"{palavras[0]}"'
    else:  # pares de palavras próximas no mesmo trecho
        pares = [f'NEAR("{a}" "{b}", 40)' for i, a in enumerate(palavras) for b in palavras[i + 1:]][:20]
        consulta = " OR ".join(pares)
    con = _conectar(agente)
    try:
        return con.execute("SELECT count(*) FROM (SELECT 1 FROM trechos WHERE trechos MATCH ? LIMIT 200)",
                           (consulta,)).fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


def como_contexto(trechos: list[dict]) -> str:
    if not trechos:
        return ""
    partes = [f"[{i}] {t['titulo']}\n{t['texto']}" for i, t in enumerate(trechos, 1)]
    return ("## Trechos da sua biblioteca (use como base; cite como [n])\n\n" + "\n\n---\n\n".join(partes)
            + "\n\n## Fontes\n" + "\n".join(f"[{i}] {t['caminho']}" for i, t in enumerate(trechos, 1)))
