# gettdone - Especificacao Tecnica V1

## 1) Objetivo

Entregar em 7 dias um micro-servico dentro da plataforma `gettdone` para:

- receber extratos bancarios (`CSV`, `XLSX`, `OFX`)
- normalizar transacoes
- classificar categorias automaticamente
- conciliar movimentacoes (creditos/debitos relacionados)
- exibir preview util
- gerar relatorio completo para download

Escopo de produto: pagina unica, sem login, sem dashboard persistente na V1.

---

## 2) Escopo V1

### Entra na V1

- Upload de arquivo: `CSV`, `XLSX`, `OFX`
- Parser por tipo de arquivo
- Normalizacao de schema unico de transacao
- Categorizacao por regras deterministicas
- Concilicao bancaria automatica (regra + heuristica)
- Preview com:
  - total de transacoes
  - total de entradas e saidas
  - totais por categoria
  - top 10 maiores gastos
  - 3 a 5 insights simples
- Download de relatorio enriquecido (`XLSX` e opcional `CSV`)
- Processamento temporario (sem historico de usuario)

### Fora da V1

- Login e multiusuario
- Dashboard e historico
- Integracao bancaria automatica
- Importacao recorrente
- Edicao manual de categorias na UI
- OCR/PDF escaneado

---

## 3) Definicao de Concilicao Bancaria (V1)

Nesta V1, concilicao significa:

1. Detectar eventos financeiros relacionados que representam a mesma movimentacao economica.
2. Marcar pares/grupos com status de conciliacao.
3. Reduzir ruido analitico (ex.: transferencia interna aparecendo como gasto).

### Casos cobertos no MVP

- Transferencia interna (`PIX`, `TED`, `DOC`, `TRANSFERENCIA`) entre contas proprias.
- Estorno/cancelamento de compra.
- Duplicidade provavel de lancamento (mesmo valor + data aproximada + descricao similar).
- Taxa associada a uma transacao principal (quando detectavel por padrao textual).

### Resultado da concilicao

Cada transacao recebe:

- `reconciliation_status`: `unmatched | matched | grouped | reversed`
- `reconciliation_group_id`: id comum de agrupamento (quando aplicavel)
- `reconciliation_reason`: motivo da conciliacao

---

## 4) Arquitetura Minima

### Frontend (pagina unica)

- Upload de arquivo
- Estado de processamento
- Render de preview
- Acao de download do relatorio

### Backend (FastAPI)

Pipeline:

1. Detecta tipo do arquivo
2. Parseia linhas brutas
3. Normaliza schema
4. Classifica categoria
5. Executa conciliacao
6. Gera insights + agregacoes
7. Persiste resultado temporario
8. Entrega preview e endpoint de download

### Armazenamento

- V1 sem banco relacional obrigatorio
- Resultado temporario em disco (`tmp/`) com TTL
- Metadado minimo em memoria (mapa `analysis_id -> path`)

---

## 5) Modelo de Dados

### 5.1 Transaction (normalizada)

```json
{
  "id": "tx_001",
  "date": "2026-04-01",
  "description": "IFOOD SAO PAULO",
  "amount": -58.9,
  "currency": "BRL",
  "type": "debit",
  "channel": "card",
  "category": "Alimentacao",
  "category_confidence": 0.94,
  "counterparty": "IFOOD",
  "raw_reference": "linha_42_csv",
  "reconciliation_status": "unmatched",
  "reconciliation_group_id": null,
  "reconciliation_reason": null
}
```

### 5.2 CategorySummary

```json
{
  "category": "Transporte",
  "total": -1284.32,
  "count": 23
}
```

### 5.3 Insight

```json
{
  "type": "largest_spend_category",
  "title": "Maior categoria de gasto",
  "description": "Voce gastou R$ 1.284,32 com Transporte."
}
```

---

## 6) Contratos de API

## 6.1 `POST /analyze`

### Request

- `multipart/form-data`
- campo: `file`

### Response 200

