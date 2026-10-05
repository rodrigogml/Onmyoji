# Integração de consultas BIS10CMD

O wrapper versionado em `available-skills/bis10cmd/scripts/bis10cmd.py` recebe respostas JSON v1 de consultas `accountStatement get/list`, além dos cadastros financeiros existentes.

O recurso `accountStatements` é validado antes de ser entregue: IDs, valores decimais em texto, operação, tipo, datas, categorias e continuidade da página. Uma consulta que não retorna sua resposta estruturada gera `statement_query_unavailable`; uma resposta incompleta gera `invalid_statement_response`. Falhas do cliente continuam preservando os dados parciais e mensagens funcionais disponíveis.

A paginação é explícita. O wrapper não calcula critérios empresariais de duplicidade nem consome automaticamente páginas; o chamador deve seguir `nextOffset` até `hasMore=false`. A política de duplicidade pertence à orientação da empresa, incluindo a conta e a natureza débito/crédito.

A versão Java correspondente precisa ser instalada junto com suas dependências. Sincronizar o Onmyōji atualiza o wrapper, mas não substitui o JAR configurado. Credenciais continuam vindo exclusivamente da KeePassVault.

As instruções propostas ficam ao lado de `SKILL.md` e `references/commands.md`. Conforme `AGENTS.md`, elas só passam a valer após a incorporação aprovada; arquivos `.proposed.md` não são instruções vigentes.
