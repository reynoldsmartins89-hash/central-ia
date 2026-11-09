"""Detector de arquivos duplicados (pelo CONTEÚDO, não pelo nome).

Como funciona (do mais barato ao mais caro):
  1. agrupa arquivos do catálogo com o MESMO tamanho;
  2. compara uma "impressão parcial" (início + fim do arquivo);
  3. confirma com a impressão digital completa (SHA-256) — só são duplicados se forem idênticos.

Separar: as CÓPIAS (nunca o original escolhido) vão para
  <disco>\\DUPLICADOS PARA REVISAR\\<caminho original>
só depois da sua aprovação. Nada é apagado: você revisa e exclui depois, quando quiser.
Arquivos de programas (dll, exe…) e pastas de sistema são ignorados — é normal estarem repetidos.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from .curador import (PASTAS_DE_PROGRAMA, REGISTRO_MOVIMENTOS, SINAIS_DE_PROGRAMA, _conectar, _livre, _raiz)

PASTA_QUARENTENA = "DUPLICADOS PARA REVISAR"
BLOCO = 64 * 1024


def _tabela():
    con = _conectar()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS duplicados (
            caminho TEXT PRIMARY KEY, grupo TEXT, tamanho INTEGER, manter INTEGER, status TEXT);
        CREATE INDEX IF NOT EXISTS idx_dup_grupo ON duplicados(grupo);
    """)
    return con


def _ignorar(caminho: str, raiz: str) -> bool:
    p = Path(caminho)
    try:
        partes = p.relative_to(raiz).parts      # só o que está DENTRO da raiz analisada
    except ValueError:
        partes = p.parts
    return (p.suffix.lower() in SINAIS_DE_PROGRAMA or PASTA_QUARENTENA in partes
            or bool({x.lower() for x in partes[:-1]} & PASTAS_DE_PROGRAMA))


def _parcial(caminho: str, tamanho: int) -> str:
    h = hashlib.sha256(str(tamanho).encode())
    with open(caminho, "rb") as f:
        h.update(f.read(BLOCO))
        if tamanho > 2 * BLOCO:
            f.seek(-BLOCO, 2)
            h.update(f.read(BLOCO))
    return h.hexdigest()


def _completo(caminho: str) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def encontrar(raizes: list[str | Path] | None = None, tamanho_minimo: int = 1_000_000, log=print) -> dict:
    """Procura duplicados entre as raízes catalogadas (ou todas). Somente leitura."""
    con = _tabela()
    filtro, args = "", []
    if raizes:
        filtro = "AND raiz IN (" + ",".join("?" * len(raizes)) + ")"
        args = [str(_raiz(r)) for r in raizes]
    grupos_tamanho = con.execute(f"""SELECT tamanho, group_concat(caminho || char(30) || raiz, char(31)) FROM arquivos
                                     WHERE tamanho >= ? {filtro} GROUP BY tamanho HAVING count(*) > 1""",
                                 [tamanho_minimo, *args]).fetchall()
    log(f"   {len(grupos_tamanho):,} grupos de arquivos com o mesmo tamanho para comparar…")
    con.execute("DELETE FROM duplicados WHERE status = 'detectado'")
    total_grupos, desperdicio = 0, 0
    for n, (tamanho, caminhos) in enumerate(grupos_tamanho, 1):
        pares = [item.split(chr(30)) for item in caminhos.split(chr(31))]
        candidatos = [c for c, r in pares if not _ignorar(c, r)]
        if len(candidatos) < 2:
            continue
        por_parcial: dict[str, list[str]] = {}
        for c in candidatos:
            try:
                por_parcial.setdefault(_parcial(c, tamanho), []).append(c)
            except OSError:
                continue
        for iguais in (g for g in por_parcial.values() if len(g) > 1):
            por_hash: dict[str, list[str]] = {}
            for c in iguais:
                try:
                    por_hash.setdefault(_completo(c), []).append(c)
                except OSError:
                    continue
            for hash_, copias in por_hash.items():
                if len(copias) < 2:
                    continue
                # mantém o mais antigo (provável original); empate → caminho mais curto
                datas = dict(con.execute(f"SELECT caminho, modificado FROM arquivos WHERE caminho IN "
                                         f"({','.join('?' * len(copias))})", copias).fetchall())
                original = min(copias, key=lambda c: (datas.get(c, 0), len(c)))
                for c in copias:
                    con.execute("INSERT OR REPLACE INTO duplicados VALUES (?, ?, ?, ?, 'detectado')",
                                (c, hash_, tamanho, int(c == original)))
                con.commit()   # libera o catálogo na hora (o hash de arquivos grandes demora)
                total_grupos += 1
                desperdicio += tamanho * (len(copias) - 1)
        if n % 500 == 0:
            con.commit()
            log(f"   {n:,}/{len(grupos_tamanho):,} grupos verificados; {total_grupos:,} duplicados até agora")
    con.commit()
    con.close()
    resumo = {"grupos": total_grupos, "espaco_recuperavel_gb": round(desperdicio / 1e9, 1)}
    log(f"✅ {total_grupos:,} conjuntos de arquivos duplicados; {resumo['espaco_recuperavel_gb']} GB em cópias")
    return resumo


