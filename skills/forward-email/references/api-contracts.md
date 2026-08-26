# Contrato da API

Operações suportadas: `domains.list`, `domains.get`, `domains.create`, `domains.update`, `domains.verify_records`, `domains.verify_smtp`, `aliases.list`, `aliases.get`, `aliases.create`, `aliases.update`, `aliases.delete` e `aliases.generate_password`.

Operações de escrita exigem `confirm: true`. A sobrescrita de senha de alias deve ser explicitamente solicitada em `body.is_override` e confirmada. O wrapper não implementa OAuth nem aceita endpoint arbitrário.

