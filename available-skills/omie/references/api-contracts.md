# Contratos da API Omie

O wrapper sempre envia `call`, `app_key`, `app_secret` e `param` (um array com um objeto) para um endpoint registrado internamente. Ele nunca aceita endpoint, método ou campos arbitrários na entrada.

## Endpoints e operações

| Operação | Método Omie | Endpoint | Entrada principal | Escrita |
|---|---|---|---|---|
| `departments.list` | `ListarDepartamentos` | `/api/v1/geral/departamentos/` | `params.page`, `params.page_size` | Não |
| `departments.get` | `ConsultarDepartamento` | `/api/v1/geral/departamentos/` | `params.codigo` | Não |
| `departments.create` | `IncluirDepartamento` | `/api/v1/geral/departamentos/` | `body.codigo`, `body.descricao` | Sim |
| `departments.update` | `AlterarDepartamento` | `/api/v1/geral/departamentos/` | `body.codigo`, `body.descricao` | Sim |
| `departments.delete` | `ExcluirDepartamento` | `/api/v1/geral/departamentos/` | `params.codigo` | Sim |
| `projects.list` | `ListarProjetos` | `/api/v1/geral/projetos/` | Paginação e filtros | Não |
| `projects.get` | `ConsultarProjeto` | `/api/v1/geral/projetos/` | `params.codigo` ou `params.codInt` | Não |
| `projects.create` | `IncluirProjeto` | `/api/v1/geral/projetos/` | `body.codInt`, `body.nome`, `body.inativo` opcional | Sim |
| `projects.update` | `AlterarProjeto` | `/api/v1/geral/projetos/` | Identificador e `body.nome` ou `body.inativo` | Sim |
| `projects.upsert` | `UpsertProjeto` | `/api/v1/geral/projetos/` | Identificador e `body.nome` ou `body.inativo` | Sim |
| `projects.delete` | `ExcluirProjeto` | `/api/v1/geral/projetos/` | `params.codigo` ou `params.codInt` | Sim |
| `categories.list` | `ListarCategorias` | `/api/v1/geral/categorias/` | Paginação e filtros | Não |
| `categories.get` | `ConsultarCategoria` | `/api/v1/geral/categorias/` | `params.codigo` | Não |
| `categories.create` | `IncluirCategoria` | `/api/v1/geral/categorias/` | `body.categoria_superior`, `body.descricao`, `body.tipo_categoria` | Sim |
| `categories.update` | `AlterarCategoria` | `/api/v1/geral/categorias/` | `body.codigo` e campos de alteração | Sim |
| `category-groups.create` | `IncluirGrupoCategoria` | `/api/v1/geral/categorias/` | `body.descricao`, `body.tipo_grupo` | Sim |
| `category-groups.update` | `AlterarGrupoCategoria` | `/api/v1/geral/categorias/` | `body.codigo` e campos de alteração | Sim |

## Paginação e filtros

As listagens paginadas de cadastros abaixo aceitam `page` ou `pagina`, e `page_size` ou `registros_por_pagina`; ambos devem ser inteiros positivos.

`projects.list` também aceita `apenas_importado_api`, `ordenar_por`, `ordem_descrescente`, `filtrar_por_data_de`, `filtrar_por_data_ate`, `filtrar_apenas_inclusao`, `filtrar_apenas_alteracao` e `nome_projeto`.

`categories.list` também aceita `filtrar_apenas_ativo`, `filtrar_por_tipo` (`R` para receita ou `D` para despesa) e `descricao`. A Omie informa que esse método retorna somente categorias ativas, não totalizadoras e exibíveis quando aplicado o filtro por tipo.

## Escritas

Toda operação marcada como escrita exige `confirm: true`. A exclusão de projeto é destrutiva. A documentação pública da Omie não lista um método de excluir ou inativar categorias; portanto o wrapper não expõe essa ação.

## Campos de categorias

