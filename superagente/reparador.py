"""Agente Reparador: recupera arquivos danificados SEM tocar no original.

Para cada problema apontado pelo Agente de Integridade, primeiro DIAGNOSTICA:
  • falso alarme — arquivo auxiliar (._ do Mac, ~$ do Office, _vti_ do FrontPage, peças de página salva)
                   ou PDF só protegido contra cópia (abre normalmente)
  • tipo errado  — o conteúdo é de outro formato (ex.: um vídeo salvo como .rar) → cópia com a extensão certa
  • reparável    — PDF, pacotes zip/Office, imagens, vídeos, áudios e RAR com estrutura danificada
  • perdido      — o arquivo é só zeros/lixo (download que nunca terminou): não há o que reparar;
                   o Reparador procura uma CÓPIA SÃ do mesmo arquivo no catálogo (HDs e Drive)
  • incompleto   — download interrompido (.part, .crdownload): reparo impossível sem a fonte

Os consertos vão para <disco>\\REPARADOS PELA CENTRAL\\<mesmo caminho>. O ORIGINAL NUNCA É ALTERADO.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .curador import _conectar, _raiz
from .integridade import IMAGENS, INCOMPLETOS, PACOTES_ZIP

PASTA_REPARADOS = "REPARADOS PELA CENTRAL"
AUXILIARES = ("._", "~$")
PASTAS_AUXILIARES = {"_vti_cnf", "_vti_pvt", "_vti_txt"}
MIDIA = {".mp4", ".m4v", ".mov", ".m4a", ".3gp", ".mp3", ".avi", ".mkv", ".wav", ".ts", ".flv", ".wmv"}


# ------------------------------------------------------------------ ferramentas
def _ffmpeg() -> str | None:
    achado = shutil.which("ffmpeg")
    if achado:
        return achado
    base = os.path.join(os.getenv("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    candidatos = glob.glob(os.path.join(base, "*FFmpeg*", "**", "ffmpeg.exe"), recursive=True)
    return candidatos[0] if candidatos else None


def _rar() -> str | None:
    for c in (r"C:\Program Files\WinRAR\Rar.exe", r"C:\Program Files (x86)\WinRAR\Rar.exe"):
        if os.path.exists(c):
            return c
    return shutil.which("rar")


def _sete_zip() -> str | None:
    for c in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if os.path.exists(c):
            return c
    return shutil.which("7z")


def _rodar(cmd: list[str], cwd: str | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# ------------------------------------------------------------------ diagnóstico
def tipo_real(cabecalho: bytes) -> str | None:
    """Extensão verdadeira pelo CONTEÚDO (assinatura), não pelo nome."""
    c = cabecalho
    if b"%PDF" in c[:1024]:
        return ".pdf"
    if c[4:8] == b"ftyp":
        return ".heic" if c[8:12] in (b"heic", b"heix", b"mif1", b"msf1") else ".mp4"
    tabela = [(b"PK\x03\x04", ".zip"), (b"Rar!", ".rar"), (b"7z\xbc\xaf", ".7z"), (b"\xff\xd8\xff", ".jpg"),
              (b"\x89PNG", ".png"), (b"GIF8", ".gif"), (b"II*\x00", ".tif"), (b"MM\x00*", ".tif"),
              (b"ID3", ".mp3"), (b"OggS", ".ogg"), (b"fLaC", ".flac"), (b"\x1aE\xdf\xa3", ".mkv"),
              (b"{\\rtf", ".rtf"), (b"\xd0\xcf\x11\xe0", ".doc"), (b"BM", ".bmp"), (b"0&\xb2u", ".wmv")]
    for assinatura, ext in tabela:
        if c.startswith(assinatura):
            return ext
    if c[:4] == b"RIFF":
        return {b"WEBP": ".webp", b"AVI ": ".avi", b"WAVE": ".wav"}.get(c[8:12])
    if len(c) > 400 and c[0] == 0x47 and c[188] == 0x47 and c[376] == 0x47:
        return ".ts"
    if c[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    return None


def _so_zeros(caminho: Path, amostras: int = 32) -> bool:
    """Lê pedaços espalhados pelo arquivo: tudo zero (ou 0xFF) = conteúdo nunca gravado."""
    tamanho = caminho.stat().st_size
    with open(caminho, "rb") as f:
        for i in range(amostras):
            f.seek(tamanho * i // amostras)
            bloco = f.read(65536)
            if bloco.strip(b"\x00") and bloco.strip(b"\xff"):
                return False
    return True


def diagnosticar(caminho: str | Path) -> tuple[str, str]:
    p = Path(caminho)
    if p.name.startswith(AUXILIARES) or PASTAS_AUXILIARES & {x.lower() for x in p.parts} \
            or p.name.lower().endswith((".js.download", ".css.download")):
        return "falso_alarme", "arquivo auxiliar do sistema/programa (não é um documento seu)"
    if p.suffix.lower() in INCOMPLETOS:
        return "incompleto", "download interrompido: o resto do arquivo nunca chegou ao disco"
    if not p.exists():
        return "sumiu", "o arquivo não existe mais neste caminho"
    if p.stat().st_size == 0:
        return "perdido", "arquivo vazio (0 bytes)"
    if _so_zeros(p):
        return "perdido", "o arquivo só tem zeros: a cópia/download nunca gravou o conteúdo"
    with open(p, "rb") as f:
        cabecalho = f.read(4096)
    real = tipo_real(cabecalho)
    ext = p.suffix.lower()
    equivalentes = {".jpeg": ".jpg", ".tiff": ".tif", ".docx": ".zip", ".xlsx": ".zip", ".pptx": ".zip",
                    ".epub": ".zip", ".odt": ".zip", ".ods": ".zip", ".odp": ".zip", ".m4v": ".mp4",
                    ".mov": ".mp4", ".m4a": ".mp4", ".3gp": ".mp4", ".001": ".rar", ".xls": ".doc", ".ppt": ".doc"}
    if real and real != equivalentes.get(ext, ext):
        return "tipo_errado", f"o conteúdo é {real}, não {ext}"
    if real is None and ext not in (".mp3", ".ts"):
        return "perdido", "conteúdo irreconhecível (sobrescrito ou embaralhado)"
    return "reparavel", ""


# ------------------------------------------------------------------ consertos (sempre numa CÓPIA)
def _verificar_pdf(destino: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(destino)).pages)


def _reparar_pdf(origem: Path, destino: Path) -> str:
    try:
        import pikepdf
        with pikepdf.open(origem, password="") as pdf:   # reconstrói a tabela interna; tira trava de cópia
            pdf.save(destino)
        return f"PDF reconstruído ({_verificar_pdf(destino)} páginas)"
    except Exception:  # noqa: BLE001
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(origem))
        doc.save(str(destino))
        return f"PDF salvo pelo motor do Chrome ({len(doc)} páginas)"


def _reparar_pacote(origem: Path, destino: Path) -> str:
    temp = Path(tempfile.mkdtemp(prefix="central-ia-reparo-"))
    try:
        salvos = 0
        try:  # 1º: aproveita entrada por entrada do que ainda abre
            with zipfile.ZipFile(origem) as z:
                for info in z.infolist():
                    try:
                        z.extract(info, temp)
                        salvos += 1
                    except Exception:  # noqa: BLE001
                        continue
        except zipfile.BadZipFile:
            pass
        if not salvos and _sete_zip():  # 2º: o 7-Zip recupera pacotes sem o índice final
            _rodar([_sete_zip(), "x", "-y", f"-o{temp}", str(origem)])
            salvos = sum(1 for x in temp.rglob("*") if x.is_file())
        if not salvos:
            raise RuntimeError("nenhuma parte do pacote pôde ser recuperada")
        arquivos = sorted((x for x in temp.rglob("*") if x.is_file()),
                          key=lambda x: (x.name != "[Content_Types].xml" and x.name != "mimetype", str(x)))
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
            for x in arquivos:
                z.write(x, x.relative_to(temp).as_posix(),
                        compress_type=zipfile.ZIP_STORED if x.name == "mimetype" else zipfile.ZIP_DEFLATED)
        return f"pacote remontado com {len(arquivos)} partes recuperadas"
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def _reparar_imagem(origem: Path, destino: Path) -> str:
    from PIL import Image, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True   # aproveita até onde a imagem foi gravada
    with Image.open(origem) as img:
        img.load()
        formato = img.format or "PNG"
        if formato == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(destino, format=formato)
    return f"imagem recuperada ({formato})"


def _reparar_midia(origem: Path, destino: Path) -> str:
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg não instalado")
    extra = ["-movflags", "+faststart"] if destino.suffix.lower() in (".mp4", ".mov", ".m4a", ".m4v") else []
    r = _rodar([ffmpeg, "-y", "-v", "error", "-err_detect", "ignore_err", "-fflags", "+genpts+discardcorrupt",
                "-i", str(origem), "-map", "0", "-c", "copy", *extra, str(destino)])
    if not destino.exists() or destino.stat().st_size < 1024:
        erro = r.stderr.decode("utf-8", "replace").strip().splitlines()
        if any("moov atom not found" in e for e in erro):
            raise RuntimeError("vídeo sem índice 'moov' (gravação interrompida): precisa de um vídeo de "
                               "referência da MESMA câmera para reconstruir")
        raise RuntimeError(erro[-1] if erro else "ffmpeg não conseguiu ler")
    return f"mídia remontada ({destino.stat().st_size / 1e6:.0f} MB aproveitados)"


def _reparar_rar(origem: Path, destino: Path) -> str:
    rar = _rar()
    if not rar:
        raise RuntimeError("WinRAR não instalado")
    temp = Path(tempfile.mkdtemp(prefix="central-ia-rar-"))
    try:
        _rodar([rar, "r", "-y", str(origem)], cwd=str(temp))   # o WinRAR grava fixed.<nome> / rebuilt.<nome>
        gerados = [x for x in temp.iterdir() if x.is_file()]
        if not gerados:
            raise RuntimeError("o WinRAR não conseguiu reconstruir este volume")
        shutil.move(str(gerados[0]), destino)
        return "compactado reconstruído pelo WinRAR"
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def _consertar(origem: Path, destino: Path, ext: str) -> str:
    if ext == ".pdf":
        return _reparar_pdf(origem, destino)
    if ext in PACOTES_ZIP:
        return _reparar_pacote(origem, destino)
    if ext in IMAGENS:
        return _reparar_imagem(origem, destino)
    if ext in MIDIA:
        return _reparar_midia(origem, destino)
    if ext in (".rar", ".001"):
        return _reparar_rar(origem, destino)
    raise RuntimeError(f"ainda não sei reparar {ext}")


# ------------------------------------------------------------------ cópia sã em outro lugar
def copia_sa(con, caminho: str) -> str | None:
    """Procura o MESMO arquivo (mesmo nome) em outro lugar dos HDs ou do Drive, sem problema registrado."""
    nome = Path(caminho).name
    linha = con.execute("""SELECT a.caminho FROM arquivos a LEFT JOIN integridade i ON i.caminho = a.caminho
                           WHERE a.nome = ? AND a.caminho <> ? AND i.caminho IS NULL
                           AND a.caminho NOT LIKE '%DUPLICADOS PARA REVISAR%' LIMIT 1""", (nome, caminho)).fetchone()
    if linha:
        return linha[0]
    try:
        linha = con.execute("SELECT caminho FROM drive WHERE nome = ? AND tamanho > 0 LIMIT 1", (nome,)).fetchone()
        return f"Google Drive: {linha[0]}" if linha else None
    except Exception:  # noqa: BLE001 — Drive ainda não catalogado
        return None


# ------------------------------------------------------------------ rodada
def _tabela():
    con = _conectar()
    con.executescript("""CREATE TABLE IF NOT EXISTS integridade (caminho TEXT PRIMARY KEY, problema TEXT, detalhe TEXT);
        CREATE TABLE IF NOT EXISTS reparos (caminho TEXT PRIMARY KEY, situacao TEXT, detalhe TEXT,
            destino TEXT, copia_sa TEXT, quando TEXT);
        CREATE INDEX IF NOT EXISTS idx_arquivos_nome ON arquivos(nome);""")
    try:
        con.execute("CREATE INDEX IF NOT EXISTS idx_drive_nome ON drive(nome)")
    except Exception:  # noqa: BLE001 — Drive ainda não catalogado
        pass
    return con


def reparar_um(con, caminho: str, raiz: Path) -> tuple[str, str, str | None, str | None]:
    situacao, detalhe = diagnosticar(caminho)
    destino = None
    if situacao in ("reparavel", "tipo_errado"):
        origem = Path(caminho)
        try:
            relativo = origem.relative_to(raiz)
        except ValueError:
            relativo = Path(origem.name)
        alvo = raiz / PASTA_REPARADOS / relativo
        alvo.parent.mkdir(parents=True, exist_ok=True)
        if situacao == "tipo_errado":
            ext = detalhe.split()[3]  # "o conteúdo é .mp4, não .rar"
            ext = ext.rstrip(",")
            alvo = alvo.with_name(alvo.name + ext)
            shutil.copy2(origem, alvo)
            detalhe += " → cópia com a extensão certa"
            if ext in (".pdf", ".zip", ".mp4"):  # e já confere se o conteúdo verdadeiro abre
                try:
                    _consertar(alvo, alvo.with_name("_" + alvo.name), ext)
                    os.replace(alvo.with_name("_" + alvo.name), alvo)
                except Exception:  # noqa: BLE001
                    alvo.with_name("_" + alvo.name).unlink(missing_ok=True)
            situacao, destino = "reparado", str(alvo)
        else:
            try:
                detalhe = _consertar(origem, alvo, origem.suffix.lower())
                situacao, destino = "reparado", str(alvo)
            except Exception as erro:  # noqa: BLE001
                alvo.unlink(missing_ok=True)
                if "password" in str(erro).lower():
                    situacao, detalhe = "protegido", ("PDF com senha (não está danificado). Contas e boletos "
                                                      "costumam usar parte do CPF como senha.")
                else:
                    situacao, detalhe = "nao_reparado", str(erro)[:300]
    sa = copia_sa(con, caminho) if situacao in ("perdido", "incompleto", "nao_reparado") else None
    return situacao, detalhe, destino, sa


def reparar(raiz: str | Path, limite: int | None = None, log=print) -> dict:
    """Trabalha os problemas do disco/pasta `raiz` ainda não tratados. Retomável."""
    raiz = _raiz(raiz)
    prefixo = str(raiz).rstrip("\\/") + "\\%"
    con = _tabela()
    fila = [c for (c,) in con.execute("""SELECT i.caminho FROM integridade i LEFT JOIN reparos r
                                         ON r.caminho = i.caminho WHERE r.caminho IS NULL AND i.caminho LIKE ?""",
                                      (prefixo,)).fetchall()][:limite]
    log(f"🛠 Reparador: {len(fila):,} arquivos com problema para tratar em {raiz}")
    contagem: dict[str, int] = {}
    for n, caminho in enumerate(fila, 1):
        try:
            situacao, detalhe, destino, sa = reparar_um(con, caminho, raiz)
        except Exception as erro:  # noqa: BLE001
            situacao, detalhe, destino, sa = "nao_reparado", f"{type(erro).__name__}: {erro}"[:300], None, None
        con.execute("INSERT OR REPLACE INTO reparos VALUES (?, ?, ?, ?, ?, ?)",
                    (caminho, situacao, detalhe, destino, sa, datetime.now().isoformat(timespec="minutes")))
        con.commit()
        contagem[situacao] = contagem.get(situacao, 0) + 1
        if n % 200 == 0:
            log(f"   {n:,}/{len(fila):,} · " + " · ".join(f"{k}: {v}" for k, v in contagem.items()))
    con.close()
    log("✅ Reparador: " + " · ".join(f"{NOMES.get(k, k)}: {v:,}" for k, v in contagem.items()))
    return contagem


NOMES = {"reparado": "✅ reparados (cópia em REPARADOS PELA CENTRAL)", "falso_alarme": "👌 falso alarme",
         "perdido": "❌ conteúdo perdido", "incompleto": "⏳ download incompleto",
         "nao_reparado": "⚠ não foi possível reparar", "sumiu": "🔍 não existe mais",
         "protegido": "🔐 protegido por senha (não está danificado)"}


def relatorio(raiz: str | None = None, exemplos: int = 5) -> str:
    con = _tabela()
    filtro, args = ("WHERE caminho LIKE ?", [str(_raiz(raiz)).rstrip("\\/") + "\\%"]) if raiz else ("", [])
    linhas = ["🛠 Agente Reparador (os originais nunca são alterados):"]
    for situacao, n in con.execute(f"SELECT situacao, count(*) FROM reparos {filtro} GROUP BY situacao "
                                   "ORDER BY count(*) DESC", args):
        linhas.append(f"\n{NOMES.get(situacao, situacao)}: {n:,}")
        for caminho, detalhe, sa in con.execute(
                f"SELECT caminho, detalhe, copia_sa FROM reparos {filtro} {'AND' if filtro else 'WHERE'} "
                "situacao = ? ORDER BY copia_sa IS NULL LIMIT ?", [*args, situacao, exemplos]):
            linhas.append(f"   • {caminho}\n     {detalhe}" + (f"\n     💚 cópia sã: {sa}" if sa else ""))
    salvos = con.execute(f"SELECT count(*) FROM reparos {filtro} {'AND' if filtro else 'WHERE'} "
                         "copia_sa IS NOT NULL", args).fetchone()[0]
    if salvos:
        linhas.append(f"\n💚 {salvos:,} arquivos perdidos/incompletos TÊM uma cópia sã em outro lugar")
    con.close()
    return "\n".join(linhas)
