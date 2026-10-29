# Autoria de schemas e migrations

Use este documento somente ao criar ou evoluir uma skill do Shikigami que exija dados estruturados persistentes.

## Estrutura obrigatória

```text
shikigami-skills/<skill>/schemas/<schema>/
├── schema.toml
└── migrations/NNNN-descricao.toml
```

O identificador do schema e os nomes de tabela e coluna usam letras minúsculas, números e sublinhado, iniciando por letra. O schema começa em `0001`. Toda migration posterior recebe o próximo número sequencial de quatro ou mais dígitos e uma descrição curta em kebab-case.

Não edite, apague, renomeie nem reordene uma migration que já possa ter sido aplicada. Para evoluir o banco, crie uma nova migration aditiva.

## Descritor

`schema.toml` declara somente a identidade:

```toml
schema_format = 1
id = "crm_leads"
title = "CRM · Leads"
description = "Leads comerciais da skill CRM."
```

## Migration inicial

```toml
schema_id = "crm_leads"
migration_version = 1

[[operations]]
type = "create_table"
table = "leads"
operations = ["create", "get", "list", "update"]

[[operations.columns]]
name = "id"
type = "text"
primary_key = true
generated = "uuid"

[[operations.columns]]
name = "name"
type = "text"
required = true
max_length = 200
```

Use apenas `create_table`, `add_column` e `create_index`. Cada tabela deve ter uma única chave primária. Declare apenas operações que a skill realmente precisa permitir ao agente. Não armazene segredos, credenciais, anexos, logs ou dados transitórios em tabelas genéricas.

## Entrega

Valide a fonte e seus testes. Não tente aplicar a migration pelo agente. Informe ao operador o schema e as migrations pendentes para revisão em `MCP > Integrações MCP > Tabelas de dados`.