```json
{
  "analysis_id": "an_abc123",
  "file_type": "ofx",
  "transactions_total": 137,
  "total_inflows": 8450.2,
  "total_outflows": -6123.77,
  "net_total": 2326.43,
  "reconciliation": {
    "matched_groups": 18,
    "reversed_entries": 3,
    "potential_duplicates": 2
  },
  "categories": [
    { "category": "Alimentacao", "total": -1520.33, "count": 31 },
    { "category": "Transporte", "total": -1284.32, "count": 23 }
  ],
  "top_expenses": [
    { "description": "ALUGUEL ABRIL", "amount": -2200.0, "date": "2026-04-05", "category": "Moradia" }
  ],
  "insights": [
    { "type": "largest_spend_category", "title": "Maior categoria de gasto", "description": "..." }
  ],
  "preview_transactions": [
    {
      "date": "2026-04-01",
      "description": "IFOOD SAO PAULO",
      "amount": -58.9,
      "category": "Alimentacao",
      "reconciliation_status": "unmatched"
    }
  ],
  "expires_at": "2026-04-09T21:10:00Z"
}
```

### Erros

- `400` arquivo invalido/nao suportado
- `422` formato suportado, mas sem colunas minimas
- `500` falha inesperada no pipeline

## 6.2 `GET /report/{analysis_id}`

### Response 200

- arquivo `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- nome sugerido: `gettdone_report_{analysis_id}.xlsx`

### Erros

- `404` analysis_id nao encontrado/expirado

## 6.3 `GET /health`

Retorna status da API para monitoramento basico.

---

## 7) Regras de Parsing e Normalizacao

## 7.1 CSV

- Detectar separador (`;` ou `,`)
- Mapear colunas por sinonimos:
  - data: `data`, `date`, `dt_lancamento`
  - descricao: `descricao`, `historico`, `memo`, `description`
  - valor: `valor`, `amount`, `vlr`
- Converter data para `YYYY-MM-DD`
- Converter valor monetario com suporte a virgula decimal

## 7.2 XLSX

- Mesma regra de mapeamento do CSV
- Ler primeira aba na V1
- Ignorar linhas totalmente vazias

## 7.3 OFX

- Extrair:
  - data (`DTPOSTED`)
  - descricao (`MEMO`/`NAME`)
  - valor (`TRNAMT`)
  - tipo (`TRNTYPE`)

## 7.4 Normalizacao

- `description`: uppercase + trim + limpeza de espacos duplicados
- `amount`: debito negativo, credito positivo
- `currency`: default `BRL` se nao informado
- `type`: derivado do sinal e metadados disponiveis

---

## 8) Regras de Categorizacao (V1)

Categorias base:

- Alimentacao
- Transporte
- Moradia
- Saude
- Lazer
- Assinaturas
- Transferencias
- Salario_Receita
- Impostos_Taxas
- Outros

Estrategia:

1. Regras por palavras-chave (case-insensitive).
2. Priorizacao por especificidade.
3. Fallback em `Outros`.

Exemplos de dicionario inicial:

- `ifood`, `restaurante`, `padaria`, `mercado` -> `Alimentacao`
- `uber`, `99`, `combustivel`, `posto` -> `Transporte`
- `netflix`, `spotify`, `prime video` -> `Assinaturas`
- `farmacia`, `drogaria`, `hospital`, `clinica` -> `Saude`
- `pix`, `ted`, `doc`, `transferencia` -> `Transferencias`
- `salario`, `folha`, `proventos` -> `Salario_Receita`
- `iof`, `tarifa`, `anuidade`, `juros`, `multa` -> `Impostos_Taxas`

---

## 9) Regras de Conciliacao (V1)

## 9.1 Transferencia interna

Match quando:

- um debito e um credito com valores absolutos iguais
- diferenca de data ate 1 dia
- descricao contem padroes de transferencia

Resultado:

- ambos `matched`
- mesmo `reconciliation_group_id`
- `reconciliation_reason = "internal_transfer"`

## 9.2 Estorno

Match quando:

- valores absolutos iguais e sinais opostos
- descricao contem `estorno`, `reversal`, `cancelamento`
- janela de ate 30 dias

Resultado:

- transacoes marcadas como `reversed`
- group id comum

## 9.3 Duplicidade provavel

Match quando:

- mesmo sinal e mesmo valor
- mesma data ou data adjacente
- alta similaridade textual na descricao

Resultado:

- status `grouped`
- `reconciliation_reason = "possible_duplicate"`

## 9.4 Taxa associada

Match quando:

- lancamento pequeno identificado como tarifa/fee
- proximo temporalmente de transacao principal relacionada

Resultado:

- agrupamento com `reconciliation_reason = "associated_fee"`

---

## 10) Relatorio de Saida (XLSX)

Abas:

1. `Transacoes_Classificadas`
2. `Resumo_Categorias`
3. `Concilicao`
4. `Insights`

Campos minimos da aba `Transacoes_Classificadas`:

- date
- description
- amount
- type
- category
- category_confidence
- reconciliation_status
- reconciliation_group_id
- reconciliation_reason

---

## 11) UX da Pagina Unica

Secoes:

1. Headline + promessa
2. Upload area (drag and drop + clique)
3. Bloco de seguranca:
   - processamento temporario
   - sem armazenamento permanente na V1
4. Estado de processamento (spinner + mensagens)
5. Preview:
   - KPIs
   - lista por categoria
   - tabela curta (10 linhas)
   - resumo de conciliacao
6. CTA de download do relatorio

Microcopys recomendadas:

- "Seus dados sao processados de forma temporaria."
- "Nao mantemos historico pessoal nesta versao inicial."

---

## 12) Estrutura de Pastas Sugerida

```text
backend/
  app/
    main.py
    routes/
      analyze.py
      report.py
      health.py
    services/
      file_parser.py
      normalizer.py
      categorizer.py
      reconciler.py
      insights.py
      report_generator.py
      storage_temp.py
    schemas/
      transaction.py
      analyze_response.py
    utils/
      text_match.py
      datetime_utils.py
  requirements.txt

