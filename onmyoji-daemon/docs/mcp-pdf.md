# PDF no MCP local

O módulo `pdf` oferece leitura e processamento documental sob demanda, no servidor stdio do Onmyōji. Trabalha com arquivos locais e não depende do daemon. O [módulo OCR](mcp-ocr.md) é reutilizado internamente quando o perfil PDF seleciona um perfil OCR habilitado.

## Preparação

Use **MCP > Integrações MCP > PDF** para criar um perfil. O setup sugere entrada, saída e trabalho em `<workspace>/.onmyoji/pdf/`, mostra o valor adotado por Enter e cria as pastas após validar a configuração. Autorize pastas adicionais do workspace de onde o agente recebe anexos quando necessário.

Cada opção de edição mostra seus atributos na segunda coluna. É possível alterar diretórios, descrição, perfil OCR, executáveis de PDF/A, habilitação e limites. Configurações ficam em `configs/mcp/pdf.toml`; documentos nunca são guardados ali. A opção de instalação prepara as bibliotecas no runtime privado do MCP, em `configs/mcp/venv`, dentro do domínio da instância. A dependência PyMuPDF usa AGPL/comercial; distribuições devem observar seus termos e os das demais dependências.

Sem perfil habilitado, nenhuma ferramenta PDF é publicada. Um perfil é implícito; com vários, consulte `pdf.list_profiles` e informe `profile`. `pdf.get_capabilities` verifica as bibliotecas no runtime privado, sem considerar pacotes presentes apenas no Python usado pelo setup. A ausência de OCR, PDF/A ou assinatura não impede operações estruturais.

### Configurar e conferir um perfil

1. Execute `python setupOnmyoji.py` na instância e abra **MCP > Integrações MCP > PDF > Criar perfil**.
2. Informe nome e descrição de uso. Aceite ou ajuste entrada, saída e trabalho; Enter usa o caminho exibido e o setup cria os diretórios validados.
3. Selecione o perfil OCR habilitado que atenderá o PDF, ou **Nenhum** para usar somente operações digitais. Prepare o OCR pelo [manual próprio](mcp-ocr.md).
4. Prepare os componentes básicos quando solicitado. Em **Gerenciar perfil > Diagnóstico e teste**, confira os motores disponíveis. A instalação de pacotes Python não instala os executáveis nativos.
5. Para PDF/A, use **Instalar ou reparar componentes > PDF/A** e configure Ghostscript, veraPDF e ICC na edição do perfil. Reabra a sessão MCP gerenciada após alterar os perfis para atualizar as ferramentas publicadas.

| Atributo | Uso administrativo |
| --- | --- |
| `description` | Explica em que contexto o agente deve escolher o perfil. |
| `readable_roots` | Autoriza as fontes; por padrão inclui as pastas `input` e `output`, permitindo reutilizar resultados. |
| `writable_roots` | Autoriza destinos finais; por padrão é a pasta `output`. |
| `work_directory` | Mantém os jobs transitórios, separado das fontes e dos resultados finais. |
| `ocr_profile` | Nome do perfil OCR habilitado usado internamente pelo PDF. Se estiver vazio, operações que precisarem de OCR solicitarão essa configuração. |
| `ghostscript_path`, `verapdf_path`, `icc_profile_path` | Recursos locais da conversão e validação PDF/A. |
| `enabled` | Controla a publicação do perfil; desabilitar ou remover o perfil não apaga os documentos. |

Os limites e a retenção são editados em **PDF > Limites e retenção**. Alterar o diretório de trabalho de um perfil já carregado exige reiniciar o MCP.

## Diretórios e recursos

O workspace definido por `codex.project_directory` deve ser externo e disjunto da instância: nenhum deles pode conter o outro. As raízes de leitura e escrita e o diretório de trabalho precisam ser caminhos absolutos dentro desse workspace. Links simbólicos e junctions são recusados, inclusive nos ancestrais dos caminhos. As permissões são revalidadas durante a execução e antes de publicar resultados; trocar o workspace exige ajustar os perfis que ainda apontem para o local anterior.

