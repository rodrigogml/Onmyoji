# Contrato da API Notion

## Envelope

Toda requisição usa `version: 1`, `operation`, `params`, `query`, `body` e opcionalmente `paginate`.

Respostas têm `ok`, `version`, `operation` e `data`. Falhas têm `ok: false` e `error.code`/`error.message`.

## Operações

O registro em `scripts/notion.py` é a lista permitida de operações e cobre:

- blocos, páginas e propriedades de páginas;
- Markdown aprimorado;
- databases e data sources, incluindo schemas, templates, filtros, ordenação e query;
- views e view queries;
- comentários;
- busca, usuários e emojis personalizados;
- uploads simples e multipart;
- introspecção/revogação OAuth quando aplicável;
- tarefas assíncronas.

IDs devem ser UUIDs do Notion. O wrapper não aceita URLs arbitrárias nem métodos HTTP arbitrários.

## Paginação

Use `paginate: true` para seguir `has_more` e `next_cursor`. O cursor é opaco. O wrapper agrega `results` e retorna uma resposta final com `has_more: false`.

## Arquivos

`file_uploads.send` recebe `body.file_path`. O wrapper monta multipart e não imprime conteúdo binário. O upload deve ser concluído antes de ser anexado a páginas ou blocos.

## Segurança

O token vem do campo configurado na KeePassVault. Respostas que contenham `token`, `access_token`, `refresh_token`, segredos ou URLs com credenciais são sanitizadas.

## Escopo

Admin API, OAuth interativo e recebimento de webhooks não fazem parte da primeira versão. Webhooks exigem endpoint público configurado na conexão Notion.

Referências oficiais:

- https://developers.notion.com/reference/intro
- https://developers.notion.com/llms.txt
