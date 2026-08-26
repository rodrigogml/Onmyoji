# Contrato Todoist

Envie `{"version":1,"operation":"tasks.list","params":{},"query":{},"body":null}`. A resposta é `{"ok":true,"version":1,"operation":"…","data":…}`; falhas retornam `ok: false` e `error.code`/`error.message`.

As operações REST permitidas incluem `user.get`, famílias `tasks`, `projects`, `sections`, `labels`, `comments`, `collaborators`, `activities`, `reminders`, `uploads`, `backups`, `emails`, `notifications` e `tokens.revoke`. `activity.list` continua aceito como alias compatível de `activities.list`. Use `sync` para o endpoint de sincronização, com `commands` e `sync_token` opcional. Consulte o dicionário `OPERATIONS` do wrapper para a lista exata.

`uploads.create` exige `body.file_path`; o caminho deve estar permitido no perfil. `delete`, `archive`, `close`, `revoke` e remoções de upload exigem `confirm: true` no pedido e confirmação prévia do usuário.

O wrapper obtém o token por pipe do KeePass Vault usando o perfil e a entrada configurados. Ele nunca revela token nem cabeçalhos e mascara campos sensíveis nas respostas.
