---
name: claude-code
description: Delegar tarefas de programação, refatoração e revisão de código ao Claude Code via terminal (claude -p).
---
# Delegação ao Claude Code

Use quando a tarefa exigir **editar arquivos de um repositório**, rodar testes ou
revisar código com contexto do projeto inteiro.

## Como delegar
```powershell
claude -p "Descreva a tarefa com objetivo, arquivos envolvidos e critério de pronto" --output-format text
```
- Rode dentro da pasta do projeto alvo.
- Peça sempre um resumo final do que mudou.

## Regras
- Uma tarefa por chamada, com critério de pronto explícito.
- Nunca peça para publicar, fazer push ou apagar dados sem aprovação humana.
- Ressalva: usa a cota do plano Claude do usuário; tarefas longas consomem limite.
