# MySQL no MCP interno

A integração MySQL executa o cliente nativo configurado, obtendo usuário e senha exclusivamente do vault MCP. Nenhuma credencial integra argumentos, respostas ou arquivos de configuração MySQL.

Cada perfil define contexto de uso, servidor, banco e socket opcionais, entrada KeePass e raízes autorizadas para scripts SQL. Com um perfil habilitado ele é implícito; com vários, `mysql.list_profiles` orienta a seleção.

As ferramentas são `mysql.query`, `mysql.execute`, `mysql.script`, `mysql.client` e `mysql.ping`. `execute`, `script` e `client` exigem `confirm=true`. Scripts só podem ser lidos das raízes configuradas. Argumentos livres do cliente começam desabilitados e precisam ser habilitados deliberadamente no perfil.

> [!WARNING]
> `mysql.execute` e `mysql.client` permitem DML, DDL e administração. O MCP não reescreve SQL nem mascara erros do servidor; confirme banco, consulta e efeito antes de executar alterações destrutivas.

O processo filho recebe a senha somente por `MYSQL_PWD`, com timeout e parâmetros de conexão definidos pelo operador. `query` interpreta a saída tabular como colunas e linhas; as demais operações devolvem somente stdout e stderr não sensíveis.
