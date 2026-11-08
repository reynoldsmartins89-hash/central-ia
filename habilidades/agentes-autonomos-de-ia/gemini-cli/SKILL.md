---
name: gemini-cli
description: Delegar pesquisa, leitura de documentos longos e tarefas com contexto grande ao Gemini CLI (gemini -p).
---
# Delegação ao Gemini CLI

Use para **documentos extensos**, comparação de muitas fontes e pesquisas que se
beneficiam de janela de contexto grande.

## Como delegar
```powershell
gemini -p "Leia os arquivos em ./fontes e produza um dossiê com citações"
```

## Regras
- Diga quais arquivos podem ser lidos; nunca envie pastas com documentos restritos.
- Peça citações com nome do arquivo de origem.
- Ressalva: sujeito aos limites da conta Google / camada gratuita.
