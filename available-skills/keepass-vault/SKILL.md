---
name: keepass-vault
description: Use para listar, consultar e alterar entradas, TOTPs e anexos de um cofre KeePassXC configurado por perfil no Onmyōji. Acione quando uma tarefa precisar recuperar ou administrar segredos, códigos TOTP ou anexos sem expor senhas ao contexto, argumentos ou logs.
---

# KeePass Vault

Use `scripts/keepass_vault.py` com `--config <CODEX_HOME>/configs/keepass.toml` e um perfil explícito. Envie uma única requisição JSON pela entrada padrão; receba uma única resposta JSON na saída padrão.

Não inclua a senha mestra, códigos TOTP ou conteúdo de anexos em mensagens, comandos, arquivos versionados ou logs. Configure o perfil com `setupSkill.py`; o configurador também pode criar um vault local isolado em `CODEX_HOME/configs/vaults/` e guardar sua senha no provedor seguro do SO. Leia [o contrato](references/contract.md) antes de integrar uma operação e [a configuração](references/configuration.md) ao criar ou revisar perfis.

Use `list.totp` para descobrir entradas com TOTP. A implementação exporta o XML do cofre uma única vez em memória; não faça uma abertura por entrada.
