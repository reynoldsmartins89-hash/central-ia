"""Lembrança de longo prazo: todas as conversas e resultados num banco pesquisável (SQLite FTS5).

Adaptado do `session_search` do Hermes Agent. A memória da conversa (memoria.py) só enxerga a
conversa atual; aqui fica TUDO o que já foi conversado ou produzido, para a Central lembrar de
"aquilo que falamos mês passado" sem você repetir.
  • cada fala é gravada no momento em que acontece (memoria.adicionar chama registrar);
  • `importar()` traz as conversas arquivadas (~/.central-ia/conversas) e os resultados das equipes;
  • `buscar()` acha os trechos; `lembrancas()` monta o bloco que vai junto com o pedido.
Tudo fica só no seu PC (~/.central-ia/sessoes.db). Nenhuma IA é chamada para buscar.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PASTA = Path.home() / ".central-ia"
BANCO = PASTA / "sessoes.db"
RAIZ = Path(__file__).resolve().parent.parent
PASTA_RESULTADOS = RAIZ / "resultados"
LIMITE_FALA = 6000
PALAVRAS_VAZIAS = set("""a o as os de da do das dos e é em um uma uns umas no na nos nas por para pra com sem
que se ao aos à às eu você voce me te isso isto aquilo ele ela mas ou como mais muito já ja tem ser foi
sobre qual quais quando onde porque por que meu minha seu sua the and of to in is it""".split())


@dataclass
class Achado:
    sessao: str
    quem: str
    quando: str
    trecho: str
    origem: str


def _conectar(banco: Path | None = None) -> sqlite3.Connection:
    caminho = banco or BANCO
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS sessoes (id TEXT PRIMARY KEY, origem TEXT, titulo TEXT, inicio REAL);
        CREATE TABLE IF NOT EXISTS falas (id INTEGER PRIMARY KEY, sessao TEXT, quem TEXT, ts REAL,
                                          texto TEXT, UNIQUE(sessao, quem, ts));
        CREATE VIRTUAL TABLE IF NOT EXISTS falas_fts USING fts5(texto, content='falas', content_rowid='id',
                                                                tokenize='unicode61 remove_diacritics 2');
        CREATE TRIGGER IF NOT EXISTS falas_ai AFTER INSERT ON falas BEGIN
            INSERT INTO falas_fts(rowid, texto) VALUES (new.id, new.texto); END;
        CREATE TRIGGER IF NOT EXISTS falas_ad AFTER DELETE ON falas BEGIN
            INSERT INTO falas_fts(falas_fts, rowid, texto) VALUES ('delete', old.id, old.texto); END;
    """)
    return con


def registrar(sessao: str, quem: str, texto: str, ts: float, origem: str = "telegram",
              titulo: str = "", banco: Path | None = None) -> None:
    """Grava uma fala. Repetir a mesma fala (mesma sessão, autor e horário) não duplica."""
    if not (texto or "").strip():
        return
    con = _conectar(banco)
    try:
        with con:   # grava as duas linhas juntas (ou nenhuma)
            con.execute("INSERT OR IGNORE INTO sessoes VALUES (?, ?, ?, ?)", (sessao, origem, titulo[:200], ts))
            con.execute("INSERT OR IGNORE INTO falas (sessao, quem, ts, texto) VALUES (?, ?, ?, ?)",
                        (sessao, quem, ts, texto[:LIMITE_FALA]))
    finally:
        con.close()


