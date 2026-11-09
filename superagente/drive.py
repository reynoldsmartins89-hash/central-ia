"""Agente Drive: lê o Google Drive — SEM baixar o Drive para o seu disco e SEM Google Cloud.

Usa o rclone (gratuito, de código aberto). Conexão única: você só AUTORIZA no navegador
(permissão SOMENTE LEITURA):   python main.py drive conectar

  • Catálogo completo (nome, pasta, tipo, tamanho, data) — só metadados.
  • Impressão digital MD5 que o PRÓPRIO GOOGLE calcula → duplicados sem baixar nada.
  • Leitura: cada arquivo vem para um TEMPORÁRIO, o Agente Leitor o lê/transcreve e o
    temporário é apagado na hora. Documentos Google (Docs/Planilhas/Apresentações) viram texto.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from .curador import _conectar

PASTA = Path.home() / ".central-ia"
# A configuração do rclone (conexões Drive e Fotos) fica num lugar FIXO da Central.
# Sem isso, programas abertos de dentro do app Claude gravam/leem numa pasta virtual dele,
# e o Vigia (que liga com o Windows) não enxerga as conexões.
os.environ.setdefault("RCLONE_CONFIG", str(PASTA / "rclone.conf"))
REMOTO = os.getenv("DRIVE_REMOTO", "gdrive")
EXPORTAVEIS = {"application/vnd.google-apps.document", "application/vnd.google-apps.presentation",
               "application/vnd.google-apps.spreadsheet"}


def _rclone() -> str:
    for c in (PASTA / "ferramentas" / "rclone.exe", shutil.which("rclone")):
        if c and Path(c).exists():
            return str(c)
    raise FileNotFoundError("rclone não encontrado (instale: winget install Rclone.Rclone)")


class ConfigIndisponivel(RuntimeError):
    """O rclone não achou a conexão no arquivo de configuração (não é erro do Google)."""


def _rodar(*args: str, timeout: float | None = None) -> str:
    # Quando o rclone renova a autorização, ele REGRAVA o rclone.conf; outro rclone que leia o
    # arquivo nesse instante não acha a conexão. Isso é passageiro: espera e tenta de novo.
    for tentativa in range(6):
        r = subprocess.run([_rclone(), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        if r.returncode == 0:
            return r.stdout
        saida = (r.stderr or r.stdout).strip()
        if "didn't find section in config file" not in saida:
            break
        time.sleep(2 + 3 * tentativa)
    else:
        raise ConfigIndisponivel(saida.splitlines()[-1])
    raise RuntimeError(saida.splitlines()[-1] if saida else "erro")


def configurado() -> bool:
    try:
        return f"{REMOTO}:" in _rodar("listremotes")
    except Exception:  # noqa: BLE001
        return False


def comando_conexao() -> list[str]:
    """Comando que abre o navegador para você autorizar (somente leitura)."""
    return [_rclone(), "config", "create", REMOTO, "drive", "scope=drive.readonly"]


def _tabela() -> sqlite3.Connection:
    con = _conectar()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS drive (
            id TEXT PRIMARY KEY, nome TEXT, mime TEXT, tamanho INTEGER, md5 TEXT,
            pais TEXT, modificado TEXT, caminho TEXT);
        CREATE INDEX IF NOT EXISTS idx_drive_md5 ON drive(md5);
    """)
    return con


def status() -> str:
    sobre = json.loads(_rodar("about", f"{REMOTO}:", "--json", timeout=120))
    return (f"✅ Drive conectado (somente leitura): {sobre.get('used', 0) / 1e12:.2f} TB em uso"
            if sobre else "Drive conectado")