Para criar categoria, `categoria_superior` deve referenciar um grupo totalizador válido; `tipo_categoria` deve ser compatível com o tipo de receita/despesa do grupo. Os campos opcionais aceitos são `natureza` e `codigo_dre`. Na alteração, o wrapper também aceita `conta_inativa`.

## Clientes e fornecedores

O cadastro é compartilhado pela Omie entre clientes, fornecedores e transportadoras. A classificação pode ser feita por `tags`; um registro sem a tag `Fornecedor` pode já existir pelo CNPJ. Para verificar existência e evitar duplicação, pesquise primeiro o documento sem restringir tags, registros importados pela API ou situação ativa.

| Operação do wrapper | Método Omie | Endpoint | Entrada | Escrita |
|---|---|---|---|---|
| `customers.list` | `ListarClientes` | `/api/v1/geral/clientes/` | Paginação e `params.clientesFiltro` | Não |
| `customers.get` | `ConsultarCliente` | `/api/v1/geral/clientes/` | `params.codigo_cliente_omie` ou `params.codigo_cliente_integracao` | Não |

O wrapper recebe JSON pelo stdin; `operation` deve conter o nome da operação do wrapper, e não o método da API. Execute `<python> <CODEX_HOME>/skills/omie/scripts/omie.py --config <CODEX_HOME>/configs/omie.toml --profile <perfil>` usando o Python da instância. Não inclua credenciais na requisição nem troque o perfil configurado por um perfil presumido.

### Localizar por CNPJ/CPF

```json
{
  "version": 1,
  "operation": "customers.list",
  "params": {
    "page": 1,
    "page_size": 50,
    "apenas_importado_api": "N",
    "clientesFiltro": {"cnpj_cpf": "18.511.742/0001-47"}
  }
}
```

`clientesFiltro` é um objeto, não uma lista. O wrapper transmite o documento como informado; mantenha-o como string para preservar zeros à esquerda. A API define a comparação do filtro. Se o formato sem pontuação não localizar um CNPJ numérico, repita com a máscara cadastral; para confirmar ausência, use a listagem completa paginada e compare os documentos localmente removendo a pontuação. Não restrinja `inativo` nessa verificação.

Uma resposta de listagem aparece em `data.clientes_cadastro`, com metadados `data.pagina`, `data.total_de_paginas`, `data.registros` e `data.total_de_registros`. Os campos de cada cadastro são preservados, incluindo `codigo_cliente_omie`, `codigo_cliente_integracao`, `cnpj_cpf`, `razao_social`, `nome_fantasia`, `tags` e `inativo`, quando fornecidos pela Omie. A consulta individual devolve o cadastro diretamente em `data`.

Percorra todas as páginas necessárias mantendo os mesmos filtros. Para correspondência exata de documento, compare os valores normalizados de entrada e resposta. Zero correspondências só indica ausência após uma consulta bem-sucedida e completa. Havendo vários cadastros com o mesmo documento, apresente os candidatos e resolva a ambiguidade antes de usar um ID. Confira `inativo` e a adequação do cadastro à operação; não crie duplicata para contornar um cadastro inativo.

### Razão social, nome fantasia e parte de nome

Para filtros nativos, substitua `clientesFiltro` por `{"razao_social": "Nome cadastrado"}` ou `{"nome_fantasia": "Nome cadastrado"}`. Faça pesquisas separadas se deseja encontrar uma correspondência em qualquer um desses campos; não presuma que a combinação de filtros significa OU.

> [!IMPORTANT]
> As fontes oficiais consultadas listam os campos de nome, mas não especificam correspondência por trecho nem curingas. O wrapper não transforma esses filtros nem garante busca parcial no servidor. Para encontrar parte de nome, liste todas as páginas sem filtro de nome e compare o trecho localmente com os dois campos. Não envie `*trecho*`, `%trecho%`, `name` ou métodos inventados esperando uma semântica não documentada.

Exemplo de comparação local dos cadastros retornados por cada página:

