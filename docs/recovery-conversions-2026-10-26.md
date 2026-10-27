# Correções das conversões auditadas em 26/10/2026

A auditoria usou somente capturas de recuperação do S3, logs do worker e leitura
dos PDFs originais. Não houve acesso ao banco de produção nem ao Render.
O recorte foi encerrado às 09:23:36 de São Paulo: 15 capturas, correspondentes
a 12 documentos distintos. Os documentos e as respostas OCR ficam em `local/`,
ignorado pelo Git; os testes versionados usam documentos fictícios.

## Resultado da repetição dos documentos

| Caso | Evidência anterior | Resultado com a correção |
| --- | --- | --- |
| Caixa, extrato nativo com linhas quebradas A | 35 movimentos, 2 conflitos de saldo | 38 movimentos, 37 verificações de saldo sem conflito |
| Caixa, extrato nativo com linhas quebradas B | 19 movimentos, 1 conflito de saldo | 22 movimentos, 21 verificações de saldo sem conflito |
| Caixa, resumo diário | 7 movimentos, 29 candidatos e confiança baixa | Mesmos 7 movimentos; apenas 7 candidatos; confiança média |
| Caixa, seis páginas vetoriais com cabeçalho nativo | Falha sem OCR | 60 movimentos; 81 controles impressos validados; nenhum conflito |
| Caixa pessoal sem movimentação | Falha de reconhecimento | 0 movimentos; saldo do período preservado; agência e conta extraídas do campo completo |
| Sicredi, quatro páginas OCR | Falha na validação financeira | 150 movimentos e 150 saldos impressos conferidos; revisão mantida em 2 linhas |
| Sicredi, 56 páginas nativas | Falso aviso de extrato incompleto | Mesmos 2.609 movimentos, descrições e ordem; paginação validada sem falso aviso |
| Santander, Inter, Sicoob e dois documentos Banrisul | Conversões com revisão | Movimentos, descrições e ordem preservados; revisão mantida |

Nas duas amostras Caixa com quebras de linha, recuperar as tarifas também
restaura o crédito posterior originalmente impresso, que antes era compensado
pelo saldo após a omissão. Os testes conferem os valores, não apenas a ausência
de avisos.

O Sicredi OCR mantém dígitos, sinais, coordenadas e referências da evidência
original ao normalizar separadores e separar células de valor/saldo mescladas.
Um possível terceiro dígito de centavos só é descartado quando os dois saldos
independentes confirmam exatamente os centavos observados; a linha continua
sinalizada para revisão. Evidências monetárias manuscritas são rejeitadas.
Não se retorna ao leitor antigo, que produzia 155 linhas e conflitos de saldo.

O modelo pessoal Caixa exige declaração explícita de ausência de lançamentos,
identificação completa da conta, período válido e estrutura conhecida. Usa o
saldo desse período, pois o saldo atual exibido no topo pode ser de outra data.
Um documento desconhecido, ativo ou com valores não explicados não vira extrato
vazio.

## Limites da validação

As 12 entradas foram repetidas localmente. Sete conversões anteriormente bem
sucedidas preservam os movimentos; duas recuperam movimentos omitidos; as três
falhas passam a ter resultados verificáveis. A comparação histórica de quatro
versões mostrou que parte do aumento de revisões veio da regra de qualidade
`2026-10-23.v2`, que passou a considerar a confiança média/baixa do parser.
Essa regra permanece igual: 10 documentos continuam em revisão e 2 são limpos.
Remover um falso aviso de paginação não torna automaticamente o extrato limpo.

Para as quatro páginas Sicredi, foi reutilizada a evidência OCR original.
Para as seis páginas Caixa, `StartDocumentTextDetection` não está autorizado no
perfil disponível. A validação usou `DetectDocumentText`, autorizado, uma vez por
página renderizada. Os blocos reais foram então repetidos pelo caminho público
de leitura do PDF, classificação, mapeamento OCR e pipeline de conversão, com
somente o gateway remoto substituído por essas respostas em cache. Isso valida
o parsing e o encaminhamento; não é um teste da submissão assíncrona em produção.
Os limites de páginas são verificados antes de chamar OCR, inclusive em PDFs
mistos, imagens inline e páginas vetoriais sem texto útil.

## Compatibilidade e implantação

Não há migração, mudança de esquema, alteração de contrato HTTP ou reprocessamento
automático de históricos. Nenhuma imagem ou configuração de produção foi
promovida. A mudança deve chegar ao worker e ao serviço de conversão na próxima
implantação autorizada; a imagem candidata de infraestrutura é um trabalho
separado.

O risco é médio, por alterar parsing financeiro e permitir OCR onde antes o
cabeçalho nativo impedia a leitura. Pode aumentar o uso de OCR nesses documentos,
respeitando os limites existentes. Após uma implantação autorizada, verificar
falhas, conflitos de saldo, revisões e consumo de OCR por modelo. Para rollback,
restaurar as versões imutáveis anteriores dos serviços afetados. Sem alterações
de banco, não há rollback de esquema. Resultados já gerados pela versão nova
permanecem armazenados e precisam de avaliação explícita antes de reprocessar.

## Validação reproduzível

Os testes de `test_oct26_recovery.py` reproduzem os defeitos com dados fictícios
e incluem rejeição de casos ambíguos. `test_oct26_recovery_http.py` inicia um
servidor local real, converte CSV e PDFs, consulta os relatórios e baixa OFX,
conferindo movimentos, sinais, conta e saldo. O fluxo atual usa `POST /convert`;
`POST /analyze` permanece desativado (`405`), e relatório inexistente retorna
`404`. A lógica de conciliação não mudou.

```powershell
backend/venv/Scripts/python.exe -m pytest backend/tests -q -n 4 --dist=worksteal --tb=short --basetemp "$env:LOCALAPPDATA/Temp/gettdone-pytest-oct26-full"
backend/venv/Scripts/python.exe -m ruff check backend
backend/venv/Scripts/python.exe scripts/lint_frontend_text.py
backend/venv/Scripts/python.exe scripts/lint_frontend_navigation.py
npm --prefix pro run lint
```

No Windows, executar pytest fora do sandbox e remover o diretório temporário
exato após validar seu caminho, conforme `AGENTS.md`.
