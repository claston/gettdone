# Backlog V2 - Conciliacao Leve Como Servico

## Objetivo da Fase 2

- Produto: Conciliacao leve como servico
- Promessa: "Envie seu extrato + sua planilha e veja o que bate e o que esta errado"

## Escopo V2 (limitado)

Para reduzir complexidade nesta fase:

- 1 extrato por analise
- 1 planilha operacional simples por analise
- matching por:
  - valor
  - data (com tolerancia)
  - descricao (basico)

Fora de escopo nesta fase:

- multiplas contas
- CNAB
- integracoes
- regras fiscais complexas

---

## Prioridades (visao rapida)

- `P0` = obrigatorio para validar com contador
- `P1` = recomendado para evolucao da V2
- `P2` = pode adiar sem bloquear

---

## Backlog Priorizado

## EPIC 1 - INPUT (entrada de dados)

1. `P0` Upload duplo (`extrato` + `planilha`)
- Estimativa: 4h
- Dependencias: V1 `POST /analyze` estavel
- Pronto quando: endpoint recebe ambos os arquivos e valida formato

2. `P0` Parser da planilha operacional (`CSV`/`XLSX`)
- Estimativa: 6h
- Dependencias: item 1
- Pronto quando: linhas da planilha entram no mesmo schema base da conciliacao

3. `P0` Deteccao automatica de colunas (`data`, `valor`, `descricao`)
- Estimativa: 4h
- Dependencias: item 2
- Pronto quando: aliases comuns sao reconhecidos sem configuracao manual

4. `P0` Normalizacao unica dos dois lados
- Estimativa: 5h
- Dependencias: item 3
- Pronto quando: extrato e planilha ficam comparaveis (data, sinal, descricao)

5. `P1` Ajuste manual de mapeamento de colunas
- Estimativa: 4h
- Dependencias: item 3
- Pronto quando: usuario consegue corrigir mapeamento ambigguo

## EPIC 2 - MOTOR DE MATCHING (core)

6. `P0` Match exato (valor igual + mesma data)
- Estimativa: 4h
- Dependencias: item 4
- Pronto quando: pares exatos sao conciliados com motivo explicito

7. `P0` Match com tolerancia (valor igual + data +/- 2 dias)
- Estimativa: 4h
- Dependencias: item 6
- Pronto quando: pares com pequena variacao de data sao conciliados

8. `P0` Match por aproximacao de descricao
- Estimativa: 5h
- Dependencias: item 6
- Pronto quando: valor igual + similaridade textual basica gera match confiavel

9. `P1` Matching 1:N leve (varios itens -> 1 lancamento)
- Estimativa: 6h
- Dependencias: itens 6, 7, 8
- Pronto quando: casos simples de consolidacao sao detectados sem ambiguidade alta

## EPIC 3 - CLASSIFICACAO DE RESULTADOS

10. `P0` Status por item: `conciliado`, `pendente`, `divergente`
- Estimativa: 4h
- Dependencias: itens 6, 7, 8
- Pronto quando: todo item recebe status final e motivo

11. `P0` Regras de divergencia
- Estimativa: 3h
- Dependencias: item 10
- Pronto quando: valor diferente ou data fora da janela entra como divergente

12. `P0` Pendencias dos dois lados
- Estimativa: 3h
- Dependencias: item 10
- Pronto quando: itens sem par no extrato/planilha sao listados corretamente

## EPIC 4 - DETECCAO DE PROBLEMAS E INSIGHTS

13. `P0` Regras de problemas operacionais
- Estimativa: 4h
- Dependencias: itens 10, 11, 12
- Pronto quando: sistema sinaliza:
  - pagamento nao encontrado no banco
  - recebimento nao registrado
  - valor divergente
  - possivel duplicidade

14. `P0` Geracao de insights de conciliacao
- Estimativa: 3h
- Dependencias: item 13
- Pronto quando: resposta inclui mensagens objetivas de pendencia/divergencia

15. `P1` Criticidade de problemas (alto/medio/baixo)
- Estimativa: 3h
- Dependencias: item 13
- Pronto quando: problemas ja saem ordenados por impacto

## EPIC 5 - OUTPUT (resultado)

16. `P0` Resumo geral de conciliacao
- Estimativa: 3h
- Dependencias: itens 10, 11, 12
- Pronto quando: total conciliado, pendente e divergente sao exibidos

17. `P0` Tabela detalhada com status e correspondencia
- Estimativa: 4h
- Dependencias: item 16
- Pronto quando: cada linha mostra status, motivo e par encontrado (quando houver)

