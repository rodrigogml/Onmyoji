# OCR no MCP local

`ocr.*` reconhece texto em imagens locais e oferece análise de layout e tabelas. Também fornece o serviço interno usado por [PDF](mcp-pdf.md). Não utiliza o transporte MCP para reconhecer cada página e não depende do daemon.

Use este módulo para transcrever fotografias e digitalizações, obter a localização das palavras ou analisar a estrutura de uma imagem. Para ler PDFs ou produzir uma cópia PDF pesquisável, utilize o [manual do MCP PDF](mcp-pdf.md). As fontes são preservadas; exportações sempre usam arquivos novos.

## Ferramentas disponíveis

| Ferramenta | Finalidade e resultado |
| --- | --- |
| `ocr.get_capabilities` | Diagnóstico imediato de bibliotecas, Tesseract, idiomas e ambiente/modelos avançados. Não instala componentes. |
| `ocr.list_languages` | Lista imediata dos códigos de idioma instalados no Tesseract do perfil. Não é o catálogo de idiomas do PaddleOCR. |
| `ocr.recognize` | Job de reconhecimento de imagem ou TIFF multipágina, com texto, páginas, caixas e confiança disponível. Usa o motor configurado ou `engine` da chamada. |
| `ocr.analyze_layout` | Job de layout e tabelas com PaddleOCR/PP-StructureV3; requer preparação avançada mesmo que o perfil use Tesseract como padrão. |
| `ocr.get_job` | Estado e progresso disponível de um job do perfil. |
| `ocr.get_job_result` | Resultado concluído, paginado quando necessário, e caminhos dos artefatos publicados. |
| `ocr.cancel_job` | Cancela execução pendente ou em andamento. |
| `ocr.list_jobs` | Histórico de jobs do perfil. |
| `ocr.list_profiles` | Relação de perfis habilitados; publicada somente quando há mais de um. |

Sem perfil habilitado, o namespace OCR não é publicado. Com um perfil, ele é implícito e o schema não aceita `profile`. Com vários, consulte `ocr.list_profiles` e acrescente `"profile":"nome"` às demais chamadas, inclusive diagnóstico e acompanhamento de jobs. Os schemas de `tools/list` definem o contrato vigente e recusam atributos desconhecidos.

## Perfis e instalação

