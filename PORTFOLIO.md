# Central de IA — a personal multi-agent AI operating system

**Author:** Reynolds R. Martins · [LinkedIn](https://www.linkedin.com/in/reynolds-r-martins) ·
Rio de Janeiro, Brazil · open to remote work worldwide

> *Leia em português [mais abaixo](#português).*

## The problem
One person, a huge amount of scattered knowledge (terabytes of courses, books, videos and documents on
external drives and Google Drive), many e-mail accounts, several content projects and a career transition —
and only a few hours a day. Off-the-shelf chatbots answer questions, but they don't **know** your material,
don't **work while you sleep** and don't **ask before acting**.

## The solution
A Python system in which a **super-agent** plans each request and delegates it to a team of **56 specialist
agents**. Each agent is defined by a plain-text skill (`SKILL.md`) plus an ordered list of AI models. It runs
24/7 on a home PC and a free-tier cloud server, and it is driven entirely from **Telegram** (text, voice,
photo or document). Every action with real-world effect waits for a ✅ from the human.

```
Telegram (text · voice · photo)  ──►  Super-agent (plans, picks specialists, merges results)
                                           │
        ┌──────────────┬───────────────────┼────────────────────┬────────────────────┐
   Knowledge agents   Content team     Operations agents      Career agents       Executor
   (RAG over the      (research,       (Reader, Curator,      (Job Hunter,        (actions on the PC,
    personal library)  writing, review) Repairer, E-mail)      LinkedIn mentor)    only after ✅)
                                           │
              Model router with automatic fallback: Gemini → Groq → Claude → local Ollama …
```

## What I designed and built (Nov 2026 – present)
| Area | What it does |
|---|---|
| **Multi-agent orchestration** | Super-agent plans the request, chooses specialists, runs them in sequence or as fixed flows and writes a consolidated result for human approval. |
| **Model-agnostic routing** | 12+ LLM providers (Gemini, Groq, Claude, OpenAI, DeepSeek, Grok, Qwen, local Ollama…) behind one interface, with automatic fallback on quota, timeout or outage. Default cost: **zero** (free tiers + local models). |
| **Personal knowledge base (RAG)** | The *Reader* agent transcribes and OCRs videos, audio, scanned PDFs and images from Google Drive and external drives into Markdown; specialists answer **grounded in that library**, citing sources. Private agents run **only on local models** — nothing leaves the machine. |
| **Autonomous operations** | A *Watchdog* agent starts with Windows, keeps every service alive, restarts what crashes, checks disk/network/AI health and reports on Telegram. A *Repairer* recovers damaged files (always on copies). |
| **E-mail manager** | Reads every IMAP account (Gmail, Yahoo, Outlook…) without marking as read, classifies with rules + AI, proposes a plan, moves only after approval, **never deletes**, and every move can be undone. |
| **Job Hunter** | Searches Gupy, Remotive, Remote OK, We Work Remotely, Himalayas and others plus LinkedIn/Indeed e-mail alerts, filters remote/hybrid and location restrictions, scores each job 0–100 against the candidate profile and alerts only the strong matches. |
| **Cloud + integrations** | Telegram bot, cloud "mailbox" on Oracle Cloud free tier (works with the PC off), **MCP servers** exposing the agents to Claude, Gemini CLI and VS Code, Open WebUI chat, Google Drive, Obsidian. |
| **Engineering quality** | Automated tests (offline, with a fake provider), CI on GitHub Actions validating every skill/agent/flow, secrets only in `.env`, human-in-the-loop by design. |

**In numbers:** ~11,000 lines of Python · 36 core modules · 56 agents · 57 skills · 12+ AI providers ·
70+ automated tests · built in under two weeks.

## How it was built
I worked as **architect and product owner**, directing AI coding agents (Claude Code, Codex, Gemini CLI)
under explicit rules in [`AGENTS.md`](AGENTS.md): every change validated by tests before it is done,
nothing published automatically, nothing deleted. That is the skill this project demonstrates:
**turning a messy real-world problem into a reliable AI system** — defining agents, guardrails, data
flows and acceptance criteria, then operating it daily.

## Skills demonstrated
Agentic AI architecture · LLM orchestration and fallback · RAG over personal data · prompt/skill design ·
Python automation · APIs and integrations (Telegram, IMAP, Google Drive, MCP) · cloud deployment
(Oracle, Linux, systemd) · risk management and safe automation (human approval, least privilege, undo) ·
project management (MBA, PMBOK) applied to AI delivery.

## Explore the code
- [`superagente/orquestrador.py`](superagente/orquestrador.py) — planning and delegation
- [`superagente/provedores.py`](superagente/provedores.py) — multi-provider router with fallback
- [`superagente/vigia.py`](superagente/vigia.py) — watchdog / self-healing services
- [`superagente/vagas.py`](superagente/vagas.py) — Job Hunter
- [`habilidades/`](habilidades) — the skill library · [`agentes/agentes.toml`](agentes/agentes.toml) — the team

---

## Português

**Central de IA** é um sistema multiagente em Python que eu projetei e opero: um **superagente** planeja cada
pedido e o distribui entre **56 agentes especialistas**, cada um definido por uma habilidade (`SKILL.md`) e por
uma lista de modelos de IA com reserva automática. Funciona 24 h no PC e num servidor gratuito na nuvem,
é comandado pelo **Telegram** (texto, áudio, foto, documento) e nada com efeito real acontece sem o meu ✅.

**Principais entregas:** orquestração de agentes; roteador de 12+ provedores de IA com custo zero por padrão;
base de conhecimento pessoal (RAG) com transcrição e OCR de vídeos, áudios e PDFs do Drive e dos HDs;
Agente Vigia que religa serviços e avisa no Telegram; gerenciador de todas as contas de e-mail que nunca apaga e
pode desfazer tudo; Caçador de Vagas com nota de aderência; servidores MCP para Claude, Gemini CLI e VS Code;
testes automatizados e CI no GitHub Actions.

**Como foi feito:** atuei como **arquiteto e dono do produto**, conduzindo agentes de programação com IA
(Claude Code, Codex, Gemini CLI) sob regras claras — testes antes de concluir, nada publicado automaticamente,
nada apagado. É a competência que o projeto demonstra: transformar um problema real e bagunçado num
sistema de IA confiável, com agentes, salvaguardas, fluxos de dados e critérios de aceite — e operá-lo todo dia.
