"""Lê (somente leitura) o começo e o fim de um disco para analisar a tabela de partições.

Uso (como Administrador): python ferramentas/ler_inicio_fim.py N pasta_destino
Gera inicio.bin (primeiros 1 MiB) e fim.bin (últimos 1 MiB) na pasta.
"""
import sys
import time
from pathlib import Path

from resgate_hd import BLOCO, SETOR, caminho_disco, discos

numero, pasta = int(sys.argv[1]), Path(sys.argv[2])
pasta.mkdir(parents=True, exist_ok=True)
tamanho = next(d["Size"] for d in discos() if d["Number"] == numero)
with open(caminho_disco(numero), "rb", buffering=0) as f:
    for nome, pos in (("inicio.bin", 0), ("fim.bin", (tamanho - BLOCO) // SETOR * SETOR)):
        t = time.monotonic()
        try:
            f.seek(pos)
            (pasta / nome).write_bytes(f.read(BLOCO))
            print(f"{nome}: lido em {time.monotonic() - t:.2f}s")
        except OSError as e:
            print(f"{nome}: FALHOU após {time.monotonic() - t:.2f}s — {e}")
