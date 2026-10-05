# Contrato do wrapper

Execute `py -3 scripts/keepass_vault.py --config <arquivo> --profile <perfil>` e envie exatamente um objeto JSON na entrada padrão. A saída é exatamente um objeto JSON: `{ "ok": true, "result": ... }` ou `{ "ok": false, "error": { "code": "...", "message": "..." } }`.

Operações preservadas: `list`, `list.totp`, `read`, `add`, `edit`, `delete`, `copy`, `attachment.export`, `attachment.import` e `attachment.delete`.

O wrapper aceita o contrato v1: `entry: { "path": "..." }`, `fields`, `source`, `destination`, `attachment`, `source` e `destination`; também aceita as formas abreviadas `path`, `values`, `source_path`, `destination_path`, `name` e `file_path`. `list.totp` devolve estruturas `{ path, uuid: null, has_totp: true }` sem retornar URI ou segredo TOTP. Para `read`, informe `field` como `title`, `username`, `password`, `url`, `notes` ou `totp`. Operações de alteração exigem perfil com acesso `read_write`; `delete`, `attachment.import` e `attachment.delete` exigem `confirm: true`.

## Configuração de TOTP

`read` aceita também `field: "otp"`, para transferência interna da URI TOTP em memória. Não exiba essa URI: ela contém a semente secreta.

`edit` aceita `values: {"totp": "<URI otpauth>"}` com `confirm: true`, em requisição separada dos demais campos. Requer a dependência `pykeepass` declarada em `requirements.txt`. Salva o atributo protegido `otp` no KDBX criptografado, preserva os demais campos e rejeita entrada ausente/duplicada ou alteração concorrente detectada.

`edit` com `values: {}`, `validate_only: true` e `confirm: true` valida acesso à entrada sem modificá-la.
