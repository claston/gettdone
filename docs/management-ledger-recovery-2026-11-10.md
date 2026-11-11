# Relatórios gerenciais com situação, categoria e saldo

O leitor compartilhado reconhece a estrutura `Extrato de … / Situação / Data /
Cliente ou Fornecedor / Documento / Categoria / Valor / Saldo`, sem selecionar
um leitor bancário pelo nome da conta ou das contrapartes.

Cada movimento preserva data, valor com sinal, saldo impresso, situação,
contraparte, documento, categoria e referências às páginas, linhas e regiões
originais. Os campos impressos também seguem no contrato `financial_events_v1`
e nos downloads CSV/Excel. Os saldos anteriores são observações, não movimentos.

## Blocos, controles e significado

- Cada período/conta mantém seus controles; paginações reiniciadas não eliminam
  movimentos repetidos legítimos. A continuidade é verificada por conta.
- Lacunas, sobreposições de períodos, cobertura não confirmada, saldo anterior
  ausente e diferenças aritméticas conservam os movimentos legíveis com revisão.
- Data/ano, valor, sinal ou associação ilegíveis impedem apresentar uma extração
  parcial como completa. Continuação sem cabeçalho preserva as linhas legíveis
  quando o contexto anterior é inequívoco e sinaliza cobertura não confirmada.
- Situações diferentes de `Conciliado` ficam preservadas sem inventar efeito de
  caixa confirmado e exigem revisão.
- Cartões e administradoras têm CSV/Excel. OFX exige conta corrente explícita,
  agência/conta legíveis, uma única identidade, movimentos conciliados e todos
  os controles aprovados. A conta de destino deve ser informada. O adaptador
  existente exporta movimentos sem inventar um saldo global.

## Texto nativo e OCR

O mesmo leitor de linhas visuais e colunas atende texto nativo e OCR. No PDF
nativo, a leitura preserva opcionalmente os objetos de texto: nomes longos podem
usar uma coluna vazia ou sobrepor uma referência sem que os caracteres dos dois
campos sejam misturados. Essa opção é exclusiva deste leitor; os demais leitores
mantêm o comportamento padrão. OCR sem evidência suficiente para separar campos
sobrepostos deve recusar a associação ambígua.

## Evidência de validação

Quatro documentos privados da auditoria foram reproduzidos offline: 32, 290,
67 e 179 movimentos, totalizando 568. A leitura independente dos objetos via
PyPDF concorda com os campos, datas, valores, sinais e páginas obtidos por
PDFium. Todos os saldos sequenciais conferem. Os originais e os resultados com
dados pessoais permanecem exclusivamente na pasta local autorizada `.tmp`.

Os testes versionados usam dados fictícios e cobrem representações nativa/OCR,
objetos sobrepostos, colunas vazias, campos quebrados, múltiplos períodos/contas,
repetições legítimas, paginação, continuidade, cobertura, ambiguidade e HTTP real
com exportações CSV/Excel/OFX. A validação OCR usa extrações simuladas; não houve
chamada ao serviço de OCR remoto.

## Compatibilidade e implantação

Sem migração de banco, alteração de API ou dependência adicional. Resultados
persistidos não são reescritos. Uma nova conversão após implantação aplica o
leitor. Rollback: reverter este lote e publicar a versão anterior, sem operação
de banco. Os casos BB sem atividade e os 34 casos inconclusivos não fazem parte
deste lote.
