# Identificação de titulares — fechamento da amostra de 30/10/2026

Esta entrega complementa os PRs #517 e #518, sem mesclar ou publicar nenhuma
das alterações. A referência da comparação é a política versão 4 do commit
`eba3552`; esta entrega usa a versão 5. Os números medem testes locais, não
uma mudança já disponível em produção.

## Fechamento da seleção

A listagem usada neste fechamento continha 305 PDFs originais. Dos objetos
listados, 227 já haviam sido examinados nas etapas anteriores. Todos os 78
objetos restantes foram baixados e examinados, sem erro de download:

- 63 conteúdos reais novos, distintos por SHA-256 das duas levas anteriores;
- 15 cópias de conteúdos já examinados, descartadas da nova amostra.

O inventário fecha em 305 objetos: 252 conteúdos reais distintos, um conteúdo
explicitamente sintético excluído e 52 cópias. Não se confunde quantidade de
objetos no S3 com quantidade de documentos distintos.

| Resultado dos 63 documentos novos | Quantidade |
| --- | ---: |
| Conversão financeira nativa concluída | 48 |
| Página sem texto nativo suficiente ou documento misto | 10 |
| Erro de conversão nativa | 3 |
| Protegido por senha | 1 |
| Somente identificação nativa, documento de 677 páginas | 1 |

Todas as páginas do documento de 677 páginas foram examinadas para a
identificação nativa. Sua conversão financeira completa não foi validada.
Esse caso permanece separado dos 48 convertidos; o corte usado para a
auditoria local não adiciona limite de páginas ao produto.

## Resultado da identificação

Nos mesmos 48 documentos convertidos desta etapa:

| Campo preenchido | Versão 4 / PR #518 | Versão 5 |
| --- | ---: | ---: |
| Nome do titular | 42 | 44 |
| CPF/CNPJ do titular | 11 | 12 |
| Número da conta | 48 | 48 |
| Agência | 37 | 38 |

Além de preencher campos, a versão 5 corrige cinco nomes parciais ou contendo
rótulo, remove um falso nome formado por mês/paginação, corrige um CNPJ cujos
dígitos haviam sido extraídos da coluna vizinha e distingue agência de conta
em um cabeçalho conjunto. As contagens anteriores incluíam esses valores
incorretos; apenas contar campos preenchidos não mede sua qualidade.

Quatro PDFs convertidos ainda ficam sem nome e dois dos nomes legíveis têm
CNPJ malformado, exigindo revisão. Há 12 resultados com confiança fiscal forte,
28 com nome e conta de origem, dois apenas com nome, quatro com titular ausente
e dois ambíguos. Campo preenchido não significa vínculo confirmado no banco.

No documento de 677 páginas, nome, CNPJ, agência e conta foram reconhecidos
por texto nativo e permaneceram iguais nesta comparação, fora da contagem de
conversão financeira.

Os três arquivos com erro de conversão também tiveram a identificação nativa
conferida separadamente: um fornece nome, CNPJ, agência e conta; outro fornece
somente nome; o terceiro não fornece identificação reconhecida. Esses resultados
não transformam uma conversão com erro em conversão concluída.

## Regressão e preservação financeira

Os campos de identificação dos 146 convertidos das duas levas anteriores
permaneceram iguais: na primeira, 54 nomes, 29 CPF/CNPJ, 63 contas e 48 agências;
na segunda, 64 nomes, 27 CPF/CNPJ, 70 contas e 50 agências.
Três documentos da segunda leva passam de confiança ambígua para titular
ausente após a exclusão de falsos nomes de mês/paginação, permanecendo pendentes.

As análises financeiras dos 194 convertidos das três etapas ficaram
integralmente iguais antes e depois da identificação. São 192 conteúdos
distintos, pois a primeira etapa contém duas cópias entre os convertidos.
O executor da conferência final não criou clientes AWS nem chamou OCR.

## Método e proteção dos dados

O S3 foi usado somente para listar e baixar originais. Não houve acesso ao
banco, alteração remota ou chamada a OCR/Textract. O executor bloqueia essas
rotas, além de desativar OCR e recuperação por IA nas configurações locais.
Os PDFs, textos e diagnósticos reais permanecem em
`local/native-identity-audit-20261030-rest/`, ignorado pelo Git.

A conferência final reutiliza as análises financeiras das conversões locais e
suas páginas de texto. Compara o objeto completo antes e depois da identificação
para assegurar que datas, valores, sinais, movimentos e referências não foram
alterados. Isso não equivale a reconverter financeiramente todos os arquivos
nem a comprovar persistência em produção.

## Correções compartilhadas

1. O rótulo `Cliente` concatenado ao nome em um cabeçalho de extrato é separado
   do valor. Palavras semelhantes que pertencem ao nome continuam preservadas.
2. Nomes empresariais quebrados antes do documento fiscal preservam a
   continuação legível, sem incorporar uma marca bancária reconhecida.
3. Um CNPJ dividido entre linhas usa somente os dígitos efetivamente visíveis.
   O começo numérico de uma razão social não pode fornecer falsos dígitos
   verificadores ao documento da coluna anterior.
4. CNPJ malformado mantém o nome legível e registra
   `identity_issues: ["unreadable_holder_tax_id"]`, com confiança ambígua e
   revisão obrigatória. Não se remove um dígito para fabricar um CNPJ válido.
   O motivo permanece disponível após a leitura nativa alternativa e no
   histórico de recebimento.
5. Rótulos conjuntos de agência/conta preservam a função de cada número.
   Mês, ano e paginação concatenados não viram nome de cliente.

Campos ausentes no cabeçalho, mascarados ou ilegíveis continuam pendentes.
Não se procura o documento de uma contraparte para completar o titular nem
se inventa o código bancário a partir de uma descrição de movimento.

## Compatibilidade e armazenamento

Sem migração, mudança de retenção, nova chamada a provedor ou alteração da
exportação. A identificação continua no resultado temporário
`conversion_jobs.result_payload`, como `organization_source`, e no histórico
existente de recebimentos em `source_json`. O novo `identity_issues` é uma
chave opcional nesse JSON existente, não uma nova coluna.

A versão 5 permite a recuperação já existente de PDFs nativos ainda disponíveis
e sem cliente confirmado. Os vínculos manuais continuam protegidos pelo fluxo
existente. Não foi consultado o conteúdo desses campos no banco de produção.

Rollback: reverter este commit e republicar API/worker anteriores. Uma reversão
de código não desfaz vínculos já gravados; eventual vínculo incorreto exige
revisão contextual. Após publicação futura, conferir vínculos, pendências e
tempo de identificação. Os PRs anteriores continuam sendo dependências desta
entrega enquanto não forem incorporados ao ramo principal.

## Validação reproduzível

Os 16 casos de `backend/tests/test_statement_identity_native_remaining.py`
usam dados sintéticos e cobrem as falhas acima, a preservação do motivo de
revisão e sua persistência no histórico. O grupo focado passou com **86 passed**,
incluindo chamadas HTTP reais ao app.

O smoke usa os endpoints vigentes: `POST /convert`, relatório disponível e
inexistente e lote PRO. `/analyze` permanece desativado, retornando 405.
Nenhuma lógica de conciliação foi alterada.

Suíte completa final: **3.677 passed, 6 skipped, 2 xfailed**, em 289,38 segundos.
Ruff, lint de texto frontend e `git diff --check` passaram. Pytest foi executado
no venv e fora do sandbox, com diretórios temporários específicos removidos
após a validação. Os arquivos de entrega foram conferidos contra os nomes e
documentos fiscais extraídos, sem copiar valores reais para os testes ou relatório.