def catalogar(log=print) -> int:
    """Lista TODOS os arquivos do Drive com MD5 do Google. Nada é baixado."""
    inicio = time.time()
    log("   pedindo ao Google a lista completa do Drive (pode levar alguns minutos)…")
    itens = json.loads(_rodar("lsjson", f"{REMOTO}:", "-R", "--files-only", "--hash", "--hash-type", "md5",
                              "--fast-list"))
    con = _tabela()
    con.execute("DELETE FROM drive")
    con.executemany("INSERT OR REPLACE INTO drive (id, nome, mime, tamanho, md5, modificado, caminho) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(i.get("ID") or i["Path"], i["Name"], i.get("MimeType", ""), max(int(i.get("Size", 0)), 0),
                      (i.get("Hashes") or {}).get("md5"), i.get("ModTime"), i["Path"]) for i in itens])
    con.commit()
    con.close()
    tamanho = sum(max(int(i.get("Size", 0)), 0) for i in itens)
    log(f"✅ Drive: {len(itens):,} arquivos ({tamanho / 1e12:.2f} TB) catalogados em "
        f"{time.time() - inicio:.0f}s — nada foi baixado")
    return len(itens)


def duplicados(limite: int = 15) -> str:
    """Duplicados pelo MD5 do Google — sem baixar nada."""
    con = _tabela()
    grupos = con.execute("""SELECT md5, max(tamanho), count(*), group_concat(caminho, char(10))
                            FROM drive WHERE md5 IS NOT NULL AND md5 != '' AND tamanho > 0
                            GROUP BY md5 HAVING count(*) > 1
                            ORDER BY max(tamanho) * (count(*) - 1) DESC""").fetchall()
    con.close()
    espaco = sum(t * (n - 1) for _, t, n, _ in grupos)
    linhas = [f"{len(grupos):,} conjuntos de duplicados no Drive · {espaco / 1e9:.1f} GB em cópias (nada baixado)"]
    for _, tamanho, n, caminhos in grupos[:limite]:
        linhas.append(f"\n{n} cópias de {tamanho / 1e6:.0f} MB:")
        linhas += [f"  • {c}" for c in caminhos.split("\n")]
    return "\n".join(linhas)


def baixar_temporario(caminho: str, mime: str) -> Path:
    """Traz UM arquivo para um temporário (apague depois de ler). Docs Google viram texto."""
    pasta = Path(tempfile.mkdtemp(prefix="central-ia-drive-"))
    extra = ["--drive-export-formats", "txt,csv,txt"] if mime in EXPORTAVEIS else []
    _rodar("copy", f"{REMOTO}:{caminho}", str(pasta), *extra, timeout=3600)
    arquivos = [p for p in pasta.rglob("*") if p.is_file()]
    if not arquivos:
        shutil.rmtree(pasta, ignore_errors=True)
        raise FileNotFoundError(caminho)
    return arquivos[0]


def ler_tudo(limite: int | None = None, minutos: float | None = None, log=print) -> dict:
    """Agente Leitor aplicado ao Drive. Retomável: o que já foi lido é pulado."""
    from . import leitor
    con = leitor._tabelas()
    fila = con.execute("""SELECT d.caminho, d.nome, d.mime FROM drive d LEFT JOIN leituras l
                          ON l.caminho = 'drive://' || d.caminho WHERE l.caminho IS NULL
                          ORDER BY d.tamanho""").fetchall()
    fila = [f for f in fila if f[2] in EXPORTAVEIS or leitor.tipo_de(f[1])][:limite]
    log(f"📚 Drive: {len(fila):,} arquivos na fila de leitura")
    fim = time.time() + minutos * 60 if minutos else None
    lidos = erros = 0
    for caminho, nome, mime in fila:
        if fim and time.time() > fim:
            log("⏸ tempo da rodada esgotado; rode de novo para continuar")
            break
        temp = None
        try:
            temp = baixar_temporario(caminho, mime)
            origem = f"drive://{caminho}"
            if leitor.tipo_de(str(temp)) == "compactado":
                lidos += leitor._ler_compactado(con, str(temp), log)
            else:
                lidos += leitor.ler_e_registrar(con, origem, str(temp))
            con.commit()
        except Exception as erro:  # noqa: BLE001
            erros += 1
            log(f"   ⚠ {nome}: {str(erro)[:100]}")
        finally:
            if temp:
                shutil.rmtree(temp.parent, ignore_errors=True)   # nada fica ocupando espaço
    con.close()
    log(f"✅ Drive: {lidos:,} fichas, {erros:,} com erro")
    return {"lidos": lidos, "erros": erros}


