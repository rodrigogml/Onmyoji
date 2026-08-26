# Configuração

O wrapper recebe um perfil INI externo por `--config`. A seção `[cloudflare]` define a API e os parâmetros operacionais. `zone_id` é opcional no perfil; quando ausente, a operação deve recebê-lo em `params`.

A seção `[vault]` aponta para o wrapper externo da skill KeePassVault. O campo configurado deve conter exclusivamente o API Token do Cloudflare. O arquivo real não deve ser versionado.

Não são aceitas credenciais OAuth, API Key global, client ID, client secret ou refresh token.

