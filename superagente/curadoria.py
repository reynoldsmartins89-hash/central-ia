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
TRECHO = 2400                # caracteres de cada candidato mostrados à IA: início + 3 pontos do miolo
# (o início de um livro costuma ser capa, ficha e sumário: o juiz precisa ver o CORPO do texto)
JUIZES = ["cli:claude/haiku", "gemini:gemini-3.5-flash", "groq:openai/gpt-oss-120b"]   # Haiku: rigoroso e rápido
NOTA_MINIMA = 7
EXTENSOES_DRIVE = {".md", ".txt", ".pdf", ".docx", ".pptx", ".epub", ".odt", ".doc", ".rtf", ".html"}
DOCUMENTOS = {".pdf", ".docx", ".doc", ".pptx", ".epub", ".odt", ".rtf", ".txt", ".md", ".html"}
# tudo o que o Leitor transcreve: vídeos, áudios e AUDIOBOOKS (.opus/.aac/.wma/.flac/.m4b também)
MIDIA = {".mp3", ".opus", ".m4a", ".m4b", ".wav", ".ogg", ".flac", ".aac", ".wma",
         ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".m4v", ".3gp", ".mpg", ".mpeg"}

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
        # só palavras específicas: "estud", "leitura" e "inteligência" pegavam milhares de pastas de outros assuntos
        "drive_nomes": ["educa", "aprendiz", "aprender", "neuroci", "pedagog", "trivium", "quadrivium",
                        "escolást", "patríst", "memória", "memoriz", "didát", "alfabetiz", "montessori",
                        "cognit", "auto-educ", "autoeduc", "vida intelectual", "técnicas de estudo", "como estudar"],
        # base doutrinária do mentor (Reynolds, 17/11/2026): a pós de Patrística e Medieval entra INTEIRA,
        # sem o filtro "fala de educação?" (o juiz tinha recusado as aulas por não falarem de ensino)
        "pastas_fixas": ["E:/FILOSOFIA CATÓLICA/Pós Graduação em Filosofia Patrística e Medieval/"
                         "_Markdown (texto de todos os arquivos)"],
    },
}

