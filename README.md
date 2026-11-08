# Central de IA (CIIA)

Um **superagente** em Python que coordena uma equipe de **agentes especialistas**,
cada um definido por uma **habilidade** (`SKILL.md`) e por uma lista de modelos de IA
em ordem de preferência — Ollama local, Gemini, Groq, Claude, ChatGPT, DeepSeek,
Grok, Kimi, Qwen, Z.AI, Perplexity…

Inspirado na organização do Hermes Agent (`optional-skills/<categoria>/<habilidade>`),
mas com núcleo próprio, independente de fornecedor e com **custo adicional zero** por padrão.

```
VOCÊ (decide e aprova)
   │
   ▼
🧠 SUPERAGENTE ── planeja ──► escolhe agentes ──► consolida ──► resultados/ (aguarda aprovação)
   │
   ├── pesquisador   (pesquisa/pesquisa-fontes)
   ├── redator       (conteudo/artigo-mae)
   ├── revisor       (pesquisa/revisao-critica)
   ├── social        (conteudo/derivados-redes-sociais)
   ├── roteirista    (conteudo/roteiro-youtube)
   ├── analista      (dados/analise-de-dados)
   ├── gestor        (gestao/gestao-de-projetos)
   ├── estrategista  (gestao/estrategia-de-negocios)
   ├── programador   (desenvolvimento/desenvolvimento-python)
   ├── privado       (só Ollama local — nada sai do computador)
   └── generalista
        │
        ▼
   modelos com reserva automática: gemini → groq → ollama …
```

## Estrutura

```
central-ia/
├── main.py                    # linha de comando
├── superagente/               # núcleo: provedores, agentes, orquestrador
├── agentes/agentes.toml       # a equipe (edite aqui para criar agentes)
├── habilidades/               # biblioteca de skills, por categoria
│   ├── agentes-autonomos-de-ia/   claude-code, gemini-cli, ollama-local
│   ├── conteudo/                  artigo-mae, derivados-redes-sociais, roteiro-youtube
│   ├── pesquisa/                  pesquisa-fontes, revisao-critica
│   ├── dados/                     analise-de-dados
│   ├── gestao/                    gestao-de-projetos, estrategia-de-negocios
│   ├── desenvolvimento/           desenvolvimento-python
│   └── geral/                     superagente, generalista
├── fluxos/                    # processos fixos (ex.: producao-editorial)
├── mcp_servidor/              # expõe os agentes para Claude Code / Gemini CLI / VS Code
├── scripts/                   # validação e exportação das skills
└── tests/                     # testes offline
```

## Começando (Windows, terminal do VS Code)

```powershell
git clone https://github.com/reynoldsmartins89-hash/central-ia.git
cd central-ia
copy .env.example .env          # depois preencha GEMINI_API_KEY e/ou GROQ_API_KEY
python main.py diagnostico      # mostra o que está pronto
```

Para IA local gratuita: instale o [Ollama](https://ollama.com) e rode `ollama pull qwen3:4b`.

### Comandos

| Comando | O que faz |
|---|---|
| `python main.py agentes` | lista a equipe |
| `python main.py habilidades` | lista as skills |
| `python main.py perguntar pesquisador "tema"` | fala direto com um agente |
| `python main.py executar "objetivo"` | **superagente** monta o plano, você aprova, ele executa |
| `python main.py fluxo producao-editorial "tema"` | tema → dossiê → artigo-mãe → revisão → redes → YouTube |
| `python main.py --modelo eco:teste executar "..."` | testa tudo **offline**, sem chamar IA |
| `python main.py --modelo ollama:qwen3:4b ...` | força todos os agentes a usar um modelo |

Os resultados ficam em `resultados/<data>_<assunto>/`, um arquivo por etapa, mais
`registro.json` e `APROVACAO.md`. **Nada é publicado automaticamente.**

## Como criar um novo agente

1. Crie a habilidade: `habilidades/<categoria>/<nome>/SKILL.md`
   ```markdown
   ---
   name: <nome>
   description: O que a habilidade faz e quando usar.
   ---
   # Instruções
   ...
   ```
   (categoria nova? crie também `habilidades/<categoria>/DESCRIPTION.md`)
2. Registre o agente em `agentes/agentes.toml`:
   ```toml
   [agentes.meu_agente]
   descricao = "Uma frase — o superagente usa isso para decidir quando chamá-lo."
   habilidade = "<categoria>/<nome>"
   modelos = ["gemini:gemini-2.5-flash", "groq:llama-3.3-70b-versatile", "ollama:qwen3:4b"]
   ```
3. Valide: `python scripts/validar_habilidades.py` e `python -m unittest -v`

Pronto: o superagente passa a enxergar e delegar para o novo agente.

## Usando os agentes dentro do Claude Code / Gemini CLI

- **Skills**: `python scripts/exportar_habilidades.py claude` (ou `gemini`) copia as habilidades para essas ferramentas.
- **MCP**: `pip install "mcp[cli]"` e depois
  `claude mcp add central-ia -- python mcp_servidor/servidor.py` —
  o Claude Code passa a ter as ferramentas `listar_agentes`, `perguntar_agente`, `executar_fluxo`.

## Custos — leia antes

Assinaturas como ChatGPT Plus, Gemini Pro ou Claude Pro **não incluem crédito de API**.
Por isso a ordem padrão dos modelos prioriza **Ollama (local)**, **Gemini API gratuita**
e **Groq gratuito**. Provedores pagos só são usados se você colocar a chave no `.env`.

## Segurança

- `.env` e `resultados/` não vão para o GitHub.
- Use o agente `privado` para documentos sensíveis: ele só roda no Ollama local.
- O superagente mostra o plano e pede confirmação antes de executar (`-s` pula).
