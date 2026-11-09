"""Agente Drive: lê o Google Drive pela API oficial — SEM baixar o Drive para o seu disco.

  • Catálogo completo (nome, pasta, tipo, tamanho, datas) — só metadados.
  • Impressão digital MD5 que o PRÓPRIO GOOGLE calcula → duplicados sem baixar nada.
  • Leitura de conteúdo: o arquivo vem direto para a MEMÓRIA (ou um temporário que é
    apagado logo depois) só enquanto o Agente Leitor o processa. Nada fica ocupando espaço.
  • Documentos Google (Docs, Planilhas, Apresentações) são exportados como texto.

Configuração única (5 minutos, feita por você no Google Cloud — veja COMANDOS.md):
  credenciais em ~/.central-ia/google_credenciais.json → `python main.py drive conectar`
Permissão pedida: SOMENTE LEITURA do Drive (drive.readonly). Mover/organizar é outra etapa,
que exigirá nova permissão e sempre a sua aprovação.
"""
from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from .curador import _conectar

PASTA = Path.home() / ".central-ia"
CREDENCIAIS = PASTA / "google_credenciais.json"
TOKEN = PASTA / "google_token.json"
ESCOPOS = ["https://www.googleapis.com/auth/drive.readonly"]
EXPORTAR = {  # documentos nativos do Google → texto
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
}


def _servico():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    cred = Credentials.from_authorized_user_file(str(TOKEN), ESCOPOS) if TOKEN.exists() else None
    if cred and cred.expired and cred.refresh_token:
        cred.refresh(Request())
    if not cred or not cred.valid:
        if not CREDENCIAIS.exists():
            raise FileNotFoundError(f"faltam as credenciais do Google em {CREDENCIAIS} (veja COMANDOS.md)")
        cred = InstalledAppFlow.from_client_secrets_file(str(CREDENCIAIS), ESCOPOS).run_local_server(port=0)
    TOKEN.write_text(cred.to_json(), encoding="utf-8")
    return build("drive", "v3", credentials=cred, cache_discovery=False)


