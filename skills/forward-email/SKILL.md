---
name: forward-email
description: Integração com a API Forward Email para consultar e administrar domínios e aliases, usando exclusivamente token obtido pela KeePassVault através de perfis INI externos.
---

# Forward Email

Use `scripts/forward_email.py --config <perfil>` e envie uma requisição JSON com `version: 1` pelo stdin. A skill usa exclusivamente token de API em autenticação Basic token-only; OAuth não faz parte do contrato.

Consulte `references/api-contracts.md` para operações, confirmação de escrita e parâmetros. Perfis reais ficam em `configs/` e são ignorados pelo Git. Nunca coloque token ou senha de alias em perfil, argumento ou log.

