---
name: direcao-de-arte
description: Planejar as imagens de cada canal (blog, Substack, LinkedIn, Instagram) a partir do artigo-mãe, com prompts de geração, termos de busca em banco de imagens e textos alternativos.
---
# Direção de arte

## Quantidade e formato por canal
| Canal | Imagens | Formato |
|---|---|---|
| Blog | 3 (1 capa + 2 internas) | 16:9 |
| Substack | 4 (1 capa + 3 internas) | 16:9 |
| LinkedIn | 1 | 1.91:1 |
| Instagram | 1 fundo por lâmina do carrossel (máx. 10) | 4:5 |

## Saída
1. Um breve conceito visual (paleta, estilo, sensação) para manter identidade entre canais.
2. Para o Instagram: o texto de cada lâmina (as imagens serão só o fundo; o texto é aplicado depois no Canva).
3. **Obrigatório, no final:** um bloco ```json com a lista de imagens, exatamente neste formato:

```json
[
  {"canal": "blog", "nome": "blog-capa", "formato": "16:9",
   "prompt_en": "detailed image generation prompt in English, no text in the image",
   "busca_en": "2-4 english keywords for stock photo search",
   "alt": "texto alternativo em português"}
]
```

## Regras
- Prompts em inglês, sem texto/letras dentro da imagem, sem marcas ou pessoas famosas.
- Coerência visual entre todas as imagens.
- `nome` em minúsculas-com-hífen e único.
