---
name: ollama-local
description: Executar modelos locais via Ollama para tarefas privadas, repetitivas ou sem custo (classificação, resumos, rascunhos).
---
# IA local com Ollama

Use quando o conteúdo for **sensível** (não pode sair da máquina) ou quando a
tarefa for simples e repetitiva.

## Modelos sugeridos (RTX 3050 6 GB / 32 GB RAM)
- `qwen3:4b` — redação, classificação, estruturação
- `gemma3:4b` — resumos e revisão
- modelos 7B–8B quantizados — tarefas mais complexas (mais lentos)

## Regras
- Documentos marcados como restritos só podem ser processados por este agente.
- Se a qualidade for insuficiente, sinalize e sugira escalar para um modelo na nuvem.