```python
# data é o campo data de uma resposta ok=true de customers.list.
# Repetir a consulta com page=2, 3, ... até data["total_de_paginas"].
termo = "papelaria".casefold()
candidatos = [
    cadastro for cadastro in data["clientes_cadastro"]
    if any(termo in (cadastro.get(campo) or "").casefold()
           for campo in ("razao_social", "nome_fantasia"))
]
```

Essa comparação é local e por trecho, sem diferenciar maiúsculas/minúsculas; acentos continuam significativos. Acumule candidatos de todas as páginas e valide o documento antes de usar o ID. O wrapper entrega uma página por chamada, não percorre páginas automaticamente.

### Consultar pelo ID e reutilizar nos lançamentos

```json
{"version":1,"operation":"customers.get","params":{"codigo_cliente_omie":123456789}}
```

Também é aceito `{"codigo_cliente_integracao":"FORN-001"}` em `params`. O ID da Omie deve ser inteiro positivo; o código de integração deve ser string não vazia de até 60 caracteres. `customers.get` não aceita CNPJ/CPF nem nome.

| Uso existente no wrapper | Campo que recebe `codigo_cliente_omie` |
|---|---|
| `payables.create`, `payables.update`, `payables.upsert` | `body.codigo_cliente_fornecedor` |
| `receivables.create`, `receivables.update`, `receivables.upsert` | `body.codigo_cliente_fornecedor` |
| Operações financeiras em lote | `body.titles[].codigo_cliente_fornecedor` |
| `account-transactions.create`, `account-transactions.update` | `body.customer_id` (enviado como `detalhes.nCodCliente`) |
| `inbound-nfe-receipts.list` | `params.nIdFornecedor` |

Esses campos recebem o ID interno numérico, não o CNPJ e não `codigo_cliente_integracao`. `financial-movements.list` é uma consulta consolidada; os lançamentos diretos usam `account-transactions.create`. Escritas continuam exigindo confirmação e os demais campos do contrato financeiro. Este incremento não adiciona criação/alteração do cadastro de fornecedores.

### Campos permitidos e erros

A listagem aceita `page`/`pagina` e `page_size`/`registros_por_pagina`, como inteiros positivos, com no máximo 50 registros por página; os padrões são 1 e 50. Também aceita `apenas_importado_api`, `filtrar_apenas_inclusao`, `filtrar_apenas_alteracao`, `exibir_caracteristicas` e `exibir_obs` (`S`/`N`), além de `filtrar_por_data_de`, `filtrar_por_data_ate` (`dd/mm/aaaa`) e `filtrar_por_hora_de`, `filtrar_por_hora_ate` (horário aceito pela Omie).

O subconjunto exposto de `clientesFiltro` é: `codigo_cliente_omie` (inteiro positivo), `codigo_cliente_integracao` (até 60 caracteres), `cnpj_cpf` (até 20), `razao_social` (até 60), `nome_fantasia` (até 100), `inativo` (`S`/`N`) e `tags` (lista não vazia, por exemplo `[{"tag":"Fornecedor"}]`). Strings precisam ser não vazias. Não confunda os campos existentes na API com o subconjunto exposto pelo wrapper; outros campos não estão liberados nessa operação.

- `unsupported_operation`: operação não registrada, como `suppliers.list` ou `ListarClientes` usada diretamente como `operation`.
- `invalid_request`: filtro, campo, tipo ou paginação inválido; CNPJ/CPF e nome ficam dentro de `params.clientesFiltro`.
- `missing_parameter`: consulta individual sem identificador.
- `omie_api_error` ou `omie_http_error`: a Omie recusou a chamada; não interprete como fornecedor inexistente. A mensagem de negócio original não é exposta pelo wrapper atual.
- Falhas de rede, perfil ou cofre também impedem concluir existência/ausência. Verifique `ok` antes de acessar `data`.

### Fontes verificadas em 08/10/2026

