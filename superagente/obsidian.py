"""Integração com o Obsidian: cada execução vira uma nota no seu cofre.

Configuração (.env):
    OBSIDIAN_VAULT=C:\\Users\\voce\\Documents\\Obsidian Vault

Estrutura criada no cofre:
    Central IA/
    ├── Painel.base          ← tabela com todas as execuções (abra no Obsidian)
    ├── Como usar.md
    └── Execuções/
        └── 2026-11-08 21h11 - <assunto>.md   (status: pendente → aprovado)
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

SUBPASTA = "Central IA"

PAINEL = """filters:
  and:
    - file.inFolder("Central IA/Execuções")
views:
  - type: table
    name: Todas as execuções
    order:
      - file.name
      - status
      - data
      - tipo
      - agentes
      - modelos
  - type: table
    name: Aguardando minha aprovação
    filters:
      and:
        - status == "pendente"
    order:
      - file.name
      - data
      - agentes
"""

COMO_USAR = """# Central de IA — como usar

- Mande qualquer pedido para o seu bot no **Telegram**. O superagente escolhe os agentes e as IAs.
- Cada execução aparece em **[[Painel.base|Painel]]** e em `Execuções/`.
- Ao revisar uma nota, troque a propriedade **status** de `pendente` para `aprovado` (ou `ajustar`).
- Nada é publicado automaticamente.
"""


def cofre() -> Path | None:
    caminho = os.getenv("OBSIDIAN_VAULT", "").strip()
    return Path(caminho) if caminho and Path(caminho).is_dir() else None


def configurado() -> bool:
    return cofre() is not None


def _nome_seguro(texto: str, limite: int = 60) -> str:
    texto = re.sub(r'[\\/:*?"<>|#^\[\]\n\r]+', " ", texto).strip()
    return (texto[:limite].rstrip() + "…") if len(texto) > limite else texto


def garantir_estrutura() -> Path | None:
    base = cofre()
    if not base:
        return None
    raiz = base / SUBPASTA
    (raiz / "Execuções").mkdir(parents=True, exist_ok=True)
    for nome, conteudo in (("Painel.base", PAINEL), ("Como usar.md", COMO_USAR)):
        arquivo = raiz / nome
        if not arquivo.exists():
            arquivo.write_text(conteudo, encoding="utf-8")
    return raiz


def salvar_execucao(titulo: str, tipo: str, registro: dict, pasta_resultado: Path) -> Path | None:
    """Cria a nota da execução no cofre. Retorna o caminho da nota (ou None sem Obsidian)."""
    raiz = garantir_estrutura()
    if not raiz:
        return None
    agora = datetime.now()
    agentes = [e["agente"] for e in registro["etapas"]]
    modelos = sorted({e["modelo"] for e in registro["etapas"]})
    yaml_lista = lambda itens: "".join(f"\n  - {json.dumps(i, ensure_ascii=False)}" for i in itens)  # noqa: E731
    linhas = [
        "---",
        "status: pendente",
        f"data: {agora:%Y-%m-%dT%H:%M}",
        f"tipo: {tipo}",
        f"pedido: {json.dumps(registro['entrada'][:300], ensure_ascii=False)}",
        f"agentes:{yaml_lista(agentes)}",
        f"modelos:{yaml_lista(modelos)}",
        f"pasta: {json.dumps(str(pasta_resultado), ensure_ascii=False)}",
        "---",
        f"# {titulo}",
        "",
        f"> **Pedido:** {registro['entrada']}",
        "",
    ]
    for i, etapa in enumerate(registro["etapas"], 1):
        texto = (pasta_resultado / etapa["arquivo"]).read_text(encoding="utf-8")
        linhas += [f"## {i}. {etapa['agente']} · `{etapa['modelo']}`", "", texto.strip(), ""]
    nota = raiz / "Execuções" / f"{agora:%Y-%m-%d %Hh%M} - {_nome_seguro(titulo)}.md"
    origem_imagens = pasta_resultado / "imagens" / "imagens.json"
    if origem_imagens.exists():
        destino = raiz / "Imagens" / nota.stem
        destino.mkdir(parents=True, exist_ok=True)
        linhas += ["## Imagens", ""]
        for item in json.loads(origem_imagens.read_text(encoding="utf-8")):
            if "arquivo" not in item:
                continue
            shutil.copy2(pasta_resultado / "imagens" / item["arquivo"], destino / item["arquivo"])
            linhas += [f"**{item['nome']}** ({item.get('canal', '')}, {item.get('formato', '')}) — {item.get('credito', item.get('fonte', ''))}",
                       f"![[{SUBPASTA}/Imagens/{nota.stem}/{item['arquivo']}]]", f"*{item.get('alt', '')}*", ""]
    nota.write_text("\n".join(linhas), encoding="utf-8")
    return nota