TEMAS.update({
    "economista_catolico": {
        "especialista": ("um economista católico escolástico (doutrina social da Igreja, Escola de Salamanca, usura, preço "
                         "justo, distributismo) que também domina contabilidade, finanças pessoais, investimentos e tributação"),
        "descricao": ("economia escolástica e doutrina social da Igreja; distributismo; usura, juros, preço justo, moeda e "
                      "bancos; ECONOMIA em geral (macro, micro, escolas econômicas); CONTABILIDADE; FINANÇAS PESSOAIS e "
                      "familiares; investimentos, orçamento, dívidas, aposentadoria; tributação; finanças de empresas"),
        "nao": "material que só cita dinheiro de passagem; marketing e vendas; editais e apostilas de outras áreas",
        "termos": ["distributismo", "usura", '"preço justo"', '"doutrina social"', '"rerum novarum"', '"quadragesimo anno"',
                   "contabilidade", '"finanças pessoais"', '"educação financeira"', '"fluxo de caixa"', "macroeconomia",
                   "microeconomia", '"escola austríaca"', '"renda fixa"', '"tesouro direto"', "previdência",
                   '"imposto de renda"', "inflação", '"balanço patrimonial"', "investimentos"],
        "drive_nomes": ["econom", "finanç", "contab", "investiment", "distribut", "usura", "tribut"],
        "pastas_fixas": ["~/.central-ia/livros/economia-escolastica-catolica/fontes"],
    },
    "bitcoin": {
        "especialista": "um consultor especializado em Bitcoin e criptomoedas",
        "descricao": ("Bitcoin, blockchain e criptomoedas: autocustódia, carteiras, segurança, mineração, halving, "
                      "Lightning, economia do Bitcoin (moeda sólida, escola austríaca), investimento, tributação e regulação"),
        "nao": "material que só cita cripto de passagem; golpes e promessas de enriquecimento sem conteúdo técnico",
        "termos": ["bitcoin", "criptomoeda", "criptomoedas", "blockchain", "satoshi", "ethereum", "autocustódia",
                   "halving", "lightning", '"carteira fria"', '"moeda sólida"', "criptoativos"],
        "drive_nomes": ["bitcoin", "cripto", "blockchain", "btc"],
    },
    "mentor_imigracao": {
        "especialista": "um mentor de imigração especialista na teoria das bandeiras",
        "descricao": ("imigração, vistos, residência, cidadania (por descendência e por investimento), teoria das bandeiras, "
                      "residência fiscal, saída fiscal do Brasil, empresas e contas no exterior, nômade digital, morar e "
                      "trabalhar fora (Paraguai, Portugal, Itália, Espanha, EUA etc.)"),
        "nao": "material que só cita viagem ou outro país de passagem; turismo",
        "termos": ["imigração", "emigrar", '"cidadania italiana"', '"cidadania portuguesa"', '"green card"',
                   '"residência fiscal"', '"saída fiscal"', '"teoria das bandeiras"', "offshore", '"nômade digital"',
                   "passaporte", '"golden visa"', "naturalização"],
        "drive_nomes": ["imigra", "cidadania", "visto", "passaport", "offshore", "bandeiras", "exterior"],
        "pastas_fixas": ["~/.central-ia/fontes_web/mentor_imigracao"],   # Settee e Capital Global (sites)
    },
    "tradicao_catolica": {
        "especialista": ("um especialista na doutrina católica tradicional, na Missa Tridentina, no Concílio de Trento e "
                         "no latim eclesiástico"),
        "descricao": ("Missa Tridentina (rito romano tradicional), liturgia e Missal de São Pio V, Concílio de Trento, "
                      "Catecismo Romano, doutrina e moral católica tradicional, Magistério, Padres e Doutores da Igreja, "
                      "LATIM eclesiástico e clássico (gramática, textos litúrgicos), canto gregoriano, espiritualidade"),
        "nao": "material que só cita a Igreja de passagem; política; esoterismo e ocultismo",
        "termos": ["tridentina", '"missa tradicional"', '"concílio de trento"', "liturgia", "missal", '"catecismo romano"',
                   "latim", "gregoriano", '"vetus ordo"', '"summorum pontificum"', "breviário", '"ofício divino"',
                   "patrística", "tomismo", "latina"],
        "drive_nomes": ["tridentin", "trento", "missa", "liturg", "latim", "latin", "catecismo", "gregorian"],
    },    "lider": {
        "especialista": ("um mentor de LIDERANÇA (militar, empresarial, pública e cristã): comando, chefia, gestão de "
                         "pessoas e equipes, decisão e caráter do líder"),
        "descricao": ("LIDERANÇA em todas as formas: liderança militar e comando, chefia e gestão de pessoas e equipes, "
                      "liderança empresarial e executiva, liderança em crises e na complexidade, tomada de decisão do "
                      "líder, influência, comunicação e oratória do líder, formação de líderes, mentoria e coaching de "
                      "líderes, virtudes, caráter e exemplo (inclusive liderança cristã e servidora), cultura e motivação "
                      "de equipes, delegação, feedback, liderança de si mesmo"),
        "nao": ("material que só cita 'líder' ou 'liderança' de passagem; notícias políticas sobre líderes de "
                "países; marketing e vendas sem foco em liderar pessoas; editais e documentos administrativos"),
        "termos": ["liderança", "lideranca", '"líder"', '"líderes"', '"liderar"', '"gestão de pessoas"', "chefia",
                   '"comando e controle"', '"leadership"', '"liderança situacional"', '"liderança servidora"',
                   '"gestão de equipes"', '"formação de líderes"'],
        "drive_nomes": ["lider", "leader", "chefia", "gestão de pessoas", "gestao de pessoas"],
    },
})


