"""Agente curador: cataloga Drive e HDs, classifica por tema e propõe reorganizar —
só move arquivos com o SEU consentimento, e tudo pode ser desfeito.

Etapas (cada uma pode ser rodada quantas vezes quiser):
  1. catalogar   — lê nomes, tamanhos e datas (não abre nem altera nada)
  2. coleções    — agrupa por PASTA; pastas com programas/sistemas ficam PROTEGIDAS
  3. classificar — a IA lê o nome da pasta + exemplos de arquivos e sugere um tema
  4. propostas   — mostra para onde cada pasta iria (nada é movido)
  5. aplicar     — move um lote de pastas, depois da sua aprovação (terminal ou Telegram)
  6. desfazer    — devolve o último lote ao lugar original

O curador move PASTAS INTEIRAS, nunca arquivos soltos de dentro delas
(isso quebraria programas instalados e cursos).

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


def _raiz(raiz: str | Path) -> Path:
    """Normaliza o disco/pasta: 'E:', 'E:\\' e 'E:"' (aspas que o Windows às vezes deixa) viram 'E:\\'."""
    texto = str(raiz).strip().strip('"').strip("'")
    if re.fullmatch(r"[A-Za-z]:", texto):
        texto += "\\"
    return Path(texto)


def _tema_oficial(tema: str, temas: list[str]) -> str | None:
    """Aceita o tema mesmo se a IA omitir o número ('COMUNICAÇÃO & …' → '01. COMUNICAÇÃO & …')."""
    if tema in temas:
        return tema
    limpo = lambda t: re.sub(r"^\s*\d+\.\s*", "", t).strip().upper()  # noqa: E731
    return next((t for t in temas if limpo(t) == limpo(tema)), None)


def _conectar() -> sqlite3.Connection:
    PASTA.mkdir(parents=True, exist_ok=True)
    # timeout + WAL: vários agentes (leitor, duplicados, integridade) podem usar o catálogo ao mesmo tempo
    con = sqlite3.connect(CATALOGO, timeout=300)
    con.execute("PRAGMA journal_mode=WAL")
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
    raiz = _raiz(raiz)
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
    filtro, args = ("WHERE raiz = ?", (str(_raiz(raiz)),)) if raiz else ("", ())
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


# ---------------------------------------------------------------- 2. coleções
# O curador organiza PASTAS INTEIRAS (coleções), nunca arquivos soltos de dentro delas:
# mover um arquivo de dentro de um programa ou de um curso quebraria o programa/curso.

SINAIS_DE_PROGRAMA = {".exe", ".dll", ".sys", ".msi", ".pyd", ".so", ".jar", ".bat", ".cmd", ".ps1"}
PASTAS_DE_PROGRAMA = {"site-packages", "node_modules", ".git", "program files", "program files (x86)",
                      "appdata", "windows", "bin", "lib", "apps", "venv", ".venv", "__pycache__"}


def _conectar_colecoes() -> sqlite3.Connection:
    con = _conectar()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS colecoes (
            caminho TEXT PRIMARY KEY, raiz TEXT, arquivos INTEGER, tamanho INTEGER,
            amostra TEXT, protegida INTEGER, motivo TEXT, tema TEXT, confianca REAL,
            destino TEXT, status TEXT DEFAULT 'identificada');
    """)
    colunas = {c[1] for c in con.execute("PRAGMA table_info(colecoes)")}
    for coluna in ("subtema", "base_classificacao"):   # colunas novas em catálogos antigos
        if coluna not in colunas:
            con.execute(f"ALTER TABLE colecoes ADD COLUMN {coluna} TEXT")
    return con


ARQUIVOS_LIDOS_POR_COLECAO = 4


