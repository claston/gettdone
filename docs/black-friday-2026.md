# Black Friday 2026

Contratação de **23/11/2026 às 00h00 até 30/11/2026 às 23h59**, horário de Brasília. Como a campanha começa hoje, as ofertas ficam disponíveis assim que a versão for publicada.
O servidor usa o intervalo fechado no início e aberto no fim: `2026-11-23T03:00:00Z <= agora < 2026-12-01T03:00:00Z`.

| Código | Plano | Preço | Franquia |
| --- | --- | --- | --- |
| `essencial_black` | Essencial Black | R$ 19,90/mês | 600 páginas/mês |
| `profissional_black` | Profissional Black | R$ 29,90/mês | 1.000 páginas/mês |
| `black_friday_3_meses` | Black Friday 3 meses | R$ 59,90, pagamento único | 1.000 páginas/mês por 3 meses |

Nos mensais Black, preço e franquia permanecem enquanto o cliente mantiver o plano. A cobrança e a confirmação do pagamento continuam no fluxo manual existente. O pacote termina três meses após a ativação, sem renovação automática. A franquia dos planos Black renova no aniversário mensal da ativação, sem acumular páginas; dias inexistentes são ajustados para o último dia do mês, preservando o aniversário original nos meses seguintes.

Planos e assinaturas anteriores não são alterados. A campanha usa versões separadas. Durante a campanha, o catálogo público apresenta as três ofertas; depois, volta aos planos públicos anteriores. Pedidos feitos durante a campanha preservam seu preço e podem ser liberados após 30/11. O prazo contratado começa na liberação. Recursos pagos, upload de 10 MB, limites por arquivo e regras de extração seguem a política existente.

## Destaque na página principal

`index.html` (incluindo `/`) e `ofx-landing.html` exibem uma faixa preta/amarela
abaixo do menu e um selo Black Friday ao lado de Planos. Ambos levam a
`planos.html`. O menor preço mensal vem de `GET /plans`; o pacote pré-pago não é
usado no valor anunciado como mensal. Não há mudança nos metadados SEO.

Os destaques começam ocultos e só aparecem durante a janela da campanha quando
o catálogo confirma uma oferta mensal Black válida. Erro, indisponibilidade ou
catálogo sem essas ofertas mantém a página normal. O navegador consulta novamente
ao retornar à aba ou restaurar a página e oculta a promoção na virada para
01/12, às 00h de Brasília, mesmo com a aba aberta. A checagem de datas no navegador
é uma proteção visual adicional; a API continua controlando as contratações.

Publicar também `black-friday-home.css` e `black-friday-home.js` junto dos dois
HTMLs. Se houver cache de HTML na CDN, invalidar `/`, `/index.html` e
`/ofx-landing.html`. Para reverter apenas o destaque, reverter esses arquivos;
os planos e contratos existentes não são afetados.

## Publicação

1. A partir de `backend`, aplicar `venv/Scripts/python.exe -m alembic upgrade head`, com o banco e schema do ambiente de destino.
2. A migração `20261123_01` insere três linhas privadas, com conflito ignorado; não altera tabelas, colunas, planos antigos ou assinaturas. As versões antigas da API continuam exibindo somente as linhas públicas anteriores.
3. Atualizar **todos os workers antes da API**. Os novos workers aceitam o campo opcional `quota_anchor_at` dos planos Black; pedidos dos planos existentes continuam sem esse campo quando vazio. Publicar a API e o frontend em seguida: a janela de vendas já está aberta desde 23/11. Não iniciar vendas com workers antigos.
4. `BLACK_FRIDAY_2026_ENABLED` tem padrão `true`. Para interromper novas vendas, definir `false` na API e reiniciar suas instâncias. Essa opção não remove direitos contratados nem impede liberar pedidos já recebidos. Invalidar o cache HTTP de `/plans` se houver CDN; o máximo normal da API é 300 segundos mais 60 de revalidação. Próximo às datas de início e fim, o cache é limitado e exige revalidação.
5. Conferir `GET /plans` antes/durante/depois da janela, os três links de checkout, os e-mails e o valor do link Pix criado pela operação. Não há criação automática de cobranças externas nesta mudança.

## Compatibilidade e rollback

- Upgrade idempotente: `ON CONFLICT` mantém os registros existentes; não há backfill nem mudança de contratos. Impacto esperado: três inserções, sem indisponibilidade programada.
- O downgrade mantém as linhas privadas para preservar referências dos pedidos e das assinaturas. Reaplicar o upgrade não duplica os planos.
- **Depois de vender**, interromper novas vendas pelo controle acima e manter a API/workers que reconhecem o vencimento. Não reverter integralmente para o runtime anterior: ele não entende o aniversário mensal e a expiração do pacote trimestral. Um rollback visual do frontend é possível mantendo o backend atualizado, mas a versão antiga do checkout não deve anunciar o pacote como mensal.
- Monitorar erros de `/plans`, `/checkout/intents`, liberações de pedidos, falhas de jobs e acesso de assinantes Black. Verificar amostras de preço/franquia, renovação mensal e bloqueio após o terceiro mês. O retorno ao catálogo normal não deve cancelar os planos Black já contratados.

## Validação

- `backend/tests/test_black_friday.py`: preços, janela de venda, cache, liberação posterior, franquias, fim de mês, expiração, recursos pagos e preservação das assinaturas anteriores.
- `backend/tests/test_black_friday_http.py`: catálogo e pedidos pela aplicação em HTTP real, com provedor de e-mail local em modo de teste.
- `backend/tests/test_black_friday_migration.py`: PostgreSQL local via `BLACK_FRIDAY_TEST_DATABASE_URL`, upgrade/downgrade/re-upgrade, leitores anteriores, direitos PRO e vencimento. Recusa bancos fora de localhost.
- `backend/venv/Scripts/python.exe scripts/smoke_black_friday.py`: desktop/celular, faixa e selo na home, destino dos links, preço dinâmico, catálogo indisponível, início/fim e expiração com aba aberta; valores, três checkouts, links indisponíveis, menu e ausência de rolagem horizontal de 320 a 1.440 px. Capturas em `tmp/black-friday/`, incluindo `home-desktop.png` e `home-mobile.png`.
- `backend/venv/Scripts/python.exe scripts/test_backend.py`: suíte completa, com temporário exclusivo e limpeza automática.
- Revisão das mensagens: página de planos, checkout, pedido na área do cliente, detalhe administrativo e e-mails. Preço trimestral identificado como pagamento único; franquia mensal e ausência de renovação explícitas. Oferta indisponível aponta para a página real de planos. Nenhuma regra de extração ou aviso financeiro foi alterado.
- A rota antiga `POST /analyze` já está desativada na versão atual (405); o fluxo de conversão vigente usa `POST /convert` e download por `/convert-report/{processing_id}`. Não reativar a rota antiga para validar esta campanha.
