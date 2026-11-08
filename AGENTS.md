# Instruções para agentes de código (Claude Code, Gemini CLI, Copilot, Codex)

Este repositório é a **Central de IA**: um superagente em Python que orquestra
agentes especialistas definidos por habilidades (`SKILL.md`).

- Agentes: `agentes/agentes.toml` (habilidade + modelos em ordem de preferência)
- Habilidades: `habilidades/<categoria>/<nome>/SKILL.md` — `name` igual ao nome da pasta
- Toda categoria nova precisa de `DESCRIPTION.md`
- Fluxos fixos: `fluxos/<nome>.toml`
- Antes de concluir qualquer mudança, rode:
  `python scripts/validar_habilidades.py` e `python -m unittest -v`
- Nunca coloque chaves de API no código; use `.env`.
- Nada é publicado automaticamente: resultados ficam em `resultados/` aguardando aprovação humana.
