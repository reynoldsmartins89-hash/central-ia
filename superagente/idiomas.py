"""Idiomas: o material do HD (E:\\IDIOMAS) vira Markdown no Google Drive, um idioma por pasta.

Pedido do Reynolds (16/11/2026): transformar todo o acervo de idiomas em Markdown, guardar no Drive
(Meu Drive\\Idiomas\\<Idioma>) e alimentar o professor de cada idioma.

Como funciona (sem disputar a GPU com a leitura geral):
  1. O Agente Leitor lê E:\\IDIOMAS ANTES do resto do acervo (ver `pastas_em_ordem`, usada em
     leitor.processar): primeiro todos os documentos, depois áudio/vídeo do idioma menor ao maior.
     Cada arquivo lido vira ficha .md e entra no índice que os professores consultam.
  2. `espelhar()` copia cada ficha pronta para o Drive, mantendo a estrutura de pastas do HD.
     Retomável: só copia o que é novo. O Vigia roda isso a cada 30 minutos.
Os arquivos originais nunca são alterados.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path

HD = Path(os.getenv("IDIOMAS_HD") or r"E:\IDIOMAS")
DRIVE = Path(os.getenv("IDIOMAS_DRIVE") or r"G:\Meu Drive\Idiomas")

# do menor acervo (resultado mais rápido) ao maior; nome da pasta no HD → nome no Drive
ORDEM = [("ÁRABE", "Árabe"), ("GREGO", "Grego"), ("HEBRAICO", "Hebraico"), ("COREANO", "Coreano"),
         ("LATIM", "Latim"), ("JAPONÊS", "Japonês"), ("ESPANHOL", "Espanhol"), ("FRANCÊS", "Francês"),
         ("ALEMÃO", "Alemão"), ("INGLÊS", "Inglês"), ("ITALIANO", "Italiano"), ("MANDARIM", "Mandarim")]
NOME_NO_DRIVE = dict(ORDEM)


def pastas_em_ordem() -> list[str]:
    """Pastas que o Agente Leitor lê antes de todo o resto, na ordem de prioridade."""
    if os.getenv("IDIOMAS_PRIMEIRO", "1").strip() == "0":
        return []
    return [str(HD / pasta) for pasta, _ in ORDEM]


def _longo(p: Path) -> str:
    """Caminho que o Windows aceita mesmo com mais de 260 caracteres."""
    texto = str(p)
    return "\\\\?\\" + texto if os.name == "nt" and len(texto) > 240 and not texto.startswith("\\\\?\\") else texto


def destino(caminho: str) -> Path | None:
    """Caminho no Drive da ficha de `caminho` (arquivo do HD ou 'pacote.rar::dentro/aula.mp3')."""
    real, _, interno = caminho.partition("::")
    try:
        rel = Path(real).relative_to(HD)
    except ValueError:
        return None
    if len(rel.parts) < 2:
        return None
    idioma = NOME_NO_DRIVE.get(rel.parts[0].upper(), rel.parts[0].title())
    partes = list(rel.parts[1:]) + ([p for p in interno.split("/") if p] if interno else [])
    partes = [re.sub(r'[<>:"|?*]+', " ", p).strip().rstrip(".") or "_" for p in partes]
    partes[-1] += ".md"   # aula.mp3 → aula.mp3.md (não colide com aula.pdf.md)
    return DRIVE.joinpath(idioma, *partes)


def _corpo_da_ficha(ficha: Path) -> str:
    texto = ficha.read_text(encoding="utf-8", errors="replace")
    if texto.startswith("---"):
        texto = texto.split("---", 2)[-1]
    return re.sub(r"\A\s*# [^\n]*\n", "", texto).strip()


def espelhar(log=print) -> dict:
    """Copia para o Drive as fichas de E:\\IDIOMAS que ainda não estão lá (ou mudaram)."""
    from .leitor import _tabelas
    if not DRIVE.parent.exists():
        log(f"⚠ {DRIVE.parent} não está disponível (o Google Drive para Desktop está aberto?)")
        return {"copiadas": 0, "erros": 0}
    con = _tabelas()
    prefixo = str(HD).rstrip("\\/") + "\\"
    linhas = con.execute("SELECT caminho, tipo, metodo, ficha, lido_em FROM leituras "
                         "WHERE status = 'lido' AND ficha IS NOT NULL AND caminho LIKE ?",
                         (prefixo + "%",)).fetchall()
    con.close()
    contagem = {"copiadas": 0, "erros": 0}
    for caminho, tipo, metodo, ficha, lido_em in linhas:
        alvo, ficha = destino(caminho), Path(ficha)
        if alvo is None or not ficha.exists():
            continue
        try:
            if os.path.exists(_longo(alvo)) and os.path.getmtime(_longo(alvo)) >= ficha.stat().st_mtime:
                continue
            os.makedirs(_longo(alvo.parent), exist_ok=True)
            rel = caminho.replace(prefixo, "", 1)
            with open(_longo(alvo), "w", encoding="utf-8") as f:
                f.write(f'---\nidioma: "{alvo.relative_to(DRIVE).parts[0]}"\n'
                        f'origem: "{rel.replace(chr(92), "/")}"\ntipo: "{tipo}"\nmetodo: "{metodo}"\n'
                        f'lido_em: "{lido_em}"\n---\n\n# {Path(caminho.split("::")[-1]).name}\n'
                        f"- **Localização no HD:** IDIOMAS\\{rel}\n- **Tipo:** {tipo} ({metodo})\n\n"
                        f"---\n\n{_corpo_da_ficha(ficha)}\n")
            contagem["copiadas"] += 1
        except OSError as erro:
            contagem["erros"] += 1
            log(f"   ⚠ {Path(caminho).name}: {erro}")
    _gravar_progresso(log)
    log(f"✅ Idiomas no Drive: {contagem['copiadas']} fichas novas ({contagem['erros']} com erro) → {DRIVE}")
    return contagem


def progresso() -> list[tuple[str, int, int]]:
    """(idioma, arquivos lidos, arquivos a ler) por idioma, na ordem de prioridade."""
    from .leitor import _tabelas, tipo_de
    con = _tabelas()
    prefixo = str(HD).rstrip("\\/") + "\\"
    total: dict[str, int] = {}
    for caminho, raiz in con.execute("SELECT caminho, raiz FROM arquivos WHERE caminho LIKE ?", (prefixo + "%",)):
        if tipo_de(caminho, raiz):
            pasta = caminho[len(prefixo):].split("\\", 1)[0].upper()
            total[pasta] = total.get(pasta, 0) + 1
    lidos: dict[str, int] = {}
    for (caminho,) in con.execute("SELECT caminho FROM leituras WHERE status = 'lido' AND caminho LIKE ? "
                                  "AND caminho NOT LIKE '%::%'", (prefixo + "%",)):
        pasta = caminho[len(prefixo):].split("\\", 1)[0].upper()
        lidos[pasta] = lidos.get(pasta, 0) + 1
    con.close()
    return [(nome, lidos.get(pasta, 0), total.get(pasta, 0)) for pasta, nome in ORDEM]


def pendentes() -> int:
    """Arquivos de E:\\IDIOMAS que o leitor ainda não tentou ler (lidos e com erro já contam como resolvidos)."""
    from .leitor import TAMANHO_MINIMO_IMAGEM, _tabelas, tipo_de
    con = _tabelas()
    prefixo = str(HD).rstrip("\\/") + "\\"
    linhas = con.execute("SELECT a.caminho, a.raiz, a.tamanho FROM arquivos a LEFT JOIN leituras l "
                         "ON l.caminho = a.caminho WHERE l.caminho IS NULL AND a.caminho LIKE ?",
                         (prefixo + "%",)).fetchall()
    con.close()
    return sum(1 for c, r, t in linhas
               if (tp := tipo_de(c, r)) and not (tp == "imagem" and (t or 0) < TAMANHO_MINIMO_IMAGEM))


# ------------------------------------------------------------------ lembrete ao ligar o PC
LEMBRETE = (Path(os.getenv("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / "Startup" / "Central de IA - Lembrete Idiomas.bat")
RAIZ_PROJETO = Path(__file__).resolve().parent.parent


def instalar_lembrete() -> Path:
    """Ao ligar o Windows, abre o lembrete (NÃO liga nada sozinho: a retomada espera o seu OK)."""
    LEMBRETE.parent.mkdir(parents=True, exist_ok=True)
    LEMBRETE.write_text(f'@echo off\ncd /d "{RAIZ_PROJETO}"\n'
                        'start "Central de IA - Lembrete" /min python main.py idiomas lembrete\n', encoding="utf-8")
    return LEMBRETE


def _avisar_telegram(texto: str) -> None:
    from . import telegram
    for _ in range(12):                      # logo após ligar, a internet pode demorar a subir
        try:
            if telegram.configurado():
                telegram.enviar(texto)
            return
        except Exception:  # noqa: BLE001
            time.sleep(10)


def lembrete(perguntar=None, ligar=None, avisar=None) -> str:
    """Roda ao ligar o PC: avisa (Telegram + janela) e só retoma a leitura se você disser SIM.
    Quando não resta nada a ler em E:\\IDIOMAS, avisa que acabou e remove a si mesmo da inicialização."""
    avisar = avisar or _avisar_telegram
    restam = pendentes()
    if restam == 0:
        LEMBRETE.unlink(missing_ok=True)
        espelhar(log=lambda *_: None)
        avisar("✅ Idiomas: todo o material de E:\\IDIOMAS foi lido e está no Drive (Meu Drive\\Idiomas). "
               "O lembrete de inicialização foi removido.")
        return "concluido"
    linhas = "\n".join(f"• {n}: {l:,} de {t:,}" for n, l, t in progresso())
    texto = (f"📚 PC ligado — a leitura dos idiomas está PAUSADA.\nFaltam {restam:,} arquivos de E:\\IDIOMAS.\n"
             f"{linhas}\n\nRetomar agora? (responda na janela que abriu no PC)")
    avisar(texto)
    if (perguntar or _perguntar_na_tela)(texto):
        (ligar or _ligar_vigia)()
        avisar("▶ Leitura dos idiomas retomada (Vigia ligado).")
        return "retomado"
    avisar("⏸ Leitura dos idiomas continua pausada. Para retomar depois: dois cliques em VIGIA.bat.")
    return "pausado"


def _perguntar_na_tela(texto: str) -> bool:
    import ctypes
    MB_YESNO, MB_ICONQUESTION, MB_TOPMOST, IDYES = 0x4, 0x20, 0x40000, 6
    return ctypes.windll.user32.MessageBoxW(None, texto, "Central de IA — retomar leitura dos idiomas?",
                                            MB_YESNO | MB_ICONQUESTION | MB_TOPMOST) == IDYES


def _ligar_vigia() -> None:
    import subprocess
    subprocess.Popen(["cmd", "/c", "start", "Central de IA - Vigia", "/min", str(RAIZ_PROJETO / "VIGIA.bat")],
                     cwd=RAIZ_PROJETO)


def _gravar_progresso(log=print) -> None:
    linhas = ["# Idiomas — progresso da leitura do HD", "",
              f"Atualizado em {datetime.now():%d/%m/%Y %H:%M}. Cada arquivo lido (documento, áudio ou vídeo "
              "transcrito) vira um .md na pasta do idioma, com a mesma estrutura de pastas do HD. "
              "Pacotes .rar/.zip contam como um arquivo e geram um .md para cada arquivo de dentro.", "",
              "| Idioma | Lidos | No HD |", "|---|---|---|"]
    for nome, lidos, total in progresso():
        linhas.append(f"| {nome} | {lidos:,} | {total:,} |".replace(",", "."))
    try:
        (DRIVE / "_Progresso.md").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    except OSError as erro:
        log(f"   ⚠ progresso: {erro}")
