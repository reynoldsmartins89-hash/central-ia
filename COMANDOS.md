# Comandos da Central de IA

Guia de referência: **o que pedir ao seu secretário** e o que ele devolve.
Regra de ouro: **nada é publicado, movido ou apagado sem o seu ✅.**

## No Telegram (uso diário)

### 🖐 Executar no seu PC (de qualquer lugar)
| Comando | O que faz |
|---|---|
| `/pc <ordem>` | O **Agente Executor** (Claude Code, sua assinatura) faz de verdade no PC: arquivos, pastas, programas, scripts, HDs, Drive e as ferramentas da Central. Ele **investiga só lendo → mostra o plano → espera o seu ✅ → executa → manda o relatório** |
| `/continuar <ordem>` | Segue a tarefa anterior do Executor, lembrando o contexto |
| *(ordem em texto livre)* | "Organize…", "mova…", "repare…" são reconhecidos como ordens e vão para o Executor |

Segurança: nada muda sem o seu ✅; cada comando passa pelo revisor de segurança do Claude Code;
o Executor nunca apaga de vez (move para "PARA APAGAR - REVISAR" ou Lixeira), não mexe em senhas/chaves,
não envia mensagens nem faz compras. Histórico em `~/.central-ia/executor.jsonl`.

### ✍️ Pedir trabalho
| Comando | O que faz | Devolve |
|---|---|---|
| *(texto livre)* | O superagente entende o pedido, monta a equipe (1 a 12+ agentes), mostra o plano com o porquê de cada um e executa após o seu ✅ | Progresso ao vivo, arquivo completo, nota no Obsidian; síntese final quando há 4+ agentes |
| `/conteudo <tema>` | Pacote editorial completo a partir de um tema | Dossiê, artigo-mãe, revisão, posts por rede, imagens, carrossel pronto e roteiro de YouTube |
| `/rapido <pergunta>` | Resposta direta, sem plano | Uma resposta curta |

**Como pedir bem:** diga *o quê*, *para quem* e *em que formato*.
Ex.: “Estudo sobre a disputa EUA–China por IA e o impacto no Brasil, **para empresários**, em **artigo, LinkedIn e vídeo**”.
Se quiser um especialista específico, mencione-o: “…use também o agente de geopolítica”.

### 🗂 Secretário (orientação, sem executar)
| Comando | O que faz |
|---|---|
| `/equipe <pedido>` | Recomenda quais agentes devem trabalhar juntos, por quê, e quem mais poderia ajudar |
| `/agentes` | Lista a equipe completa |

### 🗃 Arquivos (Drive e HDs)
| Comando | O que faz |
|---|---|
| `/buscar <assunto>` | Onde estão seus arquivos sobre um assunto, em todos os discos catalogados |
| `/curador` | Resumo do catálogo (quantidade, tipos, situação) |
| `/duplicados` | Cópias idênticas encontradas e espaço recuperável |
| `/integridade` | Arquivos corrompidos, vazios ou com download incompleto |
| `/leitura` | Quanto do acervo já foi lido/transcrito pelo Agente Leitor |
| `/reparos` | O que o Agente Reparador recuperou (cópias em `REPARADOS PELA CENTRAL`) e o que não tem conserto |

### ⚙️ Sistema
| Comando | O que faz |
|---|---|
| `/status` | Quais IAs estão funcionando agora |
| `/vigia` | O que o Agente Vigia está mantendo ligado (bot, leitura, reparos) |
| `/comandos` | Esta lista |

## Ligado o tempo todo
O **Agente Vigia** inicia junto com o Windows (atalho na pasta Inicializar). Ele liga o bot,
mantém a leitura do acervo andando, religa o que cair e avisa você no Telegram.
Não precisa abrir nada: é só mandar mensagem para o bot.

## No terminal (manutenção — feita pelo Claude Code/Copilot)

| Comando | O que faz |
|---|---|
| `python main.py` | Menu com números |
| `python main.py bot` | Liga o bot do Telegram (ou `BOT-TELEGRAM.bat`) |
| `python main.py diagnostico` | Situação de cada IA |
| `python main.py indexar [agente\|todos]` | Indexa a biblioteca dos agentes de conhecimento |
| `python main.py curador catalogar "E:\"` | Cataloga um disco (só lê) |
| `python main.py curador classificar "E:\" --quantidade 50` | Lê o conteúdo e sugere tema/subtema por pasta |
| `python main.py curador propostas "E:\"` | Mostra para onde cada pasta iria (não move) |
| `python main.py curador aplicar "E:\" --quantidade 3` | Move um lote, após aprovação |
| `python main.py curador desfazer` | Desfaz o último lote movido |
| `python main.py duplicados procurar "E:\" "F:\"` | Procura cópias idênticas (só lê) |
| `python main.py duplicados relatorio` | Relatório de duplicados |
| `python main.py duplicados separar "E:\"` | Separa cópias para `DUPLICADOS PARA REVISAR`, após aprovação |
| `python main.py integridade verificar "E:\"` | Procura arquivos corrompidos (só lê) |
| `python main.py reparar executar "E:\"` | Agente Reparador: conserta numa CÓPIA, aponta falsos alarmes e procura cópias sãs |
| `python main.py vigia` | Agente Vigia (liga sozinho com o Windows): mantém bot e leitura rodando e avisa falhas |
