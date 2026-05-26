# Plano de implementação: Textract, revisão e geração de OFX

## 1. Visão geral

O fluxo desejado para a evolução do conversor de extratos deve ser:

```text
PDF do extrato
-> upload
-> processamento temporário
-> Amazon Textract
-> extração de tabelas/texto
-> normalizador existente
-> modelo intermediário normalizado
-> tela de revisão
-> geração de OFX
-> limpeza de arquivos temporários
-> registro apenas de métricas/erros sem dados sensíveis
```

A implementação deve ser incremental. O objetivo não é substituir o normalizador atual, mas introduzir uma camada de extração estruturada com Textract e adapters que alimentem os modelos já usados pelo produto.

O fluxo alvo deve preservar a arquitetura V1 de página única: upload, processamento, prévia/revisão e download do relatório/OFX. Dados sensíveis do extrato devem existir apenas pelo tempo necessário para revisão e download, com TTL curto e limpeza automática.

## 2. Estado atual do codebase

### Componentes encontrados

- API FastAPI em `backend/app/main.py`, com routers para upload, análise, conversão, relatório, conciliação, autenticação e planos.
- Endpoint legado de análise em `backend/app/routers/analyze.py`, `POST /analyze`.
- Endpoint principal de conversão em `backend/app/routers/convert.py`, `POST /convert`, `POST /api/conversions/upload` e `POST /conversions/upload`, com suporte a SSE.
- Download e edição em `backend/app/routers/report.py`, incluindo:
  - `GET /report/{analysis_id}`;
  - `GET /convert-report/{processing_id}`;
  - `POST /convert-edits/{processing_id}`.
- Serviço de orquestração em `backend/app/application/analyze_service.py`, classe `AnalyzeService`.
- Storage temporário em `backend/app/application/storage_service.py`, classe `TempAnalysisStorage`.
- Modelos internos em `backend/app/application/models.py`.
- Schemas HTTP Pydantic em `backend/app/schemas.py`.
- Frontend estático em `frontend/`, especialmente `frontend/ofx-convert.html`, `frontend/ofx-convert.js` e `frontend/ofx-convert.css`.

### Normalizador atual

O normalizador atual está em `backend/app/application/normalizer.py`.

Funções principais:

- `normalize_transactions(transactions: list[NormalizedTransaction]) -> list[NormalizedTransaction]`
- `normalize_transaction(transaction: NormalizedTransaction) -> NormalizedTransaction`

Entrada esperada:

- Lista de `NormalizedTransaction`, dataclass definida em `backend/app/application/models.py`:

```python
@dataclass
class NormalizedTransaction:
    date: str
    description: str
    amount: float
    type: str
```

Saída gerada:

- Lista de `NormalizedTransaction` com:
  - `date` normalizada para `YYYY-MM-DD`;
  - `description` normalizada via `normalize_description_text`;
  - `amount` com sinal ajustado por `type` ou palavras-chave;
  - `type` inferido como `inflow` ou `outflow`.

O normalizador não recebe texto bruto, tabela ou payload de OCR. Ele já espera transações parseadas.

### Parsers existentes

- `backend/app/application/csv_parser.py`: `parse_csv_transactions`, retorna `list[NormalizedTransaction]`.
- `backend/app/application/xlsx_parser.py`: `parse_xlsx_transactions`, retorna `list[NormalizedTransaction]`.
- `backend/app/application/ofx_parser.py`: `parse_ofx_transactions`, retorna `list[NormalizedTransaction]`.
- `backend/app/application/pdf_parser.py`: `parse_pdf_transactions`, retorna `PdfParseResult`.
- `backend/app/application/bank_parser.py`: `parse_bank_statement_rows`, facade para CSV, XLSX, OFX e PDF.

O parser PDF atual usa:

- `pypdf.PdfReader` para texto nativo;
- `backend/app/application/pdf_ocr.py` para OCR opcional;
- heurísticas grouped/inline/tabular/columnar em `backend/app/application/normalization/pdf_*`;
- `PdfLayoutInference` de `backend/app/application/pdf_layout_inference.py`;
- perfis declarativos em `backend/app/application/layout_profiles/profiles/*.yaml`.

### OCR/PDF parsing atual

OCR atual encontrado em `backend/app/application/pdf_ocr.py`.

Características atuais:

- `PDF_OCR_ENABLED` habilita OCR explicitamente, ou autoativa em development se houver Tesseract local.
- Renderização via `pypdfium2`.
- OCR via `pytesseract`; há caminho opcional para `PaddleOCR`, mas `paddleocr` não está em `backend/requirements.txt`.
- Limites configuráveis por ambiente:
  - `PDF_OCR_MAX_PAGES`;
  - `PDF_OCR_DPI`;
  - `PDF_OCR_PAGE_TIMEOUT_SECONDS`;
  - `PDF_OCR_LANG`;
  - `PDF_OCR_ENGINE`;
  - `PDF_OCR_MAX_FILE_MB`;
  - `PDF_OCR_CONCURRENCY_LIMIT`.