def relatorio(limite: int = 15) -> str:
    con = _tabela()
    grupos, copias, espaco = con.execute("""SELECT count(DISTINCT grupo), sum(1 - manter),
                                            coalesce(sum(CASE WHEN manter = 0 THEN tamanho END), 0)
                                            FROM duplicados WHERE status = 'detectado'""").fetchone()
    maiores = con.execute("""SELECT grupo, max(tamanho), count(*) FROM duplicados WHERE status = 'detectado'
                             GROUP BY grupo ORDER BY max(tamanho) * (count(*) - 1) DESC LIMIT ?""",
                          (limite,)).fetchall()
    linhas = [f"{grupos or 0:,} conjuntos · {copias or 0:,} cópias · {espaco / 1e9:.1f} GB recuperáveis"]
    for grupo, tamanho, n in maiores:
        itens = con.execute("SELECT caminho, manter FROM duplicados WHERE grupo = ? ORDER BY manter DESC",
                            (grupo,)).fetchall()
        linhas.append(f"\n{n} cópias de {tamanho / 1e6:.0f} MB:")
        linhas += [f"  {'✔ manter ' if m else '✖ cópia  '} {c}" for c, m in itens]
    con.close()
    return "\n".join(linhas)


def separar(raiz: str | Path, quantidade: int = 100, log=print) -> int:
    """Move CÓPIAS para a quarentena do mesmo disco. Chame SOMENTE após aprovação do usuário."""
    raiz = _raiz(raiz)
    quarentena = raiz / PASTA_QUARENTENA
    lote_id = "dup-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    con, movidos = _tabela(), 0
    for (caminho,) in con.execute("""SELECT caminho FROM duplicados WHERE status = 'detectado' AND manter = 0
                                     AND caminho LIKE ? ORDER BY tamanho DESC LIMIT ?""",
                                  (str(raiz).rstrip("\\") + "\\%", quantidade)).fetchall():
        origem = Path(caminho)
        if not origem.exists():
            continue
        alvo = _livre(quarentena / origem.relative_to(raiz))
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(origem), str(alvo))
        with open(REGISTRO_MOVIMENTOS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"lote": lote_id, "de": str(origem), "para": str(alvo)}, ensure_ascii=False) + "\n")
        con.execute("UPDATE duplicados SET status = 'separado' WHERE caminho = ?", (caminho,))
        movidos += 1
    con.commit()
    con.close()
    log(f"✅ {movidos} cópias separadas em {quarentena} (lote {lote_id}). Para desfazer: curador desfazer")
    return movidos


# ------------------------------------------------------------------ verificação final e exclusão segura
def _onde_ficou() -> dict[str, str]:
    """caminho antigo da cópia → onde ela está agora (pelo registro de movimentos)."""
    mapa = {}
    if REGISTRO_MOVIMENTOS.exists():
        for linha in REGISTRO_MOVIMENTOS.read_text(encoding="utf-8").splitlines():
            if linha.strip():
                r = json.loads(linha)
                mapa[r["de"]] = r["para"]
    return mapa


