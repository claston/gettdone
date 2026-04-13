# Spike 34 - PDF de Extrato Real (Nubank PJ Nov/2023)

## Arquivo avaliado

- `backend/samples/NU_150702837_01NOV2023_30NOV2023.pdf`

## Resultado tecnico

- Extracao de texto por pagina: `OK` (4 paginas com texto extraivel)
- Layout inferido: `nubank_statement_ptbr`
- Confianca de inferencia: `1.0`
- Fallback generico usado: `nao`
- Transacoes extraidas: `12`
- Entradas extraidas: `6`
- Saidas extraidas: `6`
- Janela de datas extraida: `2023-11-06` ate `2023-11-30`

## Contrato minimo de qualidade (aceite para evolucao)

- O parser deve retornar pelo menos `1` transacao valida para PDF de texto nativo.
- O parser deve retornar ao menos uma entrada e uma saida quando o extrato possui ambos os tipos.
- A inferencia de layout deve retornar:
  - `layout_name` nao vazio
  - `layout_inference_confidence >= 0.2`
- Em caso de PDF sem texto extraivel, a API deve responder erro orientativo (`400`) sem quebrar o pipeline.

## Riscos encontrados

- PDFs escaneados sem camada de texto continuam fora do corte atual (sem OCR nesta fase).
- Variacao de layout entre bancos exige crescimento incremental dos perfis de inferencia.
- Quebras de linha no historico podem afetar granularidade da descricao, mas nao impedir extracao de data/valor.

## Decisao para seguir

- Viabilidade confirmada para fluxo texto-nativo.
- Pode seguir para parser de producao e inferencia automatica sem combobox de banco.