def exportar_md(agente: str, pasta: str, log=print) -> int:
    """Grava cada material APROVADO como um .md na pasta pedida (ex.: uma pasta do Google Drive)."""
    import hashlib
    from .conhecimento import _caminho_indice
    destino = Path(pasta).expanduser()
    destino.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_caminho_indice(agente), timeout=60)
    textos: dict[str, list[str]] = {}
    titulos: dict[str, str] = {}
    for caminho, titulo, texto in con.execute("SELECT caminho, titulo, texto FROM trechos ORDER BY rowid"):
        textos.setdefault(caminho, []).append(texto)
        titulos.setdefault(caminho, titulo or Path(caminho).stem)
    con.close()
    vistos, gravados = set(), 0
    for caminho, partes in textos.items():
        corpo = "\n\n".join(partes)
        marca = hashlib.sha1(corpo[:5000].encode("utf-8", "replace")).hexdigest()
        if marca in vistos:   # o mesmo material em dois lugares (ex.: cópias duplicadas) vira um só arquivo
            continue
        vistos.add(marca)
        nome = re.sub(r'[<>:"/\\|?*\x00-\x1f\s]+', " ", titulos[caminho]).strip()[:110] or marca[:10]
        arquivo = destino / f"{nome}.md"
        if arquivo.exists() and caminho not in arquivo.read_text(encoding="utf-8", errors="replace"):
            arquivo = destino / f"{nome} ({marca[:6]}).md"
        origem = caminho.removeprefix("drive:")
        arquivo.write_text(f"# {titulos[caminho]}\n\n> Origem: `{origem}`\n\n{corpo}\n", encoding="utf-8")
        gravados += 1
    log(f"📁 {gravados:,} arquivos .md gravados em {destino}")
    return gravados


# ------------------------------------------------------------------ candidatos
def _amostra(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto or "").strip()
    if len(texto) <= TRECHO:
        return texto
    inicio, parte = TRECHO * 3 // 8, TRECHO * 5 // 24
    pedacos = [texto[:inicio]] + [texto[int(len(texto) * f): int(len(texto) * f) + parte] for f in (0.3, 0.55, 0.8)]
    return " […] ".join(pedacos)


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


def _eh_markdown_de_agente(caminho: str) -> bool:
    """Arquivo das pastas Agente_X_Markdown (geradas pela Central): não é material novo."""
    return any(parte.endswith("_Markdown") for parte in Path(caminho).parts)


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
        # o tema precisa aparecer em pelo menos 6 trechos do arquivo (citar de passagem não basta)
        achados = {c for c, _ in con.execute("SELECT caminho, COUNT(*) FROM trechos WHERE trechos MATCH ? "
                                             "GROUP BY caminho HAVING COUNT(*) >= 6", (" OR ".join(termos),))}
        textos: dict[str, list[str]] = {}
        titulos: dict[str, str] = {}
        for caminho, titulo, texto in con.execute("SELECT caminho, titulo, texto FROM trechos"):   # UMA varredura
            if caminho in achados:
                textos.setdefault(caminho, []).append(texto)
                titulos.setdefault(caminho, titulo)
        for caminho, partes in textos.items():
            if _eh_markdown_de_agente(caminho):
                continue
            saida[caminho] = {"titulo": titulos[caminho] or Path(caminho).stem, "fonte": f"drive:{nome}",
                              "texto": "\n\n".join(partes)}
        con.close()
    return saida


