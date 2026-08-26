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
