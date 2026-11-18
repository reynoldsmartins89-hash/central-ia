"""Cérebro: transforma o Histórico das IAs do Obsidian num segundo cérebro cruzado.

O que faz (sempre pelo CONTEÚDO de cada nota, nunca pelo nome):
1. Classifica cada conversa: tema da sua vida, agentes da Central a que ela serve, resumo,
   ideias/decisões e entidades (pessoas, obras, instituições, ferramentas). Fica em cache
   (~/.central-ia/cerebro.db), então só notas novas ou alteradas voltam à IA.
2. Cruza tudo: semelhança de texto (TF-IDF) entre as notas, de preferência entre IAs diferentes.
3. Escreve no cofre, em "Central IA/Cérebro":
   - Temas/<tema>.md          → mapa do tema: linha do tempo, IAs usadas, ideias, entidades
   - Entidades/<nome>.md      → pessoa/obra/ferramenta citada em 2+ conversas, com todas elas
   - Agentes da Central.md    → cada agente com as conversas que alimentam o trabalho dele
   - Mesma pergunta, várias IAs.md → o mesmo assunto levado a IAs diferentes
   - Insights para a arquitetura.md → síntese feita pela IA, com sugestões para a Central
   e, no fim de cada nota do histórico, um bloco "Conexões" (substituído a cada rodada).

Uso:  python main.py cerebro            (tudo)
      python main.py cerebro --sem-ia   (só refaz as ligações com o que já está no cache)
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import obsidian

RAIZ_REPO = Path(__file__).resolve().parent.parent
BANCO = Path.home() / ".central-ia" / "cerebro.db"
PASTA = "Cérebro"
HISTORICO = "Histórico das IAs"
INICIO, FIM = "<!-- cerebro:inicio -->", "<!-- cerebro:fim -->"
CLASSIFICADORES = ["cli:claude/haiku", "gemini:gemini-3.5-flash", "groq:openai/gpt-oss-120b"]
SINTETIZADORES = ["cli:claude/sonnet", "cli:claude/haiku", "gemini:gemini-3.5-flash", "groq:openai/gpt-oss-120b"]
LOTE = 8
DIAS_MESMA_PERGUNTA = 3
# as próprias IAs aparecem em quase tudo: como entidades, virariam hubs gigantes que escondem o resto
IAS_COMO_ENTIDADE = {"gemini", "notebooklm", "chatgpt", "claude", "grok", "deepseek", "qwen", "perplexity",
                     "copilot", "gpt", "openai", "anthropic", "google gemini", "claude code", "gemini cli"}
VERSAO_CLASSIFICACAO = 1     # mude para reclassificar tudo

TEMAS = {
    "Renda e negócios": "ganhar dinheiro, negócios digitais, infoprodutos, empreendimentos, monetização, marketing",
    "A Guerra Ensina": "filosofia da guerra, estratégia, história militar, doutrina, o projeto/blog A Guerra Ensina",
    "Segurança de autoridades": "segurança de dignitários, proteção de autoridades, segurança pública, polícia, inteligência",
    "Carreira e LinkedIn": "currículo, LinkedIn, vagas, entrevistas, perfil profissional, trabalho remoto",
    "Imigração e trabalho fora": "imigração, vistos, green card, morar ou trabalhar no exterior, ganhar em dólar",
    "Educação e estudo": "como estudar, aprendizagem, pós-graduação, cursos, concursos, formação acadêmica",
    "Fé e filosofia": "fé católica, tradição, patrística, escolástica, filosofia, Olavo de Carvalho, COF",
    "Central de IA e tecnologia": "arquitetura de IAs, agentes, automação, programação, ferramentas de IA, nuvem",
    "Política, economia e geopolítica": "política, economia, geopolítica, história, Brasil e mundo, investimentos",
    "Idiomas": "inglês e outras línguas, atividades de idiomas",
    "Saúde e desenvolvimento pessoal": "saúde, gagueira, hábitos, mentalidade, propósito, foco, produtividade",
    "Família e vida pessoal": "família, filhos, casa, decisões pessoais, vida cotidiana",
    "Simbolismo e esoterismo": "numerologia, tarô, arquétipos, maçonaria, simbolismo",
    "Outros": "o que não se encaixa em nenhum tema acima",
}

PALAVRAS_VAZIAS = set("""
a o as os um uma uns umas de do da dos das em no na nos nas por para pra com sem sob sobre entre até que
se e ou mas como mais menos muito muita muitos muitas já não sim é são ser estar está estão foi era ter tem têm
isso isto esse essa esses essas este esta estes estas aquele aquela ele ela eles elas eu você voce vocês nós meu
minha meus minhas seu sua seus suas nosso nossa lhe também quando onde qual quais quem porque pois então assim
cada todo toda todos todas outro outra outros outras mesmo mesma tudo nada algo aqui ali lá ainda pode podem
fazer feito faz deve devem seria será sobre após antes depois forma modo vez vezes bem pelo pela pelos pelas
the and for with that this from your you are was were have has will can not but its into about what which
voce gemini chatgpt claude grok deepseek qwen perplexity notebooklm conversa mensagens resposta pergunta
https http www com br html pdf
""".split())


@dataclass
class Nota:
    caminho: Path
    rel: str                      # caminho relativo ao cofre, sem .md (para [[links]])
    ia: str
    data: str
    titulo: str
    corpo: str
    info: dict = field(default_factory=dict)

    @property
    def link(self) -> str:
        return f"[[{self.rel}|{self.titulo[:80]}]]"


# ------------------------------------------------------------------ leitura do cofre
def _frontmatter(texto: str) -> tuple[dict, str]:
    if not texto.startswith("---"):
        return {}, texto
    fim = texto.find("\n---", 3)
    if fim < 0:
        return {}, texto
    meta = {}
    for linha in texto[3:fim].splitlines():
        chave, _, valor = linha.partition(":")
        if chave.strip() and valor.strip():
            meta[chave.strip()] = valor.strip().strip('"')
    return meta, texto[fim + 4:]


def _sem_bloco(texto: str) -> str:
    return re.sub(rf"\n*{re.escape(INICIO)}.*?{re.escape(FIM)}\n*", "\n", texto, flags=re.S)


def ler_notas(cofre: Path) -> list[Nota]:
    base = cofre / obsidian.SUBPASTA / HISTORICO
    notas = []
    for arq in sorted(base.rglob("*.md")):
        if arq.parent == base:                          # o índice "Histórico das IAs.md"
            continue
        meta, corpo = _frontmatter(arq.read_text(encoding="utf-8"))
        if not meta.get("ia"):
            continue
        notas.append(Nota(arq, arq.relative_to(cofre).with_suffix("").as_posix(), meta["ia"],
                          meta.get("data", "sem-data"), meta.get("titulo") or arq.stem, _sem_bloco(corpo)))
    return notas


def _amostra(nota: Nota, limite: int = 1800) -> str:
    """As perguntas do usuário dizem o assunto; o começo das respostas, o que foi entregue."""
    perguntas, respostas = [], []
    for bloco in re.split(r"(?m)^### ", nota.corpo)[1:]:
        quem, _, texto = bloco.partition("\n")
        texto = re.sub(r"\s+", " ", texto).strip()
        (perguntas if quem.startswith("Você") else respostas).append(texto)
    if not perguntas and not respostas:
        return re.sub(r"\s+", " ", nota.corpo)[:limite]
    p = " | ".join(t[:400] for t in perguntas)[: limite * 2 // 3]
    r = " | ".join(t[:250] for t in respostas)[: limite - len(p)]
    return f"PERGUNTAS: {p}\nRESPOSTAS (início): {r}"


# ------------------------------------------------------------------ cache
def _banco(caminho: Path | None = None) -> sqlite3.Connection:
    caminho = caminho or BANCO
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho, timeout=60)
    con.execute("CREATE TABLE IF NOT EXISTS notas (rel TEXT PRIMARY KEY, hash TEXT, info TEXT)")
    return con


def _hash(nota: Nota) -> str:
    return hashlib.sha1(f"{VERSAO_CLASSIFICACAO}|{nota.corpo}".encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ classificação pela IA
def _agentes() -> dict[str, str]:
    dados = tomllib.loads((RAIZ_REPO / "agentes" / "agentes.toml").read_text(encoding="utf-8"))
    return {nome: (a.get("descricao") or a.get("habilidade") or nome) for nome, a in dados.get("agentes", {}).items()}


def _json_lista(texto: str) -> list:
    achado = re.search(r"\[.*\]", texto, re.S)
    return json.loads(achado.group(0)) if achado else []


def _classificar_lote(lote: list[Nota], agentes: dict[str, str]) -> dict[str, dict]:
    from . import provedores
    temas = "\n".join(f"- {t}: {d}" for t, d in TEMAS.items())
    nomes = ", ".join(agentes)
    sistema = (
        "Você organiza o segundo cérebro de Reynolds: conversas dele com várias IAs. Leia o CONTEÚDO de cada "
        "conversa (não se guie pelo título) e devolva SOMENTE um JSON, uma entrada por conversa:\n"
        '[{"n": 1, "tema": "um dos TEMAS", "temas_extras": ["até 2 outros TEMAS, se fizer sentido"], '
        '"agentes": ["até 3 nomes da lista de AGENTES que se beneficiariam desta conversa"], '
        '"resumo": "uma frase do que ele buscou e do que obteve", '
        '"ideias": ["até 3 ideias, decisões ou planos concretos que aparecem na conversa"], '
        '"entidades": ["até 6 nomes próprios relevantes: pessoas, livros, instituições, ferramentas, lugares"]}]\n'
        "Use exatamente os nomes dos TEMAS e dos AGENTES. Se nenhum agente servir, devolva lista vazia. "
        "Escreva em português.")
    itens = "\n\n".join(f"[{i}] IA: {n.ia} · DATA: {n.data} · TÍTULO: {n.titulo}\n{_amostra(n)}"
                        for i, n in enumerate(lote, 1))
    pedido = f"TEMAS:\n{temas}\n\nAGENTES: {nomes}\n\nConversas:\n\n{itens}"
    _, resposta = provedores.chamar_com_reserva(CLASSIFICADORES, sistema, pedido, log=lambda *_: None)
    saida = {}
    for d in _json_lista(resposta):
        try:
            n = int(d.get("n", 0))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= len(lote):
            continue
        tema = d.get("tema") if d.get("tema") in TEMAS else "Outros"
        extras = [t for t in d.get("temas_extras") or [] if t in TEMAS and t != tema][:2]
        saida[lote[n - 1].rel] = {
            "tema": tema, "temas_extras": extras,
            "agentes": [a for a in d.get("agentes") or [] if a in agentes][:3],
            "resumo": str(d.get("resumo") or "")[:300],
            "ideias": [str(i)[:250] for i in d.get("ideias") or [] if str(i).strip()][:3],
            "entidades": [str(e).strip()[:60] for e in d.get("entidades") or [] if str(e).strip()][:6],
        }
    return saida


def classificar(notas: list[Nota], usar_ia: bool = True, log=print, banco: Path | None = None) -> None:
    con = _banco(banco)
    try:
        cache = {rel: (h, json.loads(i)) for rel, h, i in con.execute("SELECT rel, hash, info FROM notas")}
        pendentes = []
        for n in notas:
            h = _hash(n)
            if n.rel in cache and cache[n.rel][0] == h:
                n.info = cache[n.rel][1]
            else:
                pendentes.append((n, h))
        if not usar_ia or not pendentes:
            log(f"   🧠 {len(notas) - len(pendentes)} notas já classificadas"
                + (f" · {len(pendentes)} sem classificação (rodando sem IA)" if pendentes else ""))
            return
        agentes = _agentes()
        log(f"   🧠 classificando {len(pendentes)} notas novas ou alteradas, em lotes de {LOTE}…")
        for i in range(0, len(pendentes), LOTE):
            lote = pendentes[i:i + LOTE]
            try:
                infos = _classificar_lote([n for n, _ in lote], agentes)
            except Exception as erro:  # noqa: BLE001 — um lote ruim não derruba a rodada
                log(f"   ⚠ lote {i // LOTE + 1}: {str(erro)[:120]}")
                continue
            for n, h in lote:
                if n.rel in infos:
                    n.info = infos[n.rel]
                    con.execute("INSERT OR REPLACE INTO notas VALUES (?, ?, ?)",
                                (n.rel, h, json.dumps(n.info, ensure_ascii=False)))
            con.commit()
            feitas = min(i + LOTE, len(pendentes))
            if feitas % (LOTE * 10) == 0 or feitas == len(pendentes):
                log(f"      {feitas}/{len(pendentes)}")
    finally:
        con.close()


# ------------------------------------------------------------------ semelhança (TF-IDF)
def _tokens(texto: str) -> list[str]:
    texto = re.sub(r"https?://\S+", " ", texto.lower())
    return [t for t in re.findall(r"[a-zà-ú]{4,}", texto) if t not in PALAVRAS_VAZIAS]


def semelhantes(notas: list[Nota], k: int = 6, minimo: float = 0.12) -> dict[str, list[tuple[float, Nota]]]:
    import numpy as np
    docs = [Counter(_tokens(f"{n.titulo} {n.titulo} {_amostra(n, 6000)}")) for n in notas]
    df = Counter(t for d in docs for t in d)
    total = len(docs)
    vocab = [t for t, c in df.most_common() if 2 <= c <= max(2, total * 0.4)][:25000]
    indice = {t: i for i, t in enumerate(vocab)}
    m = np.zeros((total, len(vocab)), dtype=np.float32)
    for i, d in enumerate(docs):
        for t, c in d.items():
            j = indice.get(t)
            if j is not None:
                m[i, j] = (1 + math.log(c)) * math.log(total / df[t])
    normas = np.linalg.norm(m, axis=1, keepdims=True)
    m /= np.where(normas == 0, 1, normas)
    sim = m @ m.T
    np.fill_diagonal(sim, 0)
    saida = {}
    for i, n in enumerate(notas):
        ordem = np.argsort(-sim[i])
        # entre IAs diferentes primeiro: é aí que o cruzamento rende
        outras = [(float(sim[i, j]), notas[j]) for j in ordem[:40] if sim[i, j] >= minimo and notas[j].ia != n.ia]
        mesmas = [(float(sim[i, j]), notas[j]) for j in ordem[:40] if sim[i, j] >= minimo and notas[j].ia == n.ia]
        saida[n.rel] = (outras[:k - 2] + mesmas[:2] + outras[k - 2:])[:k]
    return saida


# ------------------------------------------------------------------ escrita no cofre
def _arquivo(nome: str) -> str:
    return obsidian._nome_seguro(nome, 80) or "Sem nome"


def _link_pasta(sub: str, nome: str) -> str:
    return f"[[{obsidian.SUBPASTA}/{PASTA}/{sub}/{_arquivo(nome)}|{nome}]]"


def _escrever(caminho: Path, texto: str) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(texto, encoding="utf-8")


def _bloco_conexoes(n: Nota, viz: list[tuple[float, Nota]], nomes: dict[str, str]) -> str:
    linhas = [INICIO, "## 🔗 Conexões", ""]
    if n.info:
        temas = [n.info["tema"]] + n.info.get("temas_extras", [])
        linhas.append("**Temas:** " + " · ".join(_link_pasta("Temas", t) for t in temas))
        if n.info.get("agentes"):
            linhas.append("**Agentes da Central:** " + " · ".join(
                f"[[{obsidian.SUBPASTA}/{PASTA}/Agentes da Central#{a}|{a}]]" for a in n.info["agentes"]))
        ents = list(dict.fromkeys(nomes[e.lower()] for e in n.info.get("entidades", []) if e.lower() in nomes))
        if ents:
            linhas.append("**Citados:** " + " · ".join(_link_pasta("Entidades", e) for e in ents))
        if n.info.get("resumo"):
            linhas += ["", f"> {n.info['resumo']}"]
    if viz:
        linhas += ["", "**Conversas relacionadas:**"]
        linhas += [f"- {v.link} · {v.ia} · {v.data} · {s:.0%}" for s, v in viz]
    linhas += ["", FIM]
    return "\n".join(linhas)


def _gravar_bloco(n: Nota, bloco: str) -> None:
    texto = _sem_bloco(n.caminho.read_text(encoding="utf-8")).rstrip()
    n.caminho.write_text(f"{texto}\n\n{bloco}\n", encoding="utf-8")


def _todos_temas(n: Nota) -> list[str]:
    return [n.info["tema"]] + n.info.get("temas_extras", []) if n.info else []


def _perto(a: str, b: str, dias: int = DIAS_MESMA_PERGUNTA) -> bool:
    try:
        return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).days) <= dias
    except ValueError:
        return False


def montar(cofre: Path, notas: list[Nota], log=print) -> dict:
    raiz = cofre / obsidian.SUBPASTA / PASTA
    viz = semelhantes(notas)
    classificadas = [n for n in notas if n.info]

    # entidades citadas em 2+ conversas viram páginas (o resto seria ruído no grafo)
    por_entidade: dict[str, list[Nota]] = defaultdict(list)
    nome_de: dict[str, str] = {}
    for n in classificadas:
        for e in {e.lower(): e for e in n.info.get("entidades", [])}.values():
            por_entidade[e.lower()].append(n)
            nome_de.setdefault(e.lower(), e)
    validas = {e for e, ns in por_entidade.items() if len(ns) >= 2 and e not in IAS_COMO_ENTIDADE}

    for n in notas:
        _gravar_bloco(n, _bloco_conexoes(n, viz.get(n.rel, []), {e: nome_de[e] for e in validas}))

    for velho in (raiz / "Entidades").glob("*.md") if (raiz / "Entidades").exists() else []:
        velho.unlink()                                  # regeradas do zero a cada rodada
    for e in validas:
        ns = sorted(por_entidade[e], key=lambda n: n.data)
        temas = Counter(t for n in ns for t in _todos_temas(n))
        corpo = [f"# {nome_de[e]}", "", f"_Citado em {len(ns)} conversas, em {len({n.ia for n in ns})} IAs._", "",
                 "**Temas:** " + " · ".join(_link_pasta("Temas", t) for t, _ in temas.most_common(4)), "",
                 "## Conversas", ""]
        corpo += [f"- {n.data} · {n.ia} · {n.link}" + (f" — {n.info['resumo']}" if n.info.get("resumo") else "")
                  for n in ns]
        _escrever(raiz / "Entidades" / f"{_arquivo(nome_de[e])}.md", "\n".join(corpo) + "\n")

    por_tema: dict[str, list[Nota]] = defaultdict(list)
    for n in classificadas:
        for t in _todos_temas(n):
            por_tema[t].append(n)
    for tema, ns in por_tema.items():
        ns.sort(key=lambda n: n.data)
        ias = Counter(n.ia for n in ns)
        ents = Counter(e.lower() for n in ns for e in n.info.get("entidades", []) if e.lower() in validas)
        agentes = Counter(a for n in ns for a in n.info.get("agentes", []))
        corpo = [f"# {tema}", "", f"_{TEMAS[tema]}_", "",
                 f"**{len(ns)} conversas** · de {ns[0].data} a {ns[-1].data}", "",
                 "**IAs usadas:** " + " · ".join(f"{ia} ({c})" for ia, c in ias.most_common()), ""]
        if agentes:
            corpo += ["**Agentes da Central que este tema alimenta:** " + " · ".join(
                f"[[{obsidian.SUBPASTA}/{PASTA}/Agentes da Central#{a}|{a}]] ({c})" for a, c in agentes.most_common(8)), ""]
        if ents:
            corpo += ["**Mais citados:** " + " · ".join(_link_pasta("Entidades", nome_de[e]) for e, _ in ents.most_common(15)), ""]
        ideias = [(n, i) for n in ns for i in n.info.get("ideias", [])]
        if ideias:
            corpo += ["## Ideias e decisões que apareceram", ""]
            corpo += [f"- {i} — {n.link} ({n.ia}, {n.data})" for n, i in ideias[-40:]] + [""]
        corpo += ["## Linha do tempo", ""]
        mes = ""
        for n in reversed(ns):
            if n.data[:7] != mes:
                mes = n.data[:7]
                corpo += ["", f"### {mes}", ""]
            corpo.append(f"- {n.data} · {n.ia} · {n.link}" + (f" — {n.info['resumo']}" if n.info.get("resumo") else ""))
        _escrever(raiz / "Temas" / f"{_arquivo(tema)}.md", "\n".join(corpo) + "\n")

    agentes = _agentes()
    por_agente: dict[str, list[Nota]] = defaultdict(list)
    for n in classificadas:
        for a in n.info.get("agentes", []):
            por_agente[a].append(n)
    corpo = ["# Agentes da Central", "",
             "Cada agente com as suas conversas em outras IAs que servem de matéria-prima para ele.", ""]
    for a in sorted(agentes, key=lambda a: -len(por_agente.get(a, []))):
        ns = sorted(por_agente.get(a, []), key=lambda n: n.data, reverse=True)
        corpo += [f"## {a}", "", f"_{agentes[a]}_ · **{len(ns)} conversas**", ""]
        corpo += [f"- {n.data} · {n.ia} · {n.link}" for n in ns[:25]]
        if len(ns) > 25:
            corpo.append(f"- … e mais {len(ns) - 25}")
        corpo.append("")
    sem_agente = [n for n in classificadas if not n.info.get("agentes")]
    corpo += ["## Sem agente na Central", "", f"**{len(sem_agente)} conversas** não se encaixam em nenhum agente "
              "atual: são candidatas a um agente novo (veja os Insights).", ""]
    for tema, c in Counter(n.info["tema"] for n in sem_agente).most_common():
        corpo.append(f"- {_link_pasta('Temas', tema)}: {c}")
    _escrever(raiz / "Agentes da Central.md", "\n".join(corpo) + "\n")

    # o mesmo assunto levado a IAs diferentes (bom para comparar quem responde melhor)
    pares, vistos = [], set()
    for n in notas:
        for s, v in viz.get(n.rel, []):
            chave = tuple(sorted((n.rel, v.rel)))
            if v.ia != n.ia and s >= 0.35 and chave not in vistos and _perto(n.data, v.data):
                vistos.add(chave)
                pares.append((s, n, v))
    grupos: list[set[str]] = []
    por_rel = {n.rel: n for n in notas}
    for _, a, b in sorted(pares, key=lambda p: -p[0]):
        alvo = next((g for g in grupos if a.rel in g or b.rel in g), None)
        if alvo is None:
            grupos.append({a.rel, b.rel})
        else:
            alvo.update((a.rel, b.rel))
    grupos = [g for g in grupos if len({por_rel[r].ia for r in g}) >= 2]
    corpo = ["# Mesma pergunta, várias IAs", "",
             "Assuntos que você levou a mais de uma IA. Abra lado a lado para ver qual respondeu melhor; "
             "isso orienta qual IA a Central deve usar em cada tipo de tarefa.", ""]
    for g in sorted(grupos, key=lambda g: -len(g)):
        ns = sorted((por_rel[r] for r in g), key=lambda n: n.data)
        principal = max(ns, key=lambda n: len(n.info.get("resumo", "")))
        corpo += [f"## {principal.titulo[:90]}", ""]
        corpo += [f"- {n.ia} · {n.data} · {n.link}" for n in ns] + [""]
    _escrever(raiz / "Mesma pergunta, várias IAs.md", "\n".join(corpo) + "\n")

    _escrever(raiz / "Cérebro.md", "\n".join([
        "# 🧠 Cérebro", "",
        f"Mapa do seu histórico com as IAs: **{len(notas)} conversas** de {len({n.ia for n in notas})} IAs, "
        f"{len(classificadas)} classificadas pelo conteúdo. Gerado pela Central de IA "
        f"({datetime.now():%d/%m/%Y %H:%M}).", "",
        "- [[Central IA/Cérebro/Insights para a arquitetura|💡 Insights para a arquitetura]]",
        "- [[Central IA/Cérebro/Agentes da Central|🤖 Agentes da Central]]",
        "- [[Central IA/Cérebro/Mesma pergunta, várias IAs|⚖️ Mesma pergunta, várias IAs]]", "",
        "## Temas", ""] + [f"- {_link_pasta('Temas', t)} ({len(por_tema[t])})"
                           for t in sorted(por_tema, key=lambda t: -len(por_tema[t]))] + [
        "", "## Mais citados", ""] + [
        f"- {_link_pasta('Entidades', nome_de[e])} ({len(por_entidade[e])})"
        for e in sorted(validas, key=lambda e: -len(por_entidade[e]))[:30]]) + "\n")
    resumo = {"notas": len(notas), "classificadas": len(classificadas), "temas": len(por_tema),
              "entidades": len(validas), "grupos_mesma_pergunta": len(grupos), "sem_agente": len(sem_agente)}
    log(f"   🔗 {resumo['notas']} notas ligadas · {resumo['temas']} temas · {resumo['entidades']} entidades · "
        f"{resumo['grupos_mesma_pergunta']} assuntos levados a várias IAs")
    return resumo


# ------------------------------------------------------------------ insights para a arquitetura
def insights(cofre: Path, notas: list[Nota], log=print) -> Path | None:
    from . import provedores
    classificadas = [n for n in notas if n.info]
    if not classificadas:
        return None
    agentes = _agentes()
    por_tema: dict[str, list[Nota]] = defaultdict(list)
    for n in classificadas:
        por_tema[n.info["tema"]].append(n)
    dossie = []
    for tema, ns in sorted(por_tema.items(), key=lambda kv: -len(kv[1])):
        ns.sort(key=lambda n: n.data)
        ias = Counter(n.ia for n in ns)
        ags = Counter(a for n in ns for a in n.info.get("agentes", []))
        linhas = [f"### {tema} — {len(ns)} conversas ({ns[0].data} a {ns[-1].data}); IAs: "
                  + ", ".join(f"{i} {c}" for i, c in ias.most_common())
                  + "; agentes ligados: " + (", ".join(f"{a} {c}" for a, c in ags.most_common(6)) or "nenhum")]
        for n in ns[-45:]:
            ideias = "; ".join(n.info.get("ideias", []))
            linhas.append(f"- {n.data} [{n.ia}] {n.info.get('resumo', '')}" + (f" IDEIAS: {ideias}" if ideias else ""))
        dossie.append("\n".join(linhas))
    texto_dossie = "\n\n".join(dossie)[:90000]
    lista_agentes = "\n".join(f"- {a}: {d}" for a, d in agentes.items())
    sistema = (
        "Você é o arquiteto-chefe da Central de IA de Reynolds Rebelo: um superagente em Python, com bot no "
        "Telegram, que orquestra agentes especialistas (lista abaixo) e grava tudo no Obsidian. O objetivo "
        "central dele é gerar renda (Produto 1: A Guerra Ensina) e trabalhar online, em dólar. Abaixo está o "
        "dossiê de TODAS as conversas dele com outras IAs, por tema. Escreva em português do Brasil, em "
        "Markdown, denso e concreto, citando datas e IAs quando ajudar. Nada de generalidades.")
    pedido = (
        f"AGENTES QUE A CENTRAL JÁ TEM:\n{lista_agentes}\n\nDOSSIÊ:\n{texto_dossie}\n\n"
        "Escreva a nota 'Insights para a arquitetura' com estas seções:\n"
        "## 1. O retrato: o que ele mais busca (os 5 a 7 eixos que se repetem, com evidências)\n"
        "## 2. Conexões que ele talvez não tenha visto (temas que se alimentam: ex. segurança de autoridades + "
        "A Guerra Ensina + renda)\n"
        "## 3. Ideias que ficaram pelo caminho (planos e decisões das conversas que a Central ainda não executa)\n"
        "## 4. Lacunas na Central (assuntos frequentes sem agente; agentes que existem mas quase não têm matéria)\n"
        "## 5. Qual IA para quê (pelo uso real: em que tipo de tarefa cada IA aparece; sugestão de roteamento)\n"
        "## 6. Próximos passos para a Central (5 a 8 ações priorizadas, cada uma com o ganho esperado)")
    log("   💡 escrevendo os insights para a arquitetura…")
    modelo, resposta = provedores.chamar_com_reserva(SINTETIZADORES, sistema, pedido, log=log)
    raiz = cofre / obsidian.SUBPASTA / PASTA
    destino = raiz / "Insights para a arquitetura.md"
    cabecalho = (f"---\ngerado: \"{datetime.now():%Y-%m-%d %H:%M}\"\nmodelo: \"{modelo}\"\n"
                 f"conversas: {len(classificadas)}\ntags: [cerebro, insights]\n---\n\n"
                 f"# 💡 Insights para a arquitetura\n\n_Gerado a partir de {len(classificadas)} conversas. "
                 f"Mapa geral: [[Central IA/Cérebro/Cérebro|Cérebro]]._\n\n")
    _escrever(destino, cabecalho + resposta.strip() + "\n")
    copia = RAIZ_REPO / "resultados" / "cerebro" / f"{datetime.now():%Y-%m-%d}_insights.md"
    _escrever(copia, cabecalho + resposta.strip() + "\n")
    log(f"   💡 insights ({modelo}) → {destino}")
    return destino


def rodar(usar_ia: bool = True, gerar_insights: bool = True, log=print) -> dict:
    cofre = obsidian.cofre() if obsidian.garantir_estrutura() else None
    if not cofre:
        raise SystemExit("Obsidian não configurado: defina OBSIDIAN_VAULT no .env.")
    notas = ler_notas(cofre)
    log(f"🧠 Cérebro: {len(notas)} conversas no Histórico das IAs")
    classificar(notas, usar_ia=usar_ia, log=log)
    resumo = montar(cofre, notas, log=log)
    if usar_ia and gerar_insights:
        insights(cofre, notas, log=log)
    log(f"✅ Cérebro pronto: abra 'Central IA/Cérebro/Cérebro' no Obsidian (e o Grafo).")
    return resumo


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(RAIZ_REPO / ".env")
    rodar()