def _candidatos_drive(palavras: list[str], inteiras: list[str] | tuple = ()) -> tuple[dict[str, dict], list[str]]:
    """Resto do Drive (fora das pastas Agente_…): escolhe pelo nome/pasta e LÊ o conteúdo.
    As pastas `inteiras` (ex.: EBOOKS/) entram com TODOS os documentos: o nome do livro raramente diz o tema."""
    from . import drive
    con = sqlite3.connect(PASTA / "catalogo.db", timeout=120)
    filtro = " OR ".join(["lower(nome) LIKE ? OR lower(caminho) LIKE ?" for _ in palavras] +
                         ["caminho LIKE ?" for _ in inteiras])
    args = [a for p in palavras for a in (f"%{p}%", f"%{p}%")] + [f"{p}%" for p in inteiras]
    linhas = con.execute(f"SELECT caminho, nome, tamanho, md5 FROM drive WHERE caminho NOT LIKE 'Agente\\_%' "
                         f"ESCAPE '\\' AND ({filtro})", args).fetchall()
    no_hd = _copias_no_hd(con)
    con.close()
    # cópias idênticas (mesma impressão digital do Google) são lidas uma vez só; a da pasta de duplicados perde
    linhas.sort(key=lambda l: l[0].startswith(DUPLICADOS))
    saida, midias, vistos = {}, [], set()
    for caminho, nome, tamanho, md5 in linhas:
        if md5:
            if md5 in vistos:
                continue
            vistos.add(md5)
        ext = Path(caminho).suffix.lower()
        if ext in MIDIA:
            midias.append(caminho)
        elif ext in EXTENSOES_DRIVE:
            # a mesma obra no HD (mesmo nome e tamanho) é lida de lá: segundos, em vez de baixar da nuvem
            local = no_hd.get(((nome or "").lower(), tamanho))
            saida[f"drive:{caminho}"] = {"titulo": Path(caminho).stem, "fonte": "drive", "texto": None,
                                         "local": local if local and Path(local).exists()
                                         else str(drive.caminho_local(caminho))}
    return saida, midias


DUPLICADOS = "DUPLICADOS PARA REVISAR/"


def _copias_no_hd(con) -> dict[tuple[str, int], str]:
    try:
        return {((n or "").lower(), t): c for c, n, t in con.execute("SELECT caminho, nome, tamanho FROM arquivos")}
    except sqlite3.OperationalError:
        return {}


def local_do_drive(caminho: str) -> str:
    """Onde ler um arquivo do Drive: a cópia idêntica do HD, se houver; senão o próprio Drive (G:)."""
    from . import drive
    caminho = caminho.removeprefix("drive:")
    con = sqlite3.connect(PASTA / "catalogo.db", timeout=120)
    try:
        linha = con.execute("SELECT nome, tamanho FROM drive WHERE caminho = ?", (caminho,)).fetchone()
        if linha:
            achado = con.execute("SELECT caminho FROM arquivos WHERE lower(nome) = ? AND tamanho = ?",
                                 ((linha[0] or "").lower(), linha[1])).fetchone()
            if achado and Path(achado[0]).exists():
                return achado[0]
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()
    return str(drive.caminho_local(caminho))


def _amostras() -> sqlite3.Connection:
    con = sqlite3.connect(PASTA / "amostras.db", timeout=60)
    con.execute("CREATE TABLE IF NOT EXISTS amostras (caminho TEXT PRIMARY KEY, texto TEXT)")
    return con


def amostra_guardada(caminho: str) -> str | None:
    con = _amostras()
    linha = con.execute("SELECT texto FROM amostras WHERE caminho = ?", (caminho,)).fetchone()
    con.close()
    return linha[0] if linha else None


def guardar_amostra(caminho: str, texto: str) -> None:
    texto = texto or ""
    pontos = [texto[int(len(texto) * f): int(len(texto) * f) + 1500] for f in (0.3, 0.55, 0.8)]
    resumo = texto if len(texto) <= 9000 else "\n[…]\n".join([texto[:3000]] + pontos)
    con = _amostras()
    con.execute("INSERT OR REPLACE INTO amostras VALUES (?, ?)", (caminho, resumo))
    con.commit()
    con.close()


# OCR (IA de visão na placa de vídeo) liberado agora? O acervo dos agentes só libera depois dos idiomas.
OCR_PERMITIDO = lambda: True  # noqa: E731