18. `P0` Exportacao `CSV`/`XLSX` do resultado de conciliacao
- Estimativa: 4h
- Dependencias: item 17
- Pronto quando: arquivo exportado traz status e referencia cruzada

19. `P1` Aba especifica de "Problemas" no relatorio
- Estimativa: 2h
- Dependencias: item 18
- Pronto quando: pendencias e divergencias ficam destacadas em aba dedicada

## EPIC 6 - UX para venda e feedback

20. `P0` Preview com KPIs de conciliacao
- Estimativa: 4h
- Dependencias: item 16
- Pronto quando: usuario ve rapidamente o percentual conciliado

21. `P0` Bloco de problemas em destaque
- Estimativa: 3h
- Dependencias: item 14
- Pronto quando: tela prioriza o que esta errado antes dos detalhes

22. `P0` Narrativa antes/depois da conciliacao
- Estimativa: 3h
- Dependencias: itens 20, 21
- Pronto quando: fica claro o ganho operacional apos processamento
- Backend expõe um bloco estruturado com os dados da narrativa antes/depois para alimentar o front
- Backend expõe os dados que sustentam o bloco de problemas em destaque e o resumo acionavel
- Front mostra uma leitura executiva curta com estado antes, estado depois e percentual de conciliacao capado em 100%

23. `P1` Filtro rapido por status
- Estimativa: 3h
- Dependencias: item 17
- Pronto quando: usuario filtra em 1 clique conciliado/pendente/divergente

## EPIC 7 - QUALIDADE E VALIDACAO

24. `P0` Testes unitarios do motor de matching
- Estimativa: 5h
- Dependencias: itens 6, 7, 8
- Pronto quando: cenarios exato, tolerancia e descricao estao cobertos

25. `P0` Testes de integracao API (extrato + planilha)
- Estimativa: 4h
- Dependencias: itens 1, 16, 18
- Pronto quando: cenario feliz e erros de entrada estao cobertos

26. `P0` Validacoes negativas minimas
- Estimativa: 3h
- Dependencias: item 25
- Pronto quando: erros esperados retornam mensagens claras:
  - planilha sem colunas minimas
  - formato invalido
  - sem correspondencia

27. `P0` Smoke test com dataset real anonimizado (demo contador)
- Estimativa: 3h
- Dependencias: itens P0 concluidos
- Pronto quando: demo ponta-a-ponta mostra valor pratico com dados reais

## EPIC 8 - VALIDACAO DE SALDO (FUTURO, NAO ENTRA NA V2-MVP)

28. `P2` Definir contrato de saldo na planilha operacional
- Estimativa: 3h
- Dependencias: item 2
- Pronto quando:
  - existe layout documentado para metadados de saldo (`saldo_inicial`, `saldo_final`, `periodo_inicio`, `periodo_fim`, `conta`)
  - layout antigo continua aceito sem quebra (campos de saldo opcionais)

29. `P2` Extrair saldo do OFX (`LEDGERBAL`/`DTASOF`)
- Estimativa: 4h
- Dependencias: item 4
- Pronto quando:
  - parser OFX retorna saldo e data de referencia quando presentes
  - ausencia de saldo no OFX nao quebra o pipeline (apenas marca indisponivel)

30. `P2` Motor de validacao de saldo de abertura e fechamento
- Estimativa: 6h
- Dependencias: itens 28 e 29
- Pronto quando:
  - compara saldo inicial da planilha com saldo de abertura/mais proximo do OFX
  - aplica tolerancia configuravel (ex.: `0,01`)
  - valida fechamento por periodo (`saldo_inicial + movimentacoes = saldo_final`)
  - classifica resultado em `ok`, `atencao`, `inconsistente`

31. `P2` Expor `balance_check` no contrato da API de conciliacao
- Estimativa: 3h
- Dependencias: item 30
- Pronto quando:
  - resposta do `POST /reconcile` inclui bloco `balance_check`
  - bloco traz status, diferenca, regra aplicada e mensagem objetiva

32. `P2` Exibir validacao de saldo no preview e no relatorio exportado
- Estimativa: 4h
- Dependencias: itens 18 e 31
- Pronto quando:
  - preview mostra status da validacao de saldo
  - `XLSX`/`CSV` exportado inclui informacao de saldo validado e diferencas

33. `P2` Cobertura de testes para validacao de saldo
- Estimativa: 4h
- Dependencias: itens 29, 30 e 31
- Pronto quando:
  - testes unitarios cobrem casos `ok`, `atencao`, `inconsistente`
  - teste de integracao cobre caminho feliz e caminho sem saldo disponivel

