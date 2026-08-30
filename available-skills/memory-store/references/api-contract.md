# Contrato JSON

O CLI aceita uma única requisição JSON com `version: 1`. Toda resposta contém `version`, `ok` e `operation`; erros incluem `error.code` e uma mensagem segura.

## Texto

- `text.add`: `text`, `kind`, `tags`, `source_ref`, `confidence` e `confirm`.
- `text.search`: `query`, filtros opcionais `kind`, `tag`, `include_archived` e `limit`.
- `text.get`, `text.supersede`, `text.archive` e `text.restore`: usam `id`; os três últimos exigem `confirm`.

## Dados estruturados

- `schema.plan` calcula migrations pendentes; `schema.apply` aplica um manifesto e exige `confirm`.
- `record.create`, `record.update`, `record.upsert`, `record.archive` e `record.restore` exigem `confirm`.
- `record.get` usa `id`; `record.list` aceita filtros `eq`, `in`, `lt`, `lte`, `gt`, `gte`, ordenação declarada e paginação.

O manifesto contém `version: 1` e `migrations`, cada uma com `version` inteiro crescente e `operations`. Operações suportadas são `create_table`, `add_column`, `create_index`, `drop_index` e `rebuild_table`. Reconstrução usa a definição integral nova e `copy`, que mapeia coluna nova para coluna anterior ou para `{ "value": ... }`; ela falha se uma nova coluna obrigatória não tiver origem ou default. Colunas permitem `text`, `integer`, `real`, `boolean`, `date`, `datetime` e `json`, além de `required`, `primary_key`, `unique`, `enum`, `min`, `max` e `reference`.

## Administração

`health.check`, `backup.create`, `backup.list`, `backup.prune`, `export` e `restore` mantêm e verificam o namespace. Prune e restore exigem `confirm`; restore faz backup automático do estado anterior.
