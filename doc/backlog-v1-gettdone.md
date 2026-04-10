# Backlog V1 - gettdone (Extrato Pronto para Conciliacao)

## Objetivo

Entregar em 7 dias uma V1 funcional com foco em pre-processamento financeiro:

- upload de extrato (`CSV`, `XLSX`, `OFX`)
- normalizacao e limpeza de dados
- enriquecimento leve para uso operacional
- preview da transformacao
- download de arquivo pronto para conciliar

## Proposta de valor

Envie seu extrato e receba um arquivo limpo, padronizado e pronto para conciliar.

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

## EPIC 2 - Parsing e normalizacao (core)

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

## EPIC 3 - Limpeza e enriquecimento leve

9. `P0` Limpeza de descricao e padronizacao de estabelecimento
- Estimativa: 5h
- Dependencias: item 8
- Pronto quando: ruido e variacoes sao reduzidos (ex.: IFOOD SAO PAULO -> IFOOD)

10. `P0` Ajuste de sinais e tipo (entrada/saida)
- Estimativa: 4h
- Dependencias: item 8
- Pronto quando: transacoes ficam com sinal e tipo consistentes

11. `P0` Deteccao de duplicidade simples
- Estimativa: 4h
- Dependencias: item 8
- Pronto quando: registros potencialmente duplicados sao sinalizados

12. `P1` Sugestao de categoria por regras
- Estimativa: 3h
- Dependencias: item 8
- Pronto quando: `category_hint` basica e consistente por transacao

13. `P1` Identificacao de contraparte
- Estimativa: 3h
- Dependencias: item 8
- Pronto quando: contraparte e extraida para a maioria dos lancamentos

## EPIC 4 - Output pronto para conciliar

14. `P0` Agregacoes operacionais (entradas, saidas, saldo, volume)
- Estimativa: 4h
- Dependencias: itens 8, 10
- Pronto quando: `POST /analyze` entrega resumo util do processamento

15. `P0` Preview antes/depois (amostra representativa)
- Estimativa: 3h
- Dependencias: item 14
- Pronto quando: usuario entende como o extrato foi padronizado

16. `P0` Gerador de arquivo final padronizado (CSV/XLSX)
- Estimativa: 4h
- Dependencias: itens 8, 9, 10, 11, 14, 15
- Pronto quando: arquivo contem colunas padrao para conciliacao

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

20. `P0` Bloco preview (KPIs + antes/depois)
- Estimativa: 5h
- Dependencias: itens 14, 18
- Pronto quando: usuario entende o valor antes de baixar o arquivo final

21. `P0` Botao download arquivo pronto para conciliacao
- Estimativa: 2h
- Dependencias: item 4, 20
- Pronto quando: clique baixa relatorio do `analysis_id`

22. `P0` Mensagens de confianca e privacidade
- Estimativa: 1h
- Dependencias: item 18
- Pronto quando: texto de processamento temporario aparece em destaque

## EPIC 6 - Qualidade e release

23. `P0` Testes unitarios parser/normalizador/limpeza
- Estimativa: 6h
- Dependencias: itens 5-11
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

Isso entrega a proposta completa de V1 para preparar extrato e deixar pronto para conciliacao.

---

## Itens para adiar se faltar tempo

- `12` Sugestao de categoria
- `13` Identificacao de contraparte
- `17` CSV enriquecido
- `25` Dataset maior de testes

---

## Sequencia de execucao sugerida

1. API base + `/analyze`
2. Parsers + normalizacao
3. Limpeza + enriquecimento leve
4. Agregacoes + preview antes/depois + arquivo final
5. Frontend pagina unica
6. Testes + deploy + validacao com usuarios

---

## Definicao de pronto da V1

- fluxo completo upload -> preview -> download funcionando
- arquivos `CSV`, `XLSX`, `OFX` processados
- extrato padronizado com qualidade operacional
- arquivo final util para conciliacao em planilha/ERP
- app no ar e utilizavel sem explicacao assistida
