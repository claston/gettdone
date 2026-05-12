### Fatia seguinte: pacote maior de golden fixtures sintÃ©ticas (fase 3)

Branch: `feat/golden-fixtures-synthetic-pack3`

- Expande `backend/tests/fixtures/pdf_golden_samples.py` com blocos reutilizáveis de `parse_metrics` PDF:
  - cenário grouped com cobertura canônica
  - cenário inline sem saldo/documento
- Migra `test_analyze_service_multiformat.py` para reutilizar essas fixtures, removendo duplicação extensa de dicionários.
- Mantém comportamento e asserts funcionais inalterados.

Validação da fatia de golden fixtures (fase 3):

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_analyze_service_multiformat.py backend\tests\test_pdf_parser.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\tests\fixtures\pdf_golden_samples.py backend\tests\test_analyze_service_multiformat.py
```

Resultados:

- `9 passed`
- `All checks passed!`

## Fatia seguinte: pacote maior de golden fixtures sinteticas (fase 4)

Branch: `feat/golden-fixtures-synthetic-pack4`

- Expande `pdf_golden_samples.py` com blocos reutilizaveis de `parse_metrics` para cenarios PDF:
  - grouped com cobertura canonica
  - inline sem saldo/documento
- Migra `test_analyze_service_multiformat.py` para usar as fixtures compartilhadas.
- Resultado: menor duplicacao e menor risco de drift entre testes de parser e service.

Validacao da fatia de golden fixtures (fase 4):

```powershell
..\..\backend\venv\Scripts\python.exe -m pytest backend\tests\test_analyze_service_multiformat.py backend\tests\test_pdf_parser.py -q -p no:cacheprovider
..\..\backend\venv\Scripts\python.exe -m ruff check backend\tests\fixtures\pdf_golden_samples.py backend\tests\test_analyze_service_multiformat.py
```

Resultados:

- `9 passed`
- `All checks passed!`
