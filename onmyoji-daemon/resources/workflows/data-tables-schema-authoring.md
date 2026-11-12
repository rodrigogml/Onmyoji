# Autoria de schemas para Data Tables

Use este workflow somente quando o usuário solicitar criar, alterar, corrigir ou estruturar um schema que será usado pelo serviço MCP `data_tables` do Onmyōji.

Não use este workflow para bancos, tabelas, migrations, modelos ou schemas de outros sistemas: MySQL, SQLite de uma aplicação, ORM, código de projeto, planilhas, APIs externas ou bancos internos de outro módulo MCP. Para consultar ou alterar registros de um schema Data Tables já instalado, use diretamente `data_tables.*`; não leia este workflow.

## 1. Confirmar o escopo

Antes de criar qualquer arquivo, confirme que a necessidade é de dados estruturados persistentes pertencentes a uma skill do Shikigami. Não use Data Tables para memória textual, segredos, credenciais, anexos, logs, dados transitórios ou estado interno de um módulo MCP.

Se o usuário pedir uma tabela para outro sistema, pare este workflow e use as regras daquele sistema. Não transforme um banco externo em Data Tables apenas porque possui tabelas ou migrations.

## 2. Escolher a fonte canônica

Crie ou altere somente a definição versionada em:

```text
shikigami-skills/<skill>/schemas/<schema>/
├── schema.toml
└── migrations/NNNN-descricao.toml
```

Nunca use o workspace, `configs/` ou o banco SQLite como fonte de definição. O schema pertence à skill que o utiliza; não crie um schema genérico para reunir dados de skills sem relação funcional.

Os identificadores de schema, tabela e coluna usam somente letras minúsculas, números e sublinhado, e começam por letra.

## 3. Declarar a identidade do schema

`schema.toml` declara somente a identidade e a finalidade:

```toml
schema_format = 1
id = "crm_leads"
title = "CRM · Leads"
description = "Leads comerciais da skill CRM."
```

Use uma descrição que permita ao operador reconhecer a finalidade do schema no setup. Não coloque tabelas, colunas, SQL ou migrations nesse arquivo.

## 4. Criar e evoluir migrations

A primeira migration usa `0001`. Cada migration posterior usa o próximo número sequencial de quatro ou mais dígitos e uma descrição curta em kebab-case, por exemplo `0002-adicionar-email.toml`.

Nunca edite, apague, renomeie ou reordene uma migration que possa ter sido aplicada. Para alterar uma estrutura existente, crie uma nova migration aditiva.

Uma migration inicial que cria uma tabela tem esta forma:

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

Use somente `create_table`, `add_column` e `create_index`. Os tipos aceitos são `text`, `integer`, `real`, `boolean`, `date`, `datetime` e `json`. Cada tabela precisa de uma única chave primária. Declare apenas as operações que a skill realmente deve permitir por `data_tables.*`.

## 5. Entregar para instalação administrativa

Valide a fonte e seus testes. Não aplique a migration pelo agente nem tente alterar a estrutura pelo MCP.

Depois de criar ou alterar a definição, informe ao operador o schema afetado e as migrations pendentes. A revisão, instalação e habilitação ocorrem em `MCP > Integrações MCP > Tabelas de dados`.