`work_directory` é uma subpasta exclusiva para jobs e não pode ser o próprio workspace. Entradas e saídas finais não podem ficar dentro dela, mesmo que uma raiz autorizada mais ampla inclua esse caminho. Cada job usa sua própria subpasta como diretório corrente e mantém nela staging, temporários e caches das bibliotecas. A limpeza por retenção alcança esses dados transitórios, sem remover os documentos de entrada ou os resultados finais.

Recursos administrativos têm finalidade distinta dos documentos. O perfil ICC de PDF/A pode estar no workspace ou em `configs/mcp/pdf/resources`, sem links; isso não autoriza o perfil a ler documentos de `configs/`. Executáveis nativos como Ghostscript e veraPDF podem usar uma instalação administrativa fora do workspace. A instalação usa staging e caches em `tmp/document-install` para as bibliotecas documentais e `tmp/mcp-install` para a base do runtime MCP, dentro da instância; arquivos de processamento permanecem no workspace.

## Execução e resultados

Todas as operações sobre documentos retornam um job. Use `request_id` estável para repetir uma mesma solicitação sem executar novamente. Reutilizar a chave com argumentos ou fontes diferentes é erro. Operações de escrita, inclusive exportações e imagens, exigem `confirm=true` e destinos novos.

Antes de usar as ferramentas em documentos, leia o procedimento operacional com `workflows.read`, usando `id: pdf-operations`. A rota é publicada quando há perfil PDF habilitado; consulte também o [manual de Workflows](mcp-workflows.md).

As ferramentas de diagnóstico e consulta de jobs respondem diretamente. Uma operação documental retorna `job_id`: consulte `pdf.get_job` até sair de `queued` ou `running` e leia `pdf.get_job_result` somente após `succeeded`. Estados finais também incluem `failed`, `cancelled` e `interrupted`. Jobs interrompidos não são repetidos automaticamente. `pdf.cancel_job` cancela o processamento e `pdf.list_jobs` lista o histórico do perfil.

Envie para `pdf.get_job`, substituindo o identificador pelo retornado na operação:

```json
{"job_id":"0123456789abcdef0123456789abcdef"}
```

Envie para `pdf.get_job_result`:

```json
{"job_id":"0123456789abcdef0123456789abcdef","offset":0,"limit":100}
```

Resultados incluem artefatos, hashes SHA-256, tamanho, duração e dados próprios da operação. O parâmetro `offset`/`limit` pagina coleções. Quando `encoding` for `json-fragments`, concatene `result_fragment` seguindo `next_offset` e decodifique o JSON completo. Não trate um fragmento isolado como resultado completo. O arquivo `result_file` permanece no diretório de trabalho pelo prazo de retenção.

> [!IMPORTANT]
> A fonte é preservada. As saídas são preparadas e validadas antes da publicação; arquivos existentes não são substituídos. Cancelamento ou falha não devem ser tratados como conclusão parcial. Diretórios de trabalho não podem ser usados como destino final.

## Seleção e operações

Páginas começam em **1**. `pages` aceita intervalos como `1-3,7,10-12`; `page_order` aceita uma lista explícita para reordenação e duplicação. `region` usa `[x0,y0,x1,y1]` em pontos, origem superior esquerda da página visível depois da rotação. As caixas retornadas seguem o mesmo referencial.

As famílias publicadas incluem inspeção, texto e busca, tabelas, renderização, imagens, seleção/mesclagem/divisão de páginas, transformações, criação, carimbos, metadados, marcadores, anexos, anotações, formulários, tarjas, sanitização, otimização, proteção, reparo, comparação e validação. Os schemas de `tools/list` definem os argumentos e ações aceitos.

### Catálogo de ferramentas

Todas as ferramentas documentais exigem `request_id`. Usam `input_path`, exceto `merge` e `create`, que recebem suas próprias fontes. Quando houver vários perfis habilitados, `profile` é obrigatório também nas consultas de capacidade e jobs; com um perfil, o campo não faz parte do schema. Os objetos são fechados: atributos desconhecidos são recusados.

