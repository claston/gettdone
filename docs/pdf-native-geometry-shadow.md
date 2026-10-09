# Normalização geométrica nativa de PDFs — shadow mode

Esta primeira entrega avalia uma extração baseada nas coordenadas do texto nativo do PDF sem alterar o arquivo gerado para o usuário. O parser atual continua sendo a fonte oficial; a nova execução é comparada em paralelo e grava somente telemetria técnica agregável.

## Ativação gradual

```env
PDF_NATIVE_GEOMETRY_SHADOW_ENABLED=true
PDF_NATIVE_GEOMETRY_SHADOW_PERCENT=5
```

O percentual usa uma amostragem determinística pelo hash do PDF: o mesmo arquivo sempre cai no mesmo grupo. Valores inválidos ou ausentes desativam a execução. A sugestão de rollout é 5%, 25% e 100%, avançando apenas quando conflitos, regressões e latência estiverem aceitáveis.

O caminho geométrico usa somente texto já presente no PDF. Ele não chama OCR, Textract nem serviços externos.
PDFs que explicitamente não possuem texto nativo são ignorados antes da extração geométrica e aparecem como
`not_applicable`; assim, documentos escaneados não adicionam custo nem latência ao shadow mode.

## O que é medido

A tabela `pdf_native_geometry_shadow_events` armazena a classificação da comparação, layouts e parsers identificados, contagens de transações, conflitos de data/valor/sinal, duração e estatísticas da reconstrução. Não armazena nome de arquivo, texto extraído, descrição de lançamento, valor de transação, dados de conta ou identidade do usuário.

As métricas aparecem no painel admin em **Normalização geométrica de PDFs**, respeitando o período e o filtro de identidade. O painel mostra:

- avaliações e execuções concluídas;
- resgates e ganhos potenciais;
- equivalências, conflitos, regressões, erros e resultados inconclusivos;
- mediana e p95 do custo adicional;
- recorte por layout reconhecido.

Classificações importantes:

- `potential_rescue`: o parser oficial falhou e a geometria encontrou transações;
- `potential_gain`: todas as transações oficiais foram preservadas e a geometria encontrou outras;
- `equivalent`: os resultados coincidem;
- `conflict`: há divergência inequívoca de data, valor ou sinal;
- `regression`: a geometria perdeu transações ou falhou enquanto o parser oficial funcionou.
- `not_applicable`: o documento não possui texto nativo e está fora do objetivo desta implementação.

## Avaliação local com amostras privadas

Os PDFs devem permanecer fora do repositório. O comando não exibe nomes: cada amostra aparece por um prefixo SHA-256.

```powershell
cd backend
venv\Scripts\python.exe -m scripts.evaluate_pdf_native_geometry_shadow `
  --samples-dir C:\caminho\privado\pdfs
```

Por padrão, o baseline também roda sem fallback de OCR para deixar a comparação focada em texto nativo. Use `--allow-baseline-ocr` somente quando quiser medir o comportamento atualmente configurado no ambiente.

## Banco e implantação

A migração `20261009_01` é aditiva: cria uma tabela e dois índices sem alterar tabelas existentes. Ela deve ser aplicada antes de ativar a flag na aplicação.

```powershell
cd backend
venv\Scripts\python.exe -m alembic upgrade head
```

Para rollback imediato de comportamento, defina `PDF_NATIVE_GEOMETRY_SHADOW_ENABLED=false`; a tabela pode permanecer sem impacto nos fluxos existentes. O downgrade remove apenas a tabela de telemetria e seus dados, por isso deve ser usado somente depois de desativar a flag e decidir que o histórico não é mais necessário:

```powershell
cd backend
venv\Scripts\python.exe -m alembic downgrade 20261001_01
```
