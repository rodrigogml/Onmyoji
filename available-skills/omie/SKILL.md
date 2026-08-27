---
name: omie
description: Use para consultar e administrar cadastros, NF-e, contas correntes, transferências, contas a pagar e receber da Omie por perfis seguros do Onmyōji. Acione quando a tarefa precisar operar a API Omie usando app_key e app_secret recuperados exclusivamente do KeePass Vault.
---

# Omie

Use `scripts/omie.py --config <CODEX_HOME>/configs/omie.toml --profile <perfil>` e envie uma requisição JSON `version: 1` pelo stdin. O conjunto de operações registrado é fechado; não informe URLs ou métodos arbitrários.

Configure com `setupSkill.py`. A credencial é lida de uma entrada KeePass usando o perfil configurado: `app_key` e `app_secret` normalmente estão nos campos `username` e `password`. Confirme toda escrita antes de enviar `confirm: true`. Consulte as referências desta skill para os formatos de cada operação.
