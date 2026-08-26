# Operações

Use `query` para consultas que retornam linhas, `execute` para comandos parametrizados, `script` para um arquivo SQL e `client` para argumentos explícitos do cliente MySQL permitidos pelo perfil. `ping` valida a conexão.

O wrapper usa parâmetros posicionais do driver quando disponíveis. Para o cliente nativo, a senha é fornecida por variável de ambiente temporária do processo e não por argumento. Saídas são decodificadas como UTF-8 e retornadas como JSON; erros incluem código estável e mensagem sem credenciais.

Operações administrativas, DDL, importações, exportações, backups e restaurações são permitidas quando o perfil e o comando solicitado as suportarem. Confirme o alvo e o efeito antes de executar uma ação destrutiva que não tenha sido pedida explicitamente.
