# PDF canônico de layout v2

O v2 preserva sinais estruturais suficientes para revisar um extrato cujo layout não foi reconhecido. Ele é um artefato de diagnóstico, não um novo caminho de conversão. O PDF e o `layout.json` continuam armazenados no bucket privado de conversões, sob `conversion/canonical-layouts/candidates/v2` por padrão.

## Conteúdo

- Até as cinco primeiras páginas, com tamanho da página e posição de cada linha. Quando o parser usou Textract, as posições são as caixas reais retornadas pelo serviço; caso contrário, são estimadas a partir do texto extraído.
- Títulos e nomes de colunas de uma lista explícita de termos públicos, como `PERIODO DO EXTRATO`, `DATA MOV`, `HISTORICO`, `VALOR` e `SALDO`.
- Valores privados substituídos por marcadores fixos: `[TEXTO]`, `[NUMERO]`, `[DATA]`, `[HORA]`, `[VALOR]`, `[VALOR_C]` e `[VALOR_D]`. O PDF canônico não contém dígitos; o JSON pode conter o código público do banco e contagens de páginas/linhas.
- `layout_signals` com rótulos encontrados, origem das posições e contagens de formatos de linhas. `layout_match` lista até três perfis possivelmente compatíveis; `existing_candidate`, `ambiguous`, `new_candidate` e `insufficient_signals` são apenas sugestões para revisão.
- `source_page_count` e `sampled_page_count` indicam se o documento foi amostrado.

O nome do banco só aparece se for encontrado no catálogo público. A validação de privacidade lê novamente o PDF gerado, verifica o vocabulário permitido e rejeita dígitos, imagens, anexos, anotações e conteúdo ativo antes do armazenamento.

## OCR e ativação

Ative `CANONICAL_LAYOUT_CAPTURE_ENABLED=true` e `CANONICAL_LAYOUT_V2_ENABLED=true`. Para capturar também PDFs cujo parser falhou, ative `CANONICAL_LAYOUT_FAILURE_CAPTURE_ENABLED=true`. O v1 continua sendo o padrão enquanto a nova flag estiver desligada. Com `TEXTRACT_ENABLED=true`, o v2 reaproveita texto e coordenadas de uma conversão que já passou pelo Textract. Se uma captura de falha não tiver texto extraído, envia **somente as cinco primeiras páginas** ao Textract em modo texto. O v2 não chama o OCR local nesse caso. Se o Textract estiver indisponível, a captura é ignorada sem impedir a conversão.

Para voltar ao v1, desative `CANONICAL_LAYOUT_V2_ENABLED`. Os artefatos v1 e v2 ficam em prefixos separados. Não há migração de banco de dados nem reprocessamento de documentos antigos.

## Revisão de um layout

1. Confira `bank`, `source_page_count`, `sampled_page_count` e `text_source` no `layout.json`.
2. Compare os rótulos e as posições do PDF com os perfis sugeridos em `layout_match`.
3. Se os títulos, as colunas ou a ordem das linhas divergirem, use esses sinais para procurar um exemplar público e criar um perfil novo. `new_candidate` não prova, por si só, que o layout é novo.

Os artefatos v1 já gerados não podem ser enriquecidos com os dados que foram descartados na anonimização. Para avaliar um caso antigo, é necessária uma nova conversão do documento pelo cliente.
