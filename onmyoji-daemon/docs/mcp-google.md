# Google no MCP interno

A integração Google publica Gmail, People/Contatos, Drive e Calendar pelo servidor MCP local. O Client ID, o Client Secret e os refresh tokens permanecem na entrada KeePass escolhida pelo perfil; somente access tokens transitórios são mantidos em memória durante cada chamada.

## Configuração e OAuth

No setup, acesse `MCP > Integrações MCP > Google`. Cada perfil define a descrição que orienta o agente, um perfil de vault, uma entrada KeePass dedicada, o identificador interno do token OAuth, o usuário Gmail e raízes locais de leitura e escrita.

O Client ID é armazenado em `username`, o Client Secret em `password` e o estado OAuth versionado em `notes`. A ação `Autorizar conta no Google` inicia um callback temporário em `127.0.0.1`, abre o consentimento no navegador e grava somente o refresh token no vault. O callback não exige Gateway, túnel ou daemon ativo.

> [!IMPORTANT]
> Cadastre no Google Cloud um cliente OAuth do tipo aplicativo para computador. Ative as APIs Gmail, People, Drive e Calendar e conceda somente os escopos necessários à conta.

## Perfis e publicação

Com um único perfil habilitado, ele é implícito nas ferramentas. Com vários, `google.list_profiles` apresenta seus nomes e descrições para escolha. Sem perfil habilitado, nenhuma ferramenta Google é publicada.

As operações preservadas da integração legada são publicadas individualmente sob `google.gmail.*`, `google.people.*`, `google.drive.*` e `google.calendar.*`. Todas as operações que alteram dados exigem `confirm=true`; leituras não exigem confirmação.

## Arquivos

`drive.uploads.create` só lê `body.file_path` quando o arquivo pertence a uma das `readable_roots`. Respostas binárias podem ser gravadas por `output_path` somente em `writable_roots`. Sem destino, uma resposta binária abaixo do limite configurado é retornada em Base64; acima dele a chamada é recusada.

> [!WARNING]
> As raízes autorizadas são fronteiras do serviço MCP, independentes do sandbox do agente. Não inclua diretórios de credenciais, `configs/` ou outras áreas privadas da instância.
