# Integração de consultas BIS10CMD

As consultas BIS10CMD são publicadas pelo servidor MCP interno. O recurso `accountStatements` é validado antes de ser entregue: IDs, valores decimais em texto, operação, tipo, datas, categorias e continuidade da página. Uma consulta que não retorna sua resposta estruturada gera `statement_query_unavailable`; uma resposta incompleta gera `invalid_statement_response`. Falhas do cliente preservam os dados parciais e mensagens funcionais disponíveis.

A paginação é explícita. O MCP não calcula critérios empresariais de duplicidade nem consome automaticamente páginas; o chamador deve seguir `nextOffset` até `hasMore=false`. A política de duplicidade pertence à orientação da empresa, incluindo a conta e a natureza débito/crédito.

A versão Java correspondente precisa ser instalada junto com sua pasta `libs`. Atualizar o Onmyōji não substitui o JAR configurado. As credenciais JNDI e BIS10 vêm exclusivamente do vault MCP compartilhado.

Consulte a [documentação do BIS10CMD no MCP](../../onmyoji-daemon/docs/mcp-bis10cmd.md) para perfis, ferramentas e operações de escrita.