MONTAGEM = os.getenv("DRIVE_PASTA_LOCAL", r"G:\Meu Drive")   # o Drive para computador
PASTA_REPETIDOS = "DUPLICADOS PARA REVISAR"


def caminho_local(caminho: str) -> Path:
    """Caminho do Drive → caminho no G:. O Drive para computador troca : * ? " < > | por espaço."""
    for proibido in ':*?"<>|':
        caminho = caminho.replace(proibido, " ")
    return Path(MONTAGEM) / Path(caminho)


def _quem_fica(caminhos: list[str]) -> str:
    """Mantém a cópia mais 'organizada': dentro das categorias numeradas, sem '(1)', caminho mais curto."""
    import re
    return min(caminhos, key=lambda c: (not re.search(r"/\d{2}\. ", "/" + c), bool(re.search(r"\(\d+\)\.\w+$", c)),
                                        len(c)))


def separar_duplicados(prefixo: str = "", log=print) -> dict:
    """Move as CÓPIAS repetidas (mesmo MD5) para 'DUPLICADOS PARA REVISAR' no próprio Drive, pelo G:.
    É um movimento dentro do Drive (nada é baixado nem apagado) e pode ser desfeito: curador desfazer."""
    from .curador import REGISTRO_MOVIMENTOS, _livre
    raiz = Path(MONTAGEM)
    if not raiz.exists():
        raise FileNotFoundError(f"{raiz} não encontrado: o Google Drive para computador está aberto?")
    con = _tabela()
    grupos = con.execute("""SELECT group_concat(caminho, char(10)) FROM drive WHERE md5 <> '' AND tamanho > 0
                            AND caminho LIKE ? AND caminho NOT LIKE ? GROUP BY md5 HAVING count(*) > 1""",
                         (prefixo + "%", PASTA_REPETIDOS + "/%")).fetchall()
    lote = "drive-dup-" + time.strftime("%Y%m%d-%H%M%S")
    movidos = nao_achados = espaco = 0
    for (lista,) in grupos:
        caminhos = lista.split("\n")
        fica = _quem_fica(caminhos)
        for c in caminhos:
            if c == fica:
                continue
            origem = caminho_local(c)
            if not origem.exists():
                nao_achados += 1
                continue
            destino = _livre(caminho_local(f"{PASTA_REPETIDOS}/{c}"))
            destino.parent.mkdir(parents=True, exist_ok=True)
            tamanho = origem.stat().st_size
            shutil.move(str(origem), str(destino))
            with open(REGISTRO_MOVIMENTOS, "a", encoding="utf-8") as f:
                f.write(json.dumps({"lote": lote, "de": str(origem), "para": str(destino), "fica": fica},
                                   ensure_ascii=False) + "\n")
            con.execute("UPDATE drive SET caminho = ? WHERE caminho = ?",
                        (f"{PASTA_REPETIDOS}/{c}", c))
            con.commit()
            movidos += 1
            espaco += tamanho
            if movidos % 100 == 0:
                log(f"   {movidos:,} cópias separadas…")
    con.close()
    log(f"✅ {movidos:,} cópias ({espaco / 1e9:.1f} GB) separadas em {raiz / PASTA_REPETIDOS} "
        f"(lote {lote}); {nao_achados} não estavam mais no lugar. Para desfazer: curador desfazer")
    return {"movidos": movidos, "gb": espaco / 1e9, "nao_achados": nao_achados, "lote": lote}
