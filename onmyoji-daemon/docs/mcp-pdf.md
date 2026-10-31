# PDF no MCP local

O módulo `pdf` oferece leitura e processamento documental sob demanda, no servidor stdio do Onmyōji. Trabalha com arquivos locais e não depende do daemon. O [módulo OCR](mcp-ocr.md) é reutilizado internamente quando o perfil PDF seleciona um perfil OCR habilitado.

## Preparação

Use **MCP > Integrações MCP > PDF** para criar um perfil. O setup sugere entrada, saída e trabalho em `<workspace>/.onmyoji/pdf/`, mostra o valor adotado por Enter e cria as pastas após validar a configuração. Autorize pastas adicionais do workspace de onde o agente recebe anexos quando necessário.

Cada opção de edição mostra seus atributos na segunda coluna. É possível alterar diretórios, descrição, perfil OCR, executáveis de PDF/A, habilitação e limites. Configurações ficam em `configs/mcp/pdf.toml`; documentos nunca são guardados ali. A opção de instalação prepara as bibliotecas no runtime privado do MCP, em `configs/mcp/venv`, dentro do domínio da instância. A dependência PyMuPDF usa AGPL/comercial; distribuições devem observar seus termos e os das demais dependências.

Sem perfil habilitado, nenhuma ferramenta PDF é publicada. Um perfil é implícito; com vários, consulte `pdf.list_profiles` e informe `profile`. `pdf.get_capabilities` verifica as bibliotecas no runtime privado, sem considerar pacotes presentes apenas no Python usado pelo setup. A ausência de OCR, PDF/A ou assinatura não impede operações estruturais.

## Diretórios e recursos

O workspace definido por `codex.project_directory` deve ser externo e disjunto da instância: nenhum deles pode conter o outro. As raízes de leitura e escrita e o diretório de trabalho precisam ser caminhos absolutos dentro desse workspace. Links simbólicos e junctions são recusados, inclusive nos ancestrais dos caminhos. As permissões são revalidadas durante a execução e antes de publicar resultados; trocar o workspace exige ajustar os perfis que ainda apontem para o local anterior.

`work_directory` é uma subpasta exclusiva para jobs e não pode ser o próprio workspace. Entradas e saídas finais não podem ficar dentro dela, mesmo que uma raiz autorizada mais ampla inclua esse caminho. Cada job usa sua própria subpasta como diretório corrente e mantém nela staging, temporários e caches das bibliotecas. A limpeza por retenção alcança esses dados transitórios, sem remover os documentos de entrada ou os resultados finais.

Recursos administrativos têm finalidade distinta dos documentos. O perfil ICC de PDF/A pode estar no workspace ou em `configs/mcp/pdf/resources`, sem links; isso não autoriza o perfil a ler documentos de `configs/`. Executáveis nativos como Ghostscript e veraPDF podem usar uma instalação administrativa fora do workspace. A instalação usa staging e caches em `tmp/document-install` para as bibliotecas documentais e `tmp/mcp-install` para a base do runtime MCP, dentro da instância; arquivos de processamento permanecem no workspace.

## Execução e resultados

Todas as operações sobre documentos retornam um job. Use `request_id` estável para repetir uma mesma solicitação sem executar novamente. Reutilizar a chave com argumentos ou fontes diferentes é erro. Operações de escrita, inclusive exportações e imagens, exigem `confirm=true` e destinos novos.

```json
{"input_path":"C:/MeuWorkspace/.onmyoji/pdf/input/documento.pdf","output_path":"C:/MeuWorkspace/.onmyoji/pdf/output/paginas.pdf","pages":"1-3,7","request_id":"extrair-paginas-001","confirm":true}
```

Envie esses argumentos a `pdf.select_pages`. Consulte `pdf.get_job` com o `job_id` retornado; depois leia `pdf.get_job_result`. Estados finais são `succeeded`, `failed`, `cancelled` e `interrupted`. Jobs interrompidos não são repetidos automaticamente. `pdf.cancel_job` cancela o processamento e `pdf.list_jobs` lista o histórico do perfil.

Resultados incluem artefatos, hashes SHA-256, tamanho, duração e dados próprios da operação. O parâmetro `offset`/`limit` pagina coleções. Quando `encoding` for `json-fragments`, concatene `result_fragment` seguindo `next_offset` e decodifique o JSON completo. Não trate um fragmento isolado como resultado completo. O arquivo `result_file` permanece no diretório de trabalho pelo prazo de retenção.

