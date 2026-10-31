# Identificação de titulares — segunda leva nativa de 30/10/2026

Esta auditoria complementa o PR #517. A comparação usa a política versão 3 do
commit `388ab03` e a política versão 4 desta entrega; não mede uma publicação
em produção. O PR anterior permanece separado e não foi mesclado nesta tarefa.

## Seleção e restrições

- 100 PDFs de conteúdo distinto por SHA-256, sem repetir nenhum conteúdo da
  primeira leva. Foram descartadas 26 cópias e substituído um documento
  explicitamente marcado como teste sintético, encontrado no bucket.
- S3 somente para listar e baixar originais; nenhum acesso ao banco ou escrita
  remota. PDFs e diagnósticos reais permanecem em
  `local/native-identity-audit-20261030-batch2/`, ignorado pelo Git.
- Somente texto nativo, com OCR/Textract desativados e bloqueados no executor.
  PDFs com página sem texto suficiente foram excluídos por inteiro desta fase.
- Para conferir a identificação final foram reutilizados os metadados das
  conversões locais, incluindo as páginas de texto utilizadas pelo parser.
  Cada chamada verificou que a análise financeira não foi modificada.

## Resultado da segunda leva

80 converteram com texto nativo, 15 foram excluídos por página sem texto
suficiente, 4 tiveram erro de conversão preexistente e 1 estava protegido por
senha. Os 4 erros não foram contados como identificação em conversão concluída.

Nos mesmos 80 documentos convertidos:

| Campo preenchido | Versão 3 / PR #517 | Versão 4 |
| --- | ---: | ---: |
| Nome do titular | 60 | 64 |
| CPF/CNPJ do titular | 18 | 27 |
| Número da conta | 66 | 70 |
| Agência | 47 | 50 |

Contagens de campos preenchidos não significam vínculos confirmados no banco.
A comparação anterior incluía falsos nomes, como endereço postal, descrição
do produto, referência interna e rótulo concatenado. A nova versão remove esses
valores, completa um sufixo empresarial legível e recupera outras identificações.
CPF mascarado permanece sem número fiscal completo; não se usam documentos de
contrapartes para completar os dígitos ocultos.

Permanecem 16 documentos sem nome reconhecido. Entre os motivos estão cabeçalhos
postais, texto concatenado ou matricial, rótulos fora de ordem, ausência de
identificação e referências que não identificam uma conta bancária. Campos
parcialmente ilegíveis e contas mascaradas também exigem revisão. Não há
promessa de reconhecimento universal.

## Regressão da primeira leva e dados financeiros

Os 66 convertidos da primeira leva mantiveram exatamente os mesmos campos de
identificação: 54 nomes, 29 CPF/CNPJ, 63 contas e 48 agências. Nenhuma diferença
de identificação foi encontrada nessa comparação.

A identificação preservou integralmente os metadados financeiros dos 146
convertidos das duas levas. Essa verificação compara o objeto completo antes e
depois da identificação sobre as análises locais reutilizadas; não representa
nova reconversão financeira nem verificação de persistência em produção.
O executor final não criou clientes AWS nem chamou OCR/Textract.

## Política compartilhada corrigida

1. Cabeçalhos CPF/CNPJ, agência e conta podem vir em ordem de rótulos ou de
   valores, inclusive com CPF mascarado. O limite de moeda do cabeçalho encerra
   a evidência de titular antes das contrapartes nas páginas seguintes.
2. `Nome:` em uma página apenas de movimentos não estabelece outro titular sem
   uma conta de origem no mesmo cabeçalho. Nomes explícitos continuam válidos
   junto da origem e nomes distintos mantêm a revisão por ambiguidade.
3. CEP, código/nome do emissor, descrição de produto e fragmentos de rótulos não
   viram cliente. Um sufixo legal quebrado entre linhas é preservado, inclusive
   com espaçamento das letras introduzido pela extração nativa.
4. CPF/CNPJ formatado junto do nome, contratante explícito e identificação
   inicial do titular em um extrato podem fornecer evidência sem conta impressa.
   Rodapés e a identificação fiscal do emissor permanecem excluídos.
5. Relatórios de movimentações preservam nome, documento e conta impressos em
   seu cabeçalho. Banco sem código legível não recebe código por suposição.
6. Referência alfanumérica interna não vira conta numérica parcial. A evidência
   pode ficar em `account_reference` no resultado temporário; ela não é usada
   como uma conta bancária confirmada.

## Compatibilidade e rollback

Sem migração, mudança de retenção, alteração de exportação ou nova chamada a
provedor. A política passa de versão 3 para 4, permitindo a recuperação existente
de PDFs nativos antigos ainda disponíveis e sem cliente confirmado. Vínculos
manuais e prazos continuam protegidos pelo fluxo existente.

O rollback consiste em reverter o commit e republicar a API/worker anteriores.
Uma reversão de código não desfaz vínculos já gravados; eventuais vínculos
incorretos precisam de revisão contextual, sem apagar conversões ou transações.
Após eventual publicação, conferir a qualidade dos novos vínculos, pendências
de identificação e tempo de processamento.

## Validação reproduzível sem dados reais

`backend/tests/test_statement_identity_native_continuations.py` contém somente
texto sintético. Os testes cobrem CPF mascarado, contrapartes em continuações,
origens distintas, emissor, CEP, sufixos empresariais, referências internas,
relatórios e a ausência legítima de uma conta impressa.

O grupo focado com integração e HTTP real passou com **123 passed**. Ruff e lint
de texto frontend passaram. O smoke usa os endpoints vigentes: conversão e lote
PRO, relatório disponível e inexistente; `/analyze` continua desativado (405).
Pytest foi executado no venv, fora do sandbox, com `--basetemp` específico.

Suíte completa final: **3.661 passed, 6 skipped, 2 xfailed**, em 211,82 segundos.
O diretório temporário específico da suíte foi removido após a validação.
