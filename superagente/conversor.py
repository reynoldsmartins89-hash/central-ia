"""Conversor de pastas para Markdown: transforma TODOS os arquivos de uma pasta (PDF, Word, PowerPoint,
Excel, e-book, vídeo e áudio) em .md, numa subpasta organizada com a MESMA estrutura da original.

Uso:  python main.py markdown "G:/Meu Drive/MBA em Gestão de Projetos"
      (vídeos e áudios são transcritos na placa de vídeo — demora mais; os documentos vêm primeiro)
Os arquivos originais nunca são alterados. Rodar de novo só converte o que ainda falta.
"""
from __future__ import annotations

import re
from pathlib import Path

NOME_SUBPASTA = "_Markdown (texto de todos os arquivos)"
DOCUMENTOS = {".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".xls", ".epub", ".odt", ".rtf", ".txt", ".html", ".htm", ".md"}
MIDIA = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".webm", ".wmv", ".mp3", ".m4a", ".wav", ".ogg", ".aac"}


def _destino(arquivo: Path, origem: Path, destino: Path) -> Path:
    return destino / arquivo.relative_to(origem).with_suffix(".md")


def converter_pasta(origem: str | Path, midias: bool = True, log=print) -> dict:
    from . import leitor
    origem = Path(origem)
    destino = origem / NOME_SUBPASTA
    arquivos = [p for p in sorted(origem.rglob("*")) if p.is_file() and destino not in p.parents]
    docs = [p for p in arquivos if p.suffix.lower() in DOCUMENTOS]
    mids = [p for p in arquivos if p.suffix.lower() in MIDIA] if midias else []
    log(f"📁 {origem.name}: {len(docs)} documentos e {len(mids)} vídeos/áudios → {destino.name}")
    feitos = {"documentos": 0, "midias": 0, "falhas": 0}
    for p in docs + mids:   # documentos primeiro (rápidos), depois as transcrições
        alvo = _destino(p, origem, destino)
        if alvo.exists():
            continue
        try:
            if p.suffix.lower() in MIDIA:
                _, texto = leitor._transcrever(str(p))
                texto = re.sub(r"^\[\d+:\d+(?::\d+)?\]\s*(\[\w+\]\s*)?", "", texto, flags=re.M)
                tipo = "Transcrição de vídeo/áudio"
            else:
                _, _, texto = leitor.ler_completo(str(p))
                tipo = f"Documento ({p.suffix.lower()})"
        except Exception as erro:  # noqa: BLE001
            log(f"   ⚠ {p.name}: {str(erro)[:100]}")
            feitos["falhas"] += 1
            continue
        if not (texto or "").strip():
            feitos["falhas"] += 1
            continue
        alvo.parent.mkdir(parents=True, exist_ok=True)
        rel = p.relative_to(origem).parent.as_posix()
        alvo.write_text(f'---\ncurso: "{origem.name}"\nmodulo: "{rel}"\ntitulo: "{p.stem}"\ntipo: "{tipo}"\n---\n\n'
                        f"# {p.stem}\n\n_{rel or origem.name} · {tipo}_\n\n{texto.strip()}\n", encoding="utf-8")
        chave = "midias" if p.suffix.lower() in MIDIA else "documentos"
        feitos[chave] += 1
        log(f"   📝 {rel}/{p.name} ({len(texto):,} caracteres)")
    log(f"✅ {feitos['documentos']} documentos e {feitos['midias']} vídeos/áudios convertidos · {feitos['falhas']} sem texto")
    return feitos
