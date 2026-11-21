# Regressão Bradesco OCR: colunas unidas e saldos entre páginas

## Causa confirmada

O mesmo resultado de OCR de um extrato sintético com seis páginas foi reproduzido
em três versões, sem refazer o OCR entre as comparações:

| Código | Resultado |
| --- | --- |
| `cd59fb4^`, antes da mudança de 20/10 | 102 registros; incluía indevidamente o saldo anterior como movimento |
| `cd59fb4`, PR #460 | Rejeição pelo novo leitor de colunas Bradesco |
| `02889b9`, main anterior à correção | Rejeição na página 2: valor sem documento associado |
| Esta correção | 101 movimentos, nas seis páginas, com cinco divergências de saldo sinalizadas |

O Textract reuniu o histórico e o número do documento em uma única LINE. As WORDs
continuavam disponíveis, inclusive com suas coordenadas nas colunas corretas.
O leitor exigia uma LINE exclusiva para o documento e interrompia a conversão.
Os testes anteriores de Bradesco usavam células separadas e não reproduziam essa
variação de agrupamento do OCR. Portanto, passar nesses testes não demonstrava
compatibilidade com o arquivo completo.

## Correção e limites

A separação reaproveita a política compartilhada de colunas baseada nas WORDs.
Ela exige evidência posicional de todos os filhos da LINE e atua abaixo do
cabeçalho da tabela. Um número no histórico não é suficiente para criar uma
referência. O resumo repetido acima da tabela não se transforma em total de
movimentações.

Quando um saldo não confere, o resultado conserva o valor impresso e registra a
divergência. A próxima comparação parte desse controle impresso. Isso evita
substituir saldos do documento por valores calculados e propagar uma única
diferença por todas as linhas seguintes. Os limites de abertura e fechamento
também permanecem os impressos.

No arquivo diagnosticado, as cinco divergências ocorrem no primeiro movimento
das páginas 2 a 6. A leitura preserva 101 movimentos e suas referências de origem;
isso não comprova a completude financeira do período. Abertura impressa: -441,66;
último saldo de movimento impresso: 18.001,40. Não se inventaram movimentos para
fechar as diferenças nem se usou o saldo disponível do cabeçalho como fechamento.

## Evidências e prevenção

- Novos testes reproduzem seis páginas, palavras unidas, histórico com número,
  cabeçalhos repetidos, saldo negativo, continuidade e divergência financeira.
- Casos negativos exigem rejeição quando faltam palavras, posições ou documento
  legível. As referências de página e linha são verificadas.
- Testes HTTP exercitam gratuito e PRO, exportações CSV/XLSX/OFX e a presença do
  aviso de divergência. O relatório informa onde conferir no PDF.
- TDD: três falhas iniciais; após a correção, 61 testes focados passaram.
- O corpus fixado de compatibilidade permanece intacto; os novos cenários são
  testes obrigatórios adicionais, sem relaxar comparações de referência.
- O arquivo real passou por HTTP local: `/convert` e relatórios com status 200,
  relatório inexistente com 404 e processamento PRO concluído com 101 movimentos.

O OCR foi autorizado e executado pelo Textract síncrono, uma imagem por página.
As respostas reais foram reutilizadas na conversão local, com OCR local
desativado. Isso valida o leitor e os fluxos HTTP; não é um teste do processamento
assíncrono ou da implantação em produção. PDF, respostas de OCR e exportações
ficaram fora do controle de versão. A implantação depende da integração da PR.
