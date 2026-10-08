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

Todas as listagens aceitam `page` ou `pagina`, e `page_size` ou `registros_por_pagina`; ambos devem ser inteiros positivos.

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
