---
name: cloudflare
description: Integração com a API Cloudflare para consultar e administrar zonas e registros DNS, usando exclusivamente API Tokens obtidos pela KeePassVault através de perfis INI externos.
---

# Cloudflare DNS

Neste documento, `<CODEX_HOME>` é um placeholder para o diretório raiz da instância atual do Shikigami. Não o digite literalmente nem o substitua pelo workspace; use o diretório efetivamente configurado como `CODEX_HOME` para essa instância.

Use `scripts/cloudflare.py --config <CODEX_HOME>/configs/cloudflare.toml --profile <perfil>` e envie uma requisição JSON com `version: 1` pelo stdin. A skill usa exclusivamente API Token; OAuth e API Keys globais não fazem parte do contrato.

Consulte `references/api-contracts.md` para operações, confirmação de escrita e parâmetros. Perfis reais ficam em `<CODEX_HOME>/configs/cloudflare.toml` e são ignorados pelo Git. Nunca coloque token em perfil, argumento ou log.

