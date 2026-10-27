---
name: onmyoji-data-tables
description: Definir ou operar tabelas estruturadas pertencentes a skills do Shikigami. Use quando uma solicitação exigir registros persistentes com colunas, tipos, validações, filtros ou relações estáveis. Não use para memória textual de instruções, dados transitórios ou bancos internos de um módulo MCP de domínio.
---

# Tabelas de dados do Shikigami

Use `data_tables.list_schemas` antes de operar tabelas que você ainda não conhece. Em seguida, use `data_tables.describe_schema` e respeite integralmente as colunas e operações publicadas. Nunca tente criar, alterar ou reconstruir tabelas por MCP.

Ao desenvolver uma skill que precise de dados estruturados, leia [a autoria de schemas](references/schema-authoring.md). Crie o schema somente em `shikigami/skills/<skill>/schemas/<schema>/`. Não use o workspace, `configs/` ou o banco SQLite como fonte de definição.

O agente não instala migrations. Depois de criar ou alterar a fonte versionada, informe que o operador deve revisar e aplicar o schema no Setup MCP.