Amazon Textract, S3 temporário, boto3 e gateway AWS: **Não encontrado no codebase atual**.

### Modelo canônico existente

Além de `NormalizedTransaction`, existe `CanonicalTransaction` em `backend/app/application/models.py`:

```python
@dataclass
class CanonicalTransaction:
    date: str
    description: str
    amount: float
    type: str
    bank_name: str | None = None
    layout_name: str | None = None
    source_page: int | None = None
    source_line: int | None = None
    source_parser: str | None = None
    running_balance: float | None = None
    document_id: str | None = None
    external_reference_id: str | None = None
    warnings: list[str] = field(default_factory=list)
    confidence: float | None = None
```

Esse modelo já cobre parte importante do fluxo proposto: rastreabilidade de página/linha, parser de origem, saldo, referência externa, warnings e confidence.

### Geração de OFX

Geração de OFX encontrada em `backend/app/application/ofx_writer.py`.

Função principal:

- `build_ofx_statement(transactions: list[NormalizedTransaction], *, account_type=None, closing_balance=None, bank_branch=None, account_number=None, bank_id=None) -> str`

FITID estável encontrado em `backend/app/application/ofx_identity.py`:

- `build_fit_id_sequence`
- `build_stable_fit_id`

O FITID usa HMAC SHA-256 com:

- data;
- valor;
- descrição normalizada;
- ocorrência da transação duplicada;
- segredo `OFX_FITID_SECRET` ou `ACCESS_CONTROL_TOKEN_SECRET`.

### Revisão atual

Já existe uma tela de revisão parcial em `frontend/ofx-convert.html` e `frontend/ofx-convert.js`.

Funcionalidades encontradas:

- tabela de revisão com data, histórico, crédito, débito, saldo e ações;
- edição de linhas;
- inclusão de novo lançamento;
- exclusão/restauração lógica;
- envio de patches para `POST /convert-edits/{processing_id}`;
- download de OFX e Excel;
- exibição de warnings vindos de `warning_types`;
- armazenamento local de estado de tela em `localStorage`.

Não existe um `ReviewSessionService` separado. A sessão de revisão atual é representada por `AnalysisData` persistido em `TempAnalysisStorage` dentro de `analysis.json`.

### Persistência temporária atual

`TempAnalysisStorage.save_analysis` grava:

- `analysis.json`;
- `report.xlsx`;
- `converted.ofx`;
- `converted.csv`;
- `converted.xlsx`.

O TTL padrão é 24 horas. A limpeza ocorre quando leituras detectam expiração via `_is_expired` e `_cleanup_analysis`.

Risco atual: `analysis.json`, `converted.ofx`, `converted.csv` e `converted.xlsx` podem conter descrições, valores, saldos e identificadores informados pelo usuário durante o TTL. Isso é aceitável apenas como persistência temporária controlada; para o fluxo Textract, a política de retenção precisa ficar explícita.

### Dependências atuais

`backend/requirements.txt` inclui:

- `fastapi`, `uvicorn`, `python-multipart`;
- `pydantic`;
- `openpyxl`;
- `pytest`, `ruff`, `httpx`;
- `pypdf`, `pypdfium2`, `pytesseract`;
- `psycopg`, `SQLAlchemy`, `alembic`.

`boto3`, `botocore` ou AWS SDK: **Não encontrado no codebase atual**.

### Lacunas para Textract

- Não há `TextractGateway` ou cliente AWS.
- Não há armazenamento S3 temporário.
- Não há reconstrução de blocos Textract para páginas/tabelas/células/linhas.
- Não há `RawDocumentExtraction`.
- Não há adapter explícito `RawDocumentExtraction -> NormalizerInput`.
- Não há modelo HTTP específico para revisão com confidence por célula/linha.
- Não há política dedicada para apagar objeto S3 após processamento.
- Não há eventos observáveis específicos de Textract.

### Riscos técnicos encontrados

- O parser PDF atual opera principalmente sobre linhas de texto; Textract entrega blocos estruturados, então acoplar direto a AWS quebraria a arquitetura.
- Há lógica sensível de parsing em `pdf_parser.py`, com múltiplas estratégias; qualquer substituição direta aumenta risco de regressão.
- O frontend já tem revisão e edição, mas não expõe página/confiança de forma completa.
- A persistência temporária atual salva artefatos convertidos por padrão; a evolução precisa revisar retenção e logs.
- Há sinais de mojibake em alguns textos exibidos por arquivos atuais. Não é escopo desta tarefa corrigir, mas é risco para copy e observabilidade.

## 3. Arquitetura proposta

