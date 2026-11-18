# Resgate de HD — guia rápido

Ferramenta: `ferramentas/resgate_hd.py`. Atalho: `RESGATE-HD.bat`, que abre como Administrador.
Ela **só lê** o HD com defeito. Nada é gravado nele.

## Antes de ligar um HD com defeito
- **Barulho de clique, estalo, raspagem ou bipe** → desligue em até 10 segundos e não insista.
  Isso é dano mecânico (cabeça ou motor) e só um laboratório com sala limpa resolve.
  Cada nova tentativa pode riscar o disco e destruir os dados de vez.
- O Windows perguntou **"Você precisa formatar o disco"**? Clique em **Cancelar**. Sempre.
- **Não rode chkdsk**, não "repare" o disco e não abra pastas para conferir. Primeiro clone.
- HD de 3,5" (de mesa) precisa da fonte de energia própria ligada.

## Passo a passo
1. `listar` → descubra o número do HD (compare o nome e o tamanho).
2. `diagnosticar N` → lê o SMART, testa a leitura em 9 pontos do disco e dá o veredito.
3. `clonar N E:\resgate\nome.img` → cópia setor a setor para um disco saudável com espaço
   igual ou maior que o tamanho do HD. Ctrl+C pausa; o mesmo comando retoma de onde parou.

Para ler o SMART por USB, instale o smartmontools (opcional, mas ajuda):
`winget install smartmontools.smartmontools`

## HD não aparece em `listar`
Em ordem, do mais barato ao mais caro:
1. Troque o cabo e a porta USB (de preferência uma porta traseira do PC). Teste em outro PC.
2. HD de mesa: confira a fonte. Leve: teste um cabo em Y (2 USB) por falta de energia.
3. Muitas vezes o defeito está no **adaptador USB da caixinha**, e o disco está bom.
   Tire o disco da caixa e ligue num adaptador ou dock SATA-USB (custa ~R$ 50).
   ⚠ Exceção: em alguns WD My Passport/My Book, o USB fica soldado no disco ou os dados
   ficam criptografados pela própria caixinha. Nesse caso, não abra.
4. Gira, mas não aparece em lugar nenhum, ou aparece com 0 GB → firmware ou cabeças: laboratório.

## Depois da cópia (recuperar arquivos da imagem .img)
- **DMDE** (dmde.com): abre o .img, reconstrói pastas e nomes. É a melhor primeira tentativa.
- **TestDisk** (cgsecurity.org): reencontra partições perdidas.
- **PhotoRec** (vem junto com o TestDisk): recupera por tipo de arquivo (fotos, vídeos,
  documentos), mesmo com o sistema de arquivos destruído. Perde os nomes dos arquivos.
Copie os arquivos recuperados para um **terceiro** lugar, nunca de volta para o HD com defeito.
