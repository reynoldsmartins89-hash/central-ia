"""Extrai os arquivos de uma imagem de disco NTFS (feita por resgate_hd.py), sem montar nada.

Lê a MFT direto da imagem, refaz a árvore de pastas e copia cada arquivo para a pasta de
saída. Usa o mapa do clone (.mapa.json) para saber o que ainda não foi copiado do HD:
  - arquivo com algum pedaço ainda pendente → fica para a próxima rodada (rode de novo depois);
  - arquivo com setor ilegível de vez → é extraído assim mesmo (trecho zerado) e listado
    como DANIFICADO no relatório;
  - arquivo já extraído (mesmo tamanho) → pulado, então pode rodar quantas vezes quiser.
Só lê a imagem; nunca toca no HD original.

Uso: python ferramentas/extrair_ntfs.py IMAGEM PASTA_SAIDA [--particao BYTES]
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = 5
ATTR_SI, ATTR_NOME, ATTR_DADOS, ATTR_FIM = 0x10, 0x30, 0x80, 0xFFFFFFFF
FILETIME_1970 = 116444736000000000


# ------------------------------------------------------------------ estruturas
@dataclass
class Registro:
    numero: int
    em_uso: bool = False
    pasta: bool = False
    base: int = 0
    nomes: list[tuple[int, int, str]] = field(default_factory=list)  # (pai, namespace, nome)
    residente: bytes | None = None
    pedacos: list[tuple[int, list[tuple[int | None, int]]]] = field(default_factory=list)
    tamanho: int = 0
    comprimido: bool = False
    modificado: int = 0


def corrigir_fixup(rec: bytearray, setor: int = 512) -> bool:
    """Aplica a 'update sequence' do NTFS. Falso se o registro estiver corrompido."""
    usa_ofs, usa_n = struct.unpack_from("<HH", rec, 4)
    if usa_n < 2 or usa_ofs + 2 * usa_n > len(rec):
        return False
    marca = rec[usa_ofs:usa_ofs + 2]
    for i in range(1, usa_n):
        fim = i * setor
        if fim > len(rec):
            break
        if rec[fim - 2:fim] != marca:
            return False
        rec[fim - 2:fim] = rec[usa_ofs + 2 * i:usa_ofs + 2 * i + 2]
    return True


def decodificar_runlist(dados: bytes, ini: int) -> list[tuple[int | None, int]]:
    """Lista de (LCN, nº de clusters); LCN None = trecho esparso (zeros)."""
    runs, lcn, i = [], 0, ini
    while i < len(dados) and dados[i]:
        cab = dados[i]
        nl, no = cab & 0x0F, cab >> 4
        i += 1
        qtd = int.from_bytes(dados[i:i + nl], "little")
        i += nl
        if no:
            lcn += int.from_bytes(dados[i:i + no], "little", signed=True)
            runs.append((lcn, qtd))
        else:
            runs.append((None, qtd))
        i += no
    return runs


def ler_registro(numero: int, bruto: bytes) -> Registro | None:
    if bruto[:4] != b"FILE":
        return None
    rec = bytearray(bruto)
    if not corrigir_fixup(rec):
        return None
    r = Registro(numero)
    flags = struct.unpack_from("<H", rec, 0x16)[0]
    r.em_uso, r.pasta = bool(flags & 1), bool(flags & 2)
    r.base = struct.unpack_from("<Q", rec, 0x20)[0] & 0xFFFFFFFFFFFF
    pos = struct.unpack_from("<H", rec, 0x14)[0]
    while pos + 16 <= len(rec):
        tipo, compr = struct.unpack_from("<II", rec, pos)
        if tipo == ATTR_FIM or compr < 16 or pos + compr > len(rec):
            break
        nao_res, nome_len = rec[pos + 8], rec[pos + 9]
        aflags = struct.unpack_from("<H", rec, pos + 12)[0]
        if not nao_res:
            vlen, vofs = struct.unpack_from("<IH", rec, pos + 16)
            valor = bytes(rec[pos + vofs:pos + vofs + vlen])
            if tipo == ATTR_NOME and len(valor) >= 66:
                pai = struct.unpack_from("<Q", valor, 0)[0] & 0xFFFFFFFFFFFF
                n, ns = valor[64], valor[65]
                r.nomes.append((pai, ns, valor[66:66 + 2 * n].decode("utf-16-le", "replace")))
            elif tipo == ATTR_SI and len(valor) >= 16:
                r.modificado = struct.unpack_from("<Q", valor, 8)[0]
            elif tipo == ATTR_DADOS and nome_len == 0:
                r.residente, r.tamanho = valor, len(valor)
        elif tipo == ATTR_DADOS and nome_len == 0:
            vcn0 = struct.unpack_from("<Q", rec, pos + 16)[0]
            rl_ofs = struct.unpack_from("<H", rec, pos + 32)[0]
            if vcn0 == 0:
                r.tamanho = struct.unpack_from("<Q", rec, pos + 48)[0]
            r.comprimido = r.comprimido or bool(aflags & 0x0001) or bool(aflags & 0x4000)
            r.pedacos.append((vcn0, decodificar_runlist(bytes(rec[pos:pos + compr]), rl_ofs)))
        pos += compr
    return r


# ------------------------------------------------------------------ pendências
class Pendencias:
    """Faixas da imagem ainda não copiadas do HD, e setores perdidos de vez."""

    def __init__(self, mapa: dict | None, tamanho_img: int):
        faixas = []
        if mapa:
            if mapa.get("posicao", 0) < mapa.get("tamanho", 0):
                faixas.append((mapa["posicao"], mapa["tamanho"]))
            faixas += [tuple(f) for f in mapa.get("pulados", []) + mapa.get("pulados_prox", [])]
            feita = set(mapa.get("passada3_feita", []))
            faixas += [(b, b + 1024 * 1024) for b in mapa.get("blocos_ruins", []) if b not in feita]
        faixas.append((tamanho_img, 1 << 62))  # além do fim da imagem atual
        self.pend = self._unir(faixas)
        self.perdidos = sorted((s, s + 512) for s in (mapa or {}).get("setores_ruins", []))

    @staticmethod
    def _unir(faixas):
        out = []
        for i, f in sorted(faixas):
            if out and i <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], f))
            else:
                out.append((i, f))
        return out

    @staticmethod
    def _toca(lista, ini, fim) -> bool:
        k = bisect.bisect_right(lista, (fim, -1)) - 1
        while k >= 0 and lista[k][1] > ini:
            if lista[k][0] < fim:
                return True
            k -= 1
        return False

    def pendente(self, ini: int, fim: int) -> bool:
        return self._toca(self.pend, ini, fim)

    def perdido(self, ini: int, fim: int) -> bool:
        return self._toca(self.perdidos, ini, fim)


# ------------------------------------------------------------------ extração
PROIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def nome_seguro(nome: str) -> str:
    nome = PROIBIDOS.sub("_", nome).rstrip(" .")
    return nome or "_"


def longo(p: Path) -> str:
    s = str(p.resolve()) if not str(p).startswith("\\\\?\\") else str(p)
    return "\\\\?\\" + s if os.name == "nt" and not s.startswith("\\\\?\\") else s


class Volume:
    def __init__(self, img, particao: int):
        self.img, self.part = img, particao
        boot = None
        for ofs in (particao, particao + 512):
            img.seek(ofs)
            b = img.read(512)
            if b[3:7] == b"NTFS" and b[510:512] == b"\x55\xaa":
                boot = b
                break
        if boot is None:
            raise SystemExit("Setor de boot NTFS não encontrado na imagem.")
        bps, spc = struct.unpack_from("<HB", boot, 11)
        self.cluster = bps * spc
        self.mft_lcn = struct.unpack_from("<Q", boot, 48)[0]
        c = struct.unpack_from("<b", boot, 64)[0]
        self.tam_reg = 2 ** -c if c < 0 else c * self.cluster

    def ler(self, pos: int, qtd: int) -> bytes:
        self.img.seek(pos)
        d = self.img.read(qtd)
        return d + bytes(qtd - len(d))

    def extents(self, runs) -> list[tuple[int | None, int]]:
        """(posição na imagem ou None p/ esparso, bytes) a partir da lista de runs."""
        return [(None if lcn is None else self.part + lcn * self.cluster, n * self.cluster)
                for lcn, n in runs]

    def registros(self):
        mft0 = ler_registro(0, self.ler(self.part + self.mft_lcn * self.cluster, self.tam_reg))
        if not mft0 or not mft0.pedacos:
            raise SystemExit("Registro 0 da MFT ilegível.")
        runs = [r for _v, rl in sorted(mft0.pedacos) for r in rl]
        numero = 0
        for pos, qtd in self.extents(runs):
            if pos is None:
                numero += qtd // self.tam_reg
                continue
            for k in range(0, qtd, 4 * 1024 * 1024):
                bloco = self.ler(pos + k, min(4 * 1024 * 1024, qtd - k))
                for j in range(0, len(bloco), self.tam_reg):
                    yield numero, bloco[j:j + self.tam_reg]
                    numero += 1


def extrair(imagem: Path, saida: Path, particao: int = 1048576, mapa_path: Path | None = None,
            log=print) -> dict:
    mapa_path = mapa_path or imagem.with_name(imagem.name + ".mapa.json")
    mapa = json.loads(mapa_path.read_text(encoding="utf-8")) if mapa_path.exists() else None
    pend = Pendencias(mapa, imagem.stat().st_size)
    final = mapa is None or not pend.pend[:-1]  # nada mais pendente além do fim
    cont = {"extraidos": 0, "ja_existiam": 0, "pendentes": 0, "danificados": [],
            "nao_suportados": [], "registros_ilegiveis": 0}
    with open(imagem, "rb") as img:
        vol = Volume(img, particao)
        regs: dict[int, Registro] = {}
        for numero, bruto in vol.registros():
            r = ler_registro(numero, bruto)
            if r is None:
                if bruto.strip(b"\x00"):
                    cont["registros_ilegiveis"] += 1
                continue
            if not r.em_uso:
                continue
            if r.base and r.base != numero:  # registro de extensão: junta ao principal
                b = regs.setdefault(r.base, Registro(r.base))
                b.pedacos += r.pedacos
                b.nomes += r.nomes
                b.comprimido |= r.comprimido
                if r.tamanho and not b.tamanho:
                    b.tamanho = r.tamanho
                continue
            if numero in regs:  # extensões vieram antes
                ext = regs[numero]
                r.pedacos += ext.pedacos
                r.nomes += ext.nomes
                r.comprimido |= ext.comprimido
                r.tamanho = r.tamanho or ext.tamanho
            regs[numero] = r
        log(f"MFT lida: {len(regs)} registros em uso, {cont['registros_ilegiveis']} ilegíveis.")

        def nome_de(r: Registro):
            melhores = [n for n in r.nomes if n[1] != 2] or r.nomes  # 2 = nome curto DOS
            return melhores[0] if melhores else None

        cache: dict[int, Path | None] = {RAIZ: saida}

        def caminho(num: int, prof: int = 0) -> Path | None:
            if num in cache:
                return cache[num]
            r = regs.get(num)
            n = nome_de(r) if r else None
            if n is None or prof > 64:
                res = saida / "_SEM_PASTA"
            else:
                pai = caminho(n[0], prof + 1)
                res = (pai or saida / "_SEM_PASTA") / nome_seguro(n[2])
            cache[num] = res
            return res

        for num, r in regs.items():
            if r.pasta or num < 24 or not r.nomes:
                continue
            n = nome_de(r)
            if n[2].startswith("$") and n[0] == RAIZ:
                continue
            destino = caminho(num)
            rel = destino.relative_to(saida)
            if r.comprimido:
                cont["nao_suportados"].append(f"{rel} (comprimido/criptografado pelo NTFS)")
                continue
            runs = [x for _v, rl in sorted(r.pedacos, key=lambda p: p[0]) for x in rl]
            exts = vol.extents(runs)
            if r.residente is None and any(p is not None and pend.pendente(p, p + q)
                                           for p, q in exts):
                cont["pendentes"] += 1
                continue
            try:
                if os.path.exists(longo(destino)) and os.path.getsize(longo(destino)) == r.tamanho:
                    cont["ja_existiam"] += 1
                    continue
                os.makedirs(longo(destino.parent), exist_ok=True)
                tmp = longo(destino) + ".parcial"
                danificado = False
                with open(tmp, "wb") as out:
                    if r.residente is not None:
                        out.write(r.residente)
                    else:
                        falta = r.tamanho
                        for p, q in exts:
                            if falta <= 0:
                                break
                            q = min(q, falta)
                            if p is None:
                                out.seek(q, 1)
                            else:
                                danificado |= pend.perdido(p, p + q)
                                for k in range(0, q, 8 * 1024 * 1024):
                                    out.write(vol.ler(p + k, min(8 * 1024 * 1024, q - k)))
                            falta -= q
                        out.truncate(r.tamanho)
                os.replace(tmp, longo(destino))
                if r.modificado > FILETIME_1970:
                    t = (r.modificado - FILETIME_1970) / 1e7
                    os.utime(longo(destino), (t, t))
                cont["extraidos"] += 1
                if danificado:
                    cont["danificados"].append(str(rel))
            except OSError as e:
                cont["nao_suportados"].append(f"{rel} (erro ao gravar: {e})")
    cont["final"] = final
    return cont


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("imagem", type=Path)
    ap.add_argument("saida", type=Path)
    ap.add_argument("--particao", type=int, default=1048576, help="início da partição (bytes)")
    a = ap.parse_args(argv)
    t0 = time.monotonic()
    print(f"\n=== Extração {time.strftime('%d/%m/%Y %H:%M')} ===")
    c = extrair(a.imagem, a.saida, a.particao)
    print(f"Extraídos agora: {c['extraidos']} | já estavam salvos: {c['ja_existiam']} | "
          f"aguardando o clone: {c['pendentes']} | danificados: {len(c['danificados'])} | "
          f"não suportados: {len(c['nao_suportados'])}  ({time.monotonic() - t0:.0f} s)")
    relatorio = a.saida.parent / (a.saida.name + "-relatorio.txt")
    with open(relatorio, "w", encoding="utf-8") as f:
        f.write(f"Relatório da extração — {time.strftime('%d/%m/%Y %H:%M')}\n")
        f.write("Clone terminado: " + ("sim" if c["final"] else "não (rodadas seguintes trarão mais)") + "\n")
        f.write(f"Arquivos aguardando o clone: {c['pendentes']}\n\n")
        f.write("ARQUIVOS DANIFICADOS (algum trecho ilegível no HD, ficou zerado):\n")
        f.writelines(f"  {x}\n" for x in c["danificados"])
        f.write("\nNÃO EXTRAÍDOS:\n")
        f.writelines(f"  {x}\n" for x in c["nao_suportados"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
