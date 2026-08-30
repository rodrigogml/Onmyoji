---
name: notion
description: Integração com a API pública do Notion para consultar e modificar páginas, blocos, databases, data sources, views, comentários, arquivos, busca, usuários e emojis. Usa perfis INI e obtém tokens exclusivamente através de um provedor KeePassVault, sem expor segredos ao contexto, argumentos ou logs.
---

# Notion

Use `scripts/notion.py` como wrapper JSON determinístico para a API pública do Notion.

## Execução

Sempre informe `--config <CODEX_HOME>/configs/notion.toml --profile <perfil>` e envie JSON pelo stdin.

- Use `version: 1`.
- Use `operation` pertencente ao registro documentado em `references/api-contracts.md`.
- Use `params` para IDs presentes no caminho, `query` para parâmetros GET e `body` para payloads JSON.
- Use `paginate: true` quando precisar consolidar todas as páginas de um endpoint paginado.
- Use `body.file_path` para uploads de arquivos.
- Confirme operações destrutivas antes de executá-las.
- Nunca coloque tokens, senhas ou respostas secretas em prompts, argumentos, arquivos ou logs.

## Perfis

Os perfis reais ficam em `<CODEX_HOME>/configs/notion.toml`, são ignorados pelo Git. Use `configs/notion.toml.model` como modelo.

O perfil aponta para a KeePassVault. O token é lido por pipe entre processos, mantido apenas em memória e nunca reproduzido na saída.

## API e limitações

A skill usa `Notion-Version: 2026-03-11` e cobre a API pública atual, incluindo páginas, blocos, databases, data sources, views, comentários, busca, usuários, emojis, uploads e tarefas assíncronas.

Não implementar Admin API, OAuth interativo ou receptor HTTP de webhooks nesta versão. Operações que exigem credenciais administrativas, OAuth ou um servidor público devem retornar erro estruturado.

Consulte `references/api-contracts.md` para escolher operações e compreender permissões, paginação e efeitos destrutivos.
