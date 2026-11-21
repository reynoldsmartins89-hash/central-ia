# Central de IA (CIIA)

> 💼 **Portfolio / case study (EN · PT):** [PORTFOLIO.md](PORTFOLIO.md) — what this system does, how it was
> designed and the skills it demonstrates. Author: [Reynolds R. Martins](https://www.linkedin.com/in/reynolds-r-martins).

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

## 🌱 Núcleo de aprendizado (a arquitetura do Hermes, do nosso jeito)

As ideias centrais do Hermes Agent, reescritas como código próprio, sem dependência dele e
com a regra da casa: **a Central propõe, o Reynolds aprova**.

| Hermes | Central | Onde |
|---|---|---|
| `SOUL.md` (identidade) | `agentes/ALMA.md`, entra em todo agente (passa pela vigilância) | `agentes.py` |
| `MEMORY.md` + `USER.md` com limite | **caderno** (ambiente/combinados) + **perfil**, com limite, sem duplicatas | `memoria.py` |
| `session_search` (SQLite FTS5) | todas as conversas e resultados pesquisáveis; lembranças entram no pedido | `sessoes.py` · `/lembrancas` |
| compressão de contexto | o começo da conversa longa vira resumo (Objetivo, Decisões, Pendências…) | `memoria.compactar` |
| ciclo de aprendizado (cria/corrige skills) | revisor em 2º plano propõe fatos e `SKILL.md`; **só grava com o seu OK** | `aprendizado.py` · `/aprender` |
| `cronjob` | `/agendar todo dia 07:00 \| pedido`; `[SILENCIO]`; pausa após 3 falhas | `agendador.py` · `/agenda` |
| varredura de injeção | bloqueia instruções escondidas, vazamento de chaves e comandos destrutivos | `seguranca.py` |
| índice de skills | `habilidades.indice()`: catálogo compacto para a IA | `habilidades.py` |

### Assinaturas, paralelo e MCP
- **Assinaturas na frente** (`[assinaturas]` em `agentes/agentes.toml`): o Codex (ChatGPT Plus) é o
  trabalhador principal; o revisor usa o Claude; as IAs gratuitas são reserva. Assinatura que bate no
  limite "descansa" (`~/.central-ia/descanso.json`) e as outras assumem sem esperar.
- **Paralelo**: cada etapa diz de quais depende (`depende = [1, 2]`); as independentes rodam juntas
  (até `CENTRAL_PARALELO`, padrão 3). O planejador já devolve as dependências.
- **MCP**: o servidor `mcp_servidor/servidor.py` está registrado no Codex e no Claude Code. Eles enxergam
  agentes, habilidades, acervo, lembranças, memória e agenda. As ferramentas de leitura rodam sem pedir
  aprovação no Codex; `perguntar_agente` e `executar_fluxo` pedem.

Diferenças de confiança em relação ao Hermes: nada é aprendido sem aprovação; tarefa agendada
nunca aciona o Executor nem publica; tudo fica no PC (`~/.central-ia`), com IAs gratuitas.

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

## ❤️ O coração: um único comando, pelo Telegram

Dê dois cliques em **`BOT-TELEGRAM.bat`** e deixe a janela minimizada. A partir daí,
**tudo é pelo Telegram**: você escreve o pedido em texto livre, o superagente monta o plano,
você aprova com um toque, acompanha cada etapa ao vivo e recebe o resultado completo
(e a nota no Obsidian com status *pendente*).

| No Telegram | O que acontece |
|---|---|
| texto livre | o superagente escolhe agentes e IAs |
| `/conteudo <tema>` | dossiê → artigo-mãe → revisão → redes → **imagens** → roteiro YouTube |
| `/rapido <pergunta>` | resposta direta |
| `/status` · `/agentes` · `/ajuda` | situação das IAs, equipe, exemplos |

### Quem faz o quê
- **IAs por API**: Gemini, Groq (grátis), e as pagas opcionais (DeepSeek, Perplexity, Grok, Claude/OpenAI API).
- **Suas assinaturas, via ferramentas oficiais de terminal** (`cli:claude`, `cli:codex`, `cli:gemini`),
  instaladas em `~/.central-ia/ferramentas` e logadas uma única vez.
- **Ollama**: local e privado.
- **Imagens**: o agente `diretor_de_arte` planeja; `superagente/imagens.py` produz
  (Gemini Nano Banana se faturamento ativo → Pexels grátis → rascunho por IA).
- **Obsidian**: cada execução vira nota em `Central IA/Execuções` + painel `Central IA/Painel.base`.

## Jeito fácil (sem programar)

1. Dê **dois cliques em `INICIAR.bat`** — abre um menu com números.
2. Escolha **1** para pedir qualquer coisa ao superagente, ou **2** para produzir conteúdo a partir de um tema.
3. O plano chega no seu **Telegram** com botões ✅ Aprovar / ❌ Rejeitar; ao terminar, chega o aviso.
4. Os textos prontos ficam na pasta `resultados/` (opção **5** do menu abre a pasta).

### Conectar o Telegram (uma vez só)
1. No Telegram, procure **@BotFather**, envie `/newbot`, escolha um nome e copie o *token*.
2. Abra o arquivo `.env` (Bloco de Notas) e cole o token depois de `TELEGRAM_BOT_TOKEN=`.
3. Envie "oi" para o seu bot e, no menu, escolha **6) Conectar o Telegram**.

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
   modelos = ["gemini:gemini-3.5-flash", "groq:openai/gpt-oss-120b", "ollama:qwen3:4b"]
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