A arquitetura deve reaproveitar o fluxo atual e introduzir Textract como fonte alternativa/mais estruturada de extração.

### Componentes recomendados

#### `convert.py` / endpoint de upload existente

Reaproveitar `backend/app/routers/convert.py` como controller inicial. O endpoint `POST /conversions/upload` já suporta SSE e estados de processamento. Ele pode ganhar novos estágios:

- `textract_upload_started`;
- `textract_job_started`;
- `textract_polling`;
- `textract_mapping`;
- `review_session_created`.

#### `AnalyzeService` ou novo `DocumentProcessingService`

Hoje `AnalyzeService.analyze` orquestra parsing, classificação, normalização, conciliação, métricas e persistência.

Recomendação incremental:

- manter `AnalyzeService` para compatibilidade;
- criar `DocumentProcessingService` em `backend/app/application/document_processing_service.py` quando o fluxo Textract começar a crescer;
- inicialmente, encapsular o caminho Textract dentro de um método privado ou serviço chamado por `_build_transactions_for_extension`.

Responsabilidades:

- validar extensão/tamanho;
- decidir parser local vs Textract;
- chamar gateway de extração;
- converter extração bruta em entrada do normalizador;
- produzir `AnalysisData`/review session.

#### `TextractGateway`

Novo módulo sugerido: `backend/app/application/textract_gateway.py`.

Responsabilidades:

- upload temporário para S3;
- `start_document_analysis`;
- polling;
- paginação de `get_document_analysis`;
- retorno de `TextractRawResponse`;
- deleção do objeto temporário em `finally`;
- tradução de erros AWS para erros de domínio.

#### `RawDocumentExtraction`

Novo modelo intermediário sugerido em `backend/app/application/document_extraction_models.py`.

Responsabilidade:

- representar páginas, linhas, tabelas, células e confidence sem expor blocos AWS ao normalizador.

#### `TextractTableMapper`

Novo módulo sugerido: `backend/app/application/textract_table_mapper.py`.

Responsabilidades:

- reconstruir páginas;
- reconstruir tabelas;
- reconstruir células;
- preservar bounding boxes quando útil;
- gerar linhas textuais ordenadas por página;
- produzir `RawDocumentExtraction`.

#### Adapter para normalizador

Novo módulo sugerido: `backend/app/application/textract_normalizer_adapter.py`.

Responsabilidades:

- transformar `RawDocumentExtraction` em `NormalizerInput`;
- preferir tabelas quando houver estrutura confiável;
- gerar fallback textual compatível com regras atuais do parser PDF;
- produzir `NormalizedTransaction` ou uma lista de linhas parseáveis.

#### Normalizador existente

Preservar `backend/app/application/normalizer.py`.

Responsabilidade:

- continuar recebendo `list[NormalizedTransaction]`;
- normalizar data, descrição, valor e tipo;
- não conhecer Textract, S3 ou blocos AWS.

#### `CanonicalTransaction`

Reaproveitar `CanonicalTransaction` como base para `NormalizedTransaction` enriquecida. Ele já suporta `source_page`, `source_line`, `source_parser`, `running_balance`, `warnings` e `confidence`.

#### `ReviewSessionService`

Hoje a revisão usa `TempAnalysisStorage`. Recomenda-se evoluir incrementalmente:

- fase inicial: reaproveitar `TempAnalysisStorage` e `AnalysisData`;
- fase posterior: extrair `ReviewSessionService` para separar análise, revisão e artefatos.

Responsabilidades:

- criar sessão temporária;
- listar linhas;
- aplicar edições;
- validar antes do OFX;
- regenerar OFX/Excel após edição.

#### `ReviewController`

O controller atual é `backend/app/routers/report.py` com `POST /convert-edits/{processing_id}`. Recomenda-se manter compatibilidade e, se necessário, adicionar aliases REST:

- `GET /review-sessions/{id}`;
- `PATCH /review-sessions/{id}/transactions`;
- `POST /review-sessions/{id}/confirm`.

Necessário confirmar na implementação se novos endpoints devem coexistir com os atuais ou substituir gradualmente o contrato do frontend.

#### `OfxGenerator`

O gerador atual é `build_ofx_statement` em `backend/app/application/ofx_writer.py`. Recomenda-se mantê-lo e, se crescer, encapsular em `OfxGenerator` sem quebrar a função existente.

#### `TemporaryFileStorage`

`TempAnalysisStorage` cobre armazenamento temporário local de análises. Para Textract, criar componente separado para arquivos binários temporários:

- `TemporaryDocumentStorage`;
- ou `S3TemporaryDocumentStorage`.

Responsabilidades:

- salvar PDF temporário no S3;
- gerar chave não semântica;
- apagar objeto no fim do processamento;
- nunca gravar nome original como chave S3.

#### `ConversionAuditLogger`

