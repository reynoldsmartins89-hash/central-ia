"""Agente curador: cataloga Drive e HDs, classifica por tema e propõe reorganizar —
só move arquivos com o SEU consentimento, e tudo pode ser desfeito.

Etapas (cada uma pode ser rodada quantas vezes quiser):
  1. catalogar   — lê nomes, tamanhos e datas (não abre nem altera nada)
  2. classificar — a IA lê só o CAMINHO/NOME dos arquivos e sugere um tema
  3. propostas   — mostra para onde cada arquivo iria (nada é movido)
  4. aplicar     — move um lote, depois da sua aprovação (terminal ou Telegram)
  5. desfazer    — devolve o último lote ao lugar original

Regras de segurança: nunca apaga; nunca sobrescreve (renomeia se já existir);
só move dentro da mesma raiz (ex.: dentro do Google Drive); registra cada movimento.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from . import provedores

PASTA = Path.home() / ".central-ia"
CATALOGO = PASTA / "catalogo.db"
REGISTRO_MOVIMENTOS = PASTA / "movimentos.jsonl"
IGNORAR = {"$recycle.bin", "system volume information", ".git", "node_modules", "__pycache__",
           ".tmp.drivedownload", ".tmp.driveupload", "found.000"}
MODELOS = ["groq:openai/gpt-oss-120b", "gemini:gemini-3.5-flash", "gemini:gemini-flash-lite-latest",
           "ollama:qwen3:4b"]

# Temas padrão = as categorias que você já usa na pasta EBOOKS
TEMAS_PADRAO = [
    "01. COMUNICAÇÃO & HABILIDADES SOCIAIS",
    "02. MENTALIDADE, FOCO & ALTA PERFORMANCE",
    "03. NEGÓCIOS, GESTÃO & LIDERANÇA",
    "04. FINANÇAS & INVESTIMENTOS",
    "05. ESTRATÉGIA, INTELIGÊNCIA & DEFESA",
    "06. CIÊNCIAS, EXATAS & GEOTECNOLOGIA",
    "07. FILOSOFIA, POLÍTICA & SOCIEDADE",
    "08. HISTÓRIA & BIOGRAFIAS",
    "09. ESPIRITUALIDADE, TEOLOGIA & TRADIÇÃO",
    "10. SAÚDE, CORPO & ESTILO DE VIDA",
    "11. LITERATURA, FICÇÃO & CLÁSSICOS",
    "12. TECNOLOGIA, DADOS & IA",
    "13. MARKETING & CONTEÚDO DIGITAL",
    "14. DOCUMENTOS PESSOAIS & TRABALHO",
    "15. MÍDIA (FOTOS, VÍDEOS, ÁUDIOS)",
    "99. NÃO CLASSIFICADO",
]


def _conectar() -> sqlite3.Connection:
    PASTA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CATALOGO)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS arquivos (
            caminho TEXT PRIMARY KEY, raiz TEXT, nome TEXT, extensao TEXT,
            tamanho INTEGER, modificado REAL, tema TEXT, confianca REAL,
            destino TEXT, status TEXT DEFAULT 'catalogado');
        CREATE INDEX IF NOT EXISTS idx_raiz ON arquivos(raiz);
        CREATE INDEX IF NOT EXISTS idx_status ON arquivos(raiz, status);
    """)
    return con


# ---------------------------------------------------------------- 1. catalogar
def catalogar(raiz: str | Path, log=print) -> int:
    """Percorre a pasta e guarda nome, tamanho e data de cada arquivo. Somente leitura."""
    raiz = Path(raiz)
    con, total, inicio = _conectar(), 0, time.time()
    pilha = [raiz]
    while pilha:
        pasta = pilha.pop()
        try:
            entradas = list(os.scandir(pasta))
        except OSError:
            continue
        lote = []
        for e in entradas:
            if e.name.lower() in IGNORAR:
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    pilha.append(Path(e.path))
                elif e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    lote.append((e.path, str(raiz), e.name, Path(e.name).suffix.lower(), st.st_size, st.st_mtime))
            except OSError:
                continue
        con.executemany("""INSERT INTO arquivos (caminho, raiz, nome, extensao, tamanho, modificado)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(caminho) DO UPDATE SET tamanho=excluded.tamanho,
                           modificado=excluded.modificado""", lote)
        total += len(lote)
        if total and total % 20000 < len(lote):
            con.commit()
            log(f"   {total:,} arquivos catalogados ({time.time() - inicio:.0f}s)…")
    con.commit()
    con.close()
    log(f"✅ {raiz}: {total:,} arquivos catalogados em {time.time() - inicio:.0f}s")
    return total


