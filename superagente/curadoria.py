"""Curadoria de biblioteca: monta a base de um agente a partir do CONTEÚDO dos seus materiais.

Para agentes cujo material está espalhado (HDs, Drive, e-books, cursos), em vez de uma pasta:
  1. junta candidatos — acervo lido dos HDs, bibliotecas das pastas Agente_… do Drive e o resto do Drive;
  2. a IA LOCAL (no seu PC, grátis, nada sai da máquina) LÊ um trecho do conteúdo de cada candidato
     e decide se entra — nunca só pelo nome do arquivo;
  3. o texto dos aprovados vira a biblioteca do agente (~/.central-ia/indices/<agente>.db).
As decisões ficam guardadas (~/.central-ia/curadoria/<agente>.json): rodar de novo continua de onde parou.

Uso:  python main.py curadoria mentor_educacao        (e: python main.py curadoria mentor_educacao --relatorio)
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

PASTA = Path.home() / ".central-ia"
PASTA_DECISOES = PASTA / "curadoria"
LOTE = 6                     # candidatos por pergunta à IA
TRECHO = 900                 # caracteres de cada candidato mostrados à IA (início + meio)
JUIZES = ["cli:claude/haiku", "gemini:gemini-3.5-flash", "groq:openai/gpt-oss-120b"]   # Haiku: rigoroso e rápido
NOTA_MINIMA = 7
EXTENSOES_DRIVE = {".md", ".txt", ".pdf", ".docx", ".pptx", ".epub", ".odt", ".doc", ".rtf", ".html"}
MIDIA = {".mp4", ".mkv", ".avi", ".mov", ".mp3", ".m4a", ".wav", ".ogg", ".webm", ".wmv"}

TEMAS = {
    "mentor_educacao": {
        "descricao": (
            "educação e ensino; pedagogia e PSICOPEDAGOGIA (inclusive de crianças: dificuldades de aprendizagem, "
            "avaliação, intervenção, alfabetização); aprendizagem, técnicas de estudo, memória, atenção, leitura; "
            "NEUROCIÊNCIA e ciência cognitiva da aprendizagem; inteligência; EDUCAÇÃO CLÁSSICA (trivium, "
            "quadrivium, artes liberais, escolástica); patrística, santos e doutrina católica sobre EDUCAÇÃO e "
            "formação; vida intelectual, auto-educação e formação da personalidade"),
        "nao": ("material que só cita o tema de passagem; editais, documentos administrativos, planos de sessão "
                "militares, currículos pessoais, propaganda; aulas de outras matérias (direito, inglês, "
                "marketing…) que não ensinem COMO aprender ou ensinar"),
        "termos": [
            "trivium", "quadrivium", '"artes liberais"', "escolástica", "patrística", "paideia", "didascalicon",
            '"vida intelectual"', "sertillanges", '"educação clássica"', '"de magistro"', '"divini illius magistri"',
            '"hugo de são vítor"', '"auto-educação"', '"auto educação"', "autodidata",
            "neurociência", "neuroeducação", "neuroaprendizagem", "neuroplasticidade", "metacognição",
            '"memória de trabalho"', '"repetição espaçada"', '"funções executivas"', '"ciência cognitiva"',
            "psicopedagogia", "psicopedagógica", "pedagogia", "didática", '"dificuldades de aprendizagem"',
            "dislexia", "tdah", "alfabetização", '"técnicas de estudo"', '"como estudar"',
            '"inteligências múltiplas"', '"processo de aprendizagem"', '"teorias da aprendizagem"',
            "homeschooling", '"educação domiciliar"', '"técnicas de aprendizado"', '"tipos de aprendizado"',
            '"curva de aprendizagem"', "piaget", "vygotsky", "montessori", "mnemônica", "memorização"],
        "drive_nomes": ["educa", "aprendiz", "aprender", "neuroci", "pedagog", "estud", "trivium", "quadrivium",
                        "escolást", "patríst", "leitura", "memória", "memoriz", "inteligên", "didát",
                        "alfabetiz", "montessori", "cognit", "auto-educ", "autoeduc"],
    },
}


# ------------------------------------------------------------------ candidatos
def _amostra(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto or "").strip()
    if len(texto) <= TRECHO:
        return texto
    meio = len(texto) // 2
    return texto[: TRECHO * 2 // 3] + " […] " + texto[meio: meio + TRECHO // 3]


def _candidatos_acervo(termos: list[str]) -> dict[str, dict]:
    con = sqlite3.connect(PASTA / "catalogo.db", timeout=120)
    linhas = con.execute("SELECT caminho, titulo, texto FROM acervo WHERE acervo MATCH ?",
                         (" OR ".join(termos),)).fetchall()
    con.close()
    saida: dict[str, dict] = {}
    for caminho, titulo, texto in linhas:
        item = saida.setdefault(caminho, {"titulo": titulo or Path(caminho).stem, "fonte": "hd", "texto": ""})
        item["texto"] += (texto or "") + "\n\n"
    return saida


def _candidatos_bibliotecas(termos: list[str], ignorar: set[str]) -> dict[str, dict]:
    """Pastas Agente_… do Drive (já indexadas nas bibliotecas dos agentes não privados)."""
    from .agentes import carregar_equipe
    from .conhecimento import _caminho_indice
    saida: dict[str, dict] = {}
    for nome, agente in carregar_equipe()[1].items():
        if not agente.base or agente.privacidade == "local" or nome in ignorar or agente.base.startswith("curadoria:"):
            continue
        banco = _caminho_indice(nome)
        if not banco.exists():
            continue
        con = sqlite3.connect(banco, timeout=60)
        achados = {c for (c,) in con.execute("SELECT DISTINCT caminho FROM trechos WHERE trechos MATCH ?",
                                             (" OR ".join(termos),))}
        for caminho in achados:
            partes = con.execute("SELECT titulo, texto FROM trechos WHERE caminho = ? ORDER BY rowid",
                                 (caminho,)).fetchall()
            saida[caminho] = {"titulo": partes[0][0] or Path(caminho).stem, "fonte": f"drive:{nome}",
                              "texto": "\n\n".join(t for _, t in partes)}
        con.close()
    return saida


def _candidatos_drive(palavras: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Resto do Drive (fora das pastas Agente_…): escolhe pelo nome/pasta e LÊ o conteúdo."""
    from . import drive
    con = sqlite3.connect(PASTA / "catalogo.db", timeout=120)
    filtro = " OR ".join("lower(nome) LIKE ? OR lower(caminho) LIKE ?" for _ in palavras)
    args = [a for p in palavras for a in (f"%{p}%", f"%{p}%")]
    linhas = con.execute(f"SELECT caminho FROM drive WHERE caminho NOT LIKE 'Agente\\_%' ESCAPE '\\' "
                         f"AND ({filtro})", args).fetchall()
    con.close()
    saida, midias = {}, []
    for (caminho,) in linhas:
        ext = Path(caminho).suffix.lower()
        if ext in MIDIA:
            midias.append(caminho)
        elif ext in EXTENSOES_DRIVE:
            saida[f"drive:{caminho}"] = {"titulo": Path(caminho).stem, "fonte": "drive", "texto": None,
                                         "local": str(drive.caminho_local(caminho))}
    return saida, midias