frontend/
  index.html
  style.css
  app.js

doc/
  spec-v1-conciliacao-bancaria.md
```

---

## 13) Plano de Execucao (7 dias)

Dia 1:

- setup FastAPI
- endpoint `POST /analyze` com parser CSV

Dia 2:

- parser XLSX
- normalizacao unica

Dia 3:

- parser OFX
- validacao de campos obrigatorios

Dia 4:

- categorizacao por regras
- agregacoes (categorias, top gastos)

Dia 5:

- modulo de conciliacao (regras V1)
- incluir resumo de conciliacao na resposta

Dia 6:

- geracao do XLSX final
- endpoint `GET /report/{analysis_id}`

Dia 7:

- pagina unica (upload + preview + download)
- ajustes finais, testes manuais com arquivos reais

---

## 14) Criterios de Aceite (V1)

- Upload aceita `CSV`, `XLSX` e `OFX`
- Sistema retorna preview em menos de 15s para ate 5k linhas
- Categorizacao cobre os principais padroes do dataset de teste
- Concilicao identifica ao menos:
  - transferencia interna
  - estorno
  - duplicidade provavel
- Relatorio XLSX baixa sem erro
- Mensagem de seguranca de dados aparece na pagina

---

## 15) Riscos e Mitigacoes

Risco: baixa confianca para upload de extrato.
Mitigacao: mensagem clara de processamento temporario + nao retencao.

Risco: formatos de extrato muito variados.
Mitigacao: parser resiliente + feedback de erro explicito.

Risco: falso positivo na conciliacao.
Mitigacao: incluir `reconciliation_reason` e score futuro; iniciar com regras conservadoras.

---

## 16) Proximos Passos Pos-V1

- Edicao manual de categoria na interface
- Score de confianca por regra de conciliacao
- LLM apenas para ambiguidades de categoria
- Suporte a PDF (texto e OCR) em fase dedicada
- Opcao de conta e historico para usuarios recorrentes