def importar(banco: Path | None = None, pasta_conversas: Path | None = None,
             pasta_resultados: Path | None = None) -> int:
    """Traz para o banco as conversas arquivadas e os resultados das equipes. Devolve quantas falas novas."""
    con = _conectar(banco)
    antes = con.execute("SELECT count(*) FROM falas").fetchone()[0]
    con.close()
    conversas = pasta_conversas or PASTA / "conversas"
    arquivos = sorted(conversas.glob("*.json")) if conversas.exists() else []
    atual = PASTA / "conversa.json"
    if pasta_conversas is None and atual.exists():
        arquivos.append(atual)
    for arquivo in arquivos:
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sessao = f"conversa-{int(dados.get('inicio', 0))}"
        for fala in dados.get("falas", []):
            registrar(sessao, fala.get("quem", "?"), fala.get("texto", ""), float(fala.get("ts") or 0),
                      banco=banco)
    resultados = pasta_resultados or PASTA_RESULTADOS
    for registro in sorted(resultados.glob("*/registro.json")) if resultados.exists() else []:
        try:
            dados = json.loads(registro.read_text(encoding="utf-8"))
            ts = datetime.strptime(registro.parent.name[:17], "%Y-%m-%d_%H%M%S").timestamp()
        except (OSError, ValueError):
            continue
        sessao = f"equipe-{registro.parent.name}"
        titulo = str(dados.get("titulo", ""))
        registrar(sessao, "voce", str(dados.get("entrada") or titulo), ts, "equipe", titulo, banco)
        etapas = dados.get("etapas") or []
        if etapas:
            final = registro.parent / etapas[-1].get("arquivo", "")
            if final.is_file():
                registrar(sessao, "central", final.read_text(encoding="utf-8", errors="replace"),
                          ts + 1, "equipe", titulo, banco)
    con = _conectar(banco)
    depois = con.execute("SELECT count(*) FROM falas").fetchone()[0]
    con.close()
    return depois - antes


def _consulta_fts(texto: str) -> str:
    palavras = [p for p in re.findall(r"\w{3,}", (texto or "").lower()) if p not in PALAVRAS_VAZIAS]
    return " OR ".join(f'"{p}"' for p in dict.fromkeys(palavras[:12]))


def buscar(texto: str, limite: int = 6, ignorar_sessao: str | None = None,
           banco: Path | None = None) -> list[Achado]:
    """Os trechos mais parecidos com o texto, no máximo 2 por sessão (para variar as lembranças)."""
    consulta = _consulta_fts(texto)
    if not consulta:
        return []
    con = _conectar(banco)
    try:
        linhas = con.execute("""
            SELECT f.sessao, f.quem, f.ts, snippet(falas_fts, 0, '«', '»', ' … ', 40), s.origem
            FROM falas_fts JOIN falas f ON f.id = falas_fts.rowid JOIN sessoes s ON s.id = f.sessao
            WHERE falas_fts MATCH ? ORDER BY bm25(falas_fts) LIMIT ?""", (consulta, limite * 6)).fetchall()
    except sqlite3.OperationalError:
        linhas = []
    finally:
        con.close()
    achados, por_sessao = [], {}
    for sessao, quem, ts, trecho, origem in linhas:
        if sessao == ignorar_sessao or por_sessao.get(sessao, 0) >= 2:
            continue
        por_sessao[sessao] = por_sessao.get(sessao, 0) + 1
        quando = datetime.fromtimestamp(ts).strftime("%d/%m/%Y") if ts else "?"
        achados.append(Achado(sessao, quem, quando, trecho, origem))
        if len(achados) >= limite:
            break
    return achados


def lembrancas(texto: str, ignorar_sessao: str | None = None, limite: int = 4,
               banco: Path | None = None) -> str:
    """Bloco de contexto com o que já foi tratado antes sobre o assunto (vazio se nada)."""
    achados = buscar(texto, limite, ignorar_sessao, banco)
    if not achados:
        return ""
    linhas = [f"- [{a.quando}, {'você' if a.quem == 'voce' else 'Central'}"
              f"{', resultado de equipe' if a.origem == 'equipe' else ''}] {a.trecho}" for a in achados]
    return ("## Lembranças de conversas anteriores (podem estar desatualizadas; use só se ajudarem)\n"
            + "\n".join(linhas))


def formatar(achados: list[Achado]) -> str:
    if not achados:
        return "Nada encontrado nas conversas anteriores."
    return "🔎 Nas nossas conversas anteriores:\n\n" + "\n\n".join(
        f"• {a.quando} — {'você' if a.quem == 'voce' else 'Central'}"
        f"{' (equipe)' if a.origem == 'equipe' else ''}:\n  {a.trecho}" for a in achados)