def resumo(raiz: str | Path | None = None) -> str:
    con = _conectar()
    filtro, args = ("WHERE raiz = ?", (str(raiz),)) if raiz else ("", ())
    total, tamanho = con.execute(f"SELECT count(*), coalesce(sum(tamanho),0) FROM arquivos {filtro}", args).fetchone()
    tipos = con.execute(f"SELECT extensao, count(*) FROM arquivos {filtro} GROUP BY extensao "
                        "ORDER BY count(*) DESC LIMIT 8", args).fetchall()
    status = con.execute(f"SELECT status, count(*) FROM arquivos {filtro} GROUP BY status", args).fetchall()
    con.close()
    return (f"{total:,} arquivos · {tamanho / 1e12:.2f} TB\n"
            f"Tipos: " + ", ".join(f"{e or '(sem)'} {n:,}" for e, n in tipos) + "\n"
            f"Situação: " + ", ".join(f"{s} {n:,}" for s, n in status))


def buscar(termo: str, limite: int = 25) -> list[tuple[str, int]]:
    """'O que eu tenho sobre X?' — procura no nome e no caminho de todos os discos catalogados."""
    palavras = [p for p in re.findall(r"\w{3,}", termo.lower())]
    if not palavras:
        return []
    con = _conectar()
    condicao = " AND ".join("lower(caminho) LIKE ?" for _ in palavras)
    linhas = con.execute(f"SELECT caminho, tamanho FROM arquivos WHERE {condicao} "
                         f"ORDER BY modificado DESC LIMIT ?", [f"%{p}%" for p in palavras] + [limite]).fetchall()
    con.close()
    return linhas


# ---------------------------------------------------------------- 2. classificar
def classificar(raiz: str | Path, limite: int = 500, temas: list[str] | None = None,
                tamanho_lote: int = 60, log=print) -> int:
    """A IA lê apenas o caminho relativo + nome do arquivo e sugere um tema."""
    temas = temas or TEMAS_PADRAO
    raiz = str(Path(raiz))
    con = _conectar()
    pendentes = con.execute("SELECT caminho FROM arquivos WHERE raiz = ? AND tema IS NULL LIMIT ?",
                            (raiz, limite)).fetchall()
    feitos = 0
    sistema = ("Você é um bibliotecário que organiza arquivos por tema. Recebe caminhos de arquivos "
               "e devolve, para cada um, o tema mais adequado da lista. Use o nome do arquivo E as pastas "
               "do caminho como pistas. Se não der para saber, use '99. NÃO CLASSIFICADO'. "
               "Responda SOMENTE com JSON.")
    for inicio in range(0, len(pendentes), tamanho_lote):
        lote = [c for (c,) in pendentes[inicio:inicio + tamanho_lote]]
        itens = "\n".join(f"{i}: {os.path.relpath(c, raiz)}" for i, c in enumerate(lote))
        mensagem = (f"Temas permitidos:\n" + "\n".join(temas) + f"\n\nArquivos:\n{itens}\n\n"
                    'Formato: [{"i": 0, "tema": "<tema exato da lista>", "confianca": 0.0-1.0}, ...]')
        try:
            _, resposta = provedores.chamar_com_reserva(MODELOS, sistema, mensagem, log=log)
            bloco = re.search(r"\[.*\]", resposta, re.S)
            dados = json.loads(bloco.group(0)) if bloco else []
        except Exception as erro:  # noqa: BLE001
            log(f"   ⚠ lote ignorado: {str(erro)[:100]}")
            continue
        for d in dados:
            try:
                i, tema = int(d["i"]), str(d["tema"])
            except (KeyError, ValueError, TypeError):
                continue
            if 0 <= i < len(lote) and tema in temas:
                con.execute("UPDATE arquivos SET tema = ?, confianca = ?, status = 'classificado' WHERE caminho = ?",
                            (tema, float(d.get("confianca", 0.5) or 0.5), lote[i]))
                feitos += 1
        con.commit()
        log(f"   classificados {feitos}/{len(pendentes)}")
    con.close()
    return feitos


