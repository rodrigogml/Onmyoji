# Excel no MCP local

O módulo `excel.*` oferece inspeção e leitura direta de pastas OOXML e operações pelo Excel desktop em instância própria oculta. O servidor MCP gerencia jobs e não precisa executar o daemon como serviço. A fonte é preservada; alterações são preparadas em cópia e publicadas em destino novo.

## Preparação

Abra **MCP > Excel** no setup, crie um perfil e confira as raízes de leitura/escrita e o diretório de trabalho. Os caminhos precisam pertencer ao workspace externo configurado da instância. Configurações administrativas ficam em `configs/mcp/excel.toml`; planilhas, relatórios e staging ficam no workspace.

O backend direto usa leitura de ZIP/XML e não depende de Excel. O backend nativo requer Windows, Excel desktop instalado/ativado e `pywin32` no [runtime Python compartilhado](../../docs/runtime-python.md). **Instalar ou reparar bibliotecas** prepara componentes Python nesse runtime; não instala nem ativa o Office. **Diagnóstico estático** não abre Excel. Para testar a ativação oculta, use `excel.native_probe`.

Sem perfil habilitado, nenhuma ferramenta Excel é publicada. Um perfil é implícito; vários exigem `profile`, e `excel.list_profiles` permite descobri-los. Reinicie a sessão MCP gerenciada depois de alterar os perfis para atualizar as ferramentas/instruções disponibilizadas ao cliente.

Um perfil novo fica habilitado. Em **Gerenciar perfil**, a opção **Desabilitar/Habilitar** controla a publicação das ferramentas para esse perfil; desabilitar ou remover não apaga documentos nem resultados. O menu principal mostra os perfis configurados, e os diagnósticos são apresentados em fluxo, sem iniciar o Excel ou alterar políticas do Office.

| Permissão do perfil | Efeito |
| --- | --- |
| `allow_write` | Publicar pastas e exportações em destinos novos |
| `allow_vba_source` | Ler/editar fonte VBA, quando VBIDE e projeto permitirem |
| `allow_macros` | Executar procedimento VBA explicitamente selecionado; exige `trusted_content` |
| `allow_refresh` | Atualizar conexões e fontes externas |
| `allow_object_api` | Usar o adaptador de membros COM permitidos |
| `allow_signature_changes` | Permitir operações que afetem assinatura, com reconhecimento explícito da consequência |
| `allow_unverified_active_content` | Permitir abertura nativa sem inspeção integral do conteúdo ativo em formatos binários/criptografados; exige `trusted_content` |
| `allow_addins` | Autorizar arquivos de suplemento quando suportados pelo backend; exige `trusted_content` e não contorna políticas do Office |
| `trusted_content` | Declarar o perfil apto a processar conteúdo ativo confiável; não cria uma sandbox |

Por padrão, escrita está habilitada e as outras permissões estão desabilitadas. O setup não altera o Trust Center, políticas corporativas ou confiança em projetos VBA. Senhas aceitas por uma operação usam `password_ref` para o vault; não coloque senhas em código, logs ou nomes de perfil.

> [!IMPORTANT]
> O Excel é iniciado pelo modelo de objetos e permanece oculto. Não há automação por cliques/teclas e a sessão visível do usuário não é reaproveitada. Avisos de autenticação, suplementos ou VBA podem exigir UI; o job deve falhar em vez de operar o diálogo. Invisibilidade absoluta de componentes de terceiros exige uma sessão/VM separada do desktop do usuário.

## Ferramentas e tipos

Leia `workflows.read` com `id: excel-operations` antes de operar documentos. Os schemas retornados por `tools/list` são a referência exata dos atributos e limites aceitos; objetos e operações rejeitam atributos desconhecidos.

| Família | Operações |
| --- | --- |
| Diagnóstico | `get_capabilities`, `describe_feature`, `native_probe` |
| Inventário e leitura | `inspect`, `navigate`, `read_range`, `search`, `analyze_formulas` |
| Validação | `validate`, `compare` |
| Edição | `create`, `apply_changes`, `recalculate`, `refresh`, `export` |
| VBA | `get_vba_source`, `edit_vba`, `run_macro` |
| Adaptador de objetos | `describe_object`, `get_object`, `set_object`, `call_object` |
| Jobs | `get_job`, `get_job_result`, `cancel_job`, `list_jobs` |

`apply_changes.operations` recebe uma lista tipada. As famílias abrangem células/fórmulas, formatação, linhas/colunas, abas, nomes, tabelas, filtros/ordenação, validação, formatação condicional, pivôs, gráficos, segmentações, consultas M, medidas/relacionamentos do modelo, comentários, hyperlinks, impressão, proteção e Goal Seek. Cada família declara ações e campos próprios; o suporte efetivo depende do objeto, build, APIs e pré-requisitos conferidos pelo job.

