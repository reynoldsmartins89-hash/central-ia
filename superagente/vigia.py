"""Agente Vigia: o segurança da Central. Liga junto com o Windows e fica de olho em tudo.

  • Mantém o BOT DO TELEGRAM sempre ligado (se cair, religa na hora).
  • Mantém a LEITURA do acervo andando: HDs → Google Drive; quando termina, confere de novo a cada 6 h.
  • Antes da leitura, roda o AGENTE REPARADOR nos problemas novos (sempre em cópias).
  • Se algo quebrar, guarda o erro, RELIGA e avisa você no Telegram com o motivo.
  • De meia em meia hora confere a saúde: espaço em disco, Ollama (IA local) e internet.
Iniciar:  python main.py vigia   (o Windows já faz isso sozinho ao ligar o PC)
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path

PASTA = Path.home() / ".central-ia"
LOGS = PASTA / "logs"
ESTADO = PASTA / "vigia.json"
RAIZ_PROJETO = Path(__file__).resolve().parent.parent
INTERVALO_SAUDE = 30 * 60
ESPERA_NOVA_LEITURA = 6 * 3600


# ------------------------------------------------------------------ uma instância só
def processo_vivo(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    codigo = ctypes.c_ulong()
    k.GetExitCodeProcess(h, ctypes.byref(codigo))
    k.CloseHandle(h)
    return codigo.value == 259               # STILL_ACTIVE


def travar(nome: str) -> bool:
    """True se conseguiu a vez; False se já existe outro `nome` rodando."""
    PASTA.mkdir(parents=True, exist_ok=True)
    arquivo = PASTA / f"{nome}.pid"
    try:
        if processo_vivo(int(arquivo.read_text().strip())):
            return False
    except (OSError, ValueError):
        pass
    arquivo.write_text(str(os.getpid()))
    return True


# ------------------------------------------------------------------ tarefas vigiadas
class Tarefa:
    def __init__(self, nome: str, titulo: str, comandos: list[list[str]], repetir_a_cada: float | None):
        self.nome, self.titulo, self.comandos, self.repetir = nome, titulo, comandos, repetir_a_cada
        self.proc: subprocess.Popen | None = None
        self.etapa = 0
        self.proxima = 0.0
        self.falhas = 0
        self.log = LOGS / f"{nome}.log"

    def iniciar(self) -> None:
        LOGS.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-u", str(RAIZ_PROJETO / "main.py"), *self.comandos[self.etapa]]
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now():%d/%m %H:%M} ▶ {' '.join(cmd[2:])}\n")
        saida = open(self.log, "a", encoding="utf-8", errors="replace")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "CENTRAL_VIGIA": "1"}
        self.proc = subprocess.Popen(cmd, cwd=RAIZ_PROJETO, stdout=saida, stderr=subprocess.STDOUT, env=env,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def ultimas_linhas(self, n: int = 12) -> str:
        try:
            with open(self.log, encoding="utf-8", errors="replace") as f:
                return "".join(deque(f, n)).strip()
        except OSError:
            return ""

    def conferir(self, avisar) -> None:
        agora = time.time()
        if self.proc is None:
            if agora >= self.proxima:
                self.iniciar()
            return
        codigo = self.proc.poll()
        if codigo is None:
            return
        self.proc = None
        pedido = PASTA / f"reiniciar_{self.nome}"
        if pedido.exists():   # reinício PEDIDO (atualização): religa já, sem contar como erro nem avisar
            pedido.unlink(missing_ok=True)
            self.falhas, self.proxima = 0, agora
            return
        if codigo == 0:
            self.falhas = 0
            self.etapa += 1
            if self.etapa >= len(self.comandos):          # ciclo completo
                self.etapa = 0
                self.proxima = agora + (self.repetir or 5)
            return
        self.falhas += 1                                   # caiu: anota, avisa e religa
        espera = min(60 * 2 ** (self.falhas - 1), 3600)
        self.proxima = agora + espera
        erro = self.ultimas_linhas()
        ultima = next((l for l in reversed(erro.splitlines()) if l.strip()), "sem mensagem")
        avisar(f"🛡 Vigia: {self.titulo} parou com erro ({self.falhas}ª vez).\n"
               f"Motivo: {ultima[:300]}\nVou religar em {espera // 60} min. "
               f"{'Já investiguem: o erro se repete.' if self.falhas >= 3 else ''}".strip())

    def estado(self) -> dict:
        return {"titulo": self.titulo, "rodando": self.proc is not None and self.proc.poll() is None,
                "etapa": " ".join(self.comandos[self.etapa]), "falhas": self.falhas,
                "proxima": datetime.fromtimestamp(self.proxima).strftime("%d/%m %H:%M") if self.proxima else ""}


def _discos_catalogados() -> list[str]:
    try:
        from .curador import _conectar
        con = _conectar()
        raizes = [r for (r,) in con.execute("SELECT DISTINCT raiz FROM arquivos")]
        con.close()
        return raizes
    except Exception:  # noqa: BLE001
        return []


def tarefas_padrao() -> list[Tarefa]:
    leitura = [["reparar", "executar", d] for d in _discos_catalogados()]   # rápido: vem primeiro
    leitura.append(["ler"])
    from . import drive
    if drive.configurado():
        leitura.append(["drive", "ler"])
    if os.getenv("CAIXA_POSTAL", "").strip():   # leva ao servidor da nuvem o que foi lido de novo
        leitura.append(["caixa", "sincronizar"])
    tarefas = [Tarefa("bot", "o bot do Telegram", [["bot"]], repetir_a_cada=60),
               Tarefa("leitura", "a leitura do acervo (Agente Leitor/Reparador)", leitura,
                      repetir_a_cada=ESPERA_NOVA_LEITURA)]
    if (Path.home() / ".central-ia" / "openwebui" / ".venv" / "Scripts" / "open-webui.exe").exists():
        tarefas += [Tarefa("roteador", "a Central como modelo único", [["roteador"]], repetir_a_cada=30),
                    Tarefa("openwebui", "a tela de conversa (Open WebUI)", [["openwebui"]], repetir_a_cada=30)]
    return tarefas


# ------------------------------------------------------------------ saúde geral
def checar_saude() -> list[str]:
    alertas = []
    for disco in {Path.home().anchor, *[Path(d).anchor for d in _discos_catalogados()]}:
        try:
            livre = shutil.disk_usage(disco).free
            if livre < 15e9:
                alertas.append(f"💽 pouco espaço em {disco}: {livre / 1e9:.0f} GB livres")
        except OSError:
            alertas.append(f"💽 o disco {disco} não está conectado")
    try:
        urllib.request.urlopen(os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/api/tags", timeout=5)
    except Exception:  # noqa: BLE001
        alertas.append("🧠 a IA local (Ollama) não responde — leitura de imagens e PDFs escaneados fica parada")
    try:
        urllib.request.urlopen("https://api.telegram.org", timeout=10)
    except urllib.error.HTTPError:
        pass
    except Exception:  # noqa: BLE001
        alertas.append("🌐 sem internet")
    return alertas


def rodar() -> None:
    if not travar("vigia"):
        print("🛡 O Vigia já está rodando.")
        return
    from . import telegram
    from .agentes import carregar_env
    carregar_env()
    avisados: dict[str, float] = {}

    def avisar(texto: str) -> None:
        print(f"{datetime.now():%d/%m %H:%M} {texto}")
        try:
            if telegram.configurado():
                telegram.enviar(texto)
        except Exception:  # noqa: BLE001
            pass

    tarefas = tarefas_padrao()
    print("🛡 Vigia ligado: bot do Telegram + leitura do acervo + saúde do sistema. Pode minimizar.")
    proxima_saude = 0.0
    try:
        while True:
            for t in tarefas:
                t.conferir(avisar)
            if time.time() >= proxima_saude:
                proxima_saude = time.time() + INTERVALO_SAUDE
                for alerta in checar_saude():          # o mesmo alerta no máximo 1x por dia
                    if time.time() - avisados.get(alerta, 0) > 86400:
                        avisados[alerta] = time.time()
                        avisar("🛡 Vigia: " + alerta)
            ESTADO.write_text(json.dumps({"atualizado": f"{datetime.now():%d/%m %H:%M}",
                                          "tarefas": [t.estado() for t in tarefas]}, ensure_ascii=False),
                              encoding="utf-8")
            time.sleep(10)
    finally:
        for t in tarefas:
            if t.proc and t.proc.poll() is None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(t.proc.pid)], capture_output=True)


def resumo() -> str:
    try:
        dados = json.loads(ESTADO.read_text(encoding="utf-8"))
        if not processo_vivo(int((PASTA / "vigia.pid").read_text().strip())):
            raise OSError
    except (OSError, ValueError):
        return "🛡 O Vigia não está rodando. Ligue com VIGIA.bat (ou reinicie o PC)."
    linhas = [f"🛡 Vigia ativo (conferido {dados['atualizado']}):"]
    for t in dados["tarefas"]:
        situacao = f"▶ rodando: {t['etapa']}" if t["rodando"] else f"⏸ próxima rodada {t['proxima']}"
        linhas.append(f"• {t['titulo']}: {situacao}" + (f" · {t['falhas']} falha(s)" if t["falhas"] else ""))
    return "\n".join(linhas)


def reiniciar(nome: str) -> bool:
    """Pede ao Vigia para religar uma tarefa (ex.: o bot, após uma atualização) SEM contar como erro."""
    PASTA.mkdir(parents=True, exist_ok=True)
    (PASTA / f"reiniciar_{nome}").write_text(str(time.time()))
    alvo = {"bot": "main.py bot", "roteador": "main.py roteador", "openwebui": "main.py openwebui"}.get(nome, nome)
    encerrados = 0
    try:
        saida = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | Where-Object { "
                                f"$_.CommandLine -match '{alvo}' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId "
                                "-Force; 'ok' }"], capture_output=True, text=True, timeout=60).stdout
        encerrados = saida.count("ok")
    except Exception:  # noqa: BLE001
        pass
    return encerrados > 0