- [ListarClientes: parâmetros, objeto de filtro e limite de 50](https://api.omie.com.br/docs/operacoes/geral/clientes:ListarClientes).
- [ConsultarCliente: identificadores](https://api.omie.com.br/docs/operacoes/geral/clientes:ConsultarCliente).
- [Serviço de clientes: campos e estruturas retornadas](https://app.omie.com.br/api/v1/geral/clientes/). O [WSDL](https://app.omie.com.br/api/v1/geral/clientes/?WSDL) confirma `clientesFiltro` como estrutura única; a tabela HTML antiga apresenta a estrutura como array.
- [Cadastro compartilhado e classificação por tags](https://ajuda.omie.com.br/pt-BR/articles/6596048-cadastrando-um-cliente-ou-fornecedor-via-api).


## Tipos de documento dos títulos

| Operação | Método Omie | Parâmetros | Retorno em `data` |
|---|---|---|---|
| `document-types.list` | `PesquisarTipoDocumento` | `params.codigo` opcional, padrão `""` | `tipo_documento_cadastro`, lista com `codigo` e `descricao` |
| `document-types.get` | `ConsultarTipoDocumento` | `params.codigo` obrigatório e não vazio | Objeto com `codigo` e `descricao` |

Ambas são leituras no endpoint fixo `/api/v1/geral/tiposdoc/`, usando exclusivamente o perfil Omie e o provedor KeePass existentes. Não exigem `confirm`. `codigo` deve ser string de até cinco caracteres; strings apenas com espaços são inválidas. Campos adicionais, paginação e `body` não vazio são rejeitados. `params` e `body`, quando presentes, devem ser objetos. A pesquisa sem código consulta o catálogo; não há paginação nesse contrato oficial. A resposta nativa é preservada, sem transformação de códigos ou descrições.

```json
{"version":1,"operation":"document-types.list"}
{"version":1,"operation":"document-types.get","params":{"codigo":"FAT"}}
```

Ausência de código no detalhe gera `missing_parameter`; tipos, comprimentos ou campos inválidos geram `invalid_request`, antes da leitura de credenciais. Erros continuam no envelope `ok=false`: `omie_api_error` para falha de negócio em resposta JSON, `omie_http_error` para HTTP, `network_error` para conexão e `invalid_response` para JSON inválido. Mensagens externas não são expostas.

Em 08/10/2026, pesquisa e consultas individuais autenticadas no perfil `laveli` confirmaram: `99999` = Outros, `NFE` = Nota Fiscal Eletrônica, `FAT` = Fatura. Consulta individual também confirmou `NFS` = Nota Fiscal de Serviço. Esses valores são evidência da consulta, não uma enumeração fixa do wrapper. Consulte o catálogo para outros códigos.

> [!IMPORTANT]
> O wrapper disponibiliza `99999`, `NFE`, `FAT` e `NFS` tanto em `codigo_tipo_documento` de contas a pagar/receber quanto em `document_type` (`cTipo`) dos lançamentos diretos, preservando o código escolhido. A aceitação da Omie é específica de cada serviço; o catálogo de títulos não comprova aceitação em lançamentos diretos. Consulte os contratos financeiros para essa distinção.

Para corrigir um título existente, consulte `payables.get`, preserve seus campos editáveis e envie `payables.update` com o mesmo `codigo_lancamento_omie` e `codigo_tipo_documento` validado, usando `confirm:true` após autorização. Não copie indiscriminadamente campos de resposta para o body. Não use criação, upsert, nova baixa ou cancelamento para trocar o tipo de um título pago. Consulte novamente e compare valor, datas, categoria, projeto, rateio e situação/pagamento. Se a Omie impedir a alteração de um título pago, interrompa e encaminhe a restrição, sem recriá-lo ou mudar sua baixa. O teste local valida o encaminhamento dos campos; não garante que o servidor permita a edição de todo título pago.

Fonte: [Contrato oficial TiposDocumentoCadastro](https://app.omie.com.br/api/v1/geral/tiposdoc/).


## Cadastro de contraparte por CNPJ ou CPF

`customers.create` exige `confirm:true` e `body.cnpj_cpf` (CPF numérico de 11 dígitos ou máscara `XXX.XXX.XXX-XX`, ou CNPJ numérico de 14 dígitos ou máscara `XX.XXX.XXX/XXXX-XX`) e `body.razao_social` (razão social ou nome completo da pessoa física; string não vazia de até 60 caracteres). `body.codigo_cliente_integracao` é opcional, não vazio e de até 60 caracteres; quando omitido, o wrapper usa `CPF-` seguido dos 11 dígitos ou `CNPJ-` seguido dos 14 dígitos, conforme o documento. O documento é enviado com a máscara correspondente; zeros à esquerda são preservados porque o campo exige string. A validação é de formato, não de dígitos verificadores ou situação fiscal. CNPJ alfanumérico permanece fora do escopo. Campos adicionais e `params` não vazio são rejeitados; `body` e `params`, se presentes, devem ser objetos JSON.

```json
{"version":1,"operation":"customers.create","confirm":true,"body":{"cnpj_cpf":"52.438.909/0001-20","razao_social":"RAZÃO SOCIAL CONFORME DOCUMENTO"}}
```

O exemplo contém um placeholder de razão social: use a razão social efetiva da contraparte, não apenas uma abreviação presumida. Para CPF, informe o nome completo em `razao_social`. Exemplo de formato com CPF fictício (não enviar como cadastro real):

```json
{"version":1,"operation":"customers.create","confirm":true,"body":{"cnpj_cpf":"012.345.678-90","razao_social":"NOME COMPLETO DA PESSOA"}}
```

Compatibilidade verificada em 09/10/2026 no [contrato oficial de ClientesCadastro](https://app.omie.com.br/api/v1/geral/clientes/): `IncluirCliente` aceita CNPJ ou CPF em `cnpj_cpf` (string de até 20 caracteres). O campo `pessoa_fisica` está marcado como deprecated e não é enviado pelo wrapper. A verificação foi documental e por testes locais com respostas simuladas, sem inclusão em conta real da Omie.

Antes de incluir, o wrapper chama `ListarClientes` com 50 registros por página e `apenas_importado_api:"N"`, sem filtros de documento, nome, situação ou tags. Percorre todas as páginas e compara o CPF/CNPJ sem pontuação, evitando depender de correspondência por máscara da API. Não há limite fixo de quantidade de clientes. Metadados ausentes/inconsistentes, totais alterados, páginas incompletas ou IDs repetidos geram `invalid_response`; falhas de rede/API propagam o erro e impedem a inclusão. Essa varredura tem custo proporcional ao catálogo inteiro.

Se existir um único cadastro ativo com o CPF/CNPJ, retorna `data.codigo_cliente_omie`, `data.created:false` e o cadastro em `data.customer`, sem chamar inclusão ou alteração. A razão social informada não substitui a existente. Mais de uma correspondência gera `ambiguous_customer`; cadastro inativo gera `customer_inactive`. Não crie duplicata para contornar esses erros.

Se não existir correspondência, chama exclusivamente `IncluirCliente` no endpoint fixo `/api/v1/geral/clientes/`, com o perfil e KeePass configurados. Uma resposta de sucesso deve trazer `codigo_status` zero e ID positivo; retorna os campos nativos e `data.created:true`, incluindo `data.codigo_cliente_omie`. Status de erro gera `omie_api_error` com mensagem sanitizada; ID ausente/inválido gera `invalid_response`. O ID pode ser usado como `customer_id` dos lançamentos diretos ou `codigo_cliente_fornecedor` dos títulos. O wrapper não presume nem atribui tags de fornecedor.

> [!IMPORTANT]
> A consulta anterior à criação não é uma trava transacional contra alterações concorrentes. Mantenha o código de integração estável entre tentativas. A inclusão não tem retries automáticos: se houver timeout ou resposta incerta, consulte novamente antes de repetir, pois o servidor pode já ter criado o cadastro. Nenhuma alteração/upsert automática é feita para recuperar erros.

Fonte: [Cadastro de clientes e fornecedores — Omie](https://app.omie.com.br/api/v1/geral/clientes/), métodos `ListarClientes` e `IncluirCliente`.