Não encontrado no codebase atual como componente dedicado. Recomenda-se criar um wrapper leve para eventos seguros, sem payload sensível.

## 4. Modelo de dados intermediário

O padrão atual usa dataclasses para modelos internos e Pydantic para schemas HTTP. Portanto:

- usar `dataclass` para modelos internos de extração;
- usar `BaseModel` apenas para requests/responses HTTP.

### `RawDocumentExtraction`

```python
@dataclass
class RawDocumentExtraction:
    document_id: str
    source: str  # "textract" | "pypdf" | "tesseract"
    pages: list[ExtractedPage]
    warnings: list[ConversionWarning]
    metrics: dict[str, int | float | str]
```

Campos sensíveis:

- textos completos;
- descrições;
- valores;
- saldos;
- identificadores de documento se derivados de conteúdo real.

Não persistir fora da sessão temporária.

### `ExtractedPage`

```python
@dataclass
class ExtractedPage:
    page_number: int
    lines: list[ExtractedLine]
    tables: list[ExtractedTable]
    confidence: float | None = None
```

### `ExtractedTable`

```python
@dataclass
class ExtractedTable:
    page_number: int
    table_index: int
    cells: list[ExtractedCell]
    row_count: int
    column_count: int
    confidence: float | None = None
```

### `ExtractedCell`

```python
@dataclass
class ExtractedCell:
    row_index: int
    column_index: int
    text: str
    confidence: float | None = None
    bounding_box: dict[str, float] | None = None
```

### `ExtractedLine`

```python
@dataclass
class ExtractedLine:
    page_number: int
    line_index: int
    text: str
    confidence: float | None = None
    bounding_box: dict[str, float] | None = None
```

### `NormalizedStatement`

```python
@dataclass
class NormalizedStatement:
    statement_id: str
    bank_name: str | None
    bank_id: str | None
    account_type: str | None
    period_start: str | None
    period_end: str | None
    transactions: list[NormalizedTransaction]
    warnings: list[ConversionWarning]
    metrics: dict[str, int | float | str]
```

### `NormalizedTransaction`

O codebase já possui `NormalizedTransaction`. Para o fluxo enriquecido, duas opções:

1. Reaproveitar `CanonicalTransaction` para dados com origem/confiança.
2. Criar novo DTO para revisão sem alterar `NormalizedTransaction`.

Sugestão:

```python
@dataclass
class ReviewTransactionRow:
    id: str
    date: str | None
    description: str
    amount: float | None
    balance: float | None
    type: str | None
    source_page: int | None
    source_row: int | None
    confidence: float | None
    warnings: list[str]
    status: str  # "active" | "deleted" | "needs_review"
```

### `ReviewSession`

```python
@dataclass
class ReviewSession:
    review_session_id: str
    analysis_id: str
    created_at: str
    expires_at: str
    rows: list[ReviewTransactionRow]
    source_metrics: dict[str, int | float | str]
    warnings: list[ConversionWarning]
```

### `ConversionWarning`

```python
@dataclass
class ConversionWarning:
    code: str
    message: str
    severity: str  # "info" | "warning" | "error"
    source_page: int | None = None
    source_row: int | None = None
```

### Dados que não devem ser persistidos

Não persistir por padrão fora do storage temporário com TTL:

- PDF original;
- payload bruto Textract;
- texto completo extraído;
- descrições completas em logs;
- valores reais em logs;
- saldo;
- agência, conta, CPF, CNPJ, nome do cliente;
- OFX gerado fora da sessão temporária.

## 5. Integração com Amazon Textract

PDF multipágina deve usar processamento assíncrono do Textract.

### Dependências

Adicionar quando implementar:

- `boto3`;
- `botocore`;
- possivelmente `moto` ou stubs oficiais do botocore para testes.

### Variáveis de ambiente

Sugeridas:

- `AWS_REGION`;
- `TEXTRACT_TEMP_BUCKET`;
- `TEXTRACT_S3_PREFIX`;
- `TEXTRACT_JOB_POLL_INTERVAL_SECONDS`;
- `TEXTRACT_JOB_TIMEOUT_SECONDS`;
- `TEXTRACT_MAX_PAGES`;
- `TEXTRACT_MAX_FILE_MB`;
- `TEXTRACT_ENABLED`;
- `TEXTRACT_SNS_TOPIC_ARN` e `TEXTRACT_ROLE_ARN`, se optar por SNS em vez de polling simples.

### Permissões IAM mínimas

Para o serviço da aplicação:

- `s3:PutObject` no prefixo temporário;
- `s3:GetObject` no prefixo temporário, se necessário;
- `s3:DeleteObject` no prefixo temporário;
- `textract:StartDocumentAnalysis`;
- `textract:GetDocumentAnalysis`.

Se usar KMS no bucket:

- `kms:Encrypt`;
- `kms:Decrypt`;
- `kms:GenerateDataKey`.

