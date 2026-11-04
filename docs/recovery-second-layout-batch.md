# Segundo lote de recuperação de PDFs

Este lote trata cinco documentos distintos da auditoria de 4 de novembro de 2026. As reproduções versionadas usam dados fictícios; documentos, identificadores e evidências de clientes permanecem fora do repositório.

## Comportamento e limites

| Estrutura | Ajuste | Resultado no replay local |
| --- | --- | --- |
| Bradesco CDB | Aceita aplicações que imprimem somente o principal e metadados com a data antes do rótulo. Posições e resgates continuam exigindo seus campos completos. | 28 movimentos de investimento; 16 posições preservadas como observações. |
| Caixa de aplicativo | Novo leitor de cartões com data completa, referência, valor C/D e saldo C/D. Controles `SALDO DIA` não viram movimentos. | 79 movimentos, com revisão recomendada por ausência de saldo de abertura. |
| BTG conta investimento integralmente em conta corrente | Novo leitor preserva a seção de movimentações antes da separação de anexos e valida os totais, posições e saldos disponíveis. | 2 movimentos de investimento; CSV e Excel disponíveis, OFX indisponível. O traço da abertura permanece sem valor informado. |
| Caixa por período via OCR | A ausência da abertura deixa de impedir a leitura quando valores, sinais e saldos estão legíveis. | O documento real ainda é recusado: o OCR omitiu indicadores C/D, inclusive nos tokens de palavras. O teste sintético sem abertura passa. |
| Sicredi com fonte sem mapeamento legível | Caracteres de controle em quantidade relevante acionam a leitura visual existente. Uma página com candidatos a movimentos sem nenhuma transação extraída bloqueia a recuperação visual parcial. | O OCR local leu somente 43 movimentos da primeira página; as páginas seguintes também contêm candidatos. Essa extração parcial é recusada. A recuperação completa depende de nova evidência visual. |

Datas, valores e sinais não são completados a partir de palpites. O primeiro saldo após um movimento não é convertido em saldo de abertura. Aplicações, resgates e posições continuam no contrato de investimentos, sem reconciliação bancária artificial.

## Validação

- Replays locais dos cinco documentos e comparação de 24 controles das famílias Caixa, Sicredi, BTG e investimentos: 2.604 movimentos sem alteração em datas, valores, referências, avisos ou documentos financeiros estruturados.
- Testes sintéticos de conteúdo ilegível posterior, valores ausentes, totais divergentes, preservação de páginas e recusa de recuperação visual parcial.
- Chamadas HTTP reais para conversão de Caixa, BTG e CSV, consulta do relatório, exportação CSV/Excel, bloqueio de OFX para investimento e análise inexistente. O endpoint vigente de upload é `POST /convert`; `POST /analyze` responde 405.
- Suíte completa pelo runner do projeto, Ruff, verificações de texto UTF-8/navegação e `git diff --check`. Os resultados completos ficam no PR.

## Compatibilidade e operação

O lote parte de `origin/main` e é independente do primeiro lote (PR #546). Não altera esquema de dados, endpoints ou configuração de produção. Não reprocessa nem publica arquivos em produção.

O suporte novo é deliberadamente restrito às estruturas reconhecidas. A detecção de fonte ilegível pode aumentar o uso de OCR; continua respeitando os limites de páginas e a configuração existente. A proteção de cobertura é conservadora quando há datas e valores em páginas sem transações extraídas.

Após implantação, acompanhar a seleção dos novos leitores, recusas por ambiguidade e volume de OCR. Caixa com sinais ausentes e Sicredi com cobertura incompleta só devem ser considerados resolvidos após uma nova extração visual completa e sua conferência. Em caso de regressão, reverter o commit e publicar a versão anterior; não há migração ou restauração de dados.