def ler_para_julgar(caminho: str) -> tuple[str, bool]:
    """(texto, adiado). Texto INTEIRO dos documentos com texto; PDF escaneado: OCR de 5 páginas espalhadas
    (não do livro todo: é só para julgar o tema) — ou ADIADO, se a placa de vídeo ainda não está liberada."""
    from . import extrator, leitor
    if Path(caminho).suffix.lower() == ".pdf":
        antigo = extrator.LIMITE
        extrator.LIMITE = 3_000_000
        try:
            metodo, texto = extrator.extrair(caminho)
        finally:
            extrator.LIMITE = antigo
        if metodo != "ilegível":
            return texto or "", False
        if not OCR_PERMITIDO():
            return "", True
        return leitor._ler_pdf_escaneado(caminho, paginas=5, espalhadas=True)[1], False
    return leitor.ler_completo(caminho)[2] or "", False


def _ler_drive(item: dict) -> str:
    from . import leitor
    try:
        _, _, texto = leitor.ler_completo(item["local"])
        return texto or ""
    except Exception:  # noqa: BLE001
        return ""


# ------------------------------------------------------------------ julgamento pela IA local
def _julgar(tema: dict, lote: list[tuple[str, dict]]) -> dict[str, tuple[bool, str]]:
    from . import provedores
    itens = "\n\n".join(f"[{i}] TÍTULO: {item['titulo']}\nCONTEÚDO: {_amostra(item['texto'])}"
                        for i, (_, item) in enumerate(lote, 1))
    sistema = (f"Você é um bibliotecário RIGOROSO montando a biblioteca de: {tema.get('especialista', 'um mentor de educação')}. Para cada material, "
               "leia o conteúdo e dê uma NOTA de 0 a 10 para o quanto o ASSUNTO CENTRAL dele é o tema pedido "
               "(10 = é inteiramente sobre isso; 5 = toca no tema; 0 = outro assunto). Seja exigente: material de "
               "outra área que só usa palavras como 'aprender' ou 'estudo' recebe nota baixa. Responda SOMENTE JSON: "
               '[{"n": 1, "nota": 8, "tema": "assunto em até 6 palavras"}, ...]')
    secoes = tema.get("secoes") or []
    pedido = f"TEMA da biblioteca: {tema['descricao']}.\nNOTA BAIXA para: {tema['nao']}.\n"
    if secoes:   # para organizar a pasta do agente: cada material numa seção fixa
        sistema = sistema.replace('"tema": "assunto em até 6 palavras"}',
                                  '"tema": "assunto em até 6 palavras", "secao": "uma das SEÇÕES"}')
        pedido += f"SEÇÕES (escolha exatamente uma para cada material): {'; '.join(secoes)}.\n"
    pedido += f"\nMateriais:\n\n{itens}"
    _, r = provedores.chamar_com_reserva(JUIZES, sistema, pedido, log=lambda *_: None)
    dados = json.loads(re.search(r"\[.*\]", r, re.S).group(0))
    saida = {}
    for d in dados:
        n = int(d.get("n", 0))
        if 1 <= n <= len(lote):
            nota = float(d.get("nota", 0))
            secao = str(d.get("secao", "")).strip()
            secao = next((s for s in secoes if s.lower() == secao.lower()), "")
            saida[lote[n - 1][0]] = (nota >= NOTA_MINIMA, f"{str(d.get('tema', ''))[:60]}", secao)
    return saida


def _candidatos_fixos(tema: dict) -> dict[str, dict]:
    """Pastas que entram INTEIRAS, sem julgamento (ex.: as fontes do livro de Economia Escolástica)."""
    saida = {}
    for pasta in tema.get("pastas_fixas", []):
        for p in sorted(Path(pasta).expanduser().rglob("*")):
            if p.is_file() and p.suffix.lower() in (".md", ".txt") and not _eh_markdown_de_agente(str(p)):
                texto = p.read_text(encoding="utf-8", errors="replace")
                titulo = next((l.split(":", 1)[1].strip() for l in texto.splitlines()[:6] if l.startswith("titulo:")), p.stem)
                saida[str(p)] = {"titulo": titulo, "fonte": "fixa", "texto": texto}
    return saida


