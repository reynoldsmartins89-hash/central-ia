# Comandos da Central de IA

Guia de referência: **o que pedir ao seu secretário** e o que ele devolve.
Regra de ouro: **nada é publicado, movido ou apagado sem o seu ✅.**

## No Telegram (uso diário)

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

### ⚙️ Sistema
| Comando | O que faz |
|---|---|
| `/status` | Quais IAs estão funcionando agora |
| `/comandos` | Esta lista |

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
