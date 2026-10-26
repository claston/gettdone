# Correções complementares de conversão — 26/10/2026

O recorte termina às 14:21:06 de São Paulo: 79 capturas do bucket de recuperação,
correspondentes a 66 PDFs distintos. Inclui as 12 entradas da primeira auditoria,
37 documentos novos da segunda e mais 17 da atualização. A comparação é feita
por SHA-256, para que reenvios não aumentem a contagem de documentos.

Foram usados PDFs originais, evidências OCR e logs da AWS em leitura. Não houve
acesso ao banco de PRD ou ao Render, implantação, promoção de imagem ou
reprocessamento remoto. Os arquivos de clientes ficam somente em `local/`,
ignorado pelo Git. Os testes versionados usam dados fictícios.

## Resultado em relação à correção inicial (#490)

| Modelo/caso | Resultado anterior | Resultado corrigido |
| --- | --- | --- |
| NDD Conta Digital | 2 movimentos | 9; quantidade, totais e saldos diários conferidos |
| BTG PJ, dois arquivos | 1 linha indevida / 7 movimentos | 2 / 13 movimentos; a divergência financeira do segundo continua sinalizada |
| BB Autoatendimento, três nativos | 39 / 76 / 62 movimentos | 38 / 75 / 61; saldo final deixa de ser movimento e sinais C/D são associados à coluna correta |
| BB Autoatendimento OCR | Falha | 70 movimentos; valores e sinais separados/mesclados conferidos |
| BB com cheque por imagem bloqueado | Falha | 116 movimentos de caixa; depósito pendente separado e aviso mantido |
| Banco do Nordeste OCR | 131 movimentos, classificado como BB | 119 movimentos, modelo correto; saldos conferidos e sinais recuperados identificados |
| Caixa web, saldos em linhas separadas | 3 movimentos | 9 movimentos das três páginas; avisos sobre saldos iniciais conflitantes e separador OCR mantidos |
| Caixa Gerenciador, colunas Valor/Saldo | 78 movimentos | 70 movimentos; linhas de saldo diário não são exportadas como dinheiro movimentado |
| Banrisul Portal, dois arquivos | Falha | 30 / 142 movimentos; no segundo, três agendamentos ficam separados, com aviso |
| Uniprime, dois arquivos | Falha | 152 / 146 movimentos; datas/centavos cortados são recuperados com evidência independente e revisão explícita |
| InfinitePay nativo, dois arquivos | Falha | 40 / 65 movimentos; resumo e saldos diários conferidos sem OCR remoto |
| Revolut BRL | Formato não reconhecido | 32 movimentos; colunas de débito/crédito, saldos e resumo conferidos |
| Stone sem atividade | Falha | 0 movimentos, somente com declaração explícita e estrutura conhecida |

A contagem Caixa web corrige a leitura preliminar que considerou apenas a
primeira página: o documento completo contém nove movimentos, não quatro.

O conjunto final tem 10 documentos recuperados de falha, nove com movimentos
corrigidos e 45 que preservam valores, datas, descrições e ordem. Dois continuam
recusados: um trecho Sicredi sem as páginas iniciais e um CSV convertido em PDF
que apresenta cinco cabeçalhos, mas somente três campos por movimento. Não há
evidência suficiente para inventar valores ou completar esses documentos.

## Evidência e critérios de revisão

Os novos leitores nativos usam os glifos e as coordenadas reais do PDF. Nos
modelos tratados, cada célula financeira precisa pertencer a um movimento ou a
um controle reconhecido. Células sem data, valores duplicados, sinais
contraditórios, período inválido e dígitos incompatíveis impedem a validação.
Referências, descrições continuadas e evidências entre páginas são preservadas.

No Uniprime, um ano cortado só é completado quando existe exatamente um ano
compatível com o período explicitamente impresso e o prefixo visível. Centavos
cortados só são recuperados quando a diferença de dois saldos impressos
preserva todos os dígitos observados e qualquer sinal legível. Ambas as
recuperações continuam indicadas para revisão. A data de emissão não substitui
o período do extrato.