def _todos_candidatos(agente: str) -> tuple[dict, list]:
    tema = TEMAS[agente]
    candidatos = _candidatos_acervo(tema["termos"])
    candidatos.update(_candidatos_bibliotecas(tema["termos"], {agente, tema.get("agente_pasta", "")}))
    do_drive, midias = _candidatos_drive(tema["drive_nomes"], tema.get("drive_inteiras", ()))
    candidatos.update(do_drive)
    return candidatos, midias


def _decisoes(agente: str) -> dict:
    arquivo = PASTA_DECISOES / f"{agente}.json"
    return json.loads(arquivo.read_text(encoding="utf-8")) if arquivo.exists() else {}


def _gravar(agente: str, decisoes: dict) -> None:
    PASTA_DECISOES.mkdir(parents=True, exist_ok=True)
    (PASTA_DECISOES / f"{agente}.json").write_text(json.dumps(decisoes, ensure_ascii=False, indent=1),
                                                   encoding="utf-8")


LIVROS = {".pdf", ".epub", ".mobi", ".azw3", ".djvu", ".doc", ".docx"}


def _ordem(tema: dict):
    """Primeiro a pasta do tema (ex.: EBOOKS/01. COMUNICAÇÃO), depois livros, depois o que já está lido nos HDs."""
    prioridade = [p.lower() for p in tema.get("prioridade", [])]

    def chave(par):
        caminho, item = par
        return (not any(p in caminho.lower() for p in prioridade),
                Path(caminho).suffix.lower() not in LIVROS,
                item.get("fonte") != "hd")
    return chave


def montar(agente: str, log=print, a_cada=None, indexar_no_fim: bool = True) -> dict:
    tema = TEMAS[agente]
    decisoes = _decisoes(agente)
    decisoes.pop("_midias_drive", None)
    log("🔎 Juntando candidatos (HDs, pastas Agente_… do Drive e resto do Drive)…")
    candidatos, midias = _todos_candidatos(agente)
    fixos = _candidatos_fixos(tema)
    for c, i in fixos.items():   # entram direto
        decisoes[c] = {"entra": True, "tema": "fonte fixa", "titulo": i["titulo"], "fonte": "fixa"}
    candidatos.update(fixos)
    pendentes = sorted(((c, i) for c, i in candidatos.items() if c not in decisoes), key=_ordem(tema))
    log(f"   {len(candidatos):,} candidatos ({len(pendentes):,} ainda não lidos) · {len(midias)} vídeos/áudios no Drive")
    inicio, feitos, adiados = time.time(), 0, 0
    for n in range(0, len(pendentes), LOTE):
        lote = pendentes[n:n + LOTE]
        for caminho, item in lote:
            if item.get("fonte") == "hd" and Path(caminho).suffix.lower() in DOCUMENTOS and Path(caminho).exists():
                try:                           # o acervo guarda só o começo do documento: lê o corpo também
                    completo, _ = ler_para_julgar(caminho)
                    if len(completo) > len(item["texto"] or ""):
                        item["texto"] = completo
                except Exception:  # noqa: BLE001
                    pass
            if item["texto"] is None:          # arquivo do Drive: lê agora (ou usa a amostra já guardada)
                guardada = amostra_guardada(caminho)
                if guardada is not None:
                    item["texto"], item["so_amostra"] = guardada, True
                else:
                    try:
                        texto, adiado = ler_para_julgar(item["local"])
                    except Exception:  # noqa: BLE001
                        texto, adiado = "", False
                    if adiado:                 # escaneado: espera a placa de vídeo (fica pendente, não é descartado)
                        item["texto"], item["adiado"] = "", True
                    else:
                        item["texto"] = texto
                        guardar_amostra(caminho, texto)
        legiveis = [(c, i) for c, i in lote if (i["texto"] or "").strip()]
        adiados += sum(1 for _, i in lote if i.get("adiado"))
        for c, i in lote:
            if not (i["texto"] or "").strip() and not i.get("adiado"):
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
                    entra, assunto, *resto = veredito[c]
                    decisoes[c] = {"entra": entra, "tema": assunto, "titulo": i["titulo"], "fonte": i["fonte"],
                                   "secao": resto[0] if resto else ""}
        feitos += len(lote)
        if feitos % 60 < LOTE or feitos == len(pendentes):
            _gravar(agente, decisoes)
            aprovados = sum(1 for d in decisoes.values() if d["entra"])
            log(f"   lidos {feitos:,}/{len(pendentes):,} · aprovados até agora: {aprovados:,} "
                f"({(time.time() - inicio) / 60:.0f} min)"
                + (f" · {adiados} escaneados esperando a placa de vídeo" if adiados else ""))
            if a_cada:
                a_cada()
    _gravar(agente, decisoes)
    decisoes["_midias_drive"] = midias
    _gravar(agente, decisoes)
    if not indexar_no_fim:
        return {"aprovados": sum(1 for c, d in decisoes.items() if not c.startswith("_") and d["entra"])}
    return indexar(agente, candidatos, log)


