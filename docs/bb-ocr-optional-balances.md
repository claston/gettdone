# Banco do Brasil: saldos opcionais e campos unidos pelo OCR

O leitor de lançamentos com sinais entre parênteses preserva movimentos legíveis
quando o saldo inicial ou o fechamento não está impresso. Datas, descrições,
valores, sinais e referências continuam sujeitos às verificações existentes.
Valores ilegíveis não são reconstruídos pela diferença entre saldos.

Sem saldo inicial, o primeiro saldo impresso serve apenas de referência para
conferir os movimentos seguintes. Essa referência não valida os movimentos
anteriores nem fornece um saldo inicial. O resultado mantém
`balance_bounds_unavailable=1`, `financial_controls_validated=0` e recomendação
de revisão. Os saldos globais permanecem indisponíveis na análise e no OFX;
não se infere um fechamento a partir do último movimento.

O aviso existente informa que os saldos ou controles necessários para conferir
o período não estão disponíveis e orienta a conferir os lançamentos e a
cobertura no PDF. Divergências entre controles posteriores continuam gerando
revisão financeira. Movimentos repetidos e referências às páginas são preservados.

No formato de colunas `Valor R$` e `Saldo`, o Textract pode unir dois valores em
uma única palavra, como `20,00D80,00C`. O leitor separa esse caso somente quando
há exatamente dois valores monetários com sinais C/D explícitos e a região
abrange as duas colunas financeiras. A região, a confiança e as referências do
OCR original são preservadas; não se inventam duas posições para a palavra.
Texto manuscrito, valores malformados, sinal ausente e regiões incompatíveis
continuam sendo recusados. O tratamento anterior de campos separados por
espaços permanece compatível.

## Validação

`backend/tests/test_bb_optional_balances_and_joined_money.py` cobre ausência dos
limites, conferência entre saldos posteriores, valores e datas ilegíveis,
movimentos repetidos entre páginas, campos unidos, geometria incompatível e
proveniência. Os testes HTTP executam upload, worker e downloads CSV/XLSX/OFX,
incluindo ausência de saldo global inventado e presença do aviso de revisão.

A reprodução privada reutilizou o OCR já capturado, sem novas chamadas pagas:

| Caso | Resultado |
| --- | --- |
| Trecho sem saldo inicial | 17 movimentos, revisão necessária, 6 conferências posteriores |
| Cinco páginas com três pares de campos unidos | 138 movimentos, 21 controles de saldo, sem divergência |
| PDF com texto nativo usado como controle | 97 movimentos, 20 controles de saldo, sem OCR |

Os documentos de clientes e suas leituras não fazem parte do repositório.
Não há migração, mudança de contrato HTTP, reenvio automático nem alteração de
resultados históricos. A publicação requer atualizar o backend e o worker de
conversões que executam esses leitores. O rollback consiste em retornar ao
commit anterior; os resultados já armazenados continuam compatíveis.
