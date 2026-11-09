"""COF (Curso Online de Filosofia): Markdown completo no Drive + todas as aulas em ÁUDIO, por tema.

Pedido do Reynolds (09/11/2026):
  1. terminar de passar para Markdown, na pasta Agente_COF do Drive, tudo o que está no HD (E:\\COF);
  2. criar "COF em Áudio" no Drive: todas as aulas em áudio, separadas por TEMA, mantendo a ordem de
     publicação (o número da aula vem na frente do nome) — para ouvir o professor no celular.

Uso:  python scripts/cof_audio_e_markdown.py markdown   (apostilas que faltam → Markdown no Drive)
      python scripts/cof_audio_e_markdown.py temas      (título e tema de cada aula, lendo a transcrição)
      python scripts/cof_audio_e_markdown.py audios     (extrai o áudio de cada aula para a pasta do tema)
      python scripts/cof_audio_e_markdown.py indice     (índice das aulas no Drive)
Tudo é retomável: rodar de novo continua de onde parou. Os vídeos originais nunca são alterados.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from superagente import agentes, provedores  # noqa: E402

agentes.carregar_env()
HD = Path(r"E:\COF")
DRIVE_COF = Path(r"G:\Meu Drive\Agente_COF")
AUDIOS = Path(r"G:\Meu Drive\COF em Áudio")
ESTADO = Path.home() / ".central-ia" / "cof_indice.json"
TEMAS = [
    "01 - Fundamentos e método do COF", "02 - Conhecimento, lógica e os quatro discursos",
    "03 - Metafísica e ontologia", "04 - Consciência, alma e imortalidade",
    "05 - Psicologia, caracterologia e personalidade", "06 - Vida intelectual, leitura, escrita e educação",
    "07 - Literatura, arte e imaginação", "08 - Religião, simbolismo e tradição",
    "09 - Ciência política, poder e Estado", "10 - História, civilização e modernidade",
    "11 - Brasil - cultura, política e sociedade", "12 - Revolução, comunismo e guerra cultural",
    "13 - Filosofia da ciência e cientificismo", "14 - Grandes filósofos e autores",
    "15 - Ética, moral e direito", "16 - Perguntas, respostas e temas diversos",
]


def _estado() -> dict:
    return json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}


def _gravar(estado: dict) -> None:
    ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")


def _aulas() -> dict[int, Path]:
    """Número da aula → vídeo principal no HD (o PreCOF fica de fora: é a versão prévia da mesma aula)."""
    aulas: dict[int, Path] = {}
    for p in sorted((HD / "1)COF").rglob("*.mp4")):
        m = re.fullmatch(r"COF_?(\d{3})", p.stem)
        if m and int(m.group(1)) not in aulas:
            aulas[int(m.group(1))] = p
    return aulas


def _md_da_aula(video: Path) -> Path:
    return DRIVE_COF / video.relative_to(HD).with_suffix(".md")


# ------------------------------------------------------------------ 1. Markdown
def markdown() -> None:
    from superagente import leitor
    feitos = 0
    for p in sorted(HD.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in {".pdf", ".docx", ".doc", ".pptx", ".txt", ".epub", ".odt", ".rtf"}:
            continue
        destino = DRIVE_COF / p.relative_to(HD).with_suffix(".md")
        if destino.exists():
            continue
        try:
            _, metodo, texto = leitor.ler_completo(str(p))
        except Exception as erro:  # noqa: BLE001
            print(f"⚠ {p.name}: {erro}")
            continue
        if not (texto or "").strip():
            print(f"⚠ {p.name}: sem texto")
            continue
        rel = str(p.relative_to(HD).parent)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            f'---\ntema_macro: "Curso Online de Filosofia (COF)"\ncaminho_relativo: "{rel}"\n'
            f'titulo: "{p.stem}"\ntipo_documento: "Documento ({p.suffix.lower()})"\n---\n\n# {p.stem}\n'
            f"- **Localização no Acervo:** {rel}\n- **Tipo de Conteúdo Original:** Documento ({p.suffix.lower()})\n\n"
            f"---\n\n## Conteúdo\n\n{texto.strip()}\n", encoding="utf-8")
        feitos += 1
        print(f"📝 {feitos}: {p.name} ({len(texto):,} caracteres, {metodo})", flush=True)
    print(f"✅ Markdown: {feitos} arquivos novos na pasta Agente_COF do Drive")


# ------------------------------------------------------------------ 2. Temas
def temas(lote: int = 5) -> None:
    estado = _estado()
    pendentes = [(n, v) for n, v in sorted(_aulas().items()) if str(n) not in estado]
    print(f"🏷 {len(pendentes)} aulas para titular e classificar", flush=True)
    sistema = ("Você cataloga as aulas do Curso Online de Filosofia (COF) de Olavo de Carvalho. As transcrições "
               "automáticas têm erros: deduza o assunto pelo conjunto. Para cada aula dê um TÍTULO fiel e curto "
               "(até 9 palavras, sem 'Aula') e escolha UM tema da lista, copiando-o exatamente. Responda só JSON: "
               '[{"n": 123, "titulo": "...", "tema": "...", "resumo": "uma frase"}]')
    for i in range(0, len(pendentes), lote):
        bloco = pendentes[i:i + lote]
        textos = []
        for n, video in bloco:
            md = _md_da_aula(video)
            corpo = md.read_text(encoding="utf-8", errors="replace").split("---", 2)[-1] if md.exists() else ""
            corpo = re.sub(r"\s+", " ", corpo)
            meio = len(corpo) // 2
            textos.append(f"### AULA {n}\n{corpo[:3500]} […] {corpo[meio:meio + 1500]}")
        try:
            _, r = provedores.chamar_com_reserva(["cli:claude/haiku", "gemini:gemini-3.5-flash"], sistema,
                                                 "TEMAS:\n" + "\n".join(TEMAS) + "\n\n" + "\n\n".join(textos),
                                                 log=lambda *_: None)
            for d in json.loads(re.search(r"\[.*\]", r, re.S).group(0)):
                tema = d.get("tema", "") if d.get("tema") in TEMAS else TEMAS[-1]
                estado[str(int(d["n"]))] = {"titulo": d.get("titulo", "").strip(), "tema": tema,
                                            "resumo": d.get("resumo", "").strip()}
        except Exception as erro:  # noqa: BLE001
            print(f"   ⚠ aulas {bloco[0][0]}–{bloco[-1][0]}: {str(erro)[:100]} (tento de novo depois)")
            continue
        _gravar(estado)
        print(f"   {min(i + lote, len(pendentes))}/{len(pendentes)}", flush=True)
    print(f"✅ {len(estado)} aulas tituladas")


# ------------------------------------------------------------------ 3. Áudios
def _nome_seguro(texto: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", texto).strip().rstrip(".")[:90]


def audios() -> None:
    estado = _estado()
    aulas = _aulas()
    print(f"🎧 {len(aulas)} aulas · áudio AAC 64 kbps mono (voz) → {AUDIOS}", flush=True)
    for n, video in sorted(aulas.items()):
        info = estado.get(str(n), {})
        tema = info.get("tema") or "00 - A classificar"
        titulo = info.get("titulo") or ""
        destino = AUDIOS / tema / _nome_seguro(f"COF {n:03d}{' - ' + titulo if titulo else ''}")
        destino = destino.with_name(destino.name + ".m4a")
        existentes = list(AUDIOS.rglob(f"COF {n:03d}*.m4a")) if AUDIOS.exists() else []
        if any(e == destino for e in existentes):
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        if existentes:   # já extraído antes (ex.: antes de ter tema): só move para o lugar certo
            existentes[0].replace(destino)
            continue
        temporario = Path(r"E:\_central_ia_temp") / f"COF {n:03d}.m4a"
        temporario.parent.mkdir(exist_ok=True)
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1",
                            "-c:a", "aac", "-b:a", "64k", "-metadata", f"title=COF {n:03d} - {titulo}",
                            "-metadata", "artist=Olavo de Carvalho", "-metadata", "album=Curso Online de Filosofia",
                            "-metadata", f"track={n}", str(temporario)], capture_output=True, text=True)
        if r.returncode != 0 or not temporario.exists():
            print(f"   ⚠ COF {n:03d}: {(r.stderr or '')[-150:]}")
            continue
        temporario.replace(destino) if temporario.drive == destino.drive else _mover(temporario, destino)
        print(f"🎧 COF {n:03d} → {tema}/{destino.name} ({destino.stat().st_size / 1e6:.0f} MB)", flush=True)


def _mover(origem: Path, destino: Path) -> None:
    import shutil
    shutil.copyfile(origem, destino)
    origem.unlink()


# ------------------------------------------------------------------ 4. Índice
def indice() -> None:
    estado = _estado()
    aulas = _aulas()
    linhas = ["# Curso Online de Filosofia — índice das aulas", "",
              "Em ordem de publicação, com o tema de cada aula. Os áudios estão em *COF em Áudio*, "
              "uma pasta por tema (o número da aula vem na frente do nome).", ""]
    for n in sorted(aulas):
        i = estado.get(str(n), {})
        linhas.append(f"- **COF {n:03d}** — {i.get('titulo', '')} · _{i.get('tema', '')}_"
                      + (f"  \n  {i['resumo']}" if i.get("resumo") else ""))
    por_tema: dict[str, list[int]] = {}
    for n in sorted(aulas):
        por_tema.setdefault(estado.get(str(n), {}).get("tema", "00 - A classificar"), []).append(n)
    linhas += ["", "## Por tema", ""]
    for tema in sorted(por_tema):
        linhas.append(f"- **{tema}** ({len(por_tema[tema])} aulas): "
                      + ", ".join(f"{n:03d}" for n in por_tema[tema]))
    texto = "\n".join(linhas) + "\n"
    for destino in (AUDIOS / "Índice das aulas do COF.md", DRIVE_COF / "Índice das aulas do COF.md"):
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")
    print(f"✅ Índice com {len(aulas)} aulas gravado no Drive (COF em Áudio e Agente_COF)")


if __name__ == "__main__":
    {"markdown": markdown, "temas": temas, "audios": audios, "indice": indice}[sys.argv[1]]()
