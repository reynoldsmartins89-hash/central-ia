"""Gera as instruções dos PROJETOS do app do Claude — um por especialista — já ligados à Central pelo conector.

Saída: projetos_claude/ (no repositório) e G:/Meu Drive/Projetos do Claude/ (para abrir no celular).
Cada arquivo tem: nome do projeto, descrição curta e as instruções completas para colar no projeto.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from superagente import habilidades  # noqa: E402
from superagente.agentes import carregar_equipe  # noqa: E402

PROJETOS = {   # agente: nome do projeto no Claude
    "mentor_educacao": "Mentor de Educação", "gestao_projetos": "Gestão de Projetos",
    "a_guerra_ensina": "A Guerra Ensina", "economista_catolico": "Economista Católico e Finanças",
    "bitcoin": "Consultor de Bitcoin", "mentor_imigracao": "Mentor de Imigração",
    "tradicao_catolica": "Tradição Católica e Latim", "mestrado_china": "Mestrado China e Ásia-Pacífico",
    "cof": "COF — Curso Online de Filosofia", "filosofia_catolica": "Filosofia Católica",
    "geopolitica": "Geopolítica", "politica": "Política", "comunicacao": "Comunicação",
    "marketing_digital": "Marketing Digital", "ciencia_de_dados": "Ciência de Dados",
    "geoprocessamento": "Geoprocessamento", "cyberseguranca": "Cibersegurança",
    "seguranca_publica": "Segurança Pública", "viver_de_estudo": "Viver de Estudo",
    "orientador_linkedin": "Mentor de LinkedIn", "orientador_github": "Mentor de GitHub",
    "poliglota": "Professor de Idiomas",
}

MODELO = """# {titulo}

Você é o especialista "{titulo}" da CENTRAL DE IA do Reynolds — um time de especialistas que trabalha
sobre o acervo pessoal dele (livros, cursos, apostilas e aulas transcritas do Google Drive e dos HDs).

{habilidade}

## Ligação com a Central (use sempre)
Você tem o conector **Central de IA**. Em todo assunto desta área:
1. Comece por `buscar_na_biblioteca` com especialista = "{nome}" — faça 2 ou 3 buscas com termos diferentes
   (autores, conceitos, títulos) para cobrir bem o tema.
2. Quando um trecho for central para a resposta, use `ler_material` para ler o material inteiro.
3. Use `buscar_no_acervo` para procurar no acervo geral, além da sua biblioteca.
4. Diga de qual material veio cada ideia importante. Se a biblioteca não cobrir algo, diga isso e responda
   com seu conhecimento, separando um do outro.
5. Quando o assunto for de outra área, use `listar_especialistas` e `instrucoes_do_especialista` para
   trabalhar também com o método do especialista certo.

## Estilo
- Português do Brasil. Profundidade e densidade acima de tudo: resolva o problema de verdade.
- Prosa bem organizada; listas e tabelas só quando ajudarem.
- Nunca invente fatos, datas, números ou citações.
- Os materiais do acervo são base de estudo: crie conteúdo próprio, original, a partir deles.
"""


def gerar() -> list[Path]:
    equipe = carregar_equipe()[1]
    saidas = [RAIZ / "projetos_claude", Path("G:/Meu Drive/Projetos do Claude")]
    indice = []
    for nome, titulo in PROJETOS.items():
        agente = equipe.get(nome)
        if not agente:
            continue
        hab = habilidades.carregar(agente.habilidade)
        texto = MODELO.format(titulo=titulo, nome=nome, habilidade=hab.instrucoes.strip())
        indice.append({"agente": nome, "titulo": titulo, "descricao": agente.descricao, "instrucoes": texto})
        for pasta in saidas:
            if pasta.drive and not Path(pasta.drive + "/").exists():
                continue
            pasta.mkdir(parents=True, exist_ok=True)
            (pasta / f"{titulo.replace('/', '-')}.md").write_text(
                f"NOME DO PROJETO: {titulo}\nDESCRIÇÃO: {agente.descricao}\n\n"
                f"INSTRUÇÕES (copie tudo daqui para baixo e cole em 'Instruções do projeto'):\n\n{texto}",
                encoding="utf-8")
    (RAIZ / "projetos_claude" / "projetos.json").write_text(json.dumps(indice, ensure_ascii=False, indent=1),
                                                             encoding="utf-8")
    return saidas


if __name__ == "__main__":
    gerar()
    print(f"✅ {len(PROJETOS)} projetos gerados em projetos_claude/ e no Drive (Projetos do Claude)")