As operações adicionais incluem preenchimento automático, agrupamento/subtotais, cenários, tabelas de hipóteses, minigráficos, shapes, imagens, controles de formulário e exibição/congelamento. Gráficos aceitam configuração de séries, eixos, legenda e rótulos; `export` oferece imagem PNG/JPEG de gráfico e JSON de valores. `copy_sheet_from` copia de outra pasta autorizada antes da última aba existente do destino. Em `outline` com `show_detail`, o intervalo identifica uma única linha/coluna de resumo. `query_load` materializa uma consulta M existente numa tabela; atualização efetiva exige `refresh:true` e a permissão correspondente.

Pivôs tradicionais também têm operações próprias para trocar a origem, filtrar campos por itens/rótulos/valores/datas, criar campos calculados, agrupar e extrair detalhes. `pivot_drilldown` exige uma célula de dados/total e nome explícito para a aba de detalhes; a rota `ShowDetail` não é oferecida para OLAP. Em `advanced_filter` com cópia, cabeçalhos e destino ficam na mesma aba para preservar a execução sem ativação de interface. Os operadores e requisitos de cada ação aparecem no schema.

`connection` permite criar conexões OLEDB/ODBC e conexões de tabela local para o modelo, com políticas de conteúdo confiável/fontes externas. Prefira `connection_ref` para obter a string no vault. Em tabelas locais, uma conexão `WORKSHEET;<caminho da pasta fonte>` com `command_text` igual ao nome da tabela, `command_type:7` e `create_model_connection:true` carrega a tabela no modelo. Medidas DAX e relacionamentos são operações separadas. Provedores externos podem estar disponíveis para Power Query e ausentes no processo do modelo; diagnostique cada rota, sem instalar ou registrar provedores automaticamente.

Use `navigate` com `kind:model` no backend nativo para consultar tabelas/colunas, medidas e relacionamentos existentes. O inventário só acessa o modelo quando as conexões indicam que ele já existe; uma inspeção não deve criar um modelo vazio como efeito colateral.

Entradas de célula distinguem valor e fórmula. Valores em `set_values` mantêm o tipo solicitado; fórmulas são escritas em `set_formula`. Use linguagem invariável por padrão. Fórmulas localizadas exigem `language: local`, sem substituições textuais de nomes ou separadores. Arrays dinâmicos usam o contrato `Formula2`; fórmulas legadas são uma modalidade própria.

Formatos numéricos usam códigos invariáveis do Excel, como `0.00` e `yyyy-mm-dd`, inclusive em Windows/Office em português. A leitura nativa retorna os mesmos códigos, sem traduzir tokens de data ou separadores. Escritas de valores e fórmulas em intervalos filtrados endereçam as células físicas indicadas, incluindo linhas ocultas. O preenchimento de fórmulas mantém o deslocamento relativo de referências; matrizes legadas conservam sua operação como bloco único. A cópia integral com linhas/colunas ocultas ou transposição exige origem e destino sem sobreposição.

Em `table` com `action:append`, a matriz contém todas as colunas. `preserve_calculated_columns` é verdadeiro por padrão: um valor nulo mantém a fórmula que o Excel propagou para uma coluna calculada; nas demais células, nulo representa vazio. Use `false` para substituir também essas fórmulas por valores/vazios explícitos. `set_values` continua tratando nulo como limpeza da célula.

Validações de lista aceitam `list_values:["Sim","Não"]`, codificado com o separador regional da instância Excel. Um item que contenha esse separador ou uma lista que exceda o limite de 255 caracteres exige `formula1` com referência a um intervalo. `list_values` e `formula1` são exclusivos. Texto literal em `formula1` mantém a sintaxe regional bruta do Excel; expressões iniciadas por `=` usam sintaxe invariável e são traduzidas pelo parser do Excel, incluindo em formatação condicional. A tradução usa apenas uma célula auxiliar da sessão privada, sem substituir nomes ou separadores no texto e sem alterar dados da pasta processada.

Para criar a partir de modelo, use `excel.create` com `template_path`. O modelo é preservado e gera uma pasta independente; a saída padrão mantém a capacidade de macros (`.xltm` gera `.xlsm`, `.xltx` gera `.xlsx`, `.xlt` gera `.xls`). Para editar o próprio conteúdo de um modelo, use `apply_changes` com `input_path` e uma saída nova no mesmo formato de modelo.