def _tabela() -> sqlite3.Connection:
    con = _conectar()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS drive (
            id TEXT PRIMARY KEY, nome TEXT, mime TEXT, tamanho INTEGER, md5 TEXT,
            pais TEXT, modificado TEXT, caminho TEXT);
        CREATE INDEX IF NOT EXISTS idx_drive_md5 ON drive(md5);
    """)
    return con


def conectar() -> str:
    sobre = _servico().about().get(fields="user(emailAddress),storageQuota").execute()
    uso = int(sobre["storageQuota"].get("usage", 0)) / 1e12
    return f"✅ Conectado ao Drive de {sobre['user']['emailAddress']} ({uso:.2f} TB em uso) — somente leitura"


def catalogar(log=print) -> int:
    """Lista TODOS os arquivos do Drive (só metadados + MD5). Nada é baixado."""
    servico, con = _servico(), _tabela()
    campos = "nextPageToken, files(id, name, mimeType, size, md5Checksum, parents, modifiedTime)"
    token, total, inicio = None, 0, time.time()
    while True:
        r = servico.files().list(q="trashed = false", pageSize=1000, fields=campos, pageToken=token,
                                 includeItemsFromAllDrives=True, supportsAllDrives=True).execute()
        con.executemany("INSERT OR REPLACE INTO drive (id, nome, mime, tamanho, md5, pais, modificado) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(f["id"], f["name"], f["mimeType"], int(f.get("size", 0) or 0), f.get("md5Checksum"),
                          ",".join(f.get("parents", [])), f.get("modifiedTime")) for f in r.get("files", [])])
        total += len(r.get("files", []))
        con.commit()
        if total % 20000 < 1000:
            log(f"   {total:,} itens do Drive catalogados ({time.time() - inicio:.0f}s)")
        token = r.get("nextPageToken")
        if not token:
            break
    _montar_caminhos(con)
    con.close()
    log(f"✅ Drive: {total:,} itens catalogados (nenhum arquivo baixado)")
    return total


def _montar_caminhos(con: sqlite3.Connection) -> None:
    """Reconstrói o caminho 'Pasta/Subpasta/arquivo' de cada item a partir das pastas-pai."""
    pastas = {i: (n, p.split(",")[0] if p else "") for i, n, p in con.execute(
        "SELECT id, nome, pais FROM drive WHERE mime = 'application/vnd.google-apps.folder'")}
    memo: dict[str, str] = {}

    def caminho_da_pasta(pid: str, profundidade: int = 0) -> str:
        if not pid or pid not in pastas or profundidade > 50:
            return ""
        if pid not in memo:
            nome, pai = pastas[pid]
            acima = caminho_da_pasta(pai, profundidade + 1)
            memo[pid] = f"{acima}/{nome}" if acima else nome
        return memo[pid]

    con.executemany("UPDATE drive SET caminho = ? WHERE id = ?",
                    [(f"{caminho_da_pasta(p.split(',')[0] if p else '')}/{n}".lstrip("/"), i)
                     for i, n, p in con.execute("SELECT id, nome, pais FROM drive").fetchall()])
    con.commit()


def duplicados(limite: int = 15) -> str:
    """Duplicados pelo MD5 do Google — sem baixar nada."""
    con = _tabela()
    grupos = con.execute("""SELECT md5, max(tamanho), count(*), group_concat(caminho, char(10))
                            FROM drive WHERE md5 IS NOT NULL AND tamanho > 0
                            GROUP BY md5 HAVING count(*) > 1
                            ORDER BY max(tamanho) * (count(*) - 1) DESC""").fetchall()
    con.close()
    espaco = sum(t * (n - 1) for _, t, n, _ in grupos)
    linhas = [f"{len(grupos):,} conjuntos de duplicados no Drive · {espaco / 1e9:.1f} GB em cópias "
              f"(nada foi baixado)"]
    for _, tamanho, n, caminhos in grupos[:limite]:
        linhas.append(f"\n{n} cópias de {tamanho / 1e6:.0f} MB:")
        linhas += [f"  • {c}" for c in caminhos.split("\n")]
    return "\n".join(linhas)


def baixar_temporario(id_arquivo: str, mime: str, nome: str) -> Path:
    """Traz o conteúdo para um arquivo TEMPORÁRIO (apagar depois de ler). Docs Google viram texto."""
    from googleapiclient.http import MediaIoBaseDownload
    servico = _servico()
    if mime in EXPORTAR:
        pedido = servico.files().export_media(fileId=id_arquivo, mimeType=EXPORTAR[mime])
        sufixo = ".csv" if EXPORTAR[mime] == "text/csv" else ".txt"
    else:
        pedido = servico.files().get_media(fileId=id_arquivo)
        sufixo = Path(nome).suffix
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=sufixo, prefix="central-ia-")
    with temp:
        baixador = MediaIoBaseDownload(temp, pedido, chunksize=16 * 1024 * 1024)
        concluido = False
        while not concluido:
            _, concluido = baixador.next_chunk()
    return Path(temp.name)


def ler_tudo(limite: int | None = None, minutos: float | None = None, log=print) -> dict:
    """Agente Leitor aplicado ao Drive: cada arquivo vem ao temporário, é lido/transcrito e o
    temporário é APAGADO na hora. Retomável: o que já foi lido é pulado."""
    from . import leitor
    con = _tabela()
    leitor._tabelas().close()
    fila = con.execute("""SELECT d.id, d.nome, d.mime, d.caminho FROM drive d
                          LEFT JOIN leituras l ON l.caminho = 'drive://' || d.caminho
                          WHERE l.caminho IS NULL AND d.mime != 'application/vnd.google-apps.folder'
                          ORDER BY d.tamanho""").fetchall()
    fila = [f for f in fila if f[2] in EXPORTAR or leitor.tipo_de(f[1])][:limite]
    con.close()
    log(f"📚 Drive: {len(fila):,} arquivos na fila de leitura")
    fim = time.time() + minutos * 60 if minutos else None
    lidos = erros = 0
    for id_, nome, mime, caminho in fila:
        if fim and time.time() > fim:
            log("⏸ tempo da rodada esgotado; rode de novo para continuar")
            break
        temp = None
        try:
            temp = baixar_temporario(id_, mime, nome)
            tipo, metodo, texto = leitor.ler(str(temp))
            if tipo == "documento":
                texto = leitor._extrair_tudo(str(temp)) or texto
            origem = f"drive://{caminho}"
            ficha = leitor._salvar_ficha(origem, tipo, metodo, texto)
            con = leitor._tabelas()
            leitor._indexar(con, origem, tipo, texto)
            con.execute("INSERT OR REPLACE INTO leituras VALUES (?, ?, ?, 'lido', ?, ?, 0, datetime('now'), NULL)",
                        (origem, tipo, metodo, str(ficha), len(texto)))
            con.commit()
            con.close()
            lidos += 1
        except Exception as erro:  # noqa: BLE001
            erros += 1
            log(f"   ⚠ {nome}: {str(erro)[:100]}")
        finally:
            if temp and temp.exists():
                os.remove(temp)          # nada fica ocupando espaço
    log(f"✅ Drive: {lidos:,} lidos, {erros:,} com erro")
    return {"lidos": lidos, "erros": erros}
