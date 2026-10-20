# PDFs com imagem legível e texto invisível incorreto

Um PDF pode conter uma imagem da página e uma camada de texto invisível
produzida anteriormente por OCR. A presença de texto extraível não prova
que essa camada corresponde corretamente à imagem. O caso Ailos/Civia C19
tem datas como `©3/08/2026` e números como `57.458, 26` nessa camada,
embora a imagem mostre datas e valores legíveis.

## Decisão de leitura

O conversor avalia datas malformadas e fragmentação de casas decimais.
Quando encontra esses sinais, confere se o PDF realmente pinta uma imagem
que cobre a página e usa texto invisível. Texto visível ou um arquivo sem
essa imagem não é classificado automaticamente como OCR embutido ruim.
Uma camada invisível saudável pode continuar no caminho textual.

O classificador passa a `pdf_representation_v2`. Mantém as categorias e os
campos públicos existentes, acrescentando evidências como
`invisible_text_layer_present` e `embedded_text_quality_low`. Um arquivo
nessa situação fica `mixed`, com origem `unknown` quando não há evidência
de criação direta, e `requires_visual_extraction=true`. Registros antigos
com versão v1 continuam válidos; não há migração.

Na leitura visual, uma cópia temporária preserva as imagens, as dimensões,
as rotações e as coordenadas das páginas, excluindo os operadores de texto
invisível das páginas com imagem. Isso impede que a nova extração reutilize
a camada defeituosa. O original permanece intacto. A evidência mantém o
hash do documento original e registra separadamente o hash da cópia usada
no OCR.

O serviço Textract ou o adaptador local configurado recebe essa cópia.
Limites de páginas continuam sendo aplicados ao documento real. Se a
leitura visual estiver indisponível ou falhar financeiramente, o fluxo não
retorna ao texto já considerado não confiável. Uma extração visual sem
controles financeiros independentes mantém aviso de revisão manual e
recomendação `review_recommended`.

## Ailos/Civia

O parser visual reconhece o emissor Ailos/Civia, Banco 085, e as colunas
DATA, DESCRIÇÃO, DOCUMENTO, CRÉDITO, DÉBITO e SALDO. Exige datas válidas e
ordenadas dentro do período, correspondência única dos campos, saldo
anterior, todos os saldos por transação e os totais de entradas e saídas.
Valores ilegíveis, campos financeiros duplicados ou manuscritos, datas
incertas e controles inconsistentes não são preenchidos por diferenças
de saldo e não caem silenciosamente no parser genérico.

Uma nova leitura das imagens das duas páginas C19 produziu 60 movimentos,
antes 47. O controle impresso fecha exatamente:

`57.458,26 + 101.567,92 − 103.289,05 = 55.737,13`.

O líquido é −R$ 1.721,13 e as inconsistências de saldo caem de 12 para zero.
A nova evidência visual foi salva localmente para repetir a validação sem
novas chamadas AWS. PDFs e respostas de clientes ficam fora do Git; os
testes versionados usam apenas documentos sintéticos. A comparação com os
27 casos preservou as linhas e os saldos dos outros 26 documentos.

## Operação e rollback

Usa os serviços e flags OCR já existentes. PDFs com uma camada ruim podem
agora consumir OCR mesmo tendo texto selecionável; os limites existentes
continuam valendo. Nenhuma alteração de schema ou infraestrutura é
necessária. Resultados antigos precisam de reprocessamento para receber
a correção. Rollback: reverter o commit e republicar API e worker; versões
v2 já registradas continuam compatíveis com os campos existentes.