## EPIC 9 - Importacao de extrato em PDF sem selecao de banco (NOVO)

Plano detalhado: `doc/plan-pdf-ofx-inferencia.md`

34. `P0` Spike tecnico com PDF real e contrato minimo de qualidade
- Estimativa: 4h
- Dependencias: item 4
- Referencia de execucao: `doc/spike-pdf-nubank-2023-11.md`
- Pronto quando:
  - `backend/samples/NU_150702837_01NOV2023_30NOV2023.pdf` e processado ponta-a-ponta em ambiente local
  - taxa minima de extraicao de linhas de transacao e definida para aceite tecnico
  - riscos de OCR vs texto nativo ficam documentados

35. `P0` Parser PDF de extrato bancario (texto nativo)
- Estimativa: 8h
- Dependencias: item 34
- Pronto quando:
  - `PDF` entra no mesmo schema normalizado usado por `CSV`/`XLSX`/`OFX`
  - parser cobre data, descricao, valor e sinal
  - erros de conteudo invalido retornam mensagem clara

36. `P0` Inferencia automatica de layout sem combobox de banco
- Estimativa: 8h
- Dependencias: item 35
- Pronto quando:
  - mecanismo de score escolhe layout com confianca minima configuravel
  - fallback generico funciona sem exigir selecao manual do banco
  - resposta expone `layout_inference_confidence` para observabilidade

37. `P1` Gerador OFX a partir das transacoes extraidas do PDF
- Estimativa: 6h
- Dependencias: item 35
- Pronto quando:
  - sistema gera OFX valido com `STMTTRN` para todas as transacoes parseadas
  - datas e valores ficam consistentes com preview da analise
  - arquivo OFX pode ser baixado como bonus da analise

38. `P1` Endpoint de download do OFX bonus por `analysis_id`
- Estimativa: 4h
- Dependencias: itens 3 e 37
- Pronto quando:
  - existe rota dedicada para baixar `gettdone_bonus_{analysis_id}.ofx`
  - expiracao segue o mesmo TTL de `report`
  - `analysis_id` inexistente/expirado retorna `404`

39. `P0` Cobertura de testes (unitario + integracao) para fluxo PDF
- Estimativa: 6h
- Dependencias: itens 35, 36, 37 e 38
- Pronto quando:
  - existe teste feliz de `POST /analyze` com `PDF`
  - existe teste feliz de download do OFX bonus
  - existe pelo menos 1 teste negativo de PDF invalido/nao suportado

40. `P1` Observabilidade de layouts e estrategia de expansao
- Estimativa: 3h
- Dependencias: itens 36 e 39
- Pronto quando:
  - logs/metricas registram banco inferido, confianca e falhas de parsing
  - existe fila de novos layouts para cobertura incremental sem quebrar existentes
  - backlog de melhoria continua e atualizado com base em dados reais

---

## Corte recomendado para demo V2-MVP (contador)

Itens:
`1,2,3,4,6,7,8,10,11,12,13,14,16,17,18,20,21,22,24,25,26,27`

---

## Itens para adiar para V2.1

- `5` Ajuste manual de mapeamento
- `9` Matching 1:N
- `15` Criticidade de problemas
- `19` Aba extra de problemas no relatorio
- `23` Filtro rapido por status
- `28` Contrato de saldo da planilha (futuro)
- `29` Extracao de saldo no OFX (futuro)
- `30` Motor de validacao de saldo (futuro)
- `31` `balance_check` na API (futuro)
- `32` Exibicao de saldo no preview/export (futuro)
- `33` Testes de validacao de saldo (futuro)
- `34` Spike tecnico de PDF real (futuro imediato)
- `35` Parser PDF bancario (futuro imediato)
- `36` Inferencia automatica de layout sem combobox (futuro imediato)
- `37` Gerador OFX bonus (futuro imediato)
- `38` Endpoint de download do OFX bonus (futuro imediato)
- `39` Testes do fluxo PDF (futuro imediato)
- `40` Observabilidade e expansao de layouts (futuro imediato)

---

## Sequencia de execucao sugerida

1. Upload duplo + parser planilha + normalizacao unica
2. Motor de matching (exato -> tolerancia -> descricao)
3. Classificacao de status + problemas + insights
4. Output/export completo
5. UX de destaque de problemas
6. Testes e smoke test com contador

---

## Definicao de pronto da V2-MVP

- usuario envia extrato + planilha e recebe conciliacao clara
- sistema mostra o que bate, o que falta e o que diverge
- relatorio exportavel com status por linha
- preview destaca problemas antes dos detalhes
- demo com contador valida utilidade operacional real