| Ferramenta | Operações e parâmetros principais |
| --- | --- |
| `pdf.inspect` | Estrutura, páginas, caixas, fontes, imagens, campos, anexos, criptografia e indicadores de assinatura. |
| `pdf.extract_text` | `mode`: `native`, `ocr` ou `auto`; `format`: `text`, `json` ou `markdown`; aceita `pages`, `region` e exportação em `output_path`. |
| `pdf.search` | `query`, `regex`, `case_sensitive`, `pages` e `region`; retorna ocorrências com página e coordenadas. |
| `pdf.extract_tables` | Modos nativo/OCR/automático; estratégia digital `lines`, `lines_strict` ou `text`; exporta JSON, CSV ou XLSX. |
| `pdf.render_pages` | PNG/JPEG com `dpi`, seleção e região; `output_directory` para várias imagens ou `output_path` para uma página ou `contact_sheet: true`. |
| `pdf.extract_images` | Imagens incorporadas das páginas selecionadas, em `output_directory`; não equivale a renderizar a página. |
| `pdf.select_pages` | `action`: `extract`, `delete`, `reorder`, `duplicate` ou `insert`; `pages`/`page_order`, `insert_path`, `insert_at` e `output_path`. |
| `pdf.merge` | `input_paths`, `output_path` e `action`: `concat` ou `interleave`. |
| `pdf.split` | `ranges` ou `action: bookmarks` para marcadores de primeiro nível; arquivos `part-0001.pdf` etc. em `output_directory`. Sem intervalos, separa cada página. |
| `pdf.transform_pages` | `action`: `rotate`, `crop`, `resize` ou `margins`; respectivamente `angle`, `region`, `width`/`height` ou `margins: [esquerda, topo, direita, base]`. |
| `pdf.create` | Novo PDF de `text` ou de imagens em `input_paths`; `width`/`height` controlam as páginas de texto. |
| `pdf.stamp` | Texto, `image_path` ou primeira página de `overlay_path`; aceita região, cor RGB de 0 a 1, opacidade e `position`: `header`, `footer` ou `page_number`. |
| `pdf.metadata` | `action`: `get` ou `set`; `values` contém os metadados a gravar em uma cópia. |
| `pdf.bookmarks` | `action`: `get` ou `set`; `entries` contém listas `[nível, título, página]`, com página iniciando em 1. |
| `pdf.attachments` | `action`: `list`, `extract`, `add` ou `remove`; usa `attachment_name`, `attachment_path` e destinos conforme a operação. |
| `pdf.annotations` | `action`: `list`, `add` ou `remove`; entradas de tipo `highlight`, `text`, `rectangle` ou `circle`; remoção pelos `xrefs` retornados. |
| `pdf.forms` | `action`: `list`, `export`, `fill` ou `flatten`; `values` associa nomes reais dos campos aos valores. |
| `pdf.preview_redactions` | Marca regiões de `redactions` ou ocorrências de `query` em uma cópia para revisão. |
| `pdf.apply_redactions` | Aplica e verifica remoção definitiva nas mesmas regiões; também remove metadados, anexos incorporados e revisões anteriores. |
| `pdf.sanitize` | Flags `remove_metadata`, `remove_attachments`, `remove_actions`, `remove_hidden_text` e `remove_links`. |
| `pdf.optimize` | Regravação sem perdas por padrão; `lossy: true` exige `image_dpi` explícito e permite `quality` JPEG. |
| `pdf.protect` | `action`: `encrypt` com `new_password_ref`, ou `decrypt` com senha conhecida em `password_ref`. |
| `pdf.repair` | Regrava um documento recuperável em `output_path` e confere a estrutura da cópia. |
| `pdf.compare` | Compara `input_path` com `other_path`; exporta relatório em `output_path` e diferenças visuais em `output_directory`, se solicitados. |
| `pdf.validate` | `validation`: `structure`, `signatures` ou `pdfa`; `action: check` ou `convert_pdfa`; certificados locais opcionais em `trust_roots`. |
| `pdf.make_searchable` | OCRmyPDF/Tesseract, com idiomas, DPI, rotação, correção de inclinação e seleção de páginas; produz um novo PDF. |
| `pdf.get_capabilities` | Motores, pacotes, versões, idiomas e recursos disponíveis. |
| `pdf.get_job`, `pdf.get_job_result`, `pdf.cancel_job`, `pdf.list_jobs` | Estado, resultado paginado, cancelamento e histórico do perfil. |
| `pdf.list_profiles` | Disponível somente quando há múltiplos perfis habilitados. |