No Nordeste, um sinal ausente exige dois saldos independentes e a magnitude
integralmente legível. Um sinal de débito repetido pelo OCR só é normalizado
quando os sinais concordam e a conta fecha sem alterar dígitos. As duas situações
ficam identificadas. Os controles e as posições de conta após o detalhamento
não são movimentos.

O Caixa web preserva os dois saldos iniciais conflitantes como evidência e
mantém o aviso correspondente; não cria ajuste para compensar a diferença.
Quando o saldo de um movimento aparece na página seguinte, a linha mantém
referências e coordenadas das duas páginas. O novo motivo e a quantidade de
agendamentos separados atravessam o pipeline e o contrato HTTP por campos
opcionais com valores padrão.

Continuam em revisão os extratos BTG e Bradesco cujos totais/saldos impressos
divergem, além das recuperações identificadas. Um trecho Sicoob sem cabeçalho
do período continua usando o leitor genérico e permanece em revisão. Não foram
alterados os movimentos dos casos em que a diferença era apenas a regra de
confiança. A #491 trata essa regra e o aviso genérico do conversor gratuito
separadamente; esta mudança acrescenta explicações para problemas concretos.

## Validação

`test_followup_conversion_recovery.py` cobre os novos formatos, omissões,
ambiguidade e controles negativos. `test_followup_conversion_http.py` inicia o
servidor real e verifica `POST /convert`, `GET /report/{id}` (XLSX), exportação
OFX, quantidades, sinais e relatório inexistente (`404`). Os testes HTTP já
existentes também cobrem CSV e `POST /analyze` desativado (`405`).
`html-followup-warnings.test.js` verifica as explicações apresentadas no revisor.
A conciliação não foi alterada.

Na execução completa final, **3.046 testes backend passaram**, com dois skips,
dois xfails e um timeout HTTP no teste Inter em execução paralela. Esse arquivo
foi reexecutado isoladamente, sem alterar limites ou implementação.
**203 testes frontend passaram**. Ruff, ESLint, TypeScript, sintaxe JS,
UTF-8/mojibake, navegação em Chromium e os **13 casos de compatibilidade com a
base da PR** passaram. Os **52 testes focados** de recuperação e HTTP passaram,
incluindo a conta BTG com zeros à esquerda no relatório e no OFX.

```powershell
backend/venv/Scripts/python.exe -m pytest backend/tests -q -n 4 --dist=worksteal --tb=short --basetemp "$env:LOCALAPPDATA/Temp/gettdone-pytest-followup-account-final"
backend/venv/Scripts/python.exe -m ruff check backend
backend/venv/Scripts/python.exe scripts/lint_frontend_text.py
backend/venv/Scripts/python.exe scripts/lint_frontend_navigation.py
node --check frontend/ofx-convert.js
npm --prefix pro run test:run
npm --prefix pro run lint
npm --prefix pro run typecheck
```

No Windows, executar pytest fora do sandbox e remover somente o diretório
temporário exato, após verificar seu caminho, conforme `AGENTS.md`.

## Compatibilidade e rollback

Não há migração, mudança de armazenamento, novos recursos AWS ou alteração
de permissões. Os dois campos novos de diagnóstico são aditivos. Resultados
históricos permanecem como foram gerados; esta mudança vale para novas
conversões após uma implantação autorizada.

O risco é médio por alterar leitura financeira em vários modelos. As rotas
exigem estruturas conhecidas e controles independentes; os avisos de
recuperação e divergência continuam visíveis. Depois de uma implantação,
acompanhar falhas, cobertura de movimentos e inconsistências por modelo.
O uso de OCR não aumenta para os novos modelos nativos.

Para rollback, restaurar as versões imutáveis anteriores do worker e do serviço
de conversão e os assets anteriores do frontend. Não há rollback de banco.
Resultados já gerados precisam de avaliação explícita antes de eventual
reprocessamento. A promoção da imagem de infraestrutura permanece separada.

A PR está empilhada sobre a branch da #490. Ao integrar também a #491,
preservar a remoção do aviso genérico daquela PR e o cache do script `v=18`
das três páginas públicas desta mudança.
