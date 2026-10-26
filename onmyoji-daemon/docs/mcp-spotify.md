# Spotify no MCP interno

A integração Spotify publica catálogo, biblioteca, playlists, seguimento e player pelo servidor MCP local. O Client ID e o refresh token permanecem em uma entrada KeePass dedicada; o access token existe somente em memória durante a chamada.

## Configuração e OAuth

No setup, acesse `MCP > Integrações MCP > Spotify`. Cada perfil define uma descrição de uso, perfil de vault, entrada KeePass e porta do callback local. O Client ID fica em `username` e o fluxo OAuth grava o refresh token em `password`.

Cadastre no Spotify Dashboard a Redirect URI exibida pelo perfil, por padrão `http://127.0.0.1:8888/callback`. Depois use `Autorizar conta no Spotify`. O fluxo usa Authorization Code com PKCE, abre o navegador e não exige Client Secret, Gateway, túnel ou daemon ativo.

Consulte a documentação oficial do [Authorization Code com PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow) e o [guia de migração da Web API de fevereiro de 2026](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide).

> [!IMPORTANT]
> Aplicativos Spotify em Development Mode exigem que o proprietário tenha Premium e estão sujeitos aos limites de usuários e aplicativos definidos pelo Spotify. Refresh tokens expiram e, quando recusados, o perfil precisa ser autorizado novamente.

## Perfis e publicação

Com um único perfil habilitado, ele é implícito. Com vários, `spotify.list_profiles` informa nomes e descrições. Sem perfil habilitado, nenhuma ferramenta Spotify é publicada.

As 33 operações migradas são publicadas individualmente em `spotify.*`. Toda mutação exige `confirm=true`; não há allowlist de operações nem modo parcial `read_only/read_write` no perfil.

O MCP usa os contratos atuais da Web API:

- playlists são criadas em `/me/playlists` e seus conteúdos usam `/items`;
- salvar, remover, seguir e deixar de seguir usam a biblioteca genérica `/me/library` com URIs;
- `library.list` exige `query.type` com `tracks`, `albums`, `shows`, `episodes` ou `audiobooks`, porque a API mantém endpoints separados para listar cada coleção;
- pesquisas em Development Mode devem respeitar o limite vigente do Spotify.

> [!NOTE]
> Quando o Spotify rotaciona um refresh token durante a renovação, o MCP atualiza o campo protegido no vault antes de executar a operação solicitada.