Se usar SNS/SQS:

- permissões mínimas para publicar/ler a fila configurada.

### Fluxo técnico

1. Receber PDF no endpoint atual.
2. Validar extensão, tamanho e limite de páginas.
3. Gerar `document_id` e `file_hash`.
4. Subir o PDF para S3 temporário com chave como `textract/tmp/{document_id}.pdf`.
5. Chamar `start_document_analysis` com `FeatureTypes=["TABLES", "FORMS"]`.
6. Fazer polling do job até `SUCCEEDED`, `FAILED` ou timeout.
7. Paginar `get_document_analysis` usando `NextToken`.
8. Mapear blocos para `RawDocumentExtraction`.
9. Apagar o objeto S3 em `finally`.
10. Seguir para adapter e normalização.

### Tratamento de erro

Converter erros AWS para erros de domínio:

- timeout;
- throttling;
- arquivo inválido;
- acesso negado;
- job failed;
- resultado sem tabelas/linhas úteis;
- falha ao apagar S3.

Falha ao apagar S3 deve ser registrada como evento técnico seguro e acionar retry/limpeza assíncrona. Não deve retornar payload sensível ao usuário.

## 6. Como conectar Textract ao normalizador existente

Esta é a parte crítica: o normalizador atual não deve conhecer Textract.

O contrato atual mais estável é:

```text
list[NormalizedTransaction] -> normalize_transactions -> list[NormalizedTransaction]
```

O parser PDF atual transforma texto/linhas em `NormalizedTransaction`. Portanto, Textract deve entrar antes dessa etapa, como fonte estruturada para um adapter.

### Camada anti-corruption

```text
TextractRawResponse
-> RawDocumentExtraction
-> NormalizerInput
-> NormalizedStatement / list[NormalizedTransaction]
-> normalize_transactions
-> ReviewSession
```

`TextractRawResponse` deve ficar restrito ao gateway/mapper. Nenhum normalizador, controller ou frontend deve depender de chaves como `BlockType`, `Relationships`, `Geometry` ou `Confidence`.

### Adapter recomendado

Criar `textract_normalizer_adapter.py` com uma função como:

```python
def build_transactions_from_extraction(extraction: RawDocumentExtraction) -> tuple[list[NormalizedTransaction], list[CanonicalTransaction]]:
    ...
```

Estratégia:

1. Se houver tabelas com cabeçalhos reconhecidos, mapear colunas para data, descrição, valor, débito/crédito e saldo.
2. Se tabela for ruim, gerar linhas textuais ordenadas por página/posição.
3. Reutilizar funções de normalização existentes:
   - `parse_pdf_amount`;
   - `parse_row_date`;
   - `compute_tabular_signed_amount`;
   - `compute_hint_signed_amount`;
   - `resolve_tabular_profile`;
   - `select_tabular_amount_token`.
4. Produzir `CanonicalTransaction` para preservar `source_page`, `source_line`, `source_parser="textract"`, `running_balance`, `confidence` e `warnings`.
5. Derivar `NormalizedTransaction` apenas para alimentar `normalize_transactions`.

### Se o normalizador espera linhas/tabelas

O normalizador atual não espera linhas/tabelas. Quem espera linhas é `pdf_parser.py`, internamente, via `_PdfLine` e `_ParsedTransaction`, ambos privados.

Não é recomendado importar `_PdfLine` ou `_ParsedTransaction` fora de `pdf_parser.py`. Melhor criar um contrato público novo:

```python
@dataclass
class NormalizerInputRow:
    text: str
    page_number: int
    row_number: int
    cells: list[str] = field(default_factory=list)
    confidence: float | None = None
```

Necessário confirmar na implementação se parte das funções privadas de `pdf_parser.py` deve ser promovida para helpers públicos.

### Se o normalizador espera texto plano

O normalizador atual não espera texto plano, mas o parser PDF atual parte de texto por página. Como fallback, o mapper pode gerar:

```text
page_text = "\n".join(linha.text for linha in page.lines)
```

Depois disso, duas opções:

- criar `parse_pdf_page_texts(page_texts: list[str])` público a partir de `_parse_pdf_transactions_from_page_texts`;
- ou criar parser Textract próprio que compartilhe helpers de datas/valores.

Recomendação: extrair um método público testado para page texts, evitando chamar função privada.

### Se o normalizador espera modelo próprio

O modelo próprio existente é `NormalizedTransaction`, com enriquecimento via `CanonicalTransaction`.

O adapter Textract deve produzir ambos:

- `NormalizedTransaction` para compatibilidade com `normalize_transactions` e `build_ofx_statement`;
- `CanonicalTransaction` para revisão, warnings, origem e métricas.

## 7. Tela de revisão

A tela atual em `frontend/ofx-convert.html` já possui uma seção `#review-section` com tabela editável. Ela deve ser evoluída em vez de recriada.

