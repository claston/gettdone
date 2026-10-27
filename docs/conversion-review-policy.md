# Revisão com motivos identificáveis

A regra de qualidade `2026-10-26.v3` deixa de classificar um PDF para revisão
apenas porque o leitor informou confiança `medium` ou `low`. Essa faixa continua
disponível no diagnóstico técnico e não altera movimentos, saldos ou arquivos
exportados.

## Comportamento

Um PDF com modelo específico reconhecido, pontuação de layout pelo menos 0,95,
leitor identificado, movimentos extraídos e nenhum apontamento registrado pode
receber `clean`, mesmo com confiança técnica média. `clean` significa ausência
de apontamentos pelos critérios da regra; não certifica leitura integral ou
exatidão de todo o documento.

Continuam produzindo revisão os critérios existentes de ausência não confirmada
de movimentos, alertas nas linhas, divergência de saldo, modelo genérico,
pontuação de layout insuficiente/ausente e leitor não identificado. Falhas
técnicas continuam sendo falhas. As regras específicas para extratos
explicitamente sem movimentação e relatórios financeiros também permanecem.

No conversor gratuito, a recomendação técnica `review_recommended` e a faixa
isolada deixam de produzir o aviso genérico “Recomendamos revisar os lançamentos
antes de baixar o arquivo”. Sem mensagens específicas, o cartão “Atenção na
revisão” não aparece. A prévia, a edição e o download continuam disponíveis.
Os avisos específicos existentes, como divergência de saldo, documento parcial,
linhas ambíguas não convertidas e saldo inicial ambíguo, continuam visíveis.
Essa apresentação funciona também com respostas antigas que ainda informam
confiança média e recomendação técnica de revisão.

O script compartilhado tem a referência de cache atualizada nas páginas OFX,
Excel e no conversor autenticado legado. O workspace PRO não teve seu fluxo
de revisão alterado.

## Histórico e compatibilidade

O esquema do banco e os contratos da API não mudam. Novas avaliações gravam a
versão `2026-10-26.v3`. Registros persistidos sob a regra anterior conservam sua
classificação, seus motivos e sua versão. Não há reclassificação retroativa,
migração nem acesso ao banco de produção nesta mudança; indicadores históricos
podem, portanto, continuar incluindo as revisões da versão anterior.

Não foi alterada a faixa técnica do parser nem sua recomendação de exportação.
Outros consumidores dessas informações continuam recebendo os mesmos valores.
Novos motivos de erro, alterações de cobertura ou mudanças de limiar não foram
introduzidos nesta regra.

## Validação

- Testes da regra verificam que a faixa isolada não cria apontamento e que os
  alertas financeiros continuam exigindo revisão em qualquer faixa.
- Testes executam a função real do conversor gratuito para respostas antigas e
  atuais, preservando avisos específicos e eliminando a mensagem genérica.
- A avaliação dos 37 PDFs inéditos da auditoria de 26/10 resultou em 21 casos sem
  apontamentos, oito em revisão e oito falhas. Todos os casos conhecidos de
  omissões e divergências mantiveram sua classificação de revisão ou falha.
- Validação local em Chromium cobriu as páginas gratuitas OFX e Excel, em
  desktop e celular, com e sem divergência de saldo, usando dados fictícios.
- Requisições HTTP locais validaram sessão anônima, conversão CSV, consulta de
  relatório, download OFX e respostas negativas. `/analyze` permanece desativado;
  o endpoint atual é `/convert`.

## Implantação e rollback

Implantar frontend e serviços que calculam qualidade somente quando autorizado.
Esta mudança não promove a imagem de infraestrutura nem depende das correções
de parsing da PR #490. Não houve deploy durante sua preparação.

Para rollback, restaurar as versões anteriores do frontend e dos serviços
afetados. Sem migração, não há rollback de esquema. Os registros já produzidos
sob `v3` permanecem com essa versão e classificação; um rollback de código não
reescreve o histórico.