- `extract_text` oferece `native`, `ocr` e `auto`, com proveniência por bloco. Em páginas híbridas, o modo automático evita reconhecer novamente regiões que já possuem texto. OCR e leitura de layout podem ser parciais; consulte advertências.
- `extract_tables` oferece tabelas nativas e OCR avançado, com exportação JSON/CSV/XLSX. Resultados de tabelas digitalizadas dependem do backend avançado preparado no setup.
- `transform_pages` diferencia rotação, recorte, redimensionamento e margens. **Recorte visual não remove conteúdo oculto**.
- `stamp` insere texto, logotipo ou página PDF sobreposta. `{page}` e `{pages}` permitem numeração; carimbo visual não equivale a assinatura digital.
- `forms` consulta/preenche/exporta/achata AcroForms. A gravação verifica os valores canônicos, os widgets e suas aparências. XFA é identificado e não é editado.
- `attachments` lista e extrai arquivos incorporados sem executá-los. Nomes de anexos não definem caminhos arbitrários de saída.

## Manual de uso com exemplos

Os exemplos pressupõem um único perfil habilitado e fontes existentes dentro das raízes de leitura. Novos perfis autorizam o Work inteiro como raiz comum e preservam perfis anteriores sem migração automática. Quando uma operação que produz arquivo não receber destino, ela publica em `Work/.onmyoji/pdf/output/` ou `Work/.onmyoji/ocr/output/`; destinos explícitos continuam exigindo uma raiz de escrita autorizada. Cada bloco é o objeto de argumentos da ferramenta indicada. Para repetir uma tarefa com parâmetros diferentes, use outro `request_id`.

### Inspecionar, ler e localizar conteúdo

Comece conferindo páginas, rotação e recursos. Envie para `pdf.inspect`:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","request_id":"inspecionar-001"}
```

Extraia texto e coordenadas de páginas digitais. Envie para `pdf.extract_text`:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","pages":"1-3","mode":"native","format":"json","request_id":"texto-001"}
```

Envie para `pdf.search` para localizar uma expressão literal; habilite `regex` somente quando o padrão for uma expressão regular:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","query":"número do contrato","case_sensitive":false,"request_id":"buscar-001"}
```

Exporte tabelas digitais. Envie para `pdf.extract_tables`:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","pages":"2","mode":"native","strategy":"lines","format":"xlsx","output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/tabelas.xlsx","request_id":"tabelas-001","confirm":true}
```

### Extrair páginas, mesclar e rotacionar

Envie para `pdf.select_pages`:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","action":"extract","pages":"1-3,7","output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/paginas.pdf","request_id":"extrair-paginas-001","confirm":true}
```

Para reordenar ou repetir páginas, use `action: reorder` e `page_order: [3,1,1,2]`. Para excluir, informe as páginas a retirar com `action: delete`; não é permitido produzir um PDF sem páginas. Na inserção, `insert_at: 1` coloca as páginas do outro PDF no início; a posição máxima é o número de páginas resultantes da seleção mais 1.

Envie para `pdf.merge`:

```json
{"input_paths":["C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","C:/MeuWorkspace/.onmyoji/pdf/input/anexo.pdf"],"action":"concat","output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/mesclado.pdf","request_id":"mesclar-001","confirm":true}
```

`interleave` alterna a primeira página de cada documento, depois a segunda e assim por diante, até terminar todas as fontes.

Envie para `pdf.transform_pages` para acrescentar 90° à rotação das páginas selecionadas:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","action":"rotate","pages":"2,4","angle":90,"output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/rotacionado.pdf","request_id":"rotacionar-001","confirm":true}
```

Redimensionamento e margens compõem a aparência das páginas alteradas; seus campos e anotações interativas não são transferidos. Leia as advertências antes de substituir o uso de uma fonte interativa pela cópia.