## Leitura e snapshots

O modo `backend: auto` prefere inspeção direta para formatos suportados e encaminha operações nativas ao Excel. `backend: direct` solicita apenas leitura direta, e `backend: native` solicita o motor desktop. Não há fallback silencioso de escrita para uma biblioteca que regrave a pasta com menor cobertura.

A leitura distingue fórmula, valor salvo em cache, tipo e origem. `freshness: saved_cache` não comprova que o resultado está atualizado. `display_text` só pode ser afirmado quando o backend realmente o produzir; inspeção XML não equivale à renderização do Excel. `offset` e `limit` paginam células, e `snapshot_id` identifica o conteúdo da fonte.

Em células com fórmula, `value` é nulo e `cached_value` contém o resultado salvo disponível; a expressão fica em `formula`. Para CSV/TSV/texto, informe `encoding`/`delimiter` quando necessário. O padrão é UTF-8 com BOM opcional, vírgula em `.csv` e tabulação em `.tsv`; `.txt` exige delimitador. Essas fontes têm uma aba lógica `Dados` e preservam todas as entradas como texto, incluindo identificadores e entradas iniciadas por `=`. Não há inferência de fórmulas ou tipos. Comparações podem usar `other_encoding` e `other_delimiter` na segunda fonte.

Exemplo de `excel.read_range`:

```json
{
  "input_path": "C:/Work/entrada/vendas.xlsx",
  "sheet": "Vendas",
  "range": "A1:F30",
  "fields": ["value", "formula", "cached_value", "type"],
  "offset": 0,
  "limit": 100,
  "backend": "direct",
  "request_id": "vendas-leitura-001"
}
```

## Alterar e calcular

Toda escrita exige `confirm=true` e produz um job. Esse argumento confirma a operação já solicitada no protocolo; não cria uma exigência de pergunta adicional ao usuário. `output_path` pode ser omitido: o módulo deriva um nome do `request_id` em `Work/.onmyoji/excel/output/`, sujeito às raízes do perfil. Um destino existente é recusado.

Exemplo de `excel.apply_changes`:

```json
{
  "input_path": "C:/Work/entrada/relatorio.xlsm",
  "output_path": "C:/Work/saida/relatorio-atualizado.xlsm",
  "request_id": "relatorio-revisao-001",
  "confirm": true,
  "operations": [
    {"type": "set_values", "sheet": "Premissas", "range": "B2", "values": [[0.12]]},
    {"type": "set_formula", "sheet": "Resumo", "range": "D2", "formula": "=SUM(Vendas[Valor])", "notation": "A1", "language": "invariant"}
  ],
  "calculation": {"mode": "full_rebuild", "refresh_connections": false},
  "validation": {"reject_new_formula_errors": true}
}
```

Cálculo e atualização são etapas independentes. `calculation.mode` aceita `none`, `incremental`, `full` e `full_rebuild`; `refresh_connections` exige a permissão de fontes externas. Na atualização embutida em outra escrita, `refresh_require_verified` e `refresh_pivots` são verdadeiros por padrão; permitem controlar a exigência de conclusão comprovada e a atualização dos pivôs. Um pedido explícito com `refresh_require_verified:false` admite conexões sem prova universal de conclusão, mantendo `unverified` no relatório. Na ferramenta `refresh`, os campos próprios `require_verified` e `pivots` têm prioridade. `enable_vba_udfs` permite solicitar cálculo com funções VBA, sujeito às mesmas permissões de execução/conteúdo confiável. `output_mode` controla o modo persistido (`preserve`, `manual` ou `automatic`). `calculate_on_open` configura comportamento futuro, sem substituir o cálculo observado agora.

Consultas Power Query carregadas em tabela precisam de verificação individual: `RefreshAll` pode conservar dados antigos sem lançar erro. O resultado deve separar cálculo, conexões, pivôs e persistência, indicando falhas e condições não verificadas. Não interprete `xlDone` como prova de conclusão de todo conector ou serviço cloud.

> [!WARNING]
> Staging e destino novo podem mudar referências relativas e fórmulas/macros dependentes de caminho. Operações cujo resultado não possa ser preservado ou verificado são recusadas. O mesmo vale para formatos/conteúdo ativo que não possam passar pelo preflight necessário à operação.

## Jobs, resultados e recuperação

Use um `request_id` estável por solicitação. Repetir a mesma chave, argumentos e conteúdo recupera o mesmo job; reutilizá-la com outro conteúdo é conflito. Um novo cálculo de funções voláteis ou uma nova atualização de dados requer novo pedido quando se deseja um novo resultado.