# ---------------------------------------------------------------- 3. propostas
def propor(raiz: str | Path, pasta_organizada: str | Path | None = None, confianca_minima: float = 0.7) -> int:
    """Calcula para onde cada arquivo classificado iria. NADA é movido aqui."""
    raiz = Path(raiz)
    base = Path(pasta_organizada) if pasta_organizada else raiz / "ORGANIZADO PELO CURADOR"
    con = _conectar()
    linhas = con.execute("""SELECT caminho, tema FROM arquivos WHERE raiz = ? AND status = 'classificado'
                            AND confianca >= ? AND tema != '99. NÃO CLASSIFICADO'""",
                         (str(raiz), confianca_minima)).fetchall()
    n = 0
    for caminho, tema in linhas:
        atual = Path(caminho)
        if tema in atual.parts:          # já está numa pasta desse tema
            continue
        relativo = atual.relative_to(raiz)
        destino = base / tema / relativo  # mantém a estrutura original dentro do tema
        con.execute("UPDATE arquivos SET destino = ?, status = 'proposto' WHERE caminho = ?", (str(destino), caminho))
        n += 1
    con.commit()
    con.close()
    return n


def listar_propostas(raiz: str | Path, limite: int = 20) -> list[tuple[str, str, str]]:
    con = _conectar()
    linhas = con.execute("SELECT caminho, destino, tema FROM arquivos WHERE raiz = ? AND status = 'proposto' "
                         "ORDER BY confianca DESC LIMIT ?", (str(Path(raiz)), limite)).fetchall()
    con.close()
    return linhas


# ---------------------------------------------------------------- 4. aplicar / 5. desfazer
def _livre(destino: Path) -> Path:
    if not destino.exists():
        return destino
    for n in range(2, 1000):
        candidato = destino.with_name(f"{destino.stem} ({n}){destino.suffix}")
        if not candidato.exists():
            return candidato
    raise FileExistsError(destino)


def aplicar(raiz: str | Path, quantidade: int = 50, log=print) -> int:
    """Move um lote de arquivos propostos. Chame SOMENTE depois da aprovação do usuário."""
    raiz = Path(raiz)
    lote_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    movidos = 0
    con = _conectar()
    for caminho, destino in con.execute("SELECT caminho, destino FROM arquivos WHERE raiz = ? AND status = 'proposto' "
                                        "ORDER BY confianca DESC LIMIT ?", (str(raiz), quantidade)).fetchall():
        origem, alvo = Path(caminho), Path(destino)
        if alvo.anchor.lower() != raiz.anchor.lower() or raiz not in alvo.parents:
            continue                                   # nunca move para fora da raiz
        if not origem.exists():
            con.execute("UPDATE arquivos SET status = 'sumiu' WHERE caminho = ?", (caminho,))
            continue
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo = _livre(alvo)
        shutil.move(str(origem), str(alvo))
        with open(REGISTRO_MOVIMENTOS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"lote": lote_id, "de": str(origem), "para": str(alvo)}, ensure_ascii=False) + "\n")
        con.execute("UPDATE arquivos SET caminho = ?, status = 'movido' WHERE caminho = ?", (str(alvo), caminho))
        movidos += 1
    con.commit()
    con.close()
    log(f"✅ {movidos} arquivos movidos (lote {lote_id}). Para desfazer: curador desfazer")
    return movidos


def desfazer(log=print) -> int:
    """Devolve o ÚLTIMO lote movido aos lugares originais."""
    if not REGISTRO_MOVIMENTOS.exists():
        log("Nada para desfazer.")
        return 0
    registros = [json.loads(l) for l in REGISTRO_MOVIMENTOS.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not registros:
        return 0
    ultimo = registros[-1]["lote"]
    con, desfeitos = _conectar(), 0
    for r in reversed([r for r in registros if r["lote"] == ultimo]):
        de, para = Path(r["de"]), Path(r["para"])
        if para.exists() and not de.exists():
            de.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(para), str(de))
            con.execute("UPDATE arquivos SET caminho = ?, status = 'proposto' WHERE caminho = ?", (str(de), str(para)))
            desfeitos += 1
    con.commit()
    con.close()
    restantes = [r for r in registros if r["lote"] != ultimo]
    REGISTRO_MOVIMENTOS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in restantes),
                                   encoding="utf-8")
    log(f"↩ {desfeitos} arquivos devolvidos (lote {ultimo}).")
    return desfeitos