### Gerar uma imagem de uma seleção

Envie para `pdf.render_pages` para renderizar uma região de uma única página:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","pages":"1","region":[40,80,420,300],"dpi":150,"format":"png","output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/selecao.png","request_id":"renderizar-regiao-001","confirm":true}
```

Para uma cópia PDF com recorte visual, utilize `pdf.transform_pages`, `action: crop`, a mesma região e um `output_path` terminado em `.pdf`. Essa cópia mantém conteúdo oculto: use tarjas para remoção definitiva. As coordenadas devem caber na página visível inspecionada; não reutilize a mesma região em páginas de tamanhos diferentes sem conferir.

### Criar uma camada pesquisável

Prepare o perfil OCR e os idiomas antes da chamada. Envie para `pdf.make_searchable`:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/digitalizado.pdf","mode":"auto","languages":["por","eng"],"dpi":300,"rotate":true,"deskew":false,"output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/pesquisavel.pdf","request_id":"ocr-pdf-001","confirm":true}
```

Para obter somente texto, use `pdf.extract_text` com `mode: auto` ou `ocr`. `make_searchable` cria a cópia PDF com camada de texto. Nesse processamento de PDF, `deskew: true` exige `mode: ocr`, que rasteriza páginas e achata objetos interativos; a combinação com `mode: auto` é recusada. O exemplo desabilita `deskew` explicitamente, inclusive se o perfil OCR o habilitar por padrão. Revise as advertências; encontrar texto na cópia não certifica a qualidade da transcrição.

### Preencher formulários e adicionar numeração

Use primeiro `pdf.forms` com `action: list` para obter nomes e opções reais. Depois envie para `pdf.forms`, substituindo `nome` pelo campo existente na fonte:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/formulario.pdf","action":"fill","values":{"nome":"Maria da Silva"},"output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/preenchido.pdf","request_id":"formulario-001","confirm":true}
```

Checkboxes e radios devem usar os valores aceitos pelo campo, incluindo seus identificadores de estado; não presuma que o texto visível seja o valor interno. `action: flatten` incorpora as aparências e remove os campos interativos. Campos de assinatura não são preenchidos por esta ferramenta.

Envie para `pdf.stamp` para numerar as páginas:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","action":"page_number","position":"page_number","text":"Página {page} de {pages}","font_size":10,"output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/numerado.pdf","request_id":"numerar-001","confirm":true}
```

### Revisar e aplicar tarjas

Envie para `pdf.preview_redactions`:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","redactions":[{"page":1,"rect":[40,80,420,120]}],"output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/previa-tarjas.pdf","request_id":"tarjas-previa-001","confirm":true}
```

A prévia ainda contém os dados originais. Após revisar as regiões, envie para `pdf.apply_redactions` usando a fonte original e outro destino:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","redactions":[{"page":1,"rect":[40,80,420,120]}],"output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/tarjado.pdf","request_id":"tarjas-aplicar-001","confirm":true}
```

É possível selecionar ocorrências de `query` no lugar de regiões explícitas. Para texto presente apenas em imagem, a busca nativa não identifica automaticamente as palavras; localize e revise as coordenadas com renderização/OCR antes de aplicar. A saída definitiva também descarta metadados, anexos e revisões anteriores, mesmo que estejam fora das regiões.

### Proteger e validar

Cadastre a senha no cofre e envie para `pdf.protect`; a entrada é uma referência, sem senha literal:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","action":"encrypt","new_password_ref":{"credential_profile":"cofre","credential_entry_path":"Documentos/PDF","field":"password"},"permissions":["print"],"output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/protegido.pdf","request_id":"proteger-001","confirm":true}
```

Envie para `pdf.validate` para validar a estrutura sem gerar uma cópia:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","validation":"structure","action":"check","request_id":"validar-001"}
```