def _ler_drive(item: dict) -> str:
    from . import leitor
    try:
        _, _, texto = leitor.ler(item["local"])
        return texto or ""
    except Exception:  # noqa: BLE001
        return ""


# ------------------------------------------------------------------ julgamento pela IA local
def _julgar(tema: dict, lote: list[tuple[str, dict]]) -> dict[str, tuple[bool, str]]:
    from . import provedores
    itens = "\n\n".join(f"[{i}] TÍTULO: {item['titulo']}\nCONTEÚDO: {_amostra(item['texto'])}"
                        for i, (_, item) in enumerate(lote, 1))
    sistema = ("Você é um bibliotecário RIGOROSO montando a biblioteca de um mentor de educação. Para cada material, "
               "leia o conteúdo e dê uma NOTA de 0 a 10 para o quanto o ASSUNTO CENTRAL dele é o tema pedido "
               "(10 = é inteiramente sobre isso; 5 = toca no tema; 0 = outro assunto). Seja exigente: material de "
               "outra área que só usa palavras como 'aprender' ou 'estudo' recebe nota baixa. Responda SOMENTE JSON: "
               '[{"n": 1, "nota": 8, "tema": "assunto em até 6 palavras"}, ...]')
    pedido = f"TEMA da biblioteca: {tema['descricao']}.\nNOTA BAIXA para: {tema['nao']}.\n\nMateriais:\n\n{itens}"
    _, r = provedores.chamar_com_reserva(JUIZES, sistema, pedido, log=lambda *_: None)
    dados = json.loads(re.search(r"\[.*\]", r, re.S).group(0))
    saida = {}
    for d in dados:
        n = int(d.get("n", 0))
        if 1 <= n <= len(lote):
            nota = float(d.get("nota", 0))
            saida[lote[n - 1][0]] = (nota >= NOTA_MINIMA, f"{str(d.get('tema', ''))[:60]}")
    return saida


def _decisoes(agente: str) -> dict:
    arquivo = PASTA_DECISOES / f"{agente}.json"
    return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}


def _gravar(agente: str, decisoes: dict) -> None:
    PASTA_DECISOES.mkdir(parents=True, exist_ok=True)
    (PASTA_DECISOES / f"{agente}.json").write_text(json.dumps(decisoes, ensure_ascii=False, indent=1),
                                                   encoding="utf-8")