def _conferir_um(con, copia_antiga: str, grupo: str, mapa: dict) -> tuple[str, str, str | None]:
    """Relê a cópia e o original INTEIROS. Devolve (resultado, explicação, caminho atual da cópia)."""
    copia = mapa.get(copia_antiga)
    if not copia or not Path(copia).exists():
        return "copia_sumiu", "a cópia não está mais na pasta de revisão", copia
    originais = [c for (c,) in con.execute(
        "SELECT caminho FROM duplicados WHERE grupo = ? AND manter = 1", (grupo,)).fetchall()]
    if not originais or not Path(originais[0]).exists():
        return "sem_original", "o ORIGINAL não existe mais — a cópia é a única: NÃO apagar", copia
    if _completo(copia) != grupo:
        return "divergente", "a cópia mudou desde a detecção — NÃO apagar", copia
    if _completo(originais[0]) != grupo:
        return "divergente", "o original mudou desde a detecção — NÃO apagar a cópia", copia
    return "identico", f"idêntico byte a byte ao original {originais[0]}", copia


def conferir(raiz: str | Path, apagar: bool = False, log=print) -> dict:
    """Relê cada cópia separada E o seu original, inteiros (SHA-256). Com apagar=True, exclui a cópia
    SOMENTE se os dois forem idênticos naquele instante; qualquer dúvida = a cópia fica."""
    raiz = _raiz(raiz)
    prefixo = str(raiz).rstrip("\\/") + "\\%"
    con, mapa = _tabela(), _onde_ficou()
    con.execute("""CREATE TABLE IF NOT EXISTS conferencia (caminho TEXT PRIMARY KEY, copia TEXT,
                   resultado TEXT, detalhe TEXT, quando TEXT)""")
    fila = con.execute("""SELECT caminho, grupo, tamanho FROM duplicados WHERE status = 'separado'
                          AND manter = 0 AND caminho LIKE ? ORDER BY tamanho DESC""", (prefixo,)).fetchall()
    log(f"🔍 Conferindo {len(fila):,} cópias, relendo cada uma e o seu original por inteiro…")
    contagem: dict[str, int] = {}
    liberado = 0
    for n, (caminho, grupo, tamanho) in enumerate(fila, 1):
        try:
            resultado, detalhe, copia = _conferir_um(con, caminho, grupo, mapa)
        except OSError as erro:
            resultado, detalhe, copia = "erro_leitura", f"não consegui ler: {erro}", mapa.get(caminho)
        if apagar and resultado == "identico":
            Path(copia).unlink()
            resultado, liberado = "apagado", liberado + tamanho
            con.execute("UPDATE duplicados SET status = 'apagado' WHERE caminho = ?", (caminho,))
        con.execute("INSERT OR REPLACE INTO conferencia VALUES (?, ?, ?, ?, ?)",
                    (caminho, copia, resultado, detalhe, datetime.now().isoformat(timespec="minutes")))
        con.commit()
        contagem[resultado] = contagem.get(resultado, 0) + 1
        if n % 250 == 0:
            log(f"   {n:,}/{len(fila):,} · " + " · ".join(f"{k}: {v:,}" for k, v in contagem.items()))
    con.close()
    nomes = {"identico": "✅ idênticas (seguras para apagar)", "apagado": "🗑 apagadas após conferência",
             "divergente": "⚠ diferentes — mantidas", "sem_original": "⚠ original sumiu — mantidas",
             "copia_sumiu": "🔍 cópia não encontrada", "erro_leitura": "⚠ erro de leitura — mantidas"}
    log("Resultado: " + " · ".join(f"{nomes.get(k, k)}: {v:,}" for k, v in contagem.items())
        + (f" · {liberado / 1e9:.1f} GB liberados" if apagar else ""))
    return contagem
