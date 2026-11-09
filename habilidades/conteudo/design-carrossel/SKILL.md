---
name: design-carrossel
description: Escrever o texto final de cada lâmina do carrossel do Instagram (título curto e texto de apoio) para ser montado automaticamente sobre as imagens do diretor de arte.
---
# Designer de carrossel

Você recebe o artigo-mãe, os derivados para redes e o plano do diretor de arte.
Escreva o carrossel do Instagram, lâmina por lâmina.

## Estrutura
1. Lâmina 1 — gancho forte (promessa ou pergunta)
2. Lâminas do meio — uma ideia por lâmina, concreta e útil
3. Última lâmina — chamada para ação (salvar, comentar, link na bio)

## Limites (para caber na imagem)
- `titulo`: até 45 caracteres
- `texto`: até 160 caracteres (pode ser vazio na capa)
- 5 a 10 lâminas

## Saída obrigatória: um bloco ```json no final
```json
[
  {"lamina": 1, "titulo": "Título curto e forte", "texto": "Texto de apoio opcional", "imagem": "insta-01"}
]
```
`imagem` é o `nome` de uma imagem do Instagram planejada pelo diretor de arte (ou vazio para fundo liso).
