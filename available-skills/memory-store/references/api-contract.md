# Contrato JSON

O CLI aceita uma única requisição JSON com `version: 1`. Toda resposta contém `version`, `ok` e `operation`; erros incluem `error.code` e uma mensagem segura.

## Texto

- `text.add`: `text`, `kind`, `tags`, `source_ref`, `confidence` e `confirm`.
- `text.search`: `query`, filtros opcionais `kind`, `tag`, `include_archived` e `limit`.
- `text.get`, `text.supersede`, `text.archive` e `text.restore`: usam `id`; os três últimos exigem `confirm`.

## Busca unificada

- `search.query`: recebe `query`, `sources` opcional (`"text"`, `"records"` ou ambos em lista), `tables` opcional para registros, `include_archived` e `limit` de 1 a 100. Por padrão busca texto e registros ativos.
- A resposta agrupa resultados por item e inclui `source`, `id`, `table` para registros, `matched_fields`, `matches` com trechos destacados, `excerpt` e `score`. `score_order` é `ascending`: um score menor representa melhor correspondência FTS5; ele serve somente para ordenar candidatos.
- O namespace é sempre o informado ao CLI. `tables` deve conter somente tabelas declaradas nesse namespace; a operação não aceita SQL ou outros namespaces.
- `search.status` compara o índice com as memórias e registros atuais sem gravar. `search.rebuild` reconstrói o índice e exige `confirm: true`. Execute-o uma vez após atualizar uma base que já contenha dados e sempre que `search.query` informar `search_index_stale`.

## Dados estruturados

- `schema.plan` calcula migrations pendentes; `schema.apply` aplica um manifesto e exige `confirm`.
- `record.create`, `record.update`, `record.upsert`, `record.archive` e `record.restore` exigem `confirm`.
- `record.get` usa `id`; `record.list` aceita filtros `eq`, `in`, `lt`, `lte`, `gt`, `gte`, ordenação declarada e paginação.

O manifesto contém `version: 1` e `migrations`, cada uma com `version` inteiro crescente e `operations`. Operações suportadas são `create_table`, `add_column`, `create_index`, `drop_index` e `rebuild_table`. Reconstrução usa a definição integral nova e `copy`, que mapeia coluna nova para coluna anterior ou para `{ "value": ... }`; ela falha se uma nova coluna obrigatória não tiver origem ou default. Colunas permitem `text`, `integer`, `real`, `boolean`, `date`, `datetime` e `json`, além de `required`, `primary_key`, `unique`, `enum`, `min`, `max` e `reference`. Somente colunas `text` podem declarar `searchable: true`; cada uma passa a participar de `search.query`.

## Administração

`health.check`, `backup.create`, `backup.list`, `backup.prune`, `export` e `restore` mantêm e verificam o namespace. Prune e restore exigem `confirm`; restore faz backup automático do estado anterior e recompõe o índice de busca.
