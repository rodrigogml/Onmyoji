---
name: cloudflare
description: Integração com a API Cloudflare para consultar e administrar zonas e registros DNS, usando exclusivamente API Tokens obtidos pela KeePassVault através de perfis INI externos.
---

# Cloudflare DNS

Use `scripts/cloudflare.py --config <CODEX_HOME>/configs/cloudflare.toml --profile <perfil>` e envie uma requisição JSON com `version: 1` pelo stdin. A skill usa exclusivamente API Token; OAuth e API Keys globais não fazem parte do contrato.

Consulte `references/api-contracts.md` para operações, confirmação de escrita e parâmetros. Perfis reais ficam em `<CODEX_HOME>/configs/cloudflare.toml` e são ignorados pelo Git. Nunca coloque token em perfil, argumento ou log.

