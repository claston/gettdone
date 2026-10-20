# Santander: extrato consolidado digitalizado

O caso C24 contém dez páginas: anotações de conferência, capa, resumo,
movimentação da conta, detalhes de débitos automáticos, investimentos e
informações comerciais. A movimentação efetiva ocupa as páginas físicas
3 e 4. As demais seções não são novos lançamentos da conta corrente.

## Leitura das colunas

O caminho visual reconhece o Santander e o título EXTRATO CONSOLIDADO
INTELIGENTE. A tabela precisa apresentar DATA, DESCRIÇÃO, MOVIMENTOS,
CRÉDITOS, DÉBITOS e SALDO, com posições coerentes no mesmo cabeçalho.
Os cabeçalhos de tabelas posteriores não substituem essas coordenadas.

O sinal vem da coluna: a ausência de um pequeno traço de débito na leitura
OCR não transforma uma saída em entrada. A normalização de pontuação fica
restrita aos campos monetários, aceitando leituras como `79.90` e
`2.036.00`. Datas de competência na descrição de IOF não alteram a data
do movimento. As referências preservam páginas e regiões do documento.

A leitura exige o saldo anterior e o saldo final da conta corrente,
os totais mensais de créditos e débitos e o fechamento de cada dia com
movimentos. Saldo disponível, limite e posição em investimentos não são
usados como saldo da conta. Detalhes de débito automático e posições de
investimento não são duplicados como transações.

## Recuperação restrita e revisão

Este caso possui dois problemas de leitura: o valor de um IOF não aparece
no OCR e um crédito perde o primeiro dígito. A recuperação exige duas
conferências independentes: o déficit no total mensal daquela coluna e a
diferença do fechamento diário precisam indicar exatamente o mesmo valor.

Um IOF sem valor só pode ser recuperado quando sua linha tem descrição e
data identificadas e é o único movimento sem valor no documento. Um
crédito só admite a recuperação de um único dígito inicial, preservando
todos os dígitos legíveis e os centavos, em um campo com baixa confiança de
OCR. Se houver dois candidatos, controles ausentes ou divergentes, a
conversão é recusada. Outros reparos numéricos não são permitidos.

Anotações fora da linha não fornecem valores nem viram lançamentos. Uma
marca monetária só pode ser excluída quando coincide com um crédito já
recuperado independentemente, na mesma coluna e imediatamente abaixo da
linha impressa. Marcas financeiras não explicadas impedem a conversão.
Essa regra também trata anotações que o OCR classificou como texto impresso.
Valores de campos identificados como manuscritos não são aceitos.

Os dois movimentos recuperados recebem `ocr_amount_recovered_from_summary`,
exibido como **Valor recuperado**, e recomendação de conferir o original
antes de exportar. As evidências incluem resumo mensal, movimentos e
fechamento diário usados na conferência; não se inventa uma região OCR
para um valor que não foi lido. O original permanece intacto.

## Resultado e validação

C24 passa de 14 para 40 movimentos. Créditos e débitos somam, separadamente,
R$ 192.998,71. O saldo inicial, o final e o líquido são zero. As seis
inconsistências anteriores dão lugar aos dois avisos explícitos de valores
recuperados, sem inconsistências de saldo.

A validação usa o PDF original com a resposta OCR salva, nos modos `text`
e `analysis`, incluindo segmentação, classificação e conversão. Não há
novas chamadas AWS nessa repetição. Os outros 26 casos conservaram status,
transações completas e saldos. Testes versionados usam apenas dados
sintéticos, incluindo HTTP real, API pública, worker e CSV/XLSX/OFX.

Não há migração, alteração de contratos públicos ou nova configuração de
OCR. O caminho textual nativo mantém seu parser; a resolução de abertura
desse layout passa a preferir o saldo de conta corrente do mês anterior
quando ele está explicitamente identificado no resumo mensal. Análises
antigas precisam de reprocessamento. Rollback: reverter o commit e
republicar API e worker.