Consulte `excel.get_job` até sair de `queued`/`running`. Estados finais são `succeeded`, `failed`, `cancelled` e `interrupted`. Somente `succeeded` autoriza apresentar artefatos como concluídos. Consulte então `excel.get_job_result`, seguindo `next_offset`. Quando `encoding` for `json-fragments`, concatene todos os `result_fragment` antes de decodificar o JSON.

O supervisor controla prazo, cancelamento e processos próprios. Jobs interrompidos não são repetidos automaticamente. Isso é especialmente necessário em macros e consultas com efeitos externos: desfazer os arquivos publicados não desfaz mudanças em outros sistemas.

Cada diretório de jobs de um perfil tem um supervisor exclusivo durante a sessão MCP. Outro servidor que tente gerenciar o mesmo perfil recebe `profile_busy`; não pode reconciliar ou consultar a fila daquele supervisor enquanto ele estiver ativo. Perfis distintos podem enfileirar trabalhos em paralelo, mas o Excel é serializado por usuário/sessão Windows. A configuração inclui orçamento de memória para worker e Excel, além de prazo, quantidade de células/operações e tamanho de entrada/resultado.

O modo Python isolado também verifica o processo real do worker: no Windows, o executável de um venv pode iniciar um Python filho. A execução só começa depois que o supervisor verifica essa identidade e confirma sua contenção; o launcher sozinho não é tratado como prova de propriedade do worker.

`validation.require_preservation` é verdadeiro por padrão: mudanças inexplicadas em partes opacas impedem a publicação. Um pedido explícito com `false` pode admitir preservação não verificada, que deve permanecer identificada no relatório. Conferir caches e reabrir sem erro não comprova, por si só, preservação de todos os recursos.

O cálculo com UDF VBA pode alterar o binário do projeto somente pela compilação interna do Excel. Para reconhecer essa mudança sem classificar perda, o engine precisa de evidência privada vinculada aos arquivos, conferindo fontes, referências e recursos de desenho antes/depois. Isso depende do acesso VBIDE já permitido no host e no perfil; sem essa prova, a preservação permanece não verificada. Um argumento fornecido pelo cliente não substitui a evidência do engine.

Resultados incluem backend, verificações, artefatos e hashes quando aplicáveis. Relate pendências e limites junto dos arquivos. O relatório de validação estrutural não comprova equivalência visual, cálculo correto de negócio ou atualização de dados sem evidência específica.

Abas protegidas mantêm sua proteção durante a inspeção de valores, fórmulas e erros, respeitando o limite de células. Quando o Excel impedir a descoberta das validações nessa aba, `validate` informa `data_validations_not_accessible` e estado `unverified`, em vez de certificar regras que não conseguiu auditar.

Falhas de acesso a arquivos antes do enfileiramento, como bloqueio por outro processo, retornam `file_access_failed` com mensagem sanitizada. Libere o arquivo ou corrija o acesso e repita o pedido; caminhos e mensagens privadas do sistema operacional não são incluídos nesse erro público.

## Cobertura e limites

Use `get_capabilities`/`describe_feature` para distinguir implementação, disponibilidade e verificação. A versão do Office, arquitetura, canal, licença, políticas, suplementos e serviços podem mudar o suporte. Não há promessa de controlar todo recurso da interface por COM.

OOXML `.xlsx`/`.xlsm` e modelos equivalentes têm inspeção direta. Formatos binários/legados, arquivos criptografados, conteúdo XLM, projetos protegidos, assinatura VBA, controles e extensões modernas têm pré-requisitos e bloqueios específicos. VBA, Power Query e Data Model não são ativados ou reduzidos silenciosamente durante uma leitura.

Quando a inspeção de um formato não puder comprovar ausência de assinatura/conteúdo ativo, a escrita exige as políticas e reconhecimentos explícitos correspondentes. Para fontes/saídas binárias cuja preservação ainda não possa ser comprovada integralmente, use `calculation.output_mode: manual` e `validation.require_preservation:false`, reconhecendo a limitação no resultado. Todas as ferramentas de escrita aceitam esses dois objetos, incluindo edição VBA, adaptador de objetos e exportações. Isso não dispensa permissões do perfil nem aceita perdas de formato implicitamente. Uma combinação não homologada é recusada, sem substituir silenciosamente o modo solicitado. Em exportações de valores/imagens, `calculation.mode:none` mantém o cálculo não solicitado.

O [plano técnico](plans/mcp-excel.md) registra a direção de cobertura, os critérios de preservação e a estratégia de homologação. A presença de uma família no catálogo não comprova que todo subtipo existente no Excel esteja disponível no ambiente.
