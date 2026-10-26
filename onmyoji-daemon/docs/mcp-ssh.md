# SSH no MCP interno

A integração SSH publica execução remota e transferências SFTP com autenticação por senha ou chave privada. Credenciais e anexos são obtidos pelo vault MCP e nunca aparecem nos argumentos ou respostas das ferramentas.

Cada perfil define contexto de uso, host, porta, usuário, autenticação, entrada KeePass, arquivo `known_hosts` e raízes locais de upload e download. Um único perfil habilitado é implícito; com vários, `ssh.list_profiles` informa nome e contexto.

As ferramentas são `ssh.exec`, `ssh.upload` e `ssh.download`. Todas exigem `confirm=true`, pois um comando aparentemente consultivo pode produzir efeitos remotos. Uploads só leem raízes autorizadas e downloads só escrevem nas raízes configuradas; downloads existentes exigem `overwrite=true`.

## Identidade e autenticação

O setup obtém a chave pública apresentada pelo servidor, mostra a fingerprint SHA256 e só a grava após o operador digitar `CONFIAR`. Conexões usam política estrita: hosts desconhecidos ou chaves divergentes são recusados.

No modo `password`, a senha vem do campo protegido configurado. No modo `key`, a chave privada deve existir como anexo da entrada KeePass. Ela é exportada para um diretório temporário do sistema, recebe permissão restrita, é usada somente naquela sessão e removida inclusive quando a conexão falha. A passphrase opcional também permanece no cofre.

> [!IMPORTANT]
> Chaves temporárias SSH não ficam no workspace nem em `configs/`. `configs/mcp/ssh/known_hosts` contém apenas chaves públicas confirmadas e pode integrar o backup de configuração do Shikigami.

O teste de perfil autentica e encerra a sessão sem executar comando remoto.