Para PDF/A-2b, prepare os recursos administrativos e envie para `pdf.validate`:

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","validation":"pdfa","action":"convert_pdfa","output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/arquivo-pdfa.pdf","request_id":"pdfa-001","confirm":true}
```

## Tarjas, proteção e conformidade

`preview_redactions` produz uma prévia para revisão; `apply_redactions` remove texto, imagens e gráficos nas regiões selecionadas e regrava o PDF, descartando revisões antigas. A verificação inclui conteúdo extraível e aparência final. Use regiões precisas e revise a prévia; um resultado de OCR não prova sozinho a ausência de informação confidencial. Sanitização de metadados, anexos e ações ativas é uma operação complementar.

Senhas são referências do vault (`credential_profile`, `credential_entry_path`, `field: password`), nunca senhas literais no schema. `protect` oferece AES-256 ou descriptografia com senha conhecida. Não há descoberta de senhas.

Transformações de PDFs criptografados preservam a proteção na cópia e exigem referência de senha. Descriptografar é uma ação explícita de `protect`; mesclar documentos protegidos requer uma referência que abra todos eles.

Transformar um documento assinado exige `allow_signature_invalidation=true`, pois a cópia resultante não preserva a validade criptográfica original. A validação separa integridade, confiança da cadeia, validade temporal e revogação. Sem dados suficientes, o resultado é desconhecido; não se presume confiança. A validação de certificados não realiza consultas externas.

`validate` oferece estrutura, assinaturas e PDF/A. Conversão usa `validation: pdfa`, `action: convert_pdfa` e `output_path`, com Ghostscript, perfil ICC e veraPDF locais. Uma saída só é publicada como PDF/A-2b se o veraPDF comprovar conformidade. Não se promete conformidade de acessibilidade PDF/UA nem conversão fiel para Office.

## Limites e manutenção

Os padrões são 250 MiB por entrada, 2.000 páginas, 50 megapixels por renderização, um job ativo por perfil, fila de 16 solicitações e 30 minutos por execução. São configuráveis pelo setup. Processos isolados mantêm o servidor responsivo; isso não é uma sandbox de sistema operacional.

Jobs e temporários expiram após 24 horas por padrão; os resultados finais não são apagados. Texto, anexos, scripts e links do PDF são conteúdo não confiável: o serviço não executa ações incorporadas nem abre links. Processamento e modelos são locais.

### Diagnóstico de falhas

| Mensagem ou código | Como resolver |
| --- | --- |
| Ferramenta indisponível | Habilite um perfil PDF e reabra a sessão MCP gerenciada; com vários perfis, selecione o correto. |
| `invalid_argument` | Consulte o schema publicado. Confira ações, nomes dos atributos e campos obrigatórios; não envie `profile` quando só houver um perfil. |
| `path_not_allowed` | Confira workspace, raízes, ausência de links/junctions e separação do trabalho temporário; entradas e destinos devem ser absolutos. |
| `output_exists` | Escolha outro nome. O serviço não sobrescreve arquivos existentes. |
| `confirmation_required` | Inclua `confirm: true` na operação que exporta ou modifica arquivos. |
| `dependency_missing`, `dependency_unavailable` ou `backend_unavailable` | Execute o diagnóstico e prepare o componente exigido; confira também os executáveis nativos. |
| `models_unavailable` ou `language_unavailable` | Prepare modelos/idiomas pelo roteiro OCR antes do processamento. |
| `signed_document` | Para criar uma derivação, reconheça o impacto com `allow_signature_invalidation: true`; preserve a fonte assinada. |
| `invalid_password` ou `password_required` | Confira a referência do cofre e a senha conhecida da fonte. |
| `queue_full`, limites de páginas/pixels ou `timeout` | Reduza a seleção/DPI, aguarde a fila ou ajuste o limite administrativo conforme os recursos da máquina. |
| `verification_failed` ou `pdfa_nonconformant` | A saída não comprovou os requisitos e não deve ser tratada como concluída; revise o documento e os recursos do motor. |

Consulte sempre o estado final do job e suas advertências. Repetir a mesma solicitação com o mesmo `request_id` recupera o registro anterior; para tentar novamente após corrigir uma falha, use outro identificador. Procedimentos operacionais adicionais podem ser consultados nas ferramentas de [Workflows](mcp-workflows.md) quando habilitadas.