### Campos atuais

- Data;
- Histórico;
- Crédito;
- Débito;
- Saldo;
- Ações.

### Campos recomendados

- status;
- data;
- descrição;
- valor ou crédito/débito;
- saldo;
- página;
- confiança;
- warnings;
- ações.

### Funcionalidades necessárias

- visualizar transações em tabela;
- editar data;
- editar descrição;
- editar valor;
- editar saldo;
- marcar/remover linhas;
- restaurar linhas removidas;
- destacar linhas com baixa confiança;
- destacar warnings;
- confirmar e gerar OFX.

### Persistência temporária da revisão

Fase inicial:

- reaproveitar `TempAnalysisStorage`;
- persistir `review_session_id` como alias de `analysis_id`;
- continuar usando `POST /convert-edits/{processing_id}`.

Fase posterior:

- extrair `ReviewSessionService`;
- salvar apenas linhas revisáveis e metadados técnicos;
- regenerar OFX sob demanda após confirmação;
- reduzir ou eliminar persistência automática de `converted.ofx`.

## 8. Validações antes de gerar OFX

Validações devem gerar warnings sempre que possível, em vez de falhar cedo.

### Validações obrigatórias

- data válida em `YYYY-MM-DD`;
- valor numérico válido;
- descrição não vazia;
- sinal de débito/crédito resolvido;
- `type` consistente com sinal;
- saldo consistente quando `running_balance` existir;
- duplicidade provável;
- transações sem data;
- data agrupada em extratos onde uma data vale para várias transações;
- período do extrato quando disponível;
- conta/banco quando disponíveis.

### Warnings sugeridos

- `low_confidence`;
- `missing_date`;
- `missing_description`;
- `invalid_amount`;
- `ambiguous_sign`;
- `balance_inconsistent`;
- `possible_duplicate`;
- `grouped_date_inferred`;
- `statement_period_unknown`;
- `bank_account_missing`;
- `layout_fallback`;
- `textract_table_reconstruction_low_confidence`.

### Falhas bloqueantes

Falhar apenas quando:

- não houver nenhuma transação ativa;
- houver transação ativa sem data válida após revisão;
- houver transação ativa sem valor;
- o OFX não puder ser serializado;
- a sessão estiver expirada ou pertencer a outro usuário.

## 9. Geração de OFX

Manter `backend/app/application/ofx_writer.py` como base.

Campos OFX relevantes:

- `BANKID`: usar `bank_id` resolvido por `resolve_bank_code` ou informado pelo usuário.
- `ACCTID`: usar conta informada pelo usuário, preferencialmente mascarada na UI e normalizada no OFX.
- `ACCTTYPE`: hoje fixo como `CHECKING` para banco; deve continuar configurável no futuro.
- `DTSTART`: necessário adicionar, usando menor `DTPOSTED` revisado.
- `DTEND`: necessário adicionar, usando maior `DTPOSTED` revisado.
- `STMTTRN`: uma entrada por transação ativa.
- `TRNTYPE`: `CREDIT` para `inflow`, `DEBIT` para `outflow`.
- `DTPOSTED`: data em formato OFX.
- `TRNAMT`: valor com duas casas.
- `FITID`: usar `build_stable_fit_id`.
- `MEMO` e `NAME`: descrição escapada.
- `LEDGERBAL`: usar `closing_balance` quando informado/disponível.

### FITID estável

O codebase já possui boa base em `backend/app/application/ofx_identity.py`. A evolução deve manter a ideia:

- não usar CPF/CNPJ;
- não usar conta completa;
- não usar nome do cliente;
- usar HMAC com segredo de ambiente;
- usar data, valor, descrição normalizada e ocorrência para diferenciar duplicados.

Necessário confirmar na implementação se `source_page`/`source_row` deve entrar no FITID. Em geral, evitar: mudanças de OCR/layout poderiam alterar o FITID de uma mesma transação.

## 10. Privacidade, LGPD e retenção

### Regras

- Não persistir PDF original após a conversão.
- Apagar arquivo temporário local ou S3 ao final do processamento.
- Não armazenar OFX com dados sensíveis por padrão fora da sessão temporária.
- Não logar payloads de extrato.
- Não enviar documento completo para LLM.
- Se LLM for usado no futuro, enviar apenas amostras mascaradas ou estrutura de layout.
- Guardar apenas métricas técnicas.

### Métricas permitidas

- `layout_id`;
- `bank_detected`;
- `parser_used`;
- `success/failure`;
- `warning_count`;
- `processing_time`;
- `file_hash`;
- `error_type`;
- `page_count`;
- `confidence_band`;
- `textract_job_duration_ms`;
- `textract_pages_processed`;

### Logs proibidos

