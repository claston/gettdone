# Curso parceiro após a conversão gratuita

O conteúdo editorial de `marketing/templates/ofx-content-series/03-curso.*`
foi adaptado para `/curso-notas-explicativas.html`. O e-mail original permanece
disponível e nenhum envio de e-mail foi habilitado por esta alteração.

## Experiência

- Nas três páginas que usam `ofx-convert.js`, um resultado gratuito concluído
  apresenta “Você prepara balanços e notas explicativas para clientes?”, a descrição
  “Conheça o curso da Descomplicont com o professor Laudelino Jochem.” e o botão
  “Ver programa do curso”, com a foto do professor abaixo das conversões restantes,
  à esquerda dos downloads no desktop e acima
  deles no celular, sem divisor ou legenda sob o botão. O link abre a página
  interna em outra aba.
- Usuários anônimos e cadastrados gratuitos são elegíveis. O modo da conversão
  deve ser `conversion`; se houver conta, o plano atual também deve ser gratuito.
  Planos pagos e o aplicativo PRO não recebem essa chamada.
- A página explica que é um curso da Descomplicont, com Laudelino Jochem, e
  informa a possibilidade de comissão. O cartão de saída reúne foto, nome e
  formação do professor junto do botão. No celular, ele aparece antes do texto
  complementar. Não anuncia preço, desconto ou pontos profissionais. Conteúdo
  conferido em <https://descomplicont.com/dcne-2023>.
- Downloads continuam diretos. O CTA não altera arquivos, cotas ou resultados.
  A pesquisa substituída não registra impressões, mantendo seu histórico intacto.

### Avaliação da nova chamada

A referência informada para a chamada anterior foi 1 clique em 287 pessoas
(0,35%) e nenhum avanço à oferta. Confirmar que as 287 correspondem às
identidades expostas no indicador **CTR da chamada**, usando a mesma origem,
exclusão de administradores e filtro de identidade da comparação.

Após a publicação, registrar o commit e a data/hora do deploy do frontend.
O merge da PR sozinho não confirma publicação. No admin, em **Interesse no
curso parceiro**, selecionar a origem **Após a conversão**. Usar dias completos em
Brasília, começando no primeiro dia após o deploy; excluir o dia da troca
para reduzir mistura entre versões e evitar somar identidades únicas diárias.
Comparar períodos de mesma duração e dias da semana, mantendo o filtro de
identidade. Abas abertas com a versão antiga ainda podem registrar eventos.

Registrar em cada período:

| Indicador | O que comparar |
| --- | --- |
| Expostos e cliques qualificados | Numerador e denominador do CTR da chamada, além da taxa |
| Visitantes e saídas para a oferta | Contagens e CTR da página na mesma origem |
| Downloads e erros de telemetria | Verificar se a oferta ou falhas de medição prejudicaram o fluxo |

Fazer uma primeira leitura após pelo menos sete dias completos e 287
identidades expostas à nova apresentação. Esse marco serve para revisão
inicial, não garante significância estatística. Se ainda houver poucos
cliques, prolongar a observação antes de declarar melhora. O histórico com
apenas um clique é muito incerto; a ausência de clique de saída entre um único
visitante não permite concluir que a landing page seja o problema.

Esta é uma comparação antes/depois, sem distribuição aleatória: mudanças
de público, período e tráfego também podem afetar o resultado. Texto e imagem
mudam juntos; os dados não isolam o efeito de cada um. Um aumento do CTR da
chamada deve ser acompanhado de avanço à oferta; cliques isolados não provam
vendas. Os eventos e códigos de origem existentes são preservados, sem
apagar o histórico ou enviar atributos novos à API. Não há monitoramento
agendado ou resultado pós-publicação disponível nesta alteração.

### Foto do professor

