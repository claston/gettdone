# Backlog V1 - gettdone (Extrato + Conciliacao Bancaria)

## Objetivo

Entregar em 7 dias uma V1 funcional com:

- upload de extrato (`CSV`, `XLSX`, `OFX`)
- classificacao automatica
- conciliacao bancaria
- preview util
- download de relatorio

---

## Prioridades (visao rapida)

- `P0` = obrigatorio para colocar no ar
- `P1` = recomendado para boa experiencia
- `P2` = pode adiar sem bloquear lancamento

---

## Backlog Priorizado

## EPIC 1 - Base de API e pipeline

1. `P0` Criar esqueleto FastAPI (`main.py`, rotas, startup)
- Estimativa: 2h
- Dependencias: nenhuma
- Pronto quando: API sobe localmente com `/health`

2. `P0` Implementar `POST /analyze` (contrato inicial)
- Estimativa: 4h
- Dependencias: item 1
- Pronto quando: endpoint recebe arquivo e retorna JSON base com `analysis_id`

3. `P0` Implementar armazenamento temporario (TTL)
- Estimativa: 3h
- Dependencias: item 2
- Pronto quando: resultado eh salvo e expira automaticamente

4. `P0` Implementar `GET /report/{analysis_id}`
- Estimativa: 3h
- Dependencias: item 3
- Pronto quando: baixa arquivo para `analysis_id` valido e retorna `404` quando expirado

## EPIC 2 - Parsing e normalizacao

5. `P0` Parser CSV (detectar separador e colunas)
- Estimativa: 5h
- Dependencias: item 2
- Pronto quando: converte CSV comum em lista de transacoes normalizadas

6. `P0` Parser XLSX (primeira aba)
- Estimativa: 4h
- Dependencias: item 2
- Pronto quando: XLSX vira mesmo schema normalizado do CSV

7. `P0` Parser OFX
- Estimativa: 5h
- Dependencias: item 2
- Pronto quando: OFX vira mesmo schema normalizado

8. `P0` Normalizador unico (data, descricao, valor, tipo)
- Estimativa: 4h
- Dependencias: itens 5, 6, 7
- Pronto quando: qualquer parser retorna schema padrao da spec

## EPIC 3 - Categorizacao e conciliacao

9. `P0` Categorizador por regras (palavras-chave)
- Estimativa: 5h
- Dependencias: item 8
- Pronto quando: categoria + confianca basica por transacao

10. `P0` Conciliacao: transferencia interna
- Estimativa: 4h
- Dependencias: item 8
- Pronto quando: debito/credito pareados com `reconciliation_group_id`

11. `P0` Conciliacao: estorno/cancelamento
- Estimativa: 4h
- Dependencias: item 8
- Pronto quando: pares de estorno marcados como `reversed`

12. `P1` Conciliacao: duplicidade provavel
- Estimativa: 3h
- Dependencias: item 8
- Pronto quando: duplicatas agrupadas com motivo explicito

13. `P1` Conciliacao: taxa associada
- Estimativa: 3h
- Dependencias: item 8
- Pronto quando: fee agrupada quando regra detectar associacao

## EPIC 4 - Insights e relatorio

14. `P0` Agregacoes (entradas, saidas, saldo, top 10, categorias)
- Estimativa: 4h
- Dependencias: itens 8, 9
- Pronto quando: `POST /analyze` entrega resumo completo

15. `P0` Insights MVP (3-5 frases acionaveis)
- Estimativa: 3h
- Dependencias: item 14
- Pronto quando: payload retorna insights claros e consistentes

16. `P0` Gerador XLSX com abas padrao
- Estimativa: 4h
- Dependencias: itens 9, 10, 11, 14, 15
- Pronto quando: arquivo contem `Transacoes`, `Resumo`, `Conciliacao`, `Insights`

17. `P1` Export CSV enriquecido (opcional)
- Estimativa: 2h
- Dependencias: item 16
- Pronto quando: opcao adicional de download CSV

## EPIC 5 - Frontend pagina unica

18. `P0` Layout base landing + upload
- Estimativa: 4h
- Dependencias: item 2
- Pronto quando: usuario envia arquivo via UI

19. `P0` Estado de processamento (loading + erros)
- Estimativa: 3h
- Dependencias: item 18
- Pronto quando: feedback claro durante e apos envio

20. `P0` Bloco preview (KPIs + categorias + tabela curta)
- Estimativa: 5h
- Dependencias: itens 14, 18
- Pronto quando: usuario entende valor antes de baixar relatorio

21. `P0` Botao download relatorio
- Estimativa: 2h
- Dependencias: item 4, 20
- Pronto quando: clique baixa relatorio do `analysis_id`

22. `P0` Mensagens de confianca e privacidade
- Estimativa: 1h
- Dependencias: item 18
- Pronto quando: texto de processamento temporario aparece em destaque

## EPIC 6 - Qualidade e release

23. `P0` Testes unitarios parser/normalizador/categorizador
- Estimativa: 6h
- Dependencias: itens 5-9
- Pronto quando: cobertura minima dos caminhos criticos

24. `P0` Testes de integracao da API (`/analyze`, `/report`)
- Estimativa: 4h
- Dependencias: itens 2, 4, 16
- Pronto quando: cenarios feliz + erro cobertos

25. `P1` Dataset de testes reais anonimizados
- Estimativa: 2h
- Dependencias: itens 5-13
- Pronto quando: regressao validada com 3+ exemplos reais

26. `P0` Deploy MVP (backend + frontend) e smoke test online
- Estimativa: 4h
- Dependencias: itens P0 concluidos
- Pronto quando: fluxo ponta-a-ponta funciona em producao

---

## Corte recomendado para lancamento (P0)

Itens: `1,2,3,4,5,6,7,8,9,10,11,14,15,16,18,19,20,21,22,23,24,26`

Isso entrega a proposta completa de V1 com conciliacao bancaria essencial.

---

## Itens para adiar se faltar tempo

- `12` Duplicidade provavel
- `13` Taxa associada
- `17` CSV enriquecido
- `25` Dataset maior de testes

---

## Sequencia de execucao sugerida

1. API base + `/analyze`
2. Parsers + normalizacao
3. Categorizacao + conciliacao core
4. Agregacoes + insights + XLSX
5. Frontend pagina unica
6. Testes + deploy + validacao com usuarios

---

## Definicao de pronto da V1

- fluxo completo upload -> preview -> download funcionando
- arquivos `CSV`, `XLSX`, `OFX` processados
- conciliacao interna + estorno operando
- relatorio util para usuario real
- app no ar e utilizavel sem explicacao assistida
