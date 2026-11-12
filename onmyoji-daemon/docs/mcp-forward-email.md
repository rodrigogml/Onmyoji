# Forward Email no MCP interno

A integração Forward Email publica a administração de domínios e aliases pelo servidor MCP local. O token de API permanece no vault interno e é usado como usuário da autenticação Basic com senha vazia.

## Perfis

Cada perfil define seu contexto de uso, estado habilitado, referência do token no vault e um domínio padrão opcional. Com um único perfil habilitado, a seleção é implícita. Com vários perfis, `forward_email.list_profiles` apresenta seus nomes e contextos e as demais ferramentas exigem `profile`.

## Operações

O módulo permite listar, consultar, criar e atualizar domínios; verificar seus registros DNS e SMTP; e listar, consultar, criar, atualizar e remover aliases. Também permite gerar a senha de um alias.

Todas as alterações exigem `confirm=true`. A geração de senha pode retornar um segredo exibido uma única vez. Quando `body.is_override=true`, a operação pode apagar permanentemente o armazenamento IMAP existente do alias.

> [!CAUTION]
> Use `is_override` somente quando a perda do conteúdo armazenado estiver conscientemente aceita. A configuração do perfil e a referência do token ficam em `configs/mcp/`; tokens e senhas de aliases não são persistidos em arquivos do MCP.

A superfície implementada segue a [documentação oficial da API Forward Email](https://forwardemail.net/en/email-api).