`frontend/assets/laudelino-jochem-v1.png` é a foto de Laudelino Jochem publicada
na seção “Professor Laudelino Jochem” da
[página oficial do curso](https://descomplicont.com/dcne-2023), consultada em
27/11/2026. O mesmo perfil confirma que ele é contador e especialista em
Contabilidade e Finanças pela UFPR, informações exibidas na página intermediária.
Não são apresentados cargos atuais, números de alunos ou promessas de resultado.

[Arquivo original da foto](https://rockfunnelsliveincluster.blob.core.windows.net/tenants/UKDGrVH0e0iLNJyTJHNQA/images/3aBEP8k4UGpOQSuv1zlwQ.png).
A cópia local preserva o PNG original de 636 × 746 px, sem geração ou alteração
da pessoa. O navegador carrega a foto do próprio OFX Simples. O recorte visual
das fotos é feito por CSS; os atributos de dimensão reservam espaço e o texto
alternativo identifica o professor. Nome, formação e links permanecem em HTML
na página intermediária e continuam disponíveis se a imagem não carregar ou
o JavaScript estiver desativado.

A revisão usa os mesmos eventos, códigos de origem e critérios de elegibilidade.
Pode ser publicada a partir da `main`, antes da PR #635, sem depender de suas
alterações de autenticação. As versões do CSS e do script foram incrementadas
nas páginas consumidoras. Publicar o PNG junto dos arquivos de frontend.
Para desfazer apenas esta apresentação, reverter o commit e publicar novamente
o frontend; não é necessário alterar backend, banco ou eventos históricos.

### Ilustração anterior

`frontend/assets/course-accounting-v1.png`, preservada para compatibilidade com
páginas antigas em cache, é uma ilustração decorativa original
gerada com a ferramenta integrada `image_gen`, dimensionada para 336 × 336 px
para servir até três vezes o tamanho de exibição no desktop. Não é a capa
oficial do curso. Na apresentação anterior, o texto da oferta permanecia em HTML
e a imagem tinha `alt=""`
para não repetir a informação no leitor de tela. Largura e altura reservam
espaço antes do carregamento.

Prompt usado:

> Use case: ads-marketing. Create a square editorial illustration for a small
> thumbnail next to a Portuguese accounting course recommendation in a financial
> document converter website. Subject: a tidy accounting workspace with neatly
> layered financial statement sheets, simple column lines and a small bar chart,
> a dark navy calculator and a teal pen. Elegant tactile paper-cut / softly
> rendered 3D style, professional and calm, pale mint background, navy and teal
> accents, warm off-white paper. Compact centered composition that stays legible
> at 120px, generous edge padding, soft natural shadows. No text, letters, logos,
> currency symbols, people, watermarks, or decorative growth arrows. This is an
> illustrative thumbnail, not an official course cover. Output square image.

## Tracking Hotmart

Hotlink preservado: `https://go.hotmart.com/D87342786L`.
Usamos `src`, indicado para afiliados na
[documentação da Hotmart](https://help.hotmart.com/pt-br/article/216441797).
Os códigos têm menos de 30 caracteres e não usam `_`.

| Entrada na página | SRC enviado à Hotmart |
| --- | --- |
| `?from=conversao` (CTA do conversor) | `ofxs\|conversao\|dcne` |
| `?from=email` (disponível para futura divulgação manual) | `ofxs\|email\|dcne` |
| Sem origem reconhecida | `ofxs\|pagina\|dcne` |

Os pipes são codificados como `%7C` na URL. A origem vem de uma lista fixa:
nenhum nome, e-mail, ID de usuário, ID de conversão ou dado de extrato é enviado
à Hotmart. O link está marcado `rel="sponsored noopener noreferrer"`.
Sem JavaScript, o link continua funcional e usa a origem `pagina`.

Na Hotmart: **Vendas → Gestão de vendas → Dashboard de origem de vendas → SRC**.
Filtrar o período e conferir vendas atribuídas aos códigos acima; exportar o
relatório para cruzamento agregado. A confirmação depende das regras de
atribuição da Hotmart. Um clique de saída não é uma venda, e vendas antigas
não passam a ter esta origem retroativamente. Não há webhook de compras.

## Funil local

A API existente `/telemetry/events` passa a aceitar quatro eventos em
`product_events`, sem migração de banco:

| Evento | Interpretação |
| --- | --- |
| `course_cta_view` | Pelo menos 50% da chamada na área visível da tela, com a aba visível; não prova leitura |
| `course_cta_click` | Clique em “Ver programa do curso” (antes: “Saiba como”) |
| `course_page_view` | Abertura da página interna |
| `course_outbound_click` | Clique para visitar a oferta via Hotlink |

`page_path` guarda o caminho do conversor ou o caminho da página com a origem
normalizada. A identidade usa os cookies existentes; `plan_code` não é usado
para armazenar campanhas. Os indicadores de compra de planos não mudam.

A apresentação é deduplicada por conversão na sessão da aba (últimos 100 IDs).
Inserir a chamada fora da tela não registra impressão. O observador é encerrado
ao trocar ou limpar o resultado. Rolar, redimensionar ou voltar à aba permite
registrar a exposição. A fila de eventos mantém a exposição/visita antes do
respectivo clique quando a conexão está lenta.
Outra aba, limpeza ou indisponibilidade do armazenamento pode gerar repetição.
Cliques e visitas brutos não são deduplicados. Falhas de rede ou bloqueios de scripts
podem subcontar eventos, sem bloquear navegação ou download. Identidades anônimas
representam navegadores, não necessariamente pessoas únicas.

### Painel e marcações por pessoa

O resumo do admin inclui **Interesse no curso parceiro**, com os mesmos filtros
de período (dias em Brasília) e identidade do painel. O seletor de origem
separa conversor, acesso direto e e-mail. Os números são agregados no banco;
o resumo recebe no máximo três linhas, sem carregar históricos de conversões.

- **CTR da chamada**: identidades que clicaram depois de uma exposição, divididas
  pelas identidades expostas na mesma origem e período.
- **CTR da página**: identidades que clicaram para ir à Hotmart depois de uma
  visita à nossa página, divididas pelos visitantes na mesma origem e período.
- Repetições contam uma vez em cada taxa. Sem denominador, o resultado é `null`
  na API e “—” na interface. Cliques sem exposição/visita anterior no período
  não entram no numerador. Não somar taxas ou identidades entre origens/dias.
- Administradores cadastrados (`users.is_admin`) são excluídos das métricas do
  curso. Outras contas de teste precisam ser consideradas na análise; não são
  identificadas por adivinhação. Cadastro/login ou outro navegador podem separar
  a atividade em identidades diferentes.

**Usuários ativos → Interesse no curso** oferece os filtros “Visitou o curso”
e “Clicou para ver a oferta”. Eles incluem visitantes sem conversões no período.
O botão do funil abre a lista de todas as origens, preservando período e tipo
de identidade. Cada cartão mostra badges, contagens brutas e último acesso/clique
no período. Esses sinais não alteram o perfil de compra de planos, o consentimento
de marketing nem os dados de conversão. Sem filtro de curso, a lista mantém a
regra anterior de exigir conversão no período.

As respostas de `/admin/dashboard/overview` e `/admin/dashboard` recebem
`course_funnel`; `/admin/active-users` recebe campos opcionais de interesse e
aceita `course_interest=all|visited|outbound`. Autorização administrativa e
`Cache-Control: no-store` permanecem. Não há mudança de schema.

Consulta somente de leitura, para exportação no editor do Neon:

```sql
SELECT
    (created_at::timestamptz AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
    event_type,
    page_path,
    identity_type,
    count(*) AS eventos,
    count(DISTINCT identity_id) AS identidades
FROM gettdone.product_events
WHERE event_type IN ('course_cta_view', 'course_cta_click',
                     'course_page_view', 'course_outbound_click')
  AND created_at::timestamptz >= now() - interval '30 days'
GROUP BY 1, 2, 3, 4
ORDER BY 1, 2, 3, 4;
```

Esta consulta de eventos brutos inclui testes e administradores, diferentemente
dos indicadores do curso no painel. Excluir
as identidades de teste conhecidas antes da análise comercial; não somar
identidades distintas entre dias ou etapas como se fossem pessoas novas.

## Implantação, validação e retorno

Publicar primeiro o backend que aceita os novos eventos e depois o frontend.
Se o frontend chegar antes, o backend antigo rejeita apenas a telemetria nova;
links e downloads continuam funcionando. Assets do conversor têm versão nova.
Nenhum novo serviço, segredo, tabela ou dependência é necessário.

- `scripts/smoke_course_promotion.py`: três conversores, 390/1440 px, anônimo,
  cadastrado gratuito/pago, restauração, download antes/depois do clique,
  ausência de impressão da pesquisa, deduplicação, origem, telemetria fora do
  ar e link sem JavaScript. Todo tráfego é interceptado com dados sintéticos.
- `backend/tests/test_product_telemetry_api.py`: identidade e persistência,
  sem contaminar o funil de compra de planos.
- `backend/tests/test_course_telemetry_http.py`: servidor HTTP local, eventos,
  conversão, downloads, indicadores e marcações no admin e caminhos negativos. `/analyze` continua aposentado
  (405); a conversão usa `/convert`.
- `scripts/smoke_conversion_feedback.py`: pesquisa e restauração com o teste
  de parceria desativado, incluindo o caminho de rollback.
- `backend/tests/test_admin_course_metrics.py`: coortes em ordem, deduplicação,
  origem, exclusão de admin, filtros/paginação e visitantes sem conversão.
  `test_admin_overview.py` cobre paridade e SQL não vazio em SQLite/PostgreSQL.
- `scripts/smoke_admin_course_metrics.py`: seis indicadores, troca de origem,
  taxas sem denominador, badges, filtros, datas, consentimento e logout em
  390/1440 px, com dados sintéticos. Capturas em `tmp/course-promotion-preview/`.

Para encerrar o teste, mudar `ENABLED` para `false` em `course-promotion.js`,
incrementar a versão desse script nas quatro páginas e publicar o frontend.
Isso restaura a pesquisa. Alternativamente, reverter o commit. Não apagar
eventos nem respostas históricas. Acompanhar erros em `/telemetry/events`,
downloads e contagens por origem após publicar.
