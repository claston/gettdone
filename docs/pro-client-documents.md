# Clientes, Documentos e Conversor no PRO

A carteira em `/dashboard` consulta o cadastro de clientes, inclusive os que ainda
não têm documentos. O nome abre Documentos; os atalhos levam a Extratos e, quando
habilitada, à Central Financeira. O cadastro usa as entidades existentes. A edição
de identidade conserva a verificação de versão e o fluxo de confirmação vigente.

`/conversions` continua sendo o Conversor independente. O nome do menu muda, mas
suas rotas, formatos, cotas e contratos de processamento são preservados.

## Documentos

`/documents` consulta arquivos por cliente, sem cliente associado e por categoria.
Extratos, notas fiscais e comprovantes usam a classificação já produzida pela
leitura. Arquivos sem classificação, planilhas financeiras e outros relatórios
permanecem em **Outros e a identificar** e em **Todos**. Extensão PDF não comprova
o tipo de documento. Esta entrega não adiciona novos leitores de comprovantes.

O inventário do conversor usa `GET /api/pro/documents`, protegido pela autorização
PRO e pela organização do usuário. Cada consulta examina no máximo 100 registros
de origem; filtros esparsos podem retornar uma página vazia com continuação. A UI
preserva a próxima página nesse caso. A resposta contém metadados selecionados,
nunca os valores financeiros completos. Arquivos expirados não oferecem revisão.

As notas importadas pela gestão fiscal aparecem em seção própria no contexto do
cliente, reutilizando importação, revisão, download, expiração e permissões fiscais.
Na visão geral, é necessário selecionar o cliente para consultar essas notas; a
lista de arquivos do Conversor não é apresentada como inventário fiscal global.
Os vínculos com pagamentos continuam na Central Financeira. As notas acessíveis
são da empresa inteira, independentemente do mês usado pela consulta financeira.

## Importação no contexto do cliente

O formulário compartilha o serviço de envio do Conversor. Após o envio, abre os
arquivos daquele lote em Documentos, mantendo o cliente escolhido na URL. Essa
visão é explicitamente identificada como **arquivos deste envio**: pode conter
arquivos ainda sem associação ou identificados como pertencentes a outro cliente.

Após a leitura, o usuário confere o titular antes de confirmar a associação. A
ação usa a confirmação existente de documentos recebidos, incluindo o vínculo
das contas e a recuperação dos movimentos para a Central. Uma associação
existente com outro cliente não é sobrescrita automaticamente. O comando usa a
versão atual do contexto, preservando conflitos de edição. O retorno à revisão
aceita apenas rotas locais e os parâmetros de contexto autorizados.

A intenção de associação é contexto de navegação, não uma confirmação financeira.
Os arquivos permanecem no inventário mesmo se o usuário sair antes de associar.
Reabrir os documentos enviados no formulário não cria um segundo lote. Não há
duplicação de movimentos, nova retenção, arquivo fiscal permanente ou conciliação
automática por efeito desta reorganização.

## Central Financeira e compatibilidade

A Central mantém sua página própria, usa toda a largura útil e permite recolher
o menu existente. A navegação do cliente permite alternar entre Documentos e
Central. O aviso de totais possivelmente incompletos continua visível e passa a
abrir todos os documentos daquele cliente, inclusive os ainda sem categoria.

Não há migração de banco. Publicar a API com o endpoint adicional antes do frontend.
As flags de organização e de gestão fiscal continuam sendo verificadas no servidor.
O Conversor permanece utilizável independentemente da organização de documentos.
Rollback: reverter os commits e publicar a versão anterior; nenhuma limpeza de
dados ou reversão de esquema é necessária.

## Validação

- Carteira sem documentos, cadastro e navegação por cliente.
- Filtros, paginação esparsa, autorização entre usuários e expiração.
- Importação contextual sem reenvio; associação explícita com versão e conflito.
- Importação/revisão fiscal sem controles de conciliação em Documentos.
- Avisos financeiros preservados e destino de ação validado.
- Suítes completas, lint, verificação de tipos, build e navegação desktop/mobile.
