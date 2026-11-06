# Extratos em imagem com cabeçalho de impressão

## Problema e comportamento corrigido

Os registros de produção `an_8b194c1310ba` e `an_385d39136e7e`, de
6 de novembro de 2026, são duas tentativas do mesmo PDF. A página contém
uma fotografia de um extrato mensal Bradesco, mas somente os 137 caracteres
do cabeçalho e do rodapé de impressão são selecionáveis. O classificador v6
indicava texto nativo e dispensava leitura visual; o resultado era
`unsupported_table_layout`.

A política compartilhada de qualidade agora verifica a área efetivamente
ocupada pela imagem e a presença de texto nativo dentro dela. Uma imagem que
ocupa pelo menos 20% da área da página, descontadas as faixas de impressão
superior e inferior de 8%, requer OCR quando não há texto nessa região.
A inspeção considera imagens desenhadas dentro de Form XObjects e suas
transformações. Logotipos pequenos, recursos de imagem não utilizados,
imagens fora da página e imagens com texto nativo na região mantêm o caminho
anterior. Essa regra é compartilhada entre bancos e relatórios; não depende
de URL, nome do arquivo ou identidade do cliente.

O classificador v7 registra `mixed`, `document_raster_present` e
`requires_visual_extraction=true`. O processamento usa o gateway OCR
configurado e conserva os limites de páginas, a identificação do PDF
original e a proveniência dos movimentos. Se nenhum OCR estiver habilitado,
a necessidade de leitura visual é informada como ambiguidade de extração.

## Conferência do caso real

Uma leitura real com Textract reconheceu o formato já implementado
`bradesco_extrato_mensal_dia_historico_valor_ocr_v1`. A resposta real do OCR
foi reaplicada localmente ao pipeline corrigido, sem criar uma análise
nova nem modificar resultados em produção. Os PDFs e a resposta do OCR
permanecem fora do Git; os testes usam somente dados sintéticos.

Foram recuperados os seis movimentos visíveis, com datas, valores, sinais,
referências e páginas de origem. Os saldos impressos não conciliam com
todos os movimentos visíveis. O leitor passa a usar a política compartilhada
de revisão para essa divergência exclusivamente aritmética:

- mantém os movimentos e os saldos efetivamente impressos;
- confere o intervalo seguinte a partir do próximo controle impresso;
- registra `financial_validation_failed`, `review_required` e
  `financial_controls_validated=0`;
- não cria uma abertura, não insere movimentos compensatórios e não afirma
  que o saldo global foi validado.

O preview conserva a abertura impressa, inclusive quando é zero, em vez de
recalculá-la para fazer os movimentos fecharem. O OFX mantém a política
existente de omitir o saldo global quando a validação financeira falha.

Datas, sinais, valores ou associações de linhas ilegíveis continuam sujeitos
aos bloqueios de extração existentes. A alteração não relaxa esses controles.

## Compatibilidade e implantação

Não há migration, alteração de schema, nova dependência ou mudança de contrato
HTTP. API e worker devem receber a mesma versão. Os resultados já armazenados
não são reescritos. Novas conversões desses PDFs podem consumir OCR, respeitando
as configurações e os limites existentes.

Rollback: reverter a correção e republicar API e worker. Não há dados a migrar
nem a desfazer. A publicação em produção não faz parte desta validação local.

## Validação

As regressões cobrem detecção da imagem, texto de impressão acima do limite
antigo, páginas rotacionadas, Form XObjects, escala e translação, texto nativo legível, logotipos,
recursos não desenhados, OCR habilitado/desabilitado, páginas posteriores,
limite de OCR, saldos divergentes, datas, sinais e proveniência.

Os testes HTTP iniciam a aplicação localmente e exercitam upload PDF/CSV,
preview, relatórios CSV/XLSX/OFX, saldos impressos e IDs inexistentes. A rota
de upload vigente é `POST /convert`; `POST /analyze` permanece desabilitada
com HTTP 405. Os resultados finais de suíte e lint constam no PR.