> [!IMPORTANT]
> A fonte é preservada. As saídas são preparadas e validadas antes da publicação; arquivos existentes não são substituídos. Cancelamento ou falha não devem ser tratados como conclusão parcial. Diretórios de trabalho não podem ser usados como destino final.

## Seleção e operações

Páginas começam em **1**. `pages` aceita intervalos como `1-3,7,10-12`; `page_order` aceita uma lista explícita para reordenação e duplicação. `region` usa `[x0,y0,x1,y1]` em pontos, origem superior esquerda da página visível depois da rotação. As caixas retornadas seguem o mesmo referencial.

As famílias publicadas incluem inspeção, texto e busca, tabelas, renderização, imagens, seleção/mesclagem/divisão de páginas, transformações, criação, carimbos, metadados, marcadores, anexos, anotações, formulários, tarjas, sanitização, otimização, proteção, reparo, comparação e validação. Os schemas de `tools/list` definem os argumentos e ações aceitos.

- `extract_text` oferece `native`, `ocr` e `auto`, com proveniência por bloco. Em páginas híbridas, o modo automático evita reconhecer novamente regiões que já possuem texto. OCR e leitura de layout podem ser parciais; consulte advertências.
- `extract_tables` oferece tabelas nativas e OCR avançado, com exportação JSON/CSV/XLSX. Resultados de tabelas digitalizadas dependem do backend avançado preparado no setup.
- `transform_pages` diferencia rotação, recorte, redimensionamento e margens. **Recorte visual não remove conteúdo oculto**.
- `stamp` insere texto, logotipo ou página PDF sobreposta. `{page}` e `{pages}` permitem numeração; carimbo visual não equivale a assinatura digital.
- `forms` consulta/preenche/exporta/achata AcroForms. A gravação verifica os valores canônicos, os widgets e suas aparências. XFA é identificado e não é editado.
- `attachments` lista e extrai arquivos incorporados sem executá-los. Nomes de anexos não definem caminhos arbitrários de saída.

## Tarjas, proteção e conformidade

`preview_redactions` produz uma prévia para revisão; `apply_redactions` remove texto, imagens e gráficos nas regiões selecionadas e regrava o PDF, descartando revisões antigas. A verificação inclui conteúdo extraível e aparência final. Use regiões precisas e revise a prévia; um resultado de OCR não prova sozinho a ausência de informação confidencial. Sanitização de metadados, anexos e ações ativas é uma operação complementar.

Senhas são referências do vault (`credential_profile`, `credential_entry_path`, `field: password`), nunca senhas literais no schema. `protect` oferece AES-256 ou descriptografia com senha conhecida. Não há descoberta de senhas.

Transformações de PDFs criptografados preservam a proteção na cópia e exigem referência de senha. Descriptografar é uma ação explícita de `protect`; mesclar documentos protegidos requer uma referência que abra todos eles.

Transformar um documento assinado exige `allow_signature_invalidation=true`, pois a cópia resultante não preserva a validade criptográfica original. A validação separa integridade, confiança da cadeia, validade temporal e revogação. Sem dados suficientes, o resultado é desconhecido; não se presume confiança. A validação de certificados não realiza consultas externas.

`validate` oferece estrutura, assinaturas e PDF/A. Conversão usa `validation: pdfa`, `action: convert_pdfa` e `output_path`, com Ghostscript, perfil ICC e veraPDF locais. Uma saída só é publicada como PDF/A-2b se o veraPDF comprovar conformidade. Não se promete conformidade de acessibilidade PDF/UA nem conversão fiel para Office.

## Limites e manutenção

Os padrões são 250 MiB por entrada, 2.000 páginas, 50 megapixels por renderização, um job ativo por perfil, fila de 16 solicitações e 30 minutos por execução. São configuráveis pelo setup. Processos isolados mantêm o servidor responsivo; isso não é uma sandbox de sistema operacional.

Jobs e temporários expiram após 24 horas por padrão; os resultados finais não são apagados. Texto, anexos, scripts e links do PDF são conteúdo não confiável: o serviço não executa ações incorporadas nem abre links. Processamento e modelos são locais.
