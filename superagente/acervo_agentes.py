"""Acervo dos agentes: todo o material de cada tema vira Markdown dentro da pasta do agente no Drive.

Pedido do Reynolds (17/11/2026): cada pasta Agente_X do Drive ganha a subpasta Agente_X_Markdown com TUDO
o que ele tem sobre aquele tema (livros, artigos, apostilas, cursos, audiobooks), dos HDs e do Drive,
em .md e organizado para o agente ler bem. É a pasta que o agente passa a usar.

Regras:
  • Escolha pelo CONTEÚDO, nunca só pelo nome: a curadoria lê um trecho de cada candidato e decide.
  • Um material pode ir para vários agentes (cada agente julga sozinho).
  • Nada é apagado nem movido: os originais ficam onde estão; só se CRIAM arquivos .md.
  • Retomável: as decisões e o que já foi gravado ficam guardados; rodar de novo continua de onde parou.
  • Vídeos/áudios (transcrição pela placa de vídeo) só depois que a fila dos idiomas terminar.

Organização da pasta Agente_X_Markdown (pensada para a leitura do agente):
  00 - ÍNDICE.md                        o mapa da pasta: o que há, por tipo e por seção
  Livros/<Seção>/<Título>.md
  Cursos/<Curso>/<Módulo>/<Aula>.md      transcrições, na ordem e na estrutura do curso
  Textos e Apostilas/<Seção>/<Título>.md
Cada .md começa com uma ficha (título, tipo, seção, assunto, origem).

Uso:  python main.py acervo rodar | status | exportar <agente>
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path

from . import curadoria

PASTA = Path.home() / ".central-ia"
PASTA_ESTADO = PASTA / "acervo_agentes"
DRIVE = Path(os.getenv("DRIVE_LOCAL") or r"G:\Meu Drive")
ESPACO_MINIMO = 20e9          # para tudo e avisa se o Drive tiver menos que isso livre
LIVRO = 120_000               # caracteres: acima disso, um documento é tratado como livro
MIDIAS_POR_RODADA = 30
VERSAO = 3                    # muda quando a organização da pasta muda: as cópias geradas são refeitas
IGNORAR = {"relatorio_auditoria"}
AULA = re.compile(r"transcri|m[íi]dia|\.mp4|\.ts|aula|v[íi]deo|[áa]udio|palestra|live|webin", re.I)
LIXO_PASTA = re.compile(r"\.(zip|rar|7z)(\.\d+)?$|-\d{3}$", re.I)
VIDEOS = curadoria.MIDIA
DRIVE_INTEIRAS = ["EBOOKS/"]  # pastas do Drive lidas por inteiro: o nome do livro raramente diz o tema

# Ordem de trabalho (pedido do Reynolds): primeiro os exemplos que ele deu.
# chave = nome do agente em agentes.toml; pasta = pasta no Drive.
TEMAS: dict[str, dict] = {
    "comunicacao": {
        "prioridade": ['EBOOKS/01.'],  # a subpasta de EBOOKS do assunto vem primeiro
        "pasta": "Agente_Comunicacao",
        "especialista": "um especialista em COMUNICAÇÃO humana (oratória, retórica, persuasão, lógica e argumentação)",
        "descricao": ("comunicação em todas as formas: ORATÓRIA e falar em público, apresentações, RETÓRICA, "
                      "PERSUASÃO e influência, LÓGICA e argumentação, pensamento crítico, dialética, comunicação "
                      "VERBAL e NÃO VERBAL (linguagem corporal, voz, expressões), escrita e redação, storytelling, "
                      "negociação, carisma, inteligência social, conversação, PNL, comportamento humano aplicado à "
                      "comunicação"),
        "nao": ("material que só cita 'comunicação' de passagem; marketing e anúncios sem foco em comunicar; "
                "telecomunicações e redes; editais e documentos administrativos"),
        "secoes": ["Oratória e Apresentações", "Retórica", "Persuasão e Influência", "Lógica e Argumentação",
                   "Pensamento Crítico", "Comunicação Não Verbal", "Voz e Dicção", "Escrita e Redação",
                   "Storytelling", "Negociação", "Carisma e Inteligência Social", "PNL",
                   "Comportamento Humano", "Comunicação em Geral"],
        "termos": ["oratória", "retórica", "persuasão", '"falar em público"', '"comunicação não verbal"',
                   '"linguagem corporal"', "argumentação", '"pensamento crítico"', "silogismo", "falácias",
                   "storytelling", "negociação", "carisma", '"inteligência social"', "dialética", "eloquência",
                   '"técnicas de persuasão"', "influência", "dicção", "oratoria", "persuasion", "rhetoric"],
        "drive_nomes": ["comunica", "oratór", "oratori", "retóric", "persuas", "argument", "lógica", "storytell",
                        "negocia", "carisma", "linguagem corporal", "não verbal", "dicção", "rhetoric"],
    },
    "filosofia": {
        "prioridade": ['EBOOKS/07.', 'EBOOKS/09.'],  # a subpasta de EBOOKS do assunto vem primeiro
        "pasta": "Agente_Filosofia",
        "especialista": ("um filósofo da FILOSOFIA TRADICIONAL, com base em Santo Tomás de Aquino e Santo Agostinho, "
                         "que estuda toda a história da filosofia a partir dessa tradição"),
        "descricao": ("FILOSOFIA em todas as áreas e épocas: metafísica, ontologia, lógica filosófica, "
                      "epistemologia, ética e filosofia moral, antropologia filosófica, filosofia política, estética, "
                      "filosofia da natureza; filosofia antiga (Platão, Aristóteles), patrística (Santo Agostinho), "
                      "ESCOLÁSTICA e TOMISMO (Santo Tomás de Aquino), filosofia moderna e contemporânea, filosofia "
                      "católica, Curso Online de Filosofia (Olavo de Carvalho), história da filosofia"),
        "nao": ("material que só cita filósofos de passagem; autoajuda; teologia devocional sem conteúdo "
                "filosófico; política partidária e notícias"),
        "secoes": ["Tomismo e Escolástica", "Santo Agostinho e Patrística", "Filosofia Antiga",
                   "Metafísica e Ontologia", "Lógica", "Epistemologia", "Ética e Filosofia Moral",
                   "Antropologia Filosófica", "Filosofia Política", "Estética", "Filosofia Moderna",
                   "Filosofia Contemporânea", "Filosofia Católica", "Curso Online de Filosofia (COF)",
                   "História da Filosofia", "Filosofia em Geral"],
        "termos": ["filosofia", "metafísica", "ontologia", "tomismo", "escolástica", '"tomás de aquino"',
                   '"santo tomás"', "agostinho", "aristóteles", "platão", "epistemologia", '"suma teológica"',
                   "patrística", "sócrates", "kant", "hegel", "nietzsche", '"filosofia moral"', "ética",
                   '"olavo de carvalho"', "philosophy", "aquinas"],
        "drive_nomes": ["filosof", "philosoph", "metafís", "tomis", "tomás de aquino", "escolást", "agostinho",
                        "aristótel", "platão", "olavo", "cof ", "ética"],
    },
    "ciencia_de_dados": {
        "prioridade": ['EBOOKS/06.'],  # a subpasta de EBOOKS do assunto vem primeiro
        "pasta": "Agente_Ciencia_de_Dados",
        "especialista": "um cientista de dados (estatística, Python, machine learning, análise e engenharia de dados)",
        "descricao": ("ciência de dados: ESTATÍSTICA e probabilidade, PYTHON e R para dados (pandas, numpy), SQL e "
                      "bancos de dados, análise e visualização de dados, MACHINE LEARNING, deep learning, "
                      "inteligência artificial, engenharia de dados, big data, business intelligence (Power BI, "
                      "Excel avançado), matemática para dados"),
        "nao": ("material que só cita dados de passagem; programação sem relação com dados (sites, jogos); "
                "editais e documentos administrativos"),
        "secoes": ["Estatística e Probabilidade", "Python para Dados", "R", "SQL e Bancos de Dados",
                   "Análise e Visualização", "Machine Learning", "Deep Learning e IA", "Engenharia de Dados",
                   "Business Intelligence", "Matemática para Dados", "Ciência de Dados em Geral"],
        "termos": ['"ciência de dados"', '"data science"', '"machine learning"', '"aprendizado de máquina"',
                   "pandas", "numpy", "estatística", "regressão", '"redes neurais"', '"deep learning"', "sql",
                   '"power bi"', '"big data"', '"análise de dados"', "scikit"],
        "drive_nomes": ["dados", "data science", "machine learning", "estatíst", "python", "sql", "power bi",
                        "big data", "deep learning", "analytics"],
    },
    "politica": {
        "prioridade": ['EBOOKS/07.'],  # a subpasta de EBOOKS do assunto vem primeiro
        "pasta": "Agente_Politica",
        "especialista": "um especialista em ciência e filosofia política",
        "descricao": ("ciência política, filosofia política, teoria do Estado, ideologias, instituições, "
                      "democracia, liberalismo, conservadorismo, socialismo, história das ideias políticas"),
        "nao": "notícias do dia; propaganda eleitoral; material que só cita política de passagem",
        "secoes": ["Filosofia Política", "Teoria do Estado", "Ideologias", "Conservadorismo", "Liberalismo",
                   "Socialismo e Marxismo", "Instituições e Democracia", "História das Ideias Políticas",
                   "Política em Geral"],
        "termos": ['"ciência política"', '"filosofia política"', "ideologia", "conservadorismo", "liberalismo",
                   "marxismo", "socialismo", "democracia", '"teoria do estado"', "totalitarismo"],
        "drive_nomes": ["polític", "politic", "ideolog", "conservador", "liberalism", "marxis"],
    },
    "geopolitica": {
        "prioridade": ['EBOOKS/05.'],  # a subpasta de EBOOKS do assunto vem primeiro
        "pasta": "Agente_Geopolitica",
        "especialista": "um especialista em geopolítica e relações internacionais",
        "descricao": "geopolítica, relações internacionais, estratégia, poder, guerras e conflitos, cenários",
        "nao": "notícias soltas sem análise; turismo; material que só cita países de passagem",
        "secoes": ["Teoria Geopolítica", "Relações Internacionais", "Estratégia e Guerra", "Conflitos",
                   "Regiões e Países", "Cenários", "Geopolítica em Geral"],
        "termos": ["geopolítica", '"relações internacionais"', "estratégia", "hegemonia", "guerra", "otan"],
        "drive_nomes": ["geopolít", "relações internacionais", "estratég", "guerra"],
    },
    "marketing_digital": {
        "prioridade": ['EBOOKS/03.'],  # a subpasta de EBOOKS do assunto vem primeiro
        "pasta": "Agente_Marketing_Digital",
        "especialista": "um especialista em marketing digital e vendas",
        "descricao": "marketing digital: tráfego pago, conteúdo, redes sociais, funis, lançamentos, copywriting, vendas",
        "nao": "material que só cita marketing de passagem",
        "secoes": ["Tráfego Pago", "Conteúdo e Redes Sociais", "Copywriting", "Funis e Lançamentos", "Vendas",
                   "SEO", "E-mail Marketing", "Marketing em Geral"],
        "termos": ['"marketing digital"', '"tráfego pago"', "copywriting", "lançamento", "funil", "instagram",
                   '"facebook ads"', '"google ads"', "seo"],
        "drive_nomes": ["marketing", "tráfego", "copy", "lançament", "funil", "vendas", "instagram", "seo"],
    },
    "geoprocessamento": {
        "prioridade": ['EBOOKS/06.'],  # a subpasta de EBOOKS do assunto vem primeiro
        "pasta": "Agente_Geoprocessamento",
        "especialista": "um especialista em geoprocessamento",
        "descricao": "geoprocessamento, SIG, QGIS, ArcGIS, sensoriamento remoto, cartografia, análise espacial",
        "nao": "material que só cita mapas de passagem",
        "secoes": ["SIG e QGIS", "ArcGIS", "Sensoriamento Remoto", "Cartografia", "Análise Espacial",
                   "Geoprocessamento em Geral"],
        "termos": ["geoprocessamento", "qgis", "arcgis", '"sensoriamento remoto"', "cartografia", "shapefile"],
        "drive_nomes": ["geoproc", "qgis", "arcgis", "sensoriamento", "cartograf", "sig "],
    },
    "cyberseguranca": {
        "prioridade": ['EBOOKS/06.', 'EBOOKS/05.'],  # a subpasta de EBOOKS do assunto vem primeiro
        "pasta": "Agente_CyberSecurity",
        "especialista": "um especialista em cibersegurança DEFENSIVA",
        "descricao": "cibersegurança defensiva, redes, segurança da informação, forense, hardening, privacidade",
        "nao": "material que só cita segurança de passagem",
        "secoes": ["Redes", "Segurança da Informação", "Defesa e Hardening", "Forense", "Pentest (defensivo)",
                   "Privacidade", "Cibersegurança em Geral"],
        "termos": ["cibersegurança", '"segurança da informação"', "pentest", "forense", "firewall", "malware"],
        "drive_nomes": ["cyber", "ciberseg", "segurança da informação", "hacker", "pentest", "forense", "redes"],
    },
    "seguranca_publica": {
        "prioridade": ['EBOOKS/05.'],  # a subpasta de EBOOKS do assunto vem primeiro
        "pasta": "Agente_Seguranca_Publica",
        "especialista": "um especialista em segurança pública",
        "descricao": "segurança pública, policiamento, inteligência, criminologia, legislação penal e operacional",
        "nao": "material que só cita polícia de passagem",
        "secoes": ["Policiamento", "Inteligência", "Criminologia", "Legislação", "Gestão em Segurança Pública",
                   "Segurança Pública em Geral"],
        "termos": ['"segurança pública"', "policiamento", "criminologia", '"inteligência policial"'],
        "drive_nomes": ["segurança pública", "polícia", "policia", "criminolog"],
    },
}


def _registrar_temas() -> None:
    """Põe os temas na curadoria (com chave própria md_<agente>: não mexe na biblioteca do agente)."""
    for chave, t in TEMAS.items():
        curadoria.TEMAS[f"md_{chave}"] = {**t, "agente_pasta": chave, "drive_inteiras": DRIVE_INTEIRAS}


_registrar_temas()


# ------------------------------------------------------------------ caminhos
def pasta_agente(chave: str) -> Path:
    return DRIVE / TEMAS[chave]["pasta"]


def pasta_markdown(chave: str) -> Path:
    pasta = TEMAS[chave]["pasta"]
    return DRIVE / pasta / f"{pasta}_Markdown"


def _longo(p: Path) -> str:
    s = str(p)
    return s if os.name != "nt" or s.startswith("\\\\?\\") else "\\\\?\\" + str(p.resolve())


def _nome(texto: str, limite: int = 90) -> str:
    texto = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", texto or "")
    texto = re.sub(r"\s+", " ", texto).strip(" .")
    return texto[:limite].strip(" .") or "sem título"


def _estado(chave: str) -> dict:
    arquivo = PASTA_ESTADO / f"{chave}.json"
    try:
        return json.loads(arquivo.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"gravados": {}, "marcas": {}}


def _gravar_estado(chave: str, estado: dict) -> None:
    PASTA_ESTADO.mkdir(parents=True, exist_ok=True)
    (PASTA_ESTADO / f"{chave}.json").write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


def _video_irmao(arquivo: Path) -> bool:
    """A ficha .md é a transcrição de um vídeo/áudio que está ao lado dela (aula de curso)?"""
    try:
        return any((arquivo.with_suffix(ext)).exists() for ext in VIDEOS | {e.upper() for e in VIDEOS})
    except OSError:
        return False


def _base_de(fonte: str) -> Path | None:
    """Pasta do Drive de outro agente (para materiais que vieram da biblioteca dele)."""
    if not fonte.startswith("drive:"):
        return None
    nome = fonte.split(":", 1)[1]
    if nome in TEMAS:
        return DRIVE / TEMAS[nome]["pasta"]
    try:
        from .agentes import carregar_equipe
        agente = carregar_equipe()[1].get(nome)
        return Path(agente.base) if agente and agente.base and not agente.base.startswith("curadoria:") else None
    except Exception:  # noqa: BLE001
        return None


def ficha(texto: str) -> dict[str, str]:
    """Cabeçalho (--- chave: valor ---) que o Leitor põe nas transcrições e fichas."""
    if not (texto or "").startswith("---"):
        return {}
    dados = {}
    for linha in texto.split("---", 2)[1].splitlines():
        if ":" in linha:
            k, v = linha.split(":", 1)
            dados[k.strip()] = v.strip().strip('"')
    return dados


def _partes_do_curso(relativo: Path, curso: str) -> list[str]:
    """Módulos do curso a partir das pastas, sem as repetições e os nomes de volume (.zip.001, -008)."""
    vistos, saida = {curso.lower()}, []
    for parte in relativo.parts[:-1]:
        if LIXO_PASTA.search(parte) or parte.lower() in vistos:
            continue
        vistos.add(parte.lower())
        saida.append(_nome(parte, 70))
    return saida[-2:]


def destino(chave: str, caminho: str, decisao: dict, tamanho: int) -> tuple[str, str]:
    """(caminho relativo dentro de Agente_X_Markdown, tipo) — onde o material fica."""
    titulo = decisao.get("titulo") or Path(caminho).stem
    meta = decisao.get("ficha") or {}
    secao = decisao.get("secao") or ""
    origem = caminho.removeprefix("drive:")
    propria = pasta_agente(chave)
    arquivo = Path(origem)
    relativo: Path | None = None
    if arquivo.is_absolute():
        try:
            relativo = arquivo.relative_to(propria)
        except ValueError:
            relativo = None
    elif decisao.get("fonte", "").startswith("drive:") and decisao.get("fonte") != "drive":
        base = _base_de(decisao["fonte"])
        if base:
            arquivo, relativo = base / origem, Path(origem)
    eh_aula = bool(AULA.search(meta.get("tipo_documento", ""))) or (
        relativo is not None and len(relativo.parts) > 1 and _video_irmao(arquivo))
    if relativo is not None and len(relativo.parts) > 1 and eh_aula:
        # aula de um curso: Cursos/<Curso>/<Módulo>/<Aula>, na estrutura do próprio curso
        curso = meta.get("curso_ou_area") or (relativo.parts[1] if len(relativo.parts) > 2 else relativo.parts[0])
        partes = [p for p in _partes_do_curso(relativo, curso) if p.lower() != relativo.parts[0].lower()]
        return str(Path("Cursos", _nome(curso, 80), *partes, _nome(relativo.stem) + ".md")), "Curso"
    if Path(origem).suffix.lower() in VIDEOS:
        partes = [_nome(p, 70) for p in Path(origem).parts[:-1] if p not in ("\\", "/") and not p.endswith(":\\")]
        partes = partes[-2:] or ["Avulsos"]
        return str(Path("Cursos", *partes, _nome(Path(origem).stem) + ".md")), "Curso"
    if not secao and relativo is not None and len(relativo.parts) > 1:
        secao = relativo.parts[0].title()   # pastas do próprio agente (ORATÓRIA, PERSUASÃO…) já são seções
    secao = _nome(secao or "Geral", 60)
    tipo = "Livro" if tamanho >= LIVRO and not eh_aula else "Texto"
    raiz = "Livros" if tipo == "Livro" else "Textos e Apostilas"
    return str(Path(raiz, secao, _nome(titulo) + ".md")), tipo


# ------------------------------------------------------------------ exportação
def _arquivos_da_propria_pasta(chave: str) -> list[Path]:
    """Os .md/.txt que já estão na pasta do agente: entram direto (já são dele), sem julgamento."""
    pasta = pasta_agente(chave)
    if not pasta.is_dir():
        return []
    return [p for p in sorted(pasta.rglob("*")) if p.is_file() and p.suffix.lower() in (".md", ".txt")
            and not curadoria._eh_markdown_de_agente(str(p)) and p.stem not in IGNORAR]


def _texto_completo(caminho: str, decisao: dict) -> str:
    """Texto INTEIRO de um material aprovado (o julgamento usa só uma amostra)."""
    from . import leitor
    fonte = decisao.get("fonte", "")
    try:
        if fonte == "hd":
            texto = ""
            if Path(caminho).suffix.lower() in curadoria.DOCUMENTOS and Path(caminho).exists():
                texto = leitor.ler_completo(caminho)[2] or ""
            if not texto:                               # vídeo/áudio do HD: a transcrição feita pelo Leitor
                con = leitor._tabelas()
                texto = curadoria._ficha_lida(con, caminho) or ""
                if not texto:
                    texto = "\n\n".join(t for (t,) in con.execute(
                        "SELECT texto FROM acervo WHERE caminho = ?", (caminho,)) if t)
                con.close()
            return texto
        if fonte == "drive-midia":
            con = leitor._tabelas()
            texto = curadoria._ficha_lida(con, caminho) or ""
            con.close()
            return texto
        if fonte == "drive":
            return curadoria._ler_drive({"local": curadoria.local_do_drive(caminho)})
        if fonte.startswith("drive:"):                  # da biblioteca de outro agente
            from .conhecimento import _caminho_indice
            con = sqlite3.connect(_caminho_indice(fonte.split(":", 1)[1]), timeout=60)
            texto = "\n\n".join(t for (t,) in con.execute(
                "SELECT texto FROM trechos WHERE caminho = ? ORDER BY rowid", (caminho,)))
            con.close()
            return texto
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _gravar_um(chave: str, raiz: Path, estado: dict, caminho: str, texto: str, decisao: dict) -> bool:
    gravados, marcas = estado["gravados"], estado["marcas"]
    texto = (texto or "").strip()
    if texto.startswith("---"):          # ficha do Leitor: fica só o conteúdo
        texto = texto.split("---", 2)[-1].strip()
    if len(texto) < 200:
        return False
    marca = hashlib.sha1(re.sub(r"\s+", " ", texto[:8000]).encode("utf-8", "replace")).hexdigest()
    if marca in marcas:                  # o mesmo material em dois lugares: um arquivo só
        gravados[caminho] = marcas[marca]
        return False
    relativo, tipo = destino(chave, caminho, decisao, len(texto))
    alvo = raiz / relativo
    if Path(_longo(alvo)).exists():      # nome repetido de outro material: acrescenta uma marca
        alvo = alvo.with_name(f"{alvo.stem} ({marca[:6]}).md")
        relativo = str(alvo.relative_to(raiz))
    titulo = decisao.get("titulo") or Path(caminho).stem
    partes = Path(relativo).parts
    cabecalho = ["---", f"titulo: {json.dumps(titulo, ensure_ascii=False)}", f"tipo: {tipo}",
                 f"secao: {json.dumps(partes[1] if len(partes) > 2 else '', ensure_ascii=False)}",
                 f"assunto: {json.dumps(decisao.get('tema', ''), ensure_ascii=False)}",
                 f"origem: {json.dumps(caminho, ensure_ascii=False)}", f"agente: {chave}", "---", ""]
    Path(_longo(alvo.parent)).mkdir(parents=True, exist_ok=True)
    Path(_longo(alvo)).write_text("\n".join(cabecalho) + f"# {titulo}\n\n{texto}\n", encoding="utf-8")
    gravados[caminho] = marcas[marca] = relativo
    return True


def exportar(chave: str, so_propria: bool = False, propria: bool = True, log=print) -> int:
    """Grava em Agente_X_Markdown cada material aprovado que ainda não foi gravado. Nunca apaga nada."""
    raiz = pasta_markdown(chave)
    raiz.mkdir(parents=True, exist_ok=True)
    estado = _estado(chave)
    if estado.get("versao") != VERSAO and estado["gravados"]:
        # a organização mudou: refaz SÓ as cópias .md que a própria Central gerou (os originais nem são tocados)
        for rel in set(estado["gravados"].values()):
            Path(_longo(raiz / rel)).unlink(missing_ok=True)
        for pasta in sorted((p for p in raiz.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
            try:
                pasta.rmdir()            # só some se ficou vazia
            except OSError:
                pass
        estado = {"gravados": {}, "marcas": {}}
        log(f"♻ {chave}: organização nova — refazendo as cópias .md")
    estado["versao"] = VERSAO
    gravados = estado["gravados"]
    sem_texto = set(estado.get("sem_texto", []))

    def ja_gravado(caminho: str) -> bool:
        return caminho in gravados and Path(_longo(raiz / gravados[caminho])).exists()

    novos = 0
    if propria or so_propria:
        for arquivo in _arquivos_da_propria_pasta(chave):
            caminho = str(arquivo)
            if ja_gravado(caminho):
                continue
            try:
                texto = arquivo.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            meta = ficha(texto)
            titulo = (meta.get("titulo") or arquivo.stem).strip().strip('"').strip()
            if _gravar_um(chave, raiz, estado, caminho, texto,
                          {"entra": True, "titulo": titulo, "fonte": "fixa", "tema": "", "ficha": meta}):
                novos += 1
                if novos % 100 == 0:
                    _gravar_estado(chave, estado)
                    log(f"   {chave}: {novos:,} arquivos .md gravados…")
    if not so_propria:
        for caminho, d in curadoria._decisoes(f"md_{chave}").items():
            if caminho.startswith("_") or not d.get("entra") or d.get("fonte") == "fixa":
                continue
            if ja_gravado(caminho) or caminho in sem_texto:
                continue
            texto = _texto_completo(caminho, d)
            if _gravar_um(chave, raiz, estado, caminho, texto, {**d, "ficha": ficha(texto)}):
                novos += 1
                if novos % 25 == 0:
                    estado["sem_texto"] = sorted(sem_texto)
                    _gravar_estado(chave, estado)
            elif caminho not in gravados:
                sem_texto.add(caminho)
    estado["sem_texto"] = sorted(sem_texto)
    _gravar_estado(chave, estado)
    escrever_indice(chave)
    log(f"📁 {chave}: {novos:,} novos .md em {raiz} (total {len(set(gravados.values())):,})")
    return novos


def escrever_indice(chave: str) -> Path:
    """00 - ÍNDICE.md: o mapa da pasta, para o agente (e você) saber o que há e onde."""
    raiz = pasta_markdown(chave)
    arquivos = sorted(set(_estado(chave)["gravados"].values()))
    grupos: dict[str, dict[str, list[str]]] = {}
    for rel in arquivos:
        partes = Path(rel).parts
        grupos.setdefault(partes[0], {}).setdefault(partes[1] if len(partes) > 2 else "", []).append(rel)
    linhas = [f"# Biblioteca do agente {TEMAS[chave]['pasta'].removeprefix('Agente_').replace('_', ' ')}", "",
              f"{len(arquivos):,} materiais em Markdown, escolhidos pelo CONTEÚDO entre os livros, cursos e textos "
              "dos HDs e do Google Drive. Organização:", "",
              "- **Livros/<Seção>/** — obras completas", "- **Cursos/<Curso>/<Módulo>/** — aulas transcritas, na ordem do curso",
              "- **Textos e Apostilas/<Seção>/** — artigos, resumos, apostilas", "",
              "Cada arquivo começa com uma ficha (título, tipo, seção, assunto e origem do original).", ""]
    for grupo in ("Livros", "Cursos", "Textos e Apostilas"):
        if grupo not in grupos:
            continue
        total = sum(len(v) for v in grupos[grupo].values())
        linhas += [f"## {grupo} ({total:,})", ""]
        for secao, lista in sorted(grupos[grupo].items()):
            if grupo == "Cursos":
                linhas.append(f"- **{secao}** — {len(lista)} aulas")
                continue
            linhas += [f"### {secao}", ""] + [f"- [{Path(r).stem}](<{Path(r).as_posix()}>)" for r in lista] + [""]
        linhas.append("")
    arquivo = raiz / "00 - ÍNDICE.md"
    arquivo.write_text("\n".join(linhas), encoding="utf-8")
    return arquivo


# ------------------------------------------------------------------ o ciclo contínuo
def espaco_livre() -> float:
    try:
        return shutil.disk_usage(DRIVE.anchor or str(DRIVE)).free
    except OSError:
        return float("inf")


def _avisar(texto: str) -> None:
    try:
        from . import telegram
        if telegram.configurado():
            telegram.enviar(texto)
    except Exception:  # noqa: BLE001
        pass


def _idiomas_terminados() -> bool:
    try:
        from . import idiomas
        return idiomas.pendentes() == 0
    except Exception:  # noqa: BLE001
        return False


def processar(chave: str, log=print) -> dict:
    """Um agente: pasta própria → curadoria (documentos) → .md → vídeos (se a placa estiver livre) → índice."""
    from . import conhecimento
    pasta_agente(chave).mkdir(parents=True, exist_ok=True)
    log(f"\n===== {TEMAS[chave]['pasta']}")
    antes = len(_estado(chave)["gravados"])
    exportar(chave, so_propria=True, log=log)          # o que já é do agente vai para a pasta na hora
    curadoria.OCR_PERMITIDO = _idiomas_terminados   # livro escaneado (OCR na placa de vídeo) espera os idiomas
    # retomável: só lê o que ainda não foi decidido; a cada lote os aprovados já viram .md na pasta
    curadoria.montar(f"md_{chave}", log=log, indexar_no_fim=False,
                     a_cada=lambda: exportar(chave, propria=False, log=lambda *_: None))
    exportar(chave, log=log)
    if _idiomas_terminados() and curadoria.midias_pendentes(f"md_{chave}"):
        curadoria.transcrever_midias(f"md_{chave}", MIDIAS_POR_RODADA, log=log, indexar_no_fim=False)
        exportar(chave, log=log)
    conhecimento.indexar(chave, pasta_agente(chave), log=log)   # o agente passa a ler a pasta Markdown
    depois = len(_estado(chave)["gravados"])
    return {"novos": depois - antes, "total": depois}


def rodar(chaves: list[str] | None = None, log=print) -> None:
    from .agentes import carregar_env
    carregar_env()
    for chave in chaves or list(TEMAS):
        if espaco_livre() < ESPACO_MINIMO:
            _avisar(f"⚠️ Acervo dos agentes PAUSADO: o Drive tem só {espaco_livre() / 1e9:.0f} GB livres. "
                    "Nada foi apagado; libere espaço e ele continua de onde parou.")
            log("⚠ pouco espaço no Drive: parei")
            return
        resultado = processar(chave, log=log)
        if resultado["novos"]:
            _avisar(f"📚 {TEMAS[chave]['pasta']}_Markdown: +{resultado['novos']:,} materiais em Markdown "
                    f"(total {resultado['total']:,}).")


def status() -> str:
    linhas = ["📚 Acervo dos agentes (pastas Agente_X_Markdown):"]
    for chave, t in TEMAS.items():
        decisoes = {c: d for c, d in curadoria._decisoes(f"md_{chave}").items() if not c.startswith("_")}
        aprovados = sum(1 for d in decisoes.values() if d.get("entra"))
        gravados = len(set(_estado(chave)["gravados"].values()))
        midias = len(curadoria.midias_pendentes(f"md_{chave}")) if decisoes else 0
        linhas.append(f"• {t['pasta']}: {gravados:,} .md · {len(decisoes):,} lidos pela curadoria "
                      f"({aprovados:,} aprovados)" + (f" · {midias} vídeos/áudios na fila" if midias else ""))
    linhas.append(f"Espaço livre no Drive: {espaco_livre() / 1e9:.0f} GB")
    return "\n".join(linhas)
