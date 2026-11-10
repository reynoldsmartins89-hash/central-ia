"""Produto 1 — A Guerra Ensina: posicionamento, temporada de lançamento e os 4 primeiros artigos-mãe.
Tudo vai para resultados/a-guerra-ensina/ para o Reynolds revisar e aprovar (nada é publicado sozinho).
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from superagente.agentes import carregar_env, carregar_equipe  # noqa: E402

carregar_env()
DESTINO = RAIZ / "resultados" / "a-guerra-ensina"
PEDIDOS = [
    ("00 - Posicionamento e plano de lancamento",
     "Monte o POSICIONAMENTO do projeto A Guerra Ensina como negócio: promessa central, público (quem é o leitor e "
     "qual dor ele tem), 3 pilares editoriais, nome e descrição da newsletter, bio para Instagram/LinkedIn, e um "
     "plano de 90 dias com metas: mês 1 publicar 1 artigo-mãe por semana; mês 2 e-book gratuito para captar e-mails; "
     "mês 3 primeiro produto pago (e-book ou minicurso R$ 47–97) e newsletter paga. Inclua a TEMPORADA de lançamento "
     "(Modo 4) com 12 artigos e o produto de saída."),
    ("01 - Artigo - Seu cronograma e Market Garden",
     "Modo 2 — artigo-mãe sobre gestão de projetos: planos otimistas e premissas não testadas (Market Garden)."),
    ("02 - Artigo - A solidao da decisao",
     "Modo 2 — artigo-mãe sobre liderança: a solidão de quem decide (Eisenhower e o adiamento do Dia D)."),
    ("03 - Artigo - Os buracos que voce nao ve",
     "Modo 2 — artigo-mãe sobre decisões com dados: viés de sobrevivência (Abraham Wald e os aviões da 2ª Guerra)."),
    ("04 - Artigo - Nao lute a guerra que nao pode financiar",
     "Modo 2 — artigo-mãe sobre finanças pessoais: Lc 14,28–31, logística e reservas; não lutar a guerra que não "
     "se pode financiar."),
]

if __name__ == "__main__":
    DESTINO.mkdir(parents=True, exist_ok=True)
    agente = carregar_equipe()[1]["a_guerra_ensina"]
    for nome, pedido in PEDIDOS:
        arquivo = DESTINO / f"{nome}.md"
        if arquivo.exists():
            continue
        print(f"✍️ {nome}…", flush=True)
        modelo, texto = agente.executar(pedido, log=lambda *_: None)
        arquivo.write_text(f"<!-- escrito por {modelo} -->\n{texto}\n", encoding="utf-8")
        print(f"   ok ({len(texto.split())} palavras)", flush=True)
    from superagente import telegram
    telegram.enviar("⚔️ A Guerra Ensina: o posicionamento, o plano de 90 dias e os 4 primeiros artigos estão prontos "
                    "para sua revisão (resultados/a-guerra-ensina). Nada foi publicado.")
