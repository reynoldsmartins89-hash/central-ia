"""Resgate de HD — diagnóstico e cópia setor a setor de HDs com defeito.

REGRA DE OURO: esta ferramenta NUNCA escreve no HD com defeito. Ela só lê.
Os dados são copiados para um arquivo-imagem (.img) em outro disco saudável;
a recuperação de arquivos é feita depois, em cima da imagem (PhotoRec, DMDE,
TestDisk), sem forçar o HD doente de novo.

Uso (no PowerShell/Prompt **como Administrador**):
    python ferramentas/resgate_hd.py listar
    python ferramentas/resgate_hd.py diagnosticar 2
    python ferramentas/resgate_hd.py clonar 2 E:\\resgate\\hd2.img

A cópia pode ser interrompida (Ctrl+C) e retomada: o progresso fica no
arquivo .mapa.json ao lado da imagem.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

MiB = 1024 * 1024
SETOR = 512
BLOCO = 1 * MiB

# Atributos SMART que importam para decidir o destino do HD
ATRIBUTOS_CRITICOS = {
    5: "Setores realocados",
    187: "Erros incorrigíveis reportados",
    188: "Timeouts de comando",
    196: "Eventos de realocação",
    197: "Setores pendentes (instáveis)",
    198: "Setores incorrigíveis offline",
    199: "Erros de CRC (cabo/adaptador USB)",
}


# ---------------------------------------------------------------- utilidades
def e_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return os.name != "nt" or False


def powershell_json(comando: str) -> list[dict]:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"{comando} | ConvertTo-Json -Depth 3"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    saida = r.stdout.strip()
    if not saida:
        return []
    dados = json.loads(saida)
    return dados if isinstance(dados, list) else [dados]


def tamanho_legivel(n: float) -> str:
    for unidade in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000:
            return f"{n:.1f} {unidade}"
        n /= 1000
    return f"{n:.1f} PB"


def caminho_disco(numero: int) -> str:
    return rf"\\.\PhysicalDrive{numero}"


def discos() -> list[dict]:
    return powershell_json(
        "Get-Disk | Select-Object Number,FriendlyName,SerialNumber,Size,BusType,"
        "HealthStatus,OperationalStatus,PartitionStyle,IsSystem,IsBoot"
    )


def contadores_confiabilidade(numero: int) -> dict:
    try:
        dados = powershell_json(
            f"Get-PhysicalDisk | Where-Object DeviceId -eq '{numero}' | "
            "Get-StorageReliabilityCounter | Select-Object ReadErrorsTotal,"
            "ReadErrorsUncorrected,Temperature,PowerOnHours,StartStopCycleCount"
        )
        return dados[0] if dados else {}
    except Exception:
        return {}


def smart(numero: int) -> dict | None:
    """Lê o SMART pelo smartctl (smartmontools), se estiver instalado."""
    exe = shutil.which("smartctl") or r"C:\Program Files\smartmontools\bin\smartctl.exe"
    if not Path(exe).exists():
        return None
    # Adaptadores USB costumam exigir "-d sat"; tenta os dois jeitos
    for extra in ([], ["-d", "sat"]):
        r = subprocess.run([exe, "-j", "-a", *extra, f"/dev/pd{numero}"],
                           capture_output=True, text=True, errors="replace")
        try:
            dados = json.loads(r.stdout)
        except json.JSONDecodeError:
            continue
        if dados.get("ata_smart_attributes") or dados.get("smart_status"):
            return dados
    return {}


def particoes(numero: int) -> list[dict]:
    try:
        return powershell_json(
            f"Get-Partition -DiskNumber {numero} | Select-Object PartitionNumber,"
            "DriveLetter,Size,Type"
        )
    except Exception:
        return []


def volume_tipo(letra: str) -> str:
    try:
        dados = powershell_json(f"Get-Volume -DriveLetter {letra} | Select-Object FileSystem")
        return (dados[0].get("FileSystem") or "RAW") if dados else "?"
    except Exception:
        return "?"


# ------------------------------------------------------------- teste leitura
def teste_leitura(numero: int, tamanho: int, pontos: int = 9) -> list[dict]:
    """Lê 1 MiB em pontos espalhados pelo disco e mede o tempo de cada leitura."""
    resultados = []
    with open(caminho_disco(numero), "rb", buffering=0) as f:
        for i in range(pontos):
            pos = (tamanho - BLOCO) * i // (pontos - 1)
            pos -= pos % SETOR
            inicio = time.monotonic()
            try:
                f.seek(pos)
                f.read(BLOCO)
                resultados.append({"posicao": pos, "ok": True,
                                   "segundos": round(time.monotonic() - inicio, 2)})
            except OSError as e:
                resultados.append({"posicao": pos, "ok": False, "erro": str(e),
                                   "segundos": round(time.monotonic() - inicio, 2)})
    return resultados


# ------------------------------------------------------------------- clonagem
SALTO_MIN = 64 * MiB        # ao achar área ruim ou lenta, pula isso para frente…
SALTO_MAX = 4 * 1024 * MiB  # …dobrando a cada problema seguido, até este limite
LENTO_S = 2.0               # 1 MiB levando mais que isso = disco sofrendo: pula (1ª passada)


class Mapa:
    """Progresso da cópia, salvo em disco para poder pausar e retomar."""

    CAMPOS = {"posicao": 0, "blocos_ruins": [], "pulados": [],
              "setores_ruins": [], "passada3_feita": []}

    def __init__(self, caminho: Path, tamanho: int):
        self.caminho = caminho
        self.tamanho = tamanho
        d = json.loads(caminho.read_text(encoding="utf-8")) if caminho.exists() else {}
        if d and d["tamanho"] != tamanho:
            raise SystemExit("O mapa existente é de um disco com outro tamanho. Use outra imagem.")
        # posicao: até onde a 1ª passada foi | blocos_ruins: blocos de 1 MiB que falharam
        # pulados: fila de faixas puladas, lidas na 2ª passada (que pode pular de novo
        #          e devolver o resto ao fim da fila — nada é abandonado)
        # setores_ruins: setores ilegíveis na 3ª passada (setor a setor)
        for campo, padrao in self.CAMPOS.items():
            setattr(self, campo, d.get(campo, type(padrao)(padrao)))

    def salvar(self) -> None:
        tmp = self.caminho.with_suffix(".tmp")
        dados = {"tamanho": self.tamanho, **{c: getattr(self, c) for c in self.CAMPOS}}
        tmp.write_text(json.dumps(dados), encoding="utf-8")
        os.replace(tmp, self.caminho)

    def bytes_perdidos(self, setor: int = SETOR) -> int:
        return len(self.setores_ruins) * setor


def clonar(ler: Callable[[int, int], bytes], tamanho: int, destino: Path,
           bloco: int = BLOCO, setor: int = SETOR, salvar_a_cada: int = 64,
           log: Callable[[str], None] = print, salto_min: int = SALTO_MIN,
           salto_max: int = SALTO_MAX, desistir_setores: int = 8,
           areas: list[tuple[int, int]] | None = None, lento_s: float | None = LENTO_S) -> Mapa:
    """Copia `tamanho` bytes usando `ler(posicao, quantidade)` para `destino`.

    Estratégia (a mesma dos programas profissionais): salvar primeiro o que é
    fácil, deixando as áreas ruins para o fim — insistir nelas é o que mata HD fraco.
    1ª passada: blocos de 1 MiB; ao falhar ou ler devagar demais, pula para frente
    (64 MiB, 128, 256… até 4 GiB). Região fraca de disco = leituras lentas antes dos erros.
    2ª passada: lê as faixas puladas; se achar área ruim, pula de novo e põe o
    resto no fim da fila (cada volta consome ao menos 1 bloco, então termina).
    3ª passada: blocos que falharam são relidos setor a setor.
    O que continuar ilegível fica zerado na imagem e anotado no mapa.
    `areas`: se dado, copia só essas faixas (ex.: só o espaço ocupado por arquivos).
    """
    mapa = Mapa(destino.with_name(destino.name + ".mapa.json"), tamanho)
    modo = "r+b" if destino.exists() else "w+b"
    # Sem pré-alocar o arquivo: no exFAT o Windows preencheria 1 TB de zeros antes de começar.
    with open(destino, modo) as saida:
        inicio = time.monotonic()

        def tentar(pos: int, qtd: int) -> bool:
            try:
                dados = ler(pos, qtd)
                if len(dados) != qtd:
                    raise OSError("leitura incompleta")
            except OSError:
                return False
            saida.seek(pos)
            saida.write(dados)
            return True

        def varrer(ini: int, fim: int, avancar: Callable[[int], None],
                   pular: Callable[[list[int]], None], rotulo: str,
                   lento: float | None = None) -> None:
            pos, salto, n, boas = ini, salto_min, 0, 0
            while pos < fim:
                qtd = min(bloco, fim - pos)
                t0 = time.monotonic()
                if tentar(pos, qtd):
                    pos += qtd
                    if lento is not None and time.monotonic() - t0 > lento and pos < fim:
                        # leu, mas sofrendo: região fraca — deixa o resto dela para depois
                        prox = min(pos + salto, fim)
                        pular([pos, prox])
                        log(f"  ~ leitura lenta em {tamanho_legivel(pos)} — pulando "
                            f"{tamanho_legivel(prox - pos)}")
                        pos, salto, boas = prox, min(salto * 2, salto_max), 0
                        avancar(pos)
                        mapa.salvar()
                        continue
                    boas += 1
                    if boas * bloco >= salto_min * 4:  # saiu mesmo da faixa ruim
                        salto = salto_min
                else:
                    boas = 0
                    mapa.blocos_ruins.append(pos)
                    prox = min(pos + qtd + salto, fim)
                    if prox > pos + qtd:
                        pular([pos + qtd, prox])
                    log(f"  ! área ruim em {tamanho_legivel(pos)} — pulando "
                        f"{tamanho_legivel(prox - pos - qtd)}")
                    pos, salto = prox, min(salto * 2, salto_max)
                    avancar(pos)
                    mapa.salvar()
                    continue
                avancar(pos)
                n += 1
                if n % salvar_a_cada == 0:
                    mapa.salvar()
                    if rotulo == "1ª passada":
                        dec = time.monotonic() - inicio
                        log(f"  1ª passada: {pos * 100 / tamanho:5.1f}%  "
                            f"({tamanho_legivel(pos / dec if dec else 0)}/s no total, "
                            f"{len(mapa.blocos_ruins)} blocos ruins)")

        # ---- 1ª passada
        for ini, fim in (areas or [(0, tamanho)]):
            if fim <= mapa.posicao:
                continue
            varrer(max(ini, mapa.posicao), fim, lambda p: setattr(mapa, "posicao", p),
                   mapa.pulados.append, "1ª passada", lento_s)
        mapa.posicao = tamanho
        mapa.salvar()
        # ---- 2ª passada: faixas puladas
        if mapa.pulados:
            total = sum(f - i for i, f in mapa.pulados)
            log(f"2ª passada: lendo {tamanho_legivel(total)} que foram pulados…")
        while mapa.pulados:
            faixa = mapa.pulados[0]
            varrer(faixa[0], faixa[1], lambda p: faixa.__setitem__(0, p),
                   mapa.pulados.append, "2ª passada")
            mapa.pulados.pop(0)
            mapa.salvar()
        # ---- 3ª passada: setor a setor nos blocos que falharam
        pendentes = [b for b in mapa.blocos_ruins if b not in mapa.passada3_feita]
        if pendentes:
            log(f"3ª passada: refazendo {len(pendentes)} blocos ruins setor a setor…")
        for b in pendentes:
            fim = min(b + bloco, tamanho)
            erros = 0
            for pos in range(b, fim, setor):
                if erros >= desistir_setores or not tentar(pos, min(setor, fim - pos)):
                    mapa.setores_ruins.append(pos)
                    erros += 1
                else:
                    erros = 0
            mapa.passada3_feita.append(b)
            mapa.salvar()
        if saida.seek(0, os.SEEK_END) < tamanho:  # fim ilegível: completa com zeros
            saida.truncate(tamanho)
    return mapa


def leitor_disco(numero: int) -> tuple[Callable[[int, int], bytes], Callable[[], None]]:
    f = open(caminho_disco(numero), "rb", buffering=0)  # SÓ LEITURA

    def ler(pos: int, qtd: int) -> bytes:
        # Windows exige leituras alinhadas ao setor
        alinhado = -(-qtd // SETOR) * SETOR
        f.seek(pos)
        return f.read(alinhado)[:qtd]

    return ler, f.close


# ------------------------------------------------------------------- comandos
def cmd_listar(_args) -> None:
    for d in discos():
        marca = "  (DISCO DO SISTEMA — não mexer)" if d.get("IsSystem") or d.get("IsBoot") else ""
        print(f"[{d['Number']}] {d.get('FriendlyName')}  |  {tamanho_legivel(d.get('Size') or 0)}"
              f"  |  {d.get('BusType')}  |  saúde: {d.get('HealthStatus')}"
              f"  |  status: {d.get('OperationalStatus')}{marca}")
    print("\nSe o HD com defeito NÃO aparece aqui, veja a seção 'HD não aparece' no LEIA-ME.")


def veredito(disco: dict, smart_dados: dict | None, leituras: list[dict],
             parts: list[dict]) -> list[str]:
    conclusoes: list[str] = []
    tamanho = disco.get("Size") or 0
    if tamanho == 0:
        return ["HD aparece mas com tamanho 0: provável falha de firmware/cabeças ou do "
                "adaptador USB. NÃO insista. Teste tirar da caixa e ligar direto no SATA; "
                "se continuar, é caso de laboratório."]
    falhas = [r for r in leituras if not r["ok"]]
    lentas = [r for r in leituras if r["ok"] and r["segundos"] > 2]
    criticos = {}
    if smart_dados:
        for a in smart_dados.get("ata_smart_attributes", {}).get("table", []):
            if a["id"] in ATRIBUTOS_CRITICOS and a["raw"]["value"]:
                criticos[a["id"]] = a["raw"]["value"]
    if len(falhas) == len(leituras):
        conclusoes.append("Nenhum ponto do disco pôde ser lido: falha física séria (cabeças/"
                          "motor/placa). Pare de ligar este HD — cada tentativa pode piorar. "
                          "Laboratório de recuperação.")
        return conclusoes
    if criticos.get(199) and len(criticos) == 1:
        conclusoes.append("Erros de CRC: o problema provavelmente é cabo, porta USB ou o "
                          "adaptador da caixa — não o disco. Troque cabo/porta antes de tudo.")
    if falhas or lentas or any(k in criticos for k in (5, 187, 196, 197, 198)):
        conclusoes.append("HD com superfície degradando (setores ruins/leituras lentas). Os dados "
                          "provavelmente são recuperáveis, mas o tempo conta: CLONE AGORA com "
                          "'clonar', sem rodar chkdsk e sem abrir as pastas antes.")
    if parts and all(p.get("FileSystem") in ("RAW", "?", None) for p in parts):
        conclusoes.append("Partição aparece como RAW (sistema de arquivos danificado). É problema "
                          "lógico — boa chance de recuperação. Clone e use DMDE/PhotoRec na imagem. "
                          "NUNCA aceite o 'Deseja formatar?' do Windows.")
    elif not parts:
        conclusoes.append("Nenhuma partição encontrada: tabela de partições perdida. Clone e rode "
                          "TestDisk na imagem para reencontrá-las.")
    if not conclusoes:
        conclusoes.append("Leitura física normal. Se os arquivos não aparecem, o problema é lógico "
                          "(exclusão, formatação, corrupção): clone e recupere na imagem.")
    if criticos:
        conclusoes.append("SMART: " + ", ".join(f"{ATRIBUTOS_CRITICOS[k]} = {v}"
                                                for k, v in criticos.items()))
    return conclusoes


def cmd_diagnosticar(args) -> None:
    exigir_admin()
    disco = next((d for d in discos() if d["Number"] == args.disco), None)
    if not disco:
        raise SystemExit(f"Disco {args.disco} não encontrado. Rode 'listar'.")
    print(f"== Disco {args.disco}: {disco.get('FriendlyName')} ({tamanho_legivel(disco.get('Size') or 0)})")
    print(f"Windows diz: saúde {disco.get('HealthStatus')}, status {disco.get('OperationalStatus')}, "
          f"partições {disco.get('PartitionStyle')}")
    cont = contadores_confiabilidade(args.disco)
    if cont:
        print("Contadores:", ", ".join(f"{k}={v}" for k, v in cont.items() if v is not None))
    sm = smart(args.disco)
    if sm is None:
        print("SMART: smartctl não instalado (winget install smartmontools.smartmontools) — "
              "diagnóstico fica menos preciso.")
    elif not sm:
        print("SMART: o adaptador USB não deixou ler o SMART.")
    else:
        ok = sm.get("smart_status", {}).get("passed")
        print(f"SMART geral: {'APROVADO' if ok else 'REPROVADO' if ok is False else '?'}"
              f"  |  horas ligado: {sm.get('power_on_time', {}).get('hours', '?')}"
              f"  |  temperatura: {sm.get('temperature', {}).get('current', '?')} °C")
    parts = particoes(args.disco)
    for p in parts:
        p["FileSystem"] = volume_tipo(p["DriveLetter"]) if p.get("DriveLetter") else "?"
        print(f"Partição {p['PartitionNumber']}: {tamanho_legivel(p.get('Size') or 0)} "
              f"letra={p.get('DriveLetter') or '-'} sistema={p['FileSystem']}")
    leituras: list[dict] = []
    if disco.get("Size"):
        print("Teste de leitura em 9 pontos do disco (somente leitura)…")
        leituras = teste_leitura(args.disco, disco["Size"])
        for r in leituras:
            estado = f"ok em {r['segundos']}s" if r["ok"] else f"FALHOU ({r['erro']})"
            print(f"  {tamanho_legivel(r['posicao']):>10}: {estado}")
    print("\n== VEREDITO")
    for c in veredito(disco, sm, leituras, parts):
        print(" •", c)


def cmd_clonar(args) -> None:
    exigir_admin()
    disco = next((d for d in discos() if d["Number"] == args.disco), None)
    if not disco or not disco.get("Size"):
        raise SystemExit("Disco não encontrado ou sem tamanho — não dá para clonar.")
    if disco.get("IsSystem") or disco.get("IsBoot"):
        raise SystemExit("Esse é o disco do sistema. Confira o número.")
    destino = Path(args.destino).resolve()
    letras_origem = {(p.get("DriveLetter") or "").upper() for p in particoes(args.disco)}
    if destino.drive.rstrip(":").upper() in letras_origem - {""}:
        raise SystemExit("O destino está no próprio HD com defeito! Escolha outro disco.")
    destino.parent.mkdir(parents=True, exist_ok=True)
    livre = shutil.disk_usage(destino.parent).free
    ja_tem = destino.stat().st_size if destino.exists() else 0
    if livre + ja_tem < disco["Size"]:
        raise SystemExit(f"Espaço insuficiente: precisa de {tamanho_legivel(disco['Size'])}, "
                         f"livre {tamanho_legivel(livre)}.")
    print(f"Clonando disco {args.disco} ({tamanho_legivel(disco['Size'])}) → {destino}")
    print("Somente leitura no HD de origem. Ctrl+C pausa; rode o mesmo comando para retomar.\n")
    ler, fechar = leitor_disco(args.disco)
    try:
        areas = None
        if args.areas:
            areas = [tuple(a) for a in json.loads(Path(args.areas).read_text(encoding="utf-8"))]
            print(f"Copiando só {tamanho_legivel(sum(f - i for i, f in areas))} em "
                  f"{len(areas)} faixas (o resto do disco está vazio).")
        mapa = clonar(ler, disco["Size"], destino, areas=areas)
    except KeyboardInterrupt:
        print("\nPausado. Rode o mesmo comando para continuar de onde parou.")
        return
    finally:
        fechar()
    perdidos = mapa.bytes_perdidos()
    print(f"\nPronto. Ilegível: {tamanho_legivel(perdidos)} "
          f"({perdidos * 100 / disco['Size']:.4f}% do disco).")
    print("Agora DESLIGUE o HD com defeito e recupere os arquivos a partir da imagem "
          "(veja 'Depois da cópia' no LEIA-ME).")


def exigir_admin() -> None:
    if not e_admin():
        raise SystemExit("Precisa rodar como Administrador (botão direito no PowerShell → "
                         "Executar como administrador).")


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Diagnóstico e resgate de HDs (somente leitura).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar", help="lista os discos conectados").set_defaults(f=cmd_listar)
    d = sub.add_parser("diagnosticar", help="SMART + teste de leitura + veredito")
    d.add_argument("disco", type=int)
    d.set_defaults(f=cmd_diagnosticar)
    c = sub.add_parser("clonar", help="cópia setor a setor para um arquivo .img")
    c.add_argument("disco", type=int)
    c.add_argument("destino")
    c.add_argument("--areas", help="JSON com [[inicio, fim], ...] em bytes: copia só essas faixas")
    c.set_defaults(f=cmd_clonar)
    args = p.parse_args(argv)
    args.f(args)


if __name__ == "__main__":
    main()
