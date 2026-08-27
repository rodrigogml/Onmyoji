---
name: todoist
description: Use para consultar e administrar tarefas, projetos, seções, etiquetas, comentários, lembretes, arquivos e sincronização do Todoist por perfis configurados no Onmyōji. Acione quando uma tarefa precisar ler ou modificar dados do Todoist sem expor o token, que é recuperado exclusivamente do KeePass Vault.
---

# Todoist

Use `scripts/todoist.py` com `--config <CODEX_HOME>/configs/todoist.toml` e `--profile <perfil>`. Envie uma requisição JSON por stdin e receba uma única resposta JSON por stdout.

Antes de executar alterações destrutivas, confirme com o usuário. Nunca inclua token, senha ou cabeçalho HTTP em prompt, argumentos, arquivos temporários ou logs. Configure perfis com `setupSkill.py`; consulte [o contrato](references/contract.md) para as operações e o envelope.
