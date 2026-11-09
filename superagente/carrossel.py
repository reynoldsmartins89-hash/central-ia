"""Monta as lâminas do carrossel do Instagram (1080×1350) automaticamente — sem Canva.

Recebe o JSON do agente designer_carrossel e as imagens produzidas pelo diretor de arte.
Cada lâmina = foto de fundo recortada + degradê escuro + título + texto + numeração.
Cores e perfil configuráveis no .env:
    INSTAGRAM_PERFIL=@seu_perfil
    CARROSSEL_COR=#F28C28   (cor de destaque)
"""
from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

LARGURA, ALTURA = 1080, 1350
MARGEM = 80
FONTES_WINDOWS = Path(os.getenv("WINDIR", "C:/Windows")) / "Fonts"


def _fonte(negrito: bool, tamanho: int):
    from PIL import ImageFont
    for nome in (("segoeuib.ttf", "arialbd.ttf") if negrito else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(str(FONTES_WINDOWS / nome), tamanho)
        except OSError:
            continue
    return ImageFont.load_default(tamanho)


def _cor(hex_cor: str) -> tuple[int, int, int]:
    hex_cor = hex_cor.lstrip("#")
    return tuple(int(hex_cor[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _fundo(arquivo: Path | None):
    from PIL import Image, ImageOps
    if arquivo and arquivo.exists():
        try:
            return ImageOps.fit(Image.open(arquivo).convert("RGB"), (LARGURA, ALTURA))
        except OSError:
            pass
    return Image.new("RGB", (LARGURA, ALTURA), (24, 32, 48))


def _degrade(imagem):
    from PIL import Image
    camada = Image.new("L", (1, ALTURA))
    for y in range(ALTURA):  # transparente no topo → escuro embaixo
        camada.putpixel((0, y), int(235 * max(0, (y - ALTURA * 0.25) / (ALTURA * 0.75)) ** 1.2))
    preto = Image.new("RGB", (LARGURA, ALTURA), (0, 0, 0))
    return Image.composite(preto, imagem, camada.resize((LARGURA, ALTURA)))


def _lamina(dados: dict, total: int, fundo: Path | None, destino: Path) -> None:
    from PIL import ImageDraw
    destaque = _cor(os.getenv("CARROSSEL_COR", "#F28C28"))
    img = _degrade(_fundo(fundo))
    d = ImageDraw.Draw(img)
    f_titulo, f_texto, f_rodape = _fonte(True, 76), _fonte(False, 40), _fonte(True, 30)

    titulo = textwrap.fill(str(dados.get("titulo", "")).strip(), width=20)
    texto = textwrap.fill(str(dados.get("texto", "")).strip(), width=40)
    alt_titulo = d.multiline_textbbox((0, 0), titulo, font=f_titulo, spacing=10)[3] if titulo else 0
    alt_texto = d.multiline_textbbox((0, 0), texto, font=f_texto, spacing=12)[3] if texto else 0

    y = ALTURA - MARGEM - 70 - alt_texto - (40 if texto else 0) - alt_titulo
    d.rectangle((MARGEM, y - 30, MARGEM + 90, y - 22), fill=destaque)  # barrinha de destaque
    if titulo:
        d.multiline_text((MARGEM, y), titulo, font=f_titulo, fill=(255, 255, 255), spacing=10)
    if texto:
        d.multiline_text((MARGEM, y + alt_titulo + 40), texto, font=f_texto, fill=(230, 230, 230), spacing=12)
    rodape_y = ALTURA - MARGEM + 10
    d.text((MARGEM, rodape_y), os.getenv("INSTAGRAM_PERFIL", ""), font=f_rodape, fill=destaque)
    numero = f"{dados.get('lamina', '')}/{total}"
    d.text((LARGURA - MARGEM - d.textlength(numero, font=f_rodape), rodape_y), numero,
           font=f_rodape, fill=(255, 255, 255))
    img.save(destino, quality=92)


def montar(texto_designer: str, pasta: Path, log=print) -> list[Path]:
    laminas = sorted(extrair_lista_laminas(texto_designer), key=lambda l: int(l.get("lamina", 0) or 0))
    if not laminas:
        log("   ⚠ designer de carrossel não entregou o JSON das lâminas")
        return []
    mapa = {}
    indice = pasta / "imagens" / "imagens.json"
    if indice.exists():
        mapa = {i["nome"]: pasta / "imagens" / i["arquivo"]
                for i in json.loads(indice.read_text(encoding="utf-8")) if "arquivo" in i}
    destino = pasta / "carrossel"
    destino.mkdir(exist_ok=True)
    feitos = []
    for n, lamina in enumerate(laminas, 1):
        lamina.setdefault("lamina", n)
        arquivo = destino / f"lamina-{n:02d}.jpg"
        _lamina(lamina, len(laminas), mapa.get(str(lamina.get("imagem", ""))), arquivo)
        feitos.append(arquivo)
    log(f"   🎠 carrossel montado: {len(feitos)} lâminas em {destino.name}/")
    return feitos


def extrair_lista_laminas(texto: str) -> list[dict]:
    """Igual ao extrair_lista das imagens, mas a chave obrigatória é 'titulo'."""
    import re
    for bloco in reversed(re.findall(r"```json\s*(.*?)```", texto, re.S)):
        try:
            dados = json.loads(bloco)
        except json.JSONDecodeError:
            continue
        if isinstance(dados, list):
            return [d for d in dados if isinstance(d, dict) and d.get("titulo")]
    return []