- PDF original;
- texto extraído completo;
- payload bruto Textract;
- nome do cliente;
- CPF/CNPJ;
- agência;
- conta;
- histórico completo da transação;
- saldo;
- valores reais;
- OFX gerado.

### Observação sobre o estado atual

O storage temporário atual persiste `analysis.json` e artefatos convertidos durante o TTL. Para compatibilidade, isso pode continuar no curto prazo, mas deve ser documentado como retenção temporária com TTL e limpeza. A evolução recomendada é gerar OFX sob demanda na confirmação/download, ou criptografar artefatos temporários se persistidos.

## 11. Observabilidade e diagnóstico

Eventos técnicos recomendados:

### `conversion_started`

Propriedades seguras:

- `conversion_id`;
- `file_hash`;
- `extension`;
- `file_size_bytes`;
- `identity_type`;
- `timestamp`.

### `pdf_uploaded`

- `conversion_id`;
- `file_hash`;
- `page_count`;
- `scanned_likely`;
- `upload_size_bytes`.

### `textract_job_started`

- `conversion_id`;
- `textract_job_id_hash`;
- `bucket_alias`;
- `region`;
- `feature_types`;
- `page_count`.

### `textract_job_succeeded`

- `conversion_id`;
- `duration_ms`;
- `page_count`;
- `block_count`;
- `table_count`;
- `line_count`;
- `mean_confidence`.

### `textract_job_failed`

- `conversion_id`;
- `error_type`;
- `aws_error_code`;
- `duration_ms`;
- `retryable`.

### `normalization_started`

- `conversion_id`;
- `source`;
- `layout_id`;
- `table_count`;
- `line_count`.

### `normalization_succeeded`

- `conversion_id`;
- `transactions_count`;
- `warning_count`;
- `parser_used`;
- `confidence_band`;
- `duration_ms`.

### `normalization_failed`

- `conversion_id`;
- `error_type`;
- `parser_used`;
- `duration_ms`.

### `review_session_created`

- `conversion_id`;
- `review_session_id`;
- `transactions_count`;
- `warning_count`;
- `expires_at`.

### `ofx_generated`

- `conversion_id`;
- `transactions_count`;
- `has_ledger_balance`;
- `bank_id_source`;
- `duration_ms`.

### `conversion_failed`

- `conversion_id`;
- `stage`;
- `error_type`;
- `retryable`;
- `duration_ms`.

Não incluir:

- nome do cliente;
- CPF/CNPJ;
- agência;
- conta;
- histórico completo;
- saldo;
- valores reais, salvo decisão explícita com proteção e justificativa.

## 12. Estratégia incremental de implementação

### Fase 1 - Documentar e adaptar o normalizador atual

- Mapear entrada/saída atual.
- Criar modelos intermediários em `document_extraction_models.py`.
- Criar `NormalizerInputRow`.
- Criar adapter `RawDocumentExtraction -> NormalizerInput`.
- Extrair helper público para parsear `page_texts`, se necessário.
- Criar testes unitários com fixtures sintéticas.

### Fase 2 - Integrar Textract

- Adicionar dependências AWS.
- Criar `TextractGateway`.
- Implementar upload S3 temporário.
- Implementar `start_document_analysis`.
- Implementar polling/timeout.
- Implementar paginação.
- Implementar deleção do objeto em `finally`.
- Reconstruir tabelas/células/linhas.
- Criar testes com mocks/stubs.

### Fase 3 - Tela de revisão

- Evoluir payload de preview para incluir página, confiança e warnings.
- Reaproveitar `POST /convert-edits/{processing_id}`.
- Adicionar endpoint de leitura de sessão, se necessário.
- Permitir atualizar linhas com saldo/confiança preservados.
- Permitir confirmar geração do OFX.

### Fase 4 - Validação e qualidade

- Consolidar warnings.
- Usar confidence do Textract.
- Validar saldo.
- Tratar data agrupada.
- Detectar duplicidade provável.
- Criar relatório de conversão seguro.

### Fase 5 - Produção

- Limpeza automática de S3/local.
- Métricas e logs seguros.
- Limites de tamanho e páginas.
- Retries controlados.
- Timeout por job.
- Fallback para parser atual.
- Alertas para falha de deleção S3.

## 13. Testes

### Unitários

- parser de moeda brasileira (`parse_amount`, `parse_pdf_amount`);
- parser de data (`parse_statement_date`, `parse_row_date`);
- detecção de cabeçalho;
- data agrupada;
- mapeamento de tabela Textract;
- reconstrução de células por linha/coluna;
- geração de warnings;
- geração de FITID;
- geração de OFX.

### Integração

- `TextractGateway` com stub de boto3/botocore;
- fluxo completo com fixture sintética;
- `ReviewSessionService` ou `TempAnalysisStorage` com sessão de revisão;
- geração de OFX após edição;
- fallback parser atual quando Textract estiver desligado.

### Regressão