def _leitura_da_colecao(con: sqlite3.Connection, caminho: str) -> tuple[str, str]:
    """Lê o CONTEÚDO de alguns arquivos da coleção (variando os tipos). Retorna (texto, base)."""
    from . import extrator
    if Path(caminho).is_file() or not Path(caminho).exists():
        candidatos = [caminho]
    else:
        prefixo = caminho.rstrip("\\/") + os.sep
        linhas = con.execute("""SELECT caminho, extensao FROM arquivos WHERE caminho LIKE ? ESCAPE '!'
                                ORDER BY random() LIMIT 400""",
                             (prefixo.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%",)).fetchall()
        por_tipo: dict[str, list[str]] = {}
        for c, ext in linhas:
            if extrator.tem_texto(c):
                por_tipo.setdefault(ext, []).append(c)
        candidatos = []
        while por_tipo and len(candidatos) < ARQUIVOS_LIDOS_POR_COLECAO:   # um de cada tipo, em rodízio
            for ext in list(por_tipo):
                candidatos.append(por_tipo[ext].pop())
                if not por_tipo[ext]:
                    del por_tipo[ext]
                if len(candidatos) >= ARQUIVOS_LIDOS_POR_COLECAO:
                    break
    trechos = []
    for c in candidatos:
        metodo, texto = extrator.extrair(c)
        if metodo == "conteúdo" and len(texto) > 80:
            trechos.append(f"[{Path(c).name}] {texto[:900]}")
    if trechos:
        return "\n".join(trechos), f"conteúdo lido de {len(trechos)} arquivo(s)"
    return "", "sem texto legível — classificado pelo nome da pasta e dos arquivos"


PASTAS_DA_CENTRAL = {"duplicados para revisar", "reparados pela central", "para apagar - revisar",
                     "organizado pelo curador", "_central_ia_temp", "takeout"}


def _protecao_da_central(caminho: str) -> str | None:
    """Motivo para NUNCA mover: é (ou contém, ou está dentro de) a base de um agente, ou é pasta da Central."""
    alvo = Path(caminho)
    if alvo.name.lower() in PASTAS_DA_CENTRAL:
        return "pasta da própria Central (mantida como está)"
    try:
        from .agentes import carregar_equipe
        _, equipe = carregar_equipe()
    except Exception:  # noqa: BLE001
        return None
    a = str(alvo).lower().replace("/", "\\").rstrip("\\")
    for nome, ag in equipe.items():
        if not ag.base:
            continue
        b = str(Path(ag.base)).lower().replace("/", "\\").rstrip("\\")
        if a == b or b.startswith(a + "\\") or a.startswith(b + "\\"):
            return f"base de conhecimento do agente {nome} (mover quebraria o agente)"
    return None


def identificar_colecoes(raiz: str | Path, profundidade: int = 1, log=print) -> int:
    """Agrupa o catálogo em coleções (pastas no nível `profundidade` abaixo da raiz) e marca
    como PROTEGIDAS as que parecem programas, códigos ou sistemas — essas nunca são movidas."""
    raiz = _raiz(raiz)
    con = _conectar_colecoes()
    grupos: dict[str, dict] = {}
    for caminho, nome, ext, tamanho in con.execute(
            "SELECT caminho, nome, extensao, tamanho FROM arquivos WHERE raiz = ?", (str(raiz),)):
        partes = Path(caminho).relative_to(raiz).parts
        if len(partes) <= profundidade:          # arquivo solto: é a própria coleção
            chave = caminho
        else:
            chave = str(raiz.joinpath(*partes[:profundidade]))
        g = grupos.setdefault(chave, {"n": 0, "tam": 0, "amostra": [], "programa": None})
        g["n"] += 1
        g["tam"] += tamanho or 0
        if len(g["amostra"]) < 12 and ext not in SINAIS_DE_PROGRAMA:
            g["amostra"].append(nome)
        if g["programa"] is None and len(partes) > profundidade and \
                re.search(r"backup|pen ?drive|dcim", Path(chave).name, re.I):
            g["programa"] = "backup/cópia de dispositivo (mantida como está)"
        if g["programa"] is None:
            pastas = {p.lower() for p in partes[:-1]}
            if ext in SINAIS_DE_PROGRAMA:
                g["programa"] = f"contém programa ({ext})"
            elif pastas & PASTAS_DE_PROGRAMA:
                g["programa"] = f"contém pasta de sistema ({sorted(pastas & PASTAS_DE_PROGRAMA)[0]})"
    for chave, g in grupos.items():   # bases dos agentes e pastas da própria Central NUNCA saem do lugar
        motivo = _protecao_da_central(chave)
        if motivo:
            g["programa"] = motivo
    for chave, g in grupos.items():
        con.execute("""INSERT INTO colecoes (caminho, raiz, arquivos, tamanho, amostra, protegida, motivo)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(caminho) DO UPDATE SET arquivos=excluded.arquivos, tamanho=excluded.tamanho,
                       amostra=excluded.amostra, protegida=excluded.protegida, motivo=excluded.motivo""",
                    (chave, str(raiz), g["n"], g["tam"], json.dumps(g["amostra"], ensure_ascii=False),
                     int(bool(g["programa"])), g["programa"] or ""))
    con.commit()
    protegidas = sum(1 for g in grupos.values() if g["programa"])
    con.close()
    log(f"✅ {len(grupos)} coleções em {raiz} ({protegidas} protegidas por conterem programas/sistemas)")
    return len(grupos)


# ---------------------------------------------------------------- 3. classificar
def classificar(raiz: str | Path, limite: int = 200, temas: list[str] | None = None,
                tamanho_lote: int = 8, log=print) -> int:
    """A IA LÊ O CONTEÚDO de alguns arquivos de cada coleção (PDF, Word, e-book, etc.), além do nome
    da pasta e dos arquivos, e sugere tema + subtema. Coleções protegidas não são classificadas."""
    temas = temas or TEMAS_PADRAO
    raiz = _raiz(raiz)
    con = _conectar_colecoes()
    if not con.execute("SELECT 1 FROM colecoes WHERE raiz = ? LIMIT 1", (str(raiz),)).fetchone():
        con.close()
        identificar_colecoes(raiz, log=log)
        con = _conectar_colecoes()
    pendentes = con.execute("""SELECT caminho, arquivos, amostra FROM colecoes WHERE raiz = ? AND protegida = 0
                               AND tema IS NULL ORDER BY tamanho DESC LIMIT ?""", (str(raiz), limite)).fetchall()
    sistema = ("Você é um bibliotecário que organiza coleções (pastas ou arquivos) por tema e subtema. "
               "Para cada coleção você recebe o nome, exemplos de nomes de arquivos e TRECHOS DO CONTEÚDO "
               "lido de alguns arquivos. O CONTEÚDO tem prioridade sobre os nomes, que muitas vezes enganam. "
               "Escolha o tema da lista, crie um subtema curto e específico (2 a 4 palavras, ex.: 'Rosacruz e "
               "esoterismo', 'Tráfego pago', 'Receitas culinárias') e dê uma confiança de 0 a 1 "
               "(menor quando só houver nomes, sem conteúdo). Se for impossível saber, use "
               "'99. NÃO CLASSIFICADO'. Responda SOMENTE com JSON.")
    feitos = 0
    for inicio in range(0, len(pendentes), tamanho_lote):
        lote = pendentes[inicio:inicio + tamanho_lote]
        blocos, bases = [], []
        for i, (c, n, a) in enumerate(lote):
            conteudo, base = _leitura_da_colecao(con, c)
            bases.append(base)
            blocos.append(f"### {i}: '{os.path.relpath(c, raiz)}' ({n} arquivos)\n"
                          f"Nomes de exemplo: {', '.join(json.loads(a)[:8])}\n"
                          f"Conteúdo lido: {conteudo or '(nenhum texto legível: vídeos, áudios ou imagens)'}")
        log(f"   📖 conteúdo lido de {sum(b.startswith('conteúdo') for b in bases)}/{len(lote)} coleções do lote")
        mensagem = (f"Temas permitidos:\n" + "\n".join(temas) + "\n\nColeções:\n" + "\n\n".join(blocos) + "\n\n"
                    'Formato: [{"i": 0, "tema": "<tema exato da lista>", "subtema": "<subtema>", "confianca": 0.0}]')
        try:
            _, resposta = provedores.chamar_com_reserva(MODELOS, sistema, mensagem, log=log)
            bloco = re.search(r"\[.*\]", resposta, re.S)
            dados = json.loads(bloco.group(0)) if bloco else []
        except Exception as erro:  # noqa: BLE001
            log(f"   ⚠ lote ignorado: {str(erro)[:100]}")
            continue
        for d in dados:
            try:
                i, tema = int(d["i"]), _tema_oficial(str(d["tema"]), temas)
            except (KeyError, ValueError, TypeError):
                continue
            if tema and 0 <= i < len(lote):
                subtema = re.sub(r'[\\/:*?"<>|]+', " ", str(d.get("subtema") or "")).strip()[:60] or "Geral"
                con.execute("""UPDATE colecoes SET tema = ?, subtema = ?, confianca = ?, base_classificacao = ?,
                               status = 'classificada' WHERE caminho = ?""",
                            (tema, subtema, float(d.get("confianca") or 0.5), bases[i], lote[i][0]))
                feitos += 1
        con.commit()
        log(f"   classificadas {feitos}/{len(pendentes)} coleções")
    con.close()
    return feitos


# ---------------------------------------------------------------- 4. propostas
def propor(raiz: str | Path, pasta_organizada: str | Path | None = None, confianca_minima: float = 0.7) -> int:
    """Calcula para onde cada coleção iria. NADA é movido aqui."""
    raiz = _raiz(raiz)
    base = Path(pasta_organizada) if pasta_organizada else raiz / "ORGANIZADO PELO CURADOR"
    con = _conectar_colecoes()
    n = 0
    for caminho, tema, subtema in con.execute("""SELECT caminho, tema, coalesce(subtema, 'Geral') FROM colecoes
                                        WHERE raiz = ? AND protegida = 0 AND status = 'classificada'
                                        AND confianca >= ? AND tema != '99. NÃO CLASSIFICADO'""",
                                     (str(raiz), confianca_minima)).fetchall():
        atual = Path(caminho)
        if tema in atual.parts or base in atual.parents or atual == base:
            continue
        destino = base / tema / subtema / atual.name   # tema → subtema → a pasta (ou arquivo) original
        con.execute("UPDATE colecoes SET destino = ?, status = 'proposta' WHERE caminho = ?", (str(destino), caminho))
        n += 1
    con.commit()
    con.close()
    return n


def listar_propostas(raiz: str | Path, limite: int = 20) -> list[tuple[str, str, str]]:
    con = _conectar_colecoes()
    linhas = con.execute("""SELECT caminho, destino, tema FROM colecoes WHERE raiz = ? AND status = 'proposta'
                            ORDER BY confianca DESC, tamanho DESC LIMIT ?""", (str(_raiz(raiz)), limite)).fetchall()
    con.close()
    return linhas


def resumo_colecoes(raiz: str | Path) -> str:
    con = _conectar_colecoes()
    r = str(_raiz(raiz))
    total, prot = con.execute("SELECT count(*), sum(protegida) FROM colecoes WHERE raiz = ?", (r,)).fetchone()
    temas = con.execute("""SELECT tema, count(*), sum(tamanho) FROM colecoes WHERE raiz = ? AND tema IS NOT NULL
                           GROUP BY tema ORDER BY sum(tamanho) DESC""", (r,)).fetchall()
    con.close()
    linhas = [f"{total or 0} coleções · {prot or 0} protegidas (programas/sistemas, não serão movidas)"]
    linhas += [f"• {t}: {n} coleções, {s / 1e9:.1f} GB" for t, n, s in temas]
    return "\n".join(linhas)


# ---------------------------------------------------------------- 5. aplicar / 6. desfazer
def _livre(destino: Path) -> Path:
    if not destino.exists():
        return destino
    for n in range(2, 1000):
        candidato = destino.with_name(f"{destino.stem} ({n}){destino.suffix}")
        if not candidato.exists():
            return candidato
    raise FileExistsError(destino)


def aplicar(raiz: str | Path, quantidade: int = 10, log=print) -> int:
    """Move um lote de COLEÇÕES propostas. Chame SOMENTE depois da aprovação do usuário."""
    raiz = _raiz(raiz)
    lote_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    movidos = 0
    con = _conectar_colecoes()
    for caminho, destino in con.execute("""SELECT caminho, destino FROM colecoes WHERE raiz = ? AND status = 'proposta'
                                           AND protegida = 0 ORDER BY confianca DESC, tamanho DESC LIMIT ?""",
                                        (str(raiz), quantidade)).fetchall():
        origem, alvo = Path(caminho), Path(destino)
        if raiz not in alvo.parents:              # nunca move para fora da raiz
            continue
        if not origem.exists():
            con.execute("UPDATE colecoes SET status = 'sumiu' WHERE caminho = ?", (caminho,))
            continue
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo = _livre(alvo)
        shutil.move(str(origem), str(alvo))
        with open(REGISTRO_MOVIMENTOS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"lote": lote_id, "de": str(origem), "para": str(alvo)}, ensure_ascii=False) + "\n")
        con.execute("UPDATE colecoes SET caminho = ?, status = 'movida' WHERE caminho = ?", (str(alvo), caminho))
        movidos += 1
    con.commit()
    con.close()
    log(f"✅ {movidos} coleções movidas (lote {lote_id}). Para desfazer: curador desfazer")
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
    con, desfeitos = _conectar_colecoes(), 0
    for r in reversed([r for r in registros if r["lote"] == ultimo]):
        de, para = Path(r["de"]), Path(r["para"])
        if para.exists() and not de.exists():
            de.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(para), str(de))
            con.execute("UPDATE colecoes SET caminho = ?, status = 'proposta' WHERE caminho = ?", (str(de), str(para)))
            desfeitos += 1
    con.commit()
    con.close()
    restantes = [r for r in registros if r["lote"] != ultimo]
    REGISTRO_MOVIMENTOS.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in restantes),
                                   encoding="utf-8")
    log(f"↩ {desfeitos} coleções devolvidas (lote {ultimo}).")
    return desfeitos