def montar(agente: str, log=print) -> dict:
    tema = TEMAS[agente]
    decisoes = _decisoes(agente)
    decisoes.pop("_midias_drive", None)
    log("🔎 Juntando candidatos (HDs, pastas Agente_… do Drive e resto do Drive)…")
    candidatos = _candidatos_acervo(tema["termos"])
    candidatos.update(_candidatos_bibliotecas(tema["termos"], {agente}))
    do_drive, midias = _candidatos_drive(tema["drive_nomes"])
    candidatos.update(do_drive)
    pendentes = [(c, i) for c, i in candidatos.items() if c not in decisoes]
    log(f"   {len(candidatos):,} candidatos ({len(pendentes):,} ainda não lidos) · {len(midias)} vídeos/áudios no Drive")
    inicio, feitos = time.time(), 0
    for n in range(0, len(pendentes), LOTE):
        lote = pendentes[n:n + LOTE]
        for caminho, item in lote:
            if item["texto"] is None:          # arquivo do Drive: lê agora
                item["texto"] = _ler_drive(item)
        legiveis = [(c, i) for c, i in lote if (i["texto"] or "").strip()]
        for c, i in lote:
            if not (i["texto"] or "").strip():
                decisoes[c] = {"entra": False, "tema": "sem texto legível", "titulo": i["titulo"], "fonte": i["fonte"]}
        if legiveis:
            try:
                veredito = _julgar(tema, legiveis)
            except Exception as erro:  # noqa: BLE001 — resposta fora do formato: tenta um a um
                log(f"   (lote refeito um a um: {str(erro)[:80]})")
                veredito = {}
                for par in legiveis:
                    try:
                        veredito.update(_julgar(tema, [par]))
                    except Exception:  # noqa: BLE001
                        pass
            for c, i in legiveis:
                if c in veredito:
                    entra, assunto = veredito[c]
                    decisoes[c] = {"entra": entra, "tema": assunto, "titulo": i["titulo"], "fonte": i["fonte"]}
        feitos += len(lote)
        if feitos % 60 < LOTE or feitos == len(pendentes):
            _gravar(agente, decisoes)
            aprovados = sum(1 for d in decisoes.values() if d["entra"])
            log(f"   lidos {feitos:,}/{len(pendentes):,} · aprovados até agora: {aprovados:,} "
                f"({(time.time() - inicio) / 60:.0f} min)")
    _gravar(agente, decisoes)
    decisoes["_midias_drive"] = midias
    _gravar(agente, decisoes)
    total = indexar(agente, candidatos, log)
    return total


def indexar(agente: str, candidatos: dict | None = None, log=print) -> dict:
    """Grava o texto dos materiais aprovados na biblioteca do agente."""
    from .conhecimento import _caminho_indice, _conectar, _limpar, _trechos
    decisoes = {c: d for c, d in _decisoes(agente).items() if not c.startswith("_")}
    aprovados = [c for c, d in decisoes.items() if d["entra"]]
    if candidatos is None:
        tema = TEMAS[agente]
        candidatos = _candidatos_acervo(tema["termos"])
        candidatos.update(_candidatos_bibliotecas(tema["termos"], {agente}))
        do_drive, _ = _candidatos_drive(tema["drive_nomes"])
        candidatos.update(do_drive)
    _caminho_indice(agente).unlink(missing_ok=True)   # refaz do zero: a biblioteca reflete as decisões atuais
    con = _conectar(agente)
    trechos = 0
    for caminho in aprovados:
        item = candidatos.get(caminho)
        if not item:
            continue
        texto = item["texto"] if item["texto"] is not None else _ler_drive(item)
        texto = _limpar(texto or "")
        if not texto:
            continue
        titulo = f"{item['titulo']} — {decisoes[caminho]['tema']}" if decisoes[caminho].get("tema") else item["titulo"]
        for trecho in _trechos(texto):
            con.execute("INSERT INTO trechos (texto, titulo, caminho) VALUES (?, ?, ?)", (trecho, titulo, caminho))
            trechos += 1
        con.execute("INSERT OR REPLACE INTO arquivos VALUES (?, ?)", (caminho, time.time()))
    con.commit()
    con.close()
    log(f"📚 Biblioteca de {agente}: {len(aprovados):,} materiais, {trechos:,} trechos")
    return {"aprovados": len(aprovados), "trechos": trechos}


def relatorio(agente: str) -> str:
    decisoes = _decisoes(agente)
    midias = decisoes.pop("_midias_drive", [])
    aprovados = [d for d in decisoes.values() if d["entra"]]
    from collections import Counter
    temas = Counter(d["tema"].lower() for d in aprovados)
    fontes = Counter(d["fonte"].split(":")[0] for d in aprovados)
    linhas = [f"📚 Biblioteca de {agente}: {len(aprovados):,} aprovados de {len(decisoes):,} lidos",
              f"Fontes: {', '.join(f'{k}: {v}' for k, v in fontes.most_common())}",
              "Temas mais frequentes:"] + [f"• {t} ({n})" for t, n in temas.most_common(20)]
    if midias:
        linhas.append(f"\n🎬 {len(midias)} vídeos/áudios do Drive ainda não transcritos (fase 2).")
    return "\n".join(linhas)