Use **MCP > Integrações MCP > OCR**. O roteiro oferece entrada, saída e trabalho em `<workspace>/.onmyoji/ocr/`, português e inglês (`por;eng`) e 300 DPI. Enter aceita os valores mostrados. O workspace deve ser externo e disjunto da instância; raízes e trabalho ficam exclusivamente nele, sem links simbólicos ou junctions nos caminhos. A edição associa cada atributo à opção que permite alterá-lo. A [regra de diretórios compartilhada com PDF](mcp-pdf.md#diretórios-e-recursos) também se aplica ao OCR, inclusive à revalidação quando o workspace muda e à proibição de entradas e saídas finais no diretório temporário de trabalho.

Para começar:

1. Crie um perfil com nome e descrição de uso; aceite ou ajuste as pastas sugeridas. O setup cria os diretórios após validar a configuração.
2. Aceite **Preparar componentes básicos agora** ou use **Instalar ou reparar componentes > Básico PDF e OCR**.
3. Providencie o Tesseract e os dados `por` e `eng` pela instalação nativa da máquina. Em **Gerenciar perfil**, configure o executável e, se necessário, a pasta `tessdata`.
4. Use **Diagnóstico e teste** para conferir o motor, as bibliotecas e os idiomas antes de processar documentos.
5. Para layout e tabelas, prepare **OCR avançado CPU** ou **OCR avançado GPU NVIDIA**. A preparação baixa modelos explicitamente; uma chamada de OCR não faz essa instalação.

O menu de gerenciamento permite alterar dados, habilitar/desabilitar e remover o perfil. Remover o perfil preserva documentos e resultados. **Limites e retenção** altera os limites do namespace; as opções de edição exibem seus valores atuais na segunda coluna.

A configuração local é `configs/mcp/ocr.toml`. Cada perfil define engine, idiomas, DPI, executável e dados de idioma do Tesseract, diretórios autorizados e modelos avançados. `ocr.get_capabilities` diagnostica motores, versões e modelos; as bibliotecas Python são verificadas no runtime privado do MCP, sem aproveitar pacotes instalados apenas no Python do setup. `ocr.list_languages` lista os idiomas disponíveis no Tesseract instalado.

| Atributo do perfil | Uso |
| --- | --- |
| `description`, `enabled` | Contexto apresentado ao agente e publicação do perfil. |
| `readable_roots` | Pastas de entrada autorizadas; por padrão inclui `input` e `output`. |
| `writable_roots` | Pastas autorizadas para exportações finais; padrão `output`. |
| `work_directory` | Subpasta exclusiva dos jobs transitórios; padrão `work`. |
| `engine` | `tesseract` para reconhecimento básico ou `paddleocr` para o motor avançado preparado. |
| `languages` | Códigos como `por` e `eng`; informe separados por `;` no setup. |
| `dpi` | Referência de resolução OCR, entre 72 e 600; padrão 300. Em imagens, informar DPI não recupera detalhes ausentes nem aumenta a resolução da fonte. |
| `tesseract_path`, `tessdata_dir` | Executável e dados de idioma. Pasta de dados vazia usa a descoberta do Tesseract. |
| `advanced_model_directory` | `state/mcp/ocr/models` da própria instância ou uma subpasta dessa área. |

Há três opções de preparação pelo setup:

| Nível | Preparação e finalidade |
| --- | --- |
| Básico | Bibliotecas PDF/OCRmyPDF no runtime único `venv`; Tesseract e seus modelos de idioma são dependências nativas locais. |
| Avançado CPU/GPU | PaddleOCR/PP-StructureV3 no runtime único `venv`, com modelos preparados explicitamente no estado local; GPU exige instalação compatível e dispositivo disponível. |
| PDF/A | Bibliotecas de suporte e diagnóstico de Ghostscript/veraPDF; configure executáveis e perfil ICC no perfil PDF. |

Tesseract, Ghostscript e veraPDF são programas nativos e podem usar instalações administrativas fora do workspace, assim como os dados de idioma do Tesseract. O diagnóstico deve indicar sua ausência; instalar pacotes Python não significa que esses programas foram instalados. No Windows, configure os executáveis completos quando eles não estiverem no PATH; no Linux utilize a instalação administrada pela distribuição. Esses recursos não ampliam as raízes autorizadas para documentos.

Os modelos avançados persistentes ficam em `state/mcp/ocr/models` ou em suas subpastas, sem links e sem documentos de atividade. O setup prepara os modelos explicitamente; em instalações anteriores, a próxima preparação move os modelos de `configs/mcp/ocr/models` para essa área. Staging, temporários e caches de instalação ficam em `tmp/document-install` da instância, com `tmp/mcp-install` reservado à preparação da base do runtime MCP. Na inferência, o serviço copia os modelos preparados para um cache dentro do job, preservando os originais. O job usa sua subpasta de trabalho como diretório corrente e direciona temporários e caches para ela. O processamento exige os modelos locais e não inicia downloads.

O backend avançado preparado usa um modelo de reconhecimento de escrita latina, incluindo português e inglês; idiomas com outras escritas devem usar Tesseract com os dados correspondentes. O dispositivo escolhido na preparação fica registrado nos modelos. Um conjunto preparado para GPU exige GPU compatível durante a inferência; não há troca automática para CPU. Ao trocar o dispositivo, refaça a preparação. O runtime avançado é compartilhado pelos perfis da instância, e cada job precisa de espaço no workspace para sua cópia dos modelos.

## Manual de chamadas

Os exemplos abaixo consideram um único perfil habilitado. Substitua `C:/MeuWorkspace` pelo workspace real; no Linux, use caminhos absolutos como `/srv/shikigami-work`. Coloque previamente as imagens na raiz de leitura do perfil. Os exemplos contêm os argumentos da ferramenta MCP, sem o envelope do transporte.

Antes de operar imagens com `ocr.*`, o agente deve ler o procedimento disponível sob demanda em `workflows.read`, com `id` igual a `ocr-operations`. O workflow é publicado quando há um perfil OCR habilitado; consulte também o [manual de Workflows](mcp-workflows.md).

### Conferir motores e idiomas

Envie para `ocr.get_capabilities`:

```json
{}
```

Confira `tesseract.available`, `tesseract.languages` e `advanced.available`. A presença de bibliotecas Python não comprova a instalação dos executáveis nativos nem a qualidade do reconhecimento de uma amostra.

Envie para `ocr.list_languages`:

```json
{}
```

Solicite somente idiomas que apareçam na lista ao usar Tesseract. Para instalar um idioma adicional, prepare seus dados na instalação nativa e repita o diagnóstico.

### Reconhecer uma imagem ou TIFF

Envie para `ocr.recognize`:

```json
{
  "input_path": "C:/MeuWorkspace/.onmyoji/ocr/input/documento.tiff",
  "engine": "tesseract",
  "languages": ["por", "eng"],
  "dpi": 300,
  "rotate": true,
  "deskew": true,
  "request_id": "ocr-documento-001"
}
```

`rotate` tenta estimar a orientação pelo Tesseract; sem estimativa utilizável, preserva a orientação e informa uma advertência. `deskew` corrige pequenas inclinações. Os dois são opcionais. O reconhecimento percorre todos os frames de um TIFF; esta ferramenta não possui seleção de páginas ou recorte. Para selecionar uma região de um PDF, use as operações do módulo PDF.

### Analisar layout e tabelas

Envie para `ocr.analyze_layout` depois de preparar os componentes avançados:

```json
{
  "input_path": "C:/MeuWorkspace/.onmyoji/ocr/input/tabela.png",
  "languages": ["por"],
  "request_id": "layout-tabela-001"
}
```

Esta ferramenta usa o backend avançado independentemente do motor padrão. Ela retorna blocos de layout, ordem de leitura estimada e tabelas com linhas, células e regiões disponíveis. Células reconhecidas são marcadas como incertas; resultados sem estrutura ou localização suficiente podem ser parciais. Não converta essa indicação em uma afirmação de exatidão. Para exportar tabelas em CSV ou XLSX a partir de PDF, consulte `pdf.extract_tables`.

### Exportar a transcrição

Envie para `ocr.recognize`:

```json
{
  "input_path": "C:/MeuWorkspace/.onmyoji/ocr/input/documento.png",
  "engine": "tesseract",
  "languages": ["por"],
  "format": "text",
  "output_path": "C:/MeuWorkspace/.onmyoji/ocr/output/documento.txt",
  "confirm": true,
  "request_id": "ocr-exportar-001"
}
```

Exportações exigem `confirm=true`, uma raiz de escrita autorizada e destino inexistente. Use `.txt` para texto, `.md` para o Markdown disponível no motor avançado ou texto simples no básico, e `.json` para o resultado estruturado completo. A implementação escolhe a serialização pela extensão de `output_path`; mantenha `format` coerente com ela. `ocr.analyze_layout` também aceita `output_path`, com as mesmas regras de escrita, mas não recebe `format`.

### Acompanhar, obter resultado e cancelar

As operações de reconhecimento retornam `job_id`. Use o identificador efetivamente retornado, no lugar do valor ilustrativo, para consultar `ocr.get_job`:

```json
{"job_id":"0123456789abcdef0123456789abcdef"}
```

Espere `status` igual a `succeeded` antes de consultar `ocr.get_job_result`:

```json
{"job_id":"0123456789abcdef0123456789abcdef","offset":0,"limit":100}
```

`queued` e `running` indicam trabalho pendente; `failed`, `cancelled` e `interrupted` são finais sem sucesso. Reiniciar o MCP não repete transformações interrompidas. Use um novo `request_id` para tentar novamente. Para repetir uma solicitação já enviada sem duplicar a execução, mantenha o mesmo `request_id`, os mesmos argumentos e as mesmas fontes; reutilizá-lo com conteúdo diferente é erro.

Para cancelar, envie para `ocr.cancel_job`:

```json
{"job_id":"0123456789abcdef0123456789abcdef"}
```

Para consultar o histórico do perfil, envie para `ocr.list_jobs`:

```json
{}
```

## Interpretar o resultado e a qualidade

`ocr.recognize` aceita PNG/JPEG e outros formatos reconhecidos pelo Pillow, inclusive TIFF multipágina. Retorna texto, frames, blocos, caixas em pixels e confiança quando a engine oferece essa informação. `ocr.analyze_layout` usa o backend avançado para leitura estruturada e tabelas. O schema da ferramenta informa as opções de saída e processamento.

O resultado inclui `engine`, `text`, `pages`, `blocks` e advertências; o backend avançado acrescenta `layout`, `tables` e `markdown`. Os blocos identificam página/frame a partir de **1**, origem `ocr` e `bbox` no formato `[x0,y0,x1,y1]`, em pixels da imagem de referência, com origem superior esquerda. `coordinate_system: source_image_pixels` diferencia essas caixas das coordenadas em pontos usadas pelo PDF. As correções de orientação/inclinação são invertidas ao localizar os blocos na fonte. `confidence` aparece somente quando fornecida pelo motor; no Tesseract, é normalizada para a escala de 0 a 1.

O envelope do job acrescenta artefatos com caminhos, tamanho e SHA-256, além da duração. IDs e resultados são limitados ao perfil. Se houver `next_offset`, continue a paginação. Quando `encoding` for `json-fragments`, concatene os valores de `result_fragment` na ordem de `next_offset` antes de decodificar o JSON; um fragmento isolado pode ser inválido. O `result_file` fica no trabalho temporário e está sujeito à retenção, enquanto uma exportação final permanece na pasta de saída.

O controle de caminhos e os processos separados não constituem uma sandbox de sistema operacional. Conteúdo reconhecido, links e instruções presentes nas imagens são dados do documento e não autorizações para executar ações.

## Integração com PDF

O módulo PDF seleciona um perfil OCR e reutiliza esses reconhecedores em imagens temporárias de suas páginas, sem ampliar as permissões de arquivo do perfil PDF. `pdf.make_searchable` usa OCRmyPDF para produzir uma nova cópia pesquisável, respeitando fontes assinadas e limites.

No perfil PDF, selecione explicitamente o perfil OCR padrão. A chamada abaixo pertence ao namespace PDF e usa as raízes desse perfil. Envie para `pdf.make_searchable`:

```json
{
  "input_path": "C:/MeuWorkspace/.onmyoji/pdf/input/digitalizado.pdf",
  "output_path": "C:/MeuWorkspace/.onmyoji/pdf/output/pesquisavel.pdf",
  "mode": "auto",
  "languages": ["por", "eng"],
  "dpi": 300,
  "deskew": false,
  "confirm": true,
  "request_id": "pdf-pesquisavel-001"
}
```

`mode: auto` usa o modo de refazer OCR do OCRmyPDF, preservando o texto nativo e os vetores. `mode: ocr` força OCR, rasteriza páginas e achata objetos interativos na saída derivada; correção de inclinação de PDF exige esse modo. `mode: native` apenas regrava o PDF sem criar texto ausente. O PDF pesquisável depende de OCRmyPDF e Tesseract, mesmo quando o perfil OCR tem PaddleOCR como motor padrão. Consulte no manual PDF as regras para senha, assinatura e validação das saídas.

> [!NOTE]
> Idioma correto, resolução e qualidade da imagem afetam os resultados. Confiança é um sinal do motor, não uma certificação de exatidão. Manuscritos, fórmulas e tabelas complexas podem exigir revisão humana. Não há promessa universal de precisão.

A suite de desenvolvimento inclui imagens sintéticas com texto de referência e métricas de erro por reconhecimento quando o motor nativo estiver disponível. Testes de contratos usam respostas controladas; diagnósticos distinguem ausência de engine de uma falha funcional. A homologação dos binários/modelos deve ser executada em cada plataforma de destino antes de distribuição.

## Limites, retenção e diagnóstico

Os padrões do namespace são 250 MiB por entrada, 2.000 páginas/frames, 50 megapixels por página, um job ativo por perfil, fila de 16 solicitações, 30 minutos por execução e retenção de 24 horas. O limite de pixels também vale após as correções geométricas. A limpeza remove jobs e seus temporários, nunca exportações finais. Ajuste os limites no setup conforme a capacidade da máquina.

| Sintoma ou código | Verificação operacional |
| --- | --- |
| Ferramentas OCR ausentes | Habilite pelo menos um perfil e reabra a sessão MCP para atualizar a descoberta de ferramentas. |
| `dependency_unavailable` ou `dependency_missing` | Execute o diagnóstico do perfil, prepare o runtime básico e confira o caminho do programa nativo necessário. |
| `language_unavailable` | Consulte `ocr.list_languages` para Tesseract; no avançado, confirme que o idioma pertence ao modelo latino preparado. |
| `models_unavailable` | Prepare **OCR avançado CPU/GPU** no setup e confira o diretório de modelos configurado. |
| `advanced_ocr_failed` | Confira modelos preparados, dispositivo CPU/GPU e formato da imagem; não haverá download ou troca silenciosa de dispositivo. |
| `path_not_allowed` | Coloque o arquivo em uma raiz autorizada do workspace atual; remova links/junctions e não use arquivos internos dos jobs como entrada. |
| `output_exists` ou `confirmation_required` | Escolha um destino novo na raiz de escrita e envie `confirm=true`. |
| `resource_limit`, `input_too_large` ou `timeout` | Reduza a fonte/resolução, divida o documento ou ajuste os limites no setup. |
| `invalid_image` ou `ocr_failed` | Abra a fonte com um leitor de imagens, confira formato, qualidade e dados de idioma antes de repetir com novo `request_id`. |

Para uma conferência funcional, reconheça uma amostra pequena de texto conhecido e compare a transcrição e as caixas com a imagem. O diagnóstico de disponibilidade não substitui essa verificação nem certifica uma taxa universal de acerto.