# ------------------------------------------------------------------ fase 2: vídeos/áudios do Drive
def _ficha_lida(con, origem: str) -> str | None:
    """Texto da transcrição já feita pelo Agente Leitor (fica no acervo: nunca transcreve duas vezes)."""
    linha = con.execute("SELECT ficha FROM leituras WHERE caminho = ? AND status = 'lido'", (origem,)).fetchone()
    if not linha or not linha[0] or not Path(linha[0]).exists():
        return None
    texto = Path(linha[0]).read_text(encoding="utf-8", errors="replace")
    return texto.split("---", 2)[-1] if texto.startswith("---") else texto


def _candidatos_midias(midias: list[str]) -> dict[str, dict]:
    """Vídeos/áudios do Drive que já têm transcrição (para entrar na biblioteca quando aprovados)."""
    from .leitor import _tabelas
    con = _tabelas()
    saida = {}
    for caminho in midias:
        texto = _ficha_lida(con, f"drive:{caminho}")
        if texto is not None:
            saida[f"drive:{caminho}"] = {"titulo": Path(caminho).stem, "fonte": "drive-midia", "texto": texto}
    con.close()
    return saida


def midias_pendentes(agente: str) -> list[str]:
    decisoes = _decisoes(agente)
    return [m for m in decisoes.get("_midias_drive", []) if f"drive:{m}" not in decisoes]


