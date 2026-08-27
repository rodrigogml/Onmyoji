# Configuração

O wrapper recebe um perfil INI externo por `--config`. A seção `[forward_email]` define a API e o domínio padrão. O domínio também pode ser informado em `params.domain`.

A seção `[vault]` aponta para o wrapper externo da skill KeePassVault. O campo configurado deve conter exclusivamente o token da API Forward Email. A autenticação HTTP será Basic com o token como usuário e senha vazia, conforme a documentação oficial (`-u API_TOKEN:`).

Não são aceitas credenciais OAuth, refresh token, client ID ou client secret.

