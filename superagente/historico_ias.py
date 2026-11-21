"""Histórico das IAs no Obsidian: cada conversa exportada (ChatGPT, Gemini, Claude, Grok, DeepSeek…)
vira uma nota em `Central IA/Histórico das IAs/<IA>/` no seu cofre.

Nada é apagado nem sobrescrito: nota que já existe fica como está (rodar de novo só acrescenta as novas).

Uso:
    python -m superagente.historico_ias <arquivo-ou-pasta> [<arquivo-ou-pasta> ...]

Formatos reconhecidos (pelo CONTEÚDO, não pelo nome):
  • conversations.json do ChatGPT (export oficial)
  • conversations.json do Claude (export oficial: lista com "chat_messages"); do export novo, em partes,
    também projects/<uuid>.json (→ Claude/Projetos) e frames/artifacts/<id>/artifact.json (→ Claude/Artefatos)
  • qualquer documento (.docx, .pdf, .html, .txt, .md) → uma nota com o texto inteiro;
    a IA é deduzida do nome do arquivo/pasta só para escolher a subpasta.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from . import obsidian

PASTA_COFRE = "Histórico das IAs"
IAS = ("ChatGPT", "Gemini", "Claude", "Grok", "DeepSeek", "Qwen", "Perplexity", "Copilot")


def _data(ts) -> str:
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    if isinstance(ts, str) and re.match(r"\d{4}-\d{2}-\d{2}", ts):
        return ts[:10]
    return "sem-data"


def _texto_partes(conteudo: dict) -> str:
    partes = []
    for p in conteudo.get("parts") or []:
        if isinstance(p, str):
            partes.append(p)
        elif isinstance(p, dict) and isinstance(p.get("text"), str):
            partes.append(p["text"])           # áudio transcrito (modo voz)
    if not partes and isinstance(conteudo.get("text"), str):
        partes.append(conteudo["text"])        # código, resultados de ferramenta
    return "\n".join(t for t in partes if t.strip()).strip()


def conversa_chatgpt(c: dict) -> tuple[str, str, list[tuple[str, str]]]:
    """(título, data, [(quem, texto)]) seguindo o ramo principal da conversa (o que você viu na tela)."""
    mapa, no, ramo = c.get("mapping") or {}, c.get("current_node"), []
    while no and no in mapa:
        ramo.append(mapa[no])
        no = mapa[no].get("parent")
    falas = []
    for n in reversed(ramo):
        m = n.get("message") or {}
        papel = (m.get("author") or {}).get("role")
        if papel not in ("user", "assistant") or (m.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
            continue
        texto = _texto_partes(m.get("content") or {})
        if texto:
            falas.append(("Você" if papel == "user" else "ChatGPT", texto))
    return (c.get("title") or "Sem título", _data(c.get("create_time")), falas)


def conversa_claude(c: dict) -> tuple[str, str, list[tuple[str, str]]]:
    falas = []
    for m in c.get("chat_messages") or []:
        texto = (m.get("text") or "").strip() or "\n".join(
            b.get("text", "") for b in m.get("content") or [] if isinstance(b, dict)).strip()
        if texto:
            falas.append(("Você" if m.get("sender") == "human" else "Claude", texto))
    return (c.get("name") or "Sem título", _data(c.get("created_at")), falas)


def conversa_deepseek(c: dict) -> tuple[str, str, list[tuple[str, str]]]:
    """Export do DeepSeek (uma conversa por arquivo): segue o ramo de current_message_id;
    guarda a pergunta, os anexos e a resposta final (sem o 'pensamento' nem as buscas)."""
    mapa = {m.get("id"): m for m in c.get("messages") or []}
    no, ramo = c.get("current_message_id"), []
    while no is not None and no in mapa:
        ramo.append(mapa[no])
        no = mapa[no].get("parent_id")
    falas = []
    for m in reversed(ramo):
        partes = []
        for fr in m.get("fragments") or []:
            if fr.get("type") in ("REQUEST", "RESPONSE", "TEMPLATE_RESPONSE") and (fr.get("content") or "").strip():
                partes.append(fr["content"].strip())
            elif fr.get("type") == "FILE":
                partes += [f"📎 _{a.get('file_name')}_" for a in fr.get("files") or [] if a.get("file_name")]
        if partes:
            falas.append(("Você" if m.get("role") == "USER" else "DeepSeek", "\n\n".join(partes)))
    return (c.get("title") or "Sem título", _data(c.get("inserted_at")), falas)


def conversa_qwen(c: dict) -> tuple[str, str, list[tuple[str, str]]]:
    """Export do Qwen (chat.qwen.ai): árvore em chat.history.messages; a resposta fica nas fases 'answer'."""
    h = (c.get("chat") or {}).get("history") or {}
    mapa, no, ramo = h.get("messages") or {}, h.get("currentId") or c.get("currentId"), []
    while no and no in mapa:
        ramo.append(mapa[no])
        no = mapa[no].get("parentId")
    falas = []
    for m in reversed(ramo):
        texto = (m.get("content") or "").strip()
        if not texto:
            texto = "\n\n".join(p["content"].strip() for p in m.get("content_list") or []
                                if p.get("phase") in ("answer", None) and isinstance(p.get("content"), str)
                                and p["content"].strip())
        anexos = [f"📎 _{a.get('name') or (a.get('file') or {}).get('filename')}_" for a in m.get("files") or []
                  if isinstance(a, dict) and (a.get("name") or (a.get("file") or {}).get("filename"))]
        texto = "\n\n".join(anexos + ([texto] if texto else []))
        if texto:
            falas.append(("Você" if m.get("role") == "user" else "Qwen", texto))
    return (c.get("title") or "Sem título", _data(c.get("created_at")), falas)


def conversa_grok(c: dict) -> tuple[str, str, list[tuple[str, str]]]:
    """Export do Grok (prod-grok-backend.json): segue leaf_response_id pelos parent_response_id."""
    info = c.get("conversation") or {}
    mapa = {r["response"].get("_id"): r["response"] for r in c.get("responses") or [] if r.get("response")}
    no, ramo = info.get("leaf_response_id"), []
    while no and no in mapa:
        ramo.append(mapa[no])
        no = mapa[no].get("parent_response_id")
    if not ramo:                               # sem folha: usa a ordem do arquivo
        ramo = list(reversed(list(mapa.values())))
    falas = [("Você" if r.get("sender") == "human" else "Grok", (r.get("message") or "").strip())
             for r in reversed(ramo) if (r.get("message") or "").strip()]
    return (info.get("title") or "Sem título", _data(info.get("create_time")), falas)


def conversa_perplexity(c: dict) -> tuple[str, str, list[tuple[str, str]]]:
    falas = []
    for e in c.get("entries") or []:
        if (e.get("query") or "").strip():
            falas.append(("Você", e["query"].strip()))
        if (e.get("answer") or "").strip():
            falas.append(("Perplexity", e["answer"].strip()))
    return (c.get("context_title") or "Sem título", _data(c.get("created_at")), falas)


def _html_para_md(html: str) -> str:
    try:
        from markdownify import markdownify
        return markdownify(html, heading_style="ATX").strip()
    except ImportError:
        return re.sub(r"<[^>]+>", "", html).strip()
    except RecursionError:                     # HTML aninhado demais: fica o texto puro
        from bs4 import BeautifulSoup
        return re.sub(r"\n{3,}", "\n\n", BeautifulSoup(html, "html.parser").get_text("\n")).strip()


def _gemini_por_dia(atividades: list[dict]) -> list[dict]:
    """O Takeout do Gemini ('Minha atividade') traz cada pergunta solta, sem id de conversa.
    Agrupa por dia (horário local): uma nota por dia, perguntas e respostas em ordem."""
    dias: dict[str, list] = {}
    for a in sorted(atividades, key=lambda a: a.get("time") or ""):
        titulo = (a.get("title") or "").strip()
        resposta = "\n\n".join(_html_para_md(h.get("html") or "") for h in a.get("safeHtmlItem") or [])
        if not resposta and not titulo.startswith("Prompted"):
            continue                           # "Cleared", "Used"... sem conteúdo
        try:
            momento = datetime.fromisoformat(a["time"].replace("Z", "+00:00")).astimezone()
        except (KeyError, ValueError):
            continue
        pergunta = titulo[len("Prompted "):] if titulo.startswith("Prompted ") else f"_{titulo}_"
        anexos = [f"📎 _{n}_" for n in a.get("attachedFiles") or []]
        links = [f"🔗 {s.get('url') or s.get('name')}" for s in a.get("subtitles") or [] if s.get("url") or s.get("name")]
        falas = dias.setdefault(momento.strftime("%Y-%m-%d"), [])
        falas.append((f"Você · {momento:%H:%M}", "\n\n".join([pergunta] + anexos + links).strip()))
        if resposta:
            falas.append(("Gemini", resposta))
    return [{"dia": d, "falas": f} for d, f in dias.items()]


def conversa_gemini(c: dict) -> tuple[str, str, list[tuple[str, str]]]:
    perguntas = [t for quem, t in c["falas"] if quem.startswith("Você")]
    assunto = re.sub(r"\s+", " ", (perguntas[0].splitlines() or [""])[0]).strip(" _")[:70] if perguntas else ""
    extra = f" (+{len(perguntas) - 1})" if len(perguntas) > 1 else ""
    return (f"{assunto or 'Gemini'}{extra}", c["dia"], c["falas"])


def _qual_export(dados) -> str | None:
    if isinstance(dados, list) and dados and isinstance(dados[0], dict) and dados[0].get("header") == "Gemini Apps":
        return "Gemini"
    if isinstance(dados, list) and dados and isinstance(dados[0], dict) and "history" in (dados[0].get("chat") or {}):
        return "Qwen"
    if isinstance(dados, list) and dados and isinstance(dados[0], dict):
        if "responses" in dados[0] and "conversation" in dados[0]:
            return "Grok"
        if "entries" in dados[0] and "context_uuid" in dados[0]:
            return "Perplexity"
    if isinstance(dados, list) and dados and isinstance(dados[0], dict):
        if "mapping" in dados[0]:
            return "ChatGPT"
        if "chat_messages" in dados[0]:
            return "Claude"
        if "fragments" in str(dados[0].get("messages", ""))[:2000]:
            return "DeepSeek"
    return None


LEITORES = {"ChatGPT": conversa_chatgpt, "Claude": conversa_claude, "DeepSeek": conversa_deepseek,
            "Qwen": conversa_qwen, "Grok": conversa_grok, "Perplexity": conversa_perplexity,
            "Gemini": conversa_gemini}


def _ia_pelo_caminho(caminho: Path) -> str:
    alvo = caminho.as_posix().lower()
    return next((ia for ia in IAS if ia.lower() in alvo), "Outras IAs")


def _nota(destino: Path, titulo: str, data: str, ia: str, origem: Path, corpo: str) -> bool:
    """Grava a nota se ainda não existir. Devolve True se criou."""
    if destino.exists():
        return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    titulo_yaml = titulo.replace('"', "'")
    destino.write_text(f'---\nia: "{ia}"\ndata: "{data}"\ntitulo: "{titulo_yaml}"\norigem: "{origem.name}"\n'
                       f"tags: [historico-ia, {ia.lower().replace(' ', '-')}]\n---\n\n# {titulo}\n\n{corpo.strip()}\n",
                       encoding="utf-8")
    return True


def _nome_arquivo(data: str, titulo: str, usados: set[str]) -> str:
    base = f"{data} - {obsidian._nome_seguro(titulo, 80) or 'Sem título'}"
    nome, i = base, 2
    while nome.lower() in usados:
        nome, i = f"{base} ({i})", i + 1
    usados.add(nome.lower())
    return nome + ".md"


def importar_projeto_claude(arquivo: Path, p: dict, raiz: Path, log=print) -> dict:
    """Export novo do Claude (projects/<uuid>.json): descrição, instruções e documentos do projeto."""
    titulo, data = p.get("name") or "Projeto sem nome", _data(p.get("created_at"))
    partes = [f"_Projeto do Claude · criado em {data} · {len(p.get('docs') or [])} documento(s)_"]
    if (p.get("description") or "").strip():
        partes += ["## Descrição", p["description"].strip()]
    if (p.get("prompt_template") or "").strip():
        partes += ["## Instruções do projeto", p["prompt_template"].strip()]
    for doc in p.get("docs") or []:
        partes += [f"## Documento: {doc.get('filename') or 'sem nome'}", (doc.get("content") or "").strip()]
    nome = f"{data} - {obsidian._nome_seguro(titulo, 80) or 'Projeto'}.md"
    criou = _nota(raiz / "Claude" / "Projetos" / nome, titulo, data, "Claude", arquivo, "\n\n".join(partes))
    log(f"   Claude (projeto): {titulo} → {'nota criada' if criou else 'já existia'}")
    return {"criadas": int(criou), "existentes": int(not criou), "vazias": 0}


def importar_artefato_claude(arquivo: Path, a: dict, raiz: Path, log=print) -> dict:
    """Export novo do Claude (frames/artifacts/<id>/artifact.json): artefatos e páginas, com as versões."""
    versoes = sorted(a.get("versions") or [], key=lambda v: v.get("created_at") or "")
    if not versoes:
        return {"criadas": 0, "existentes": 0, "vazias": 1}
    titulo = versoes[-1].get("title") or "Artefato sem nome"
    data = _data(versoes[0].get("created_at"))
    pasta = arquivo.parent
    partes = [f"_{'Página' if a.get('kind') == 'page' else 'Artefato'} do Claude · {len(versoes)} versão(ões)_",
              "## Versões"]
    for v in versoes:
        arq = pasta / "versions" / f"{v.get('id')}.html"
        desc = (v.get("description") or "").strip()
        partes.append(f"- {_data(v.get('created_at'))}: {desc if a.get('kind') != 'page' else v.get('title')}"
                      + (f" (arquivo `{arq}`)" if arq.exists() else ""))
    pagina = pasta / "page.md"
    if pagina.exists():
        partes += ["## Conteúdo", pagina.read_text(encoding="utf-8").strip()]
    nome = f"{data} - {obsidian._nome_seguro(titulo, 80) or 'Artefato'}.md"
    criou = _nota(raiz / "Claude" / "Artefatos" / nome, titulo, data, "Claude", arquivo, "\n\n".join(partes))
    log(f"   Claude (artefato): {titulo} → {'nota criada' if criou else 'já existia'}")
    return {"criadas": int(criou), "existentes": int(not criou), "vazias": 0}


def importar_export(arquivo: Path, raiz: Path, log=print) -> dict:
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    if isinstance(dados, dict) and "prompt_template" in dados and "docs" in dados:
        return importar_projeto_claude(arquivo, dados, raiz, log)
    if isinstance(dados, dict) and dados.get("kind") in ("artifact", "page") and "versions" in dados:
        return importar_artefato_claude(arquivo, dados, raiz, log)
    if isinstance(dados, dict) and "messages" in dados:
        dados = [dados]                        # DeepSeek: um arquivo por conversa
    elif isinstance(dados, dict) and isinstance(dados.get("data"), list):
        dados = dados["data"]                  # Qwen: {"success": ..., "data": [...]}
    elif isinstance(dados, dict) and isinstance(dados.get("conversations"), list):
        dados = dados["conversations"]         # Grok e Perplexity: {"conversations": [...]}
    ia = _qual_export(dados)
    if not ia:
        return {"criadas": 0, "existentes": 0, "vazias": 0}
    if ia == "Gemini":
        dados = _gemini_por_dia(dados)
    ler = LEITORES[ia]
    feitos, usados = {"criadas": 0, "existentes": 0, "vazias": 0}, set()
    for c in sorted(dados, key=lambda c: str(c.get("create_time") or c.get("created_at")
                                             or c.get("inserted_at") or "")):
        titulo, data, falas = ler(c)
        if not falas:
            feitos["vazias"] += 1
            continue
        corpo = f"_Conversa com o {ia} · {data} · {len(falas)} mensagens_\n\n" + "\n\n".join(
            f"### {quem}\n\n{texto}" for quem, texto in falas)
        criou = _nota(raiz / ia / _nome_arquivo(data, titulo, usados), titulo, data, ia, arquivo, corpo)
        feitos["criadas" if criou else "existentes"] += 1
    log(f"   {ia}: {feitos['criadas']} notas novas, {feitos['existentes']} já existiam, {feitos['vazias']} vazias")
    return feitos


def importar_documento(arquivo: Path, raiz: Path, log=print) -> dict:
    from . import leitor
    _, _, texto = leitor.ler_completo(str(arquivo))
    if not (texto or "").strip():
        log(f"   ⚠ {arquivo.name}: sem texto legível")
        return {"criadas": 0, "existentes": 0, "vazias": 1}
    ia = _ia_pelo_caminho(arquivo)
    data = datetime.fromtimestamp(arquivo.stat().st_mtime).strftime("%Y-%m-%d")
    corpo = f"_Documento salvo de conversas com o {ia} · arquivo `{arquivo.name}`_\n\n{texto}"
    criou = _nota(raiz / ia / f"{data} - {obsidian._nome_seguro(arquivo.stem, 80)}.md",
                  arquivo.stem, data, ia, arquivo, corpo)
    log(f"   {ia}: {arquivo.name} → {'nota criada' if criou else 'já existia'}")
    return {"criadas": int(criou), "existentes": int(not criou), "vazias": 0}


def _indice(raiz: Path) -> None:
    linhas = ["# Histórico das IAs", "",
              "Todas as conversas exportadas das suas IAs, uma nota por conversa. Gerado pela Central de IA.", ""]
    for pasta in sorted(p for p in raiz.iterdir() if p.is_dir()):
        notas = sorted(pasta.glob("*.md"))
        linhas += [f"## {pasta.name} ({len(notas)})", ""] + [f"- [[{pasta.name}/{n.stem}|{n.stem}]]" for n in notas] + [""]
        for sub in (pasta / "Projetos", pasta / "Artefatos"):
            sub_notas = sorted(sub.glob("*.md")) if sub.is_dir() else []
            if sub_notas:
                linhas += [f"### {pasta.name} · {sub.name} ({len(sub_notas)})", ""] + [
                    f"- [[{pasta.name}/{sub.name}/{n.stem}|{n.stem}]]" for n in sub_notas] + [""]
    (raiz / "Histórico das IAs.md").write_text("\n".join(linhas), encoding="utf-8")


def _nome_zip(info) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except UnicodeError:
        return info.filename


def _chat_notebooklm(html: str) -> list[tuple[str, str]]:
    """'Chat History' do NotebookLM: blocos 'USER:' / 'MODEL:' em HTML, do mais novo para o mais antigo."""
    blocos = re.split(r"(?m)^(USER|MODEL): ", html)
    falas = [("Você" if quem == "USER" else "NotebookLM", _html_para_md(texto))
             for quem, texto in zip(blocos[1::2], blocos[2::2]) if texto.strip()]
    return list(reversed(falas)) if falas and falas[0][0] != "Você" else falas


def _tem_notebooklm(arq: Path) -> bool:
    import zipfile
    try:
        with zipfile.ZipFile(arq) as z:
            return any("/NotebookLM/" in _nome_zip(i) for i in z.infolist()[:50])
    except (zipfile.BadZipFile, OSError):
        return False


def importar_notebooklm(arquivos_zip: list[Path], raiz: Path, log=print) -> dict:
    """Takeout do NotebookLM: uma pasta por caderno com chats, notas e relatórios em texto,
    e um índice do caderno (NotebookLM/<caderno>.md) que lista as fontes e os áudios/vídeos."""
    import zipfile
    cadernos: dict[str, dict] = {}
    feitos = {"criadas": 0, "existentes": 0, "vazias": 0}
    base = raiz / "NotebookLM"
    for arq in arquivos_zip:
        with zipfile.ZipFile(arq) as z:
            for info in z.infolist():
                partes = _nome_zip(info).split("/")
                if len(partes) < 4 or partes[1] != "NotebookLM" or info.is_dir():
                    continue
                nome, cad = partes[-1], cadernos.setdefault(partes[2], {"fontes": set(), "midias": [], "notas": [], "meta": {}})
                secao = partes[3] if len(partes) > 4 else ""
                if not secao and nome.endswith("metadata.json"):
                    cad["meta"] = json.loads(z.read(info) or b"{}")
                elif secao in ("Sources", "Discovered Sources"):
                    if not nome.endswith("metadata.json"):
                        cad["fontes"].add(re.sub(r"\.(html|json)$", "", nome))
                elif nome.endswith("metadata.json"):
                    continue
                elif secao == "Chat History" and nome.endswith(".html"):
                    falas = _chat_notebooklm(z.read(info).decode("utf-8", "replace"))
                    if falas:
                        titulo = re.sub(r"\s+", " ", falas[0][1].splitlines()[0])[:70].strip(" *#_") or "Chat"
                        cad["notas"].append(("Chat", titulo, "\n\n".join(f"### {q}\n\n{t}" for q, t in falas)))
                elif secao in ("Notes", "Artifacts") and (nome.endswith((".html", ".md")) or "." not in nome):
                    try:
                        texto = z.read(info).decode("utf-8")
                    except UnicodeDecodeError:             # sem extensão e binário (slide, imagem)
                        cad["midias"].append(nome)
                        continue
                    if texto.lstrip().lower().startswith("<!doctype"):
                        cad["midias"].append(f"{nome} (página interativa: quiz, mapa ou tabela)")
                        continue
                    try:
                        corpo = texto if nome.endswith(".md") else _html_para_md(texto)
                    except Exception:
                        cad["midias"].append(nome)
                        continue
                    cad["notas"].append(("Nota" if secao == "Notes" else "Relatório",
                                         re.sub(r"\.(html|md)$", "", nome)[:90], corpo))
                else:
                    cad["midias"].append(nome)
    usados: set[str] = set()
    for pasta, cad in sorted(cadernos.items()):
        meta = cad["meta"].get("metadata") or {}
        titulo = cad["meta"].get("title") or pasta
        data = _data(meta.get("createTime"))
        seguro = base_nome = obsidian._nome_seguro(titulo, 80) or "Caderno"
        i = 2
        while seguro.lower() in usados:        # dois cadernos com o mesmo título
            seguro, i = f"{base_nome} ({i})", i + 1
        usados.add(seguro.lower())
        links = []
        for tipo, t, corpo in cad["notas"]:
            nome = f"{tipo} - {obsidian._nome_seguro(t, 70) or tipo}"
            destino = base / seguro / f"{nome}.md"
            i = 2
            while destino.exists() and destino.read_text(encoding="utf-8").split("\n---\n", 1)[-1].find(corpo[:200]) < 0:
                destino, i = base / seguro / f"{nome} ({i}).md", i + 1
            criou = _nota(destino, t, data, "NotebookLM", arq, f"_{tipo} do caderno [[{seguro}]] do NotebookLM_\n\n{corpo}")
            feitos["criadas" if criou else "existentes"] += 1
            links.append(f"- [[NotebookLM/{seguro}/{destino.stem}|{destino.stem}]]")
        partes = [f"_Caderno do NotebookLM · criado em {data} · {len(cad['fontes'])} fontes_", ""]
        if links:
            partes += ["## Chats, notas e relatórios", ""] + links + [""]
        if cad["midias"]:
            partes += ["## Áudios, vídeos e outros artefatos (ficam no arquivo do Takeout)", ""] + \
                      [f"- {m}" for m in sorted(cad["midias"])] + [""]
        if cad["fontes"]:
            partes += ["## Fontes carregadas", ""] + [f"- {f}" for f in sorted(cad["fontes"])]
        indice = base / f"{seguro}.md"
        if indice.exists():
            indice.unlink()                    # o índice é regenerado a cada importação
        _nota(indice, titulo, data, "NotebookLM", arq, "\n".join(partes))
    log(f"   NotebookLM: {len(cadernos)} cadernos · {feitos['criadas']} notas novas, {feitos['existentes']} já existiam")
    return feitos


def importar(caminhos: list[str | Path], raiz: Path | None = None, log=print) -> dict:
    if raiz is None:
        base = obsidian.garantir_estrutura()
        if not base:
            raise SystemExit("Obsidian não configurado: defina OBSIDIAN_VAULT no .env.")
        raiz = base / PASTA_COFRE
    raiz.mkdir(parents=True, exist_ok=True)
    total = {"criadas": 0, "existentes": 0, "vazias": 0}
    zips_nlm = [Path(c) for c in caminhos if str(c).lower().endswith(".zip") and _tem_notebooklm(Path(c))]
    if zips_nlm:
        r = importar_notebooklm(zips_nlm, raiz, log)
        for k in total:
            total[k] += r[k]
    for c in map(Path, caminhos):
        arquivos = [c] if c.is_file() else sorted(p for p in c.rglob("*") if p.is_file())
        for a in arquivos:
            if a.suffix.lower() == ".json":
                try:
                    r = importar_export(a, raiz, log)
                except (ValueError, UnicodeDecodeError):
                    continue
            elif a.suffix.lower() in (".docx", ".pdf", ".html", ".htm", ".txt", ".md") and c.is_file():
                r = importar_documento(a, raiz, log)
            else:
                continue
            for k in total:
                total[k] += r[k]
    _indice(raiz)
    log(f"✅ Obsidian → {raiz}: {total['criadas']} notas novas · {total['existentes']} já existiam")
    return total


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    importar(sys.argv[1:])
