# Contrato da API

Operações suportadas: `zones.list`, `zones.get`, `dns.records.list`, `dns.records.get`, `dns.records.create`, `dns.records.update`, `dns.records.replace`, `dns.records.delete`, `dns.records.export`, `dns.records.import` e `dns.usage`.

Operações de escrita (`create`, `update`, `replace`, `delete` e `import`) exigem `confirm: true`. Registros usam os campos da API Cloudflare, sem normalização silenciosa. `zone_id` pode vir do perfil ou de `params.zone_id`.

