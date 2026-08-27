# Contrato do wrapper

Execute `py -3 scripts/keepass_vault.py --config <arquivo> --profile <perfil>` e envie exatamente um objeto JSON na entrada padrão. A saída é exatamente um objeto JSON: `{ "ok": true, "result": ... }` ou `{ "ok": false, "error": { "code": "...", "message": "..." } }`.

Operações preservadas: `list`, `list.totp`, `read`, `add`, `edit`, `delete`, `copy`, `attachment.export`, `attachment.import` e `attachment.delete`.

O wrapper aceita o contrato v1: `entry: { "path": "..." }`, `fields`, `source`, `destination`, `attachment`, `source` e `destination`; também aceita as formas abreviadas `path`, `values`, `source_path`, `destination_path`, `name` e `file_path`. `list.totp` devolve estruturas `{ path, uuid: null, has_totp: true }` sem retornar URI ou segredo TOTP. Para `read`, informe `field` como `title`, `username`, `password`, `url`, `notes` ou `totp`. Operações de alteração exigem perfil com acesso `read_write`; `delete`, `attachment.import` e `attachment.delete` exigem `confirm: true`.
