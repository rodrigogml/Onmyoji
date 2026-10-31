# Autoria de schemas para Data Tables

Use este workflow somente quando o usuário solicitar criar, alterar, corrigir ou estruturar um schema que será usado pelo serviço MCP `data_tables` do Onmyōji.

Não use este workflow para bancos, tabelas, migrations, modelos ou schemas de outros sistemas: MySQL, SQLite de uma aplicação, ORM, código de projeto, planilhas, APIs externas ou bancos internos de outro módulo MCP. Para consultar ou alterar registros de um schema Data Tables já instalado, use diretamente `data_tables.*`; não leia este workflow.

## Procedimento

1. Confirme que a necessidade é de dados estruturados persistentes pertencentes a uma skill do Shikigami. Não use Data Tables para memória textual, segredos, credenciais, anexos, logs ou dados transitórios.
2. Crie ou altere somente a definição versionada em `shikigami-skills/<skill>/schemas/<schema>/`. Nunca use o workspace, `configs/` ou o banco SQLite como fonte de definição.
3. Mantenha `schema.toml` para a identidade do schema e `migrations/NNNN-descricao.toml` para toda mudança estrutural. Identificadores de schema, tabela e coluna usam letras minúsculas, números e sublinhado, começando por letra.
4. Inicie com `0001`; toda migration posterior recebe o próximo número sequencial de quatro ou mais dígitos e uma descrição curta em kebab-case. Nunca edite, apague, renomeie ou reordene uma migration que possa ter sido aplicada. Para evoluir a estrutura, crie uma nova migration aditiva.
5. Use apenas operações e colunas que o serviço Data Tables suporta. Declare somente operações que a skill realmente precisa expor ao agente. Cada tabela deve ter uma única chave primária.
6. Não aplique migrations pelo agente. Após alterar a fonte, informe ao operador o schema e as migrations pendentes para revisão e aplicação no menu `MCP > Integrações MCP > Tabelas de dados`.