- fixtures sintéticas por banco/layout;
- snapshots de OFX;
- casos com OCR ruim;
- casos com tabela quebrada;
- data agrupada;
- sinal C/D;
- saldo inconsistente;
- linhas duplicadas.

## 14. Riscos técnicos

| Risco | Mitigação |
| --- | --- |
| Textract identificar tabela incorretamente | Reconstituir linhas textuais como fallback e marcar `textract_table_reconstruction_low_confidence`. |
| PDF escaneado com baixa qualidade | Usar confidence, destacar revisão humana e limitar geração automática. |
| Extrato com data agrupada | Reutilizar regras atuais de grouped date e marcar `grouped_date_inferred`. |
| Débito/crédito sem sinal claro | Usar colunas, descrição, saldo e warning `ambiguous_sign`. |
| Saldo não bater | Rodar `annotate_balance_consistency` e exibir warning por linha. |
| Linhas quebradas | Agrupar por geometria e página antes de normalizar. |
| Múltiplas contas no mesmo PDF | Detectar mudança de conta/banco e exigir revisão ou split. |
| PDF muito grande | Limitar páginas/tamanho antes de S3/Textract. |
| Custo por página | Registrar páginas processadas e aplicar quota/limites. |
| Dependência de serviço externo | Timeout, retry limitado e fallback para parser atual quando possível. |
| Vazamento de dados em logs | Usar `ConversionAuditLogger` com allowlist de propriedades seguras. |
| Persistência temporária sensível | TTL curto, limpeza, evitar nomes originais em chaves S3 e considerar criptografia. |
| Regressão no parser atual | Não alterar `normalizer.py` nem `pdf_parser.py` sem testes de regressão. |

## 15. Backlog técnico

- [ ] Mapear normalizador atual.
- [ ] Criar `RawDocumentExtraction`.
- [ ] Criar `ExtractedPage`.
- [ ] Criar `ExtractedTable`.
- [ ] Criar `ExtractedCell`.
- [ ] Criar `ExtractedLine`.
- [ ] Criar `ConversionWarning`.
- [ ] Criar `NormalizerInputRow`.
- [ ] Criar `TextractGateway`.
- [ ] Criar `TextractTableMapper`.
- [ ] Criar `NormalizerInputAdapter`.
- [ ] Reaproveitar ou extrair parser público para `page_texts`.
- [ ] Criar `ReviewSessionService`.
- [ ] Evoluir endpoint de upload.
- [ ] Evoluir endpoint de revisão.
- [ ] Evoluir endpoint de geração de OFX.
- [ ] Criar warnings de validação.
- [ ] Exibir página/confiança/warnings na revisão.
- [ ] Criar testes com fixtures sintéticas.
- [ ] Criar política de logs seguros.
- [ ] Criar limpeza automática de arquivos temporários.
- [ ] Adicionar métricas de Textract.
- [ ] Adicionar fallback para parser atual.

## 16. Decisões arquiteturais recomendadas

### ADR 001 - Não acoplar normalizador ao formato bruto do Textract

Decisão: blocos AWS devem ser convertidos para `RawDocumentExtraction` antes de chegar ao parser/normalizador.

Consequência: reduz lock-in, facilita testes e mantém `normalizer.py` estável.

### ADR 002 - Não persistir PDF/OFX por padrão

Decisão: PDF original deve ser apagado ao fim do processamento. OFX deve ser gerado sob demanda ou mantido apenas em storage temporário com TTL.

Consequência: reduz risco LGPD e impacto de incidente.

### ADR 003 - Usar modelo intermediário antes da geração de OFX

Decisão: gerar OFX apenas a partir de dados revisados/normalizados, não diretamente do Textract.

Consequência: melhora qualidade e auditabilidade.

### ADR 004 - Gerar warnings em vez de falhar cedo

Decisão: ambiguidades devem virar warnings revisáveis quando a transação ainda puder ser apresentada ao usuário.

Consequência: aumenta taxa de recuperação de documentos difíceis sem mascarar risco.

### ADR 005 - Usar revisão humana antes do OFX quando houver baixa confiança

Decisão: baixa confiança, saldo inconsistente ou sinal ambíguo devem destacar revisão antes do download.

Consequência: reduz OFX incorreto em layouts/OCR problemáticos.

### ADR 006 - Guardar apenas telemetria técnica sem payload sensível

Decisão: logs e métricas devem seguir allowlist explícita.

Consequência: diagnóstico continua possível sem expor extratos.

## 17. Entregável final

Este documento orienta a implementação futura em:

```text
docs/textract-review-ofx-implementation-plan.md
```

Próximo passo recomendado: implementar a Fase 1 com testes unitários e fixtures sintéticas, preservando o fluxo atual de `AnalyzeService`, `TempAnalysisStorage`, revisão no frontend e `build_ofx_statement`.