def transcrever_midias(agente: str, limite: int | None = None, log=print, indexar_no_fim: bool = True) -> dict:
    """FASE 2: transcreve (Whisper, no PC) os vídeos/áudios do Drive achados na curadoria, a IA julga o
    conteúdo como nos documentos e os aprovados entram na biblioteca. Retomável: rode de novo e continua."""
    from . import drive, leitor
    tema = TEMAS[agente]
    pendentes = midias_pendentes(agente)[:limite] if limite else midias_pendentes(agente)
    log(f"🎬 {agente}: {len(pendentes)} vídeos/áudios do Drive para transcrever e julgar")
    con = leitor._tabelas()
    lote: list[tuple[str, dict]] = []
    feitos, inicio = 0, time.time()

    def julgar_lote() -> None:
        decisoes = _decisoes(agente)
        try:
            veredito = _julgar(tema, lote)
        except Exception:  # noqa: BLE001 — resposta fora do formato: um a um
            veredito = {}
            for par in lote:
                try:
                    veredito.update(_julgar(tema, [par]))
                except Exception:  # noqa: BLE001
                    pass
        for c, i in lote:
            if c in veredito:
                entra, assunto, *resto = veredito[c]
                decisoes[c] = {"entra": entra, "tema": assunto, "titulo": i["titulo"], "fonte": i["fonte"],
                               "secao": resto[0] if resto else ""}
                log(f"   {'✅' if entra else '—'} {i['titulo'][:70]} · {assunto}")
        _gravar(agente, decisoes)
        lote.clear()

    for caminho in pendentes:
        origem = f"drive:{caminho}"
        texto = _ficha_lida(con, origem)
        if texto is None:
            local = drive.caminho_local(caminho)
            if not local.exists():
                log(f"   ⚠ não encontrado no G: {caminho}")
                continue
            try:
                leitor.ler_e_registrar(con, origem, str(local))
                con.commit()
                texto = _ficha_lida(con, origem) or ""
            except Exception as erro:  # noqa: BLE001
                log(f"   ⚠ {Path(caminho).name}: {str(erro)[:100]}")
                continue
        feitos += 1
        titulo = Path(caminho).stem
        if not texto.strip():
            decisoes = _decisoes(agente)
            decisoes[origem] = {"entra": False, "tema": "sem fala transcrita", "titulo": titulo, "fonte": "drive-midia"}
            _gravar(agente, decisoes)
            continue
        lote.append((origem, {"titulo": titulo, "fonte": "drive-midia", "texto": texto}))
        log(f"   🎙 {feitos}/{len(pendentes)} {titulo[:70]} ({(time.time() - inicio) / 60:.0f} min)")
        if len(lote) >= LOTE:
            julgar_lote()
    if lote:
        julgar_lote()
    con.close()
    return indexar(agente, log=log) if indexar_no_fim else {}


def indexar(agente: str, candidatos: dict | None = None, log=print) -> dict:
    """Grava o texto dos materiais aprovados na biblioteca do agente."""
    from .conhecimento import _caminho_indice, _conectar, _limpar, _trechos
    decisoes = {c: d for c, d in _decisoes(agente).items() if not c.startswith("_")}
    aprovados = [c for c, d in decisoes.items() if d["entra"]]
    if candidatos is None:
        candidatos, _ = _todos_candidatos(agente)
        candidatos.update(_candidatos_fixos(TEMAS[agente]))
    candidatos = {**candidatos, **_candidatos_midias(_decisoes(agente).get("_midias_drive", []))}
    _caminho_indice(agente).unlink(missing_ok=True)   # refaz do zero: a biblioteca reflete as decisões atuais
    con = _conectar(agente)
    trechos = 0
    for caminho in aprovados:
        item = candidatos.get(caminho)
        if not item:
            continue
        texto = item["texto"] if item["texto"] is not None and not item.get("so_amostra") else _ler_drive(item)
        if item["fonte"] == "hd" and Path(caminho).suffix.lower() in DOCUMENTOS and Path(caminho).exists():
            try:   # o acervo guarda só o início dos documentos: o aprovado entra por INTEIRO
                from . import leitor
                completo = leitor.ler_completo(caminho)[2]
                if len(completo or "") > len(texto or ""):
                    texto = completo
            except Exception:  # noqa: BLE001
                pass
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
    midias = [m for m in decisoes.pop("_midias_drive", []) if f"drive:{m}" not in decisoes]
    aprovados = [d for d in decisoes.values() if d["entra"]]
    from collections import Counter
    temas = Counter(d["tema"].lower() for d in aprovados)
    fontes = Counter(d["fonte"].split(":")[0] for d in aprovados)
    linhas = [f"📚 Biblioteca de {agente}: {len(aprovados):,} aprovados de {len(decisoes):,} lidos",
              f"Fontes: {', '.join(f'{k}: {v}' for k, v in fontes.most_common())}",
              "Temas mais frequentes:"] + [f"• {t} ({n})" for t, n in temas.most_common(20)]
    if midias:
        linhas.append(f"\n🎬 {len(midias)} vídeos/áudios do Drive ainda não transcritos "
                      f"(fase 2: python main.py curadoria {agente} --midias).")
    return "\n".join(linhas)
