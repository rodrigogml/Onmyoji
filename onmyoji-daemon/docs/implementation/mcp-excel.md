# Implementação do MCP Excel

**Revisão:** 2026-11-07. **Estado:** implementação e homologação local concluídas para o catálogo entregue.

O módulo `excel.*` integra o servidor MCP stdio existente, com perfis administrativos, setup, processamento por jobs e backends separados para leitura direta e Excel desktop oculto. O [manual operacional](../mcp-excel.md) descreve uso e limites; o [plano](../plans/mcp-excel.md) registra as decisões e o escopo de referência.

O catálogo contém 26 ferramentas e 48 tipos de operação em lote; `list_profiles` é acrescentada quando há vários perfis habilitados. A existência de uma família não certifica todos os seus subtipos, combinações, serviços ou versões do Excel.

## Entregas e evidências

| Contrato | Implementação e verificação |
| --- | --- |
| Leitura de arquivos | OOXML strict/transitional, inventário CFB, CSV/TSV/TXT, valores/fórmulas/caches, snapshots e paginação; corpus sintético em `test_mcp_excel_ooxml.py` e `test_mcp_excel_text.py` |
| Catálogo e configuração | Schemas fechados, permissões independentes, perfis, publicação condicional, setup e workflow; `test_mcp_excel_tools.py`, `test_mcp_excel_setup.py` e transporte real em `test_mcp_excel_stdio.py` |
| Executor oculto | COM STA exclusivo, PID/instante/executável/sessão verificados, mutex e Job Objects; testes reais de processo preexistente e encerramento abrupto em `test_mcp_excel_native_integration.py` |
| Runtime isolado | Handshake identifica o Python real quando o executável do venv é um launcher; aceitação ocorre após comprovação de descendência e contenção; `test_mcp_excel_venv.py` |
| Jobs e fontes | Idempotência por conteúdo e políticas, fontes auxiliares/modelos/FRX, reserva de perfil, cancelamento, prazo, memória, recuperação e publicação exclusiva; `test_mcp_excel_jobs.py` e `test_mcp_excel_supervision.py` |
| Edição e cálculo | Operações em lote, tabelas, fórmulas modernas, filtros, validações, gráficos e recálculo nativo; valores e fórmulas conferidos após salvar/reabrir e nos caches OOXML |
| Contratos de edição no Excel real | Datas 1900/1904, códigos numéricos invariáveis em pt-BR, cópias/transposição, linhas filtradas, proteção, validação e edição de tabelas; `test_mcp_excel_operations_integration.py` |
| VBA e UDF | Fonte, referências, edição/importação/exportação, execução explícita e cálculo com UDF condicionado às permissões; prova nativa respeita a configuração VBIDE já existente no host |
| Apresentação | Shapes, imagens, controles de formulário, minigráficos, séries/eixos, impressão, views e exportações; `test_mcp_excel_presentation.py` e `test_mcp_excel_worker_integration.py` |
| Análise e dados | Preenchimento, grupos, subtotais, cenários, tabelas de hipóteses, cópia entre pastas, consultas locais, Data Model, DAX e relacionamentos; `test_mcp_excel_analysis.py` e `test_mcp_excel_analysis_integration.py` |
| Pivôs avançados | Origem, filtros, campos calculados, agrupamento e drill-down, além de AdvancedFilter; `test_mcp_excel_pivot_operations.py` e `test_mcp_excel_pivot_integration.py` |
| Preservação | Conteúdo conhecido comparado semanticamente, inventário nativo antes/depois e partes opacas acompanhadas; `test_mcp_excel_preservation.py`; perdas não autorizadas impedem publicação |
| Atualização e exportação | Etapas de dados, cálculo e pivôs precisam respeitar dependências; exportação deve materializar resultados depois do cálculo solicitado; regressões em `test_mcp_excel_refresh_integration.py` e `test_mcp_excel_worker_integration.py` |

Os arquivos de testes da tabela ficam em `onmyoji-daemon/tests/`. Os testes usam fontes sintéticas e temporários em `tmp/`, sem editar documentos do usuário ou a configuração global do Excel.

## Ambiente e comandos de aceite

A homologação utiliza Windows, Python 3.12.1 x64 e Excel Microsoft 365 x64, instalação `16.0.20527.20150`. O transporte foi exercitado com MCP 2.3.0. Os testes de Excel real são opt-in por `ONMYOJI_TEST_EXCEL=1`; a suíte de contratos e leitura direta funciona sem iniciar Office.

No PowerShell, a seleção dos arquivos é explícita porque argumentos glob não são expandidos para o executável Python:

```powershell
$excelTests = @(rg --files onmyoji-daemon/tests -g 'test_mcp_excel*.py')
python -B -m pytest $excelTests -q --basetemp=tmp/pytest-excel -o cache_dir=tmp/pytest-cache-excel

$env:ONMYOJI_TEST_EXCEL = '1'
python -B -m pytest $excelTests -q --basetemp=tmp/pytest-excel-native -o cache_dir=tmp/pytest-cache-excel-native
```

Para regressão geral, usar `python -B -m pytest`, com dependências opcionais preparadas no runtime de teste. Diretórios de teste paralelos precisam ser distintos. O teste OCR legado de limite de caminho depende de um caminho efetivamente longo; o diretório base de verificação deve satisfazer essa condição, sem alterar o limite do código.

A regressão geral aprovou **3.004 testes e 301 subtestes**, com **39 casos ignorados** por requisitos opcionais, em 231,53 s. O comando usou `--basetemp=tmp/pytest-excel-final-monorepo-regression-long-path`; dependências de teste foram preparadas somente em `tmp/`. As correções finais ficaram restritas ao módulo Excel e foram revalidadas pela suíte específica completa abaixo.

A execução final de todos os `test_mcp_excel*.py`, com **`ONMYOJI_TEST_EXCEL=1`**, aprovou **474 testes sem casos ignorados**, em **273,21 s**. Ela utilizou o runtime de teste em `tmp/excel-coverage-runtime`, `--basetemp=tmp/pytest-excel-final-native-all` e cobertura de subprocessos. O cenário real de VBA verificou edição, execução e UDF; o cálculo de dependentes de pivôs, as exportações após recálculo e os cinco corpora de edição passaram com as verificações de preservação habilitadas.

A cobertura final do módulo, incluindo branches e subprocessos, foi de **87%**. Leitura OOXML atingiu 99%, texto tabular 100%, inspeção 98%, inspeção de texto 98% e preservação 93%. A medição não representa 100% de cobertura nem comprova todo recurso ou combinação do Excel; caminhos específicos de plataforma, falhas de infraestrutura e subtipos nativos ainda têm cobertura parcial. Os relatórios transitórios ficam em `tmp/excel-final-coverage/`.

## Decisões e limites operacionais

Um supervisor possui o diretório de jobs de cada perfil. Outro servidor MCP recebe `profile_busy` enquanto esse supervisor estiver ativo; a fila não é distribuída entre servidores. O mutex Windows serializa instâncias Excel também entre perfis distintos. Essa reserva impede reconciliar jobs ativos de outro processo.

O worker usa cópias e destinos novos. Rollback cobre artefatos publicados pelo MCP, não efeitos externos de VBA ou consultas. O modo nativo exige sessão de usuário; não deve executar Excel em Session 0. Avisos de terceiros podem exigir interação; o job interrompe a instância própria em vez de operar diálogos.

Recursos sem API pública, dependentes de serviço/licença/suplemento ou sem evidência de conclusão permanecem indisponíveis, bloqueados ou não verificados conforme o caso. Compilação integral de VBA, edição visual de UserForms, isolamento absoluto de UI de terceiros e sandbox de macros não são garantidos. `Visible=False` não substitui isolamento em outra sessão/VM quando esse requisito for absoluto.

Preservação integral não é inferida de uma abertura sem erro. Binários, criptografia e mudanças opacas podem exigir reconhecimento explícito da limitação com `validation.require_preservation=false`; o relatório continua não verificado. Os testes não devem relaxar essa política apenas para fazer uma operação passar: normalizações legítimas precisam ser demonstradas e perdas precisam continuar bloqueadas.

## Resultado do aceite

As regressões encontradas foram corrigidas sem desabilitar as verificações de preservação dos corpora. A suíte final comprovou endereços físicos em linhas filtradas, preenchimento relativo de fórmulas, matriz CSE única, cópia integral com formatação, datas 1900/1904, inteiro de 16 dígitos representável preservado em Excel/XML, regras localizadas pelo parser nativo e listas tipadas com separador regional.

A comparação reconhece somente normalizações demonstradas: recompilação UDF com prova privada de fonte/referências/desenho, metadados XLDAPR vinculados às fórmulas autorizadas, efeitos da conversão de tabela, remoção do último hyperlink e visibilidade das linhas afetadas pela limpeza do filtro. Conteúdo fora desses escopos permanece protegido. Encoding UTF-8 sem BOM, sintaxe Python, JSONs, links locais e whitespace foram conferidos.

O módulo pode ser habilitado em **MCP > Excel** no setup. A homologação é local, vinculada ao ambiente e ao catálogo descritos; as limitações operacionais acima continuam parte do contrato.
