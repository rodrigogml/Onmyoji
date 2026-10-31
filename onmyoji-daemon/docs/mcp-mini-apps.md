# MiniApps no MCP interno

O módulo `mini_apps` administra publicações do Gateway Mini Apps e sua infraestrutura local. As chamadas seguem pelo RPC autenticado do daemon; os arquivos das aplicações são referenciados no workspace, sem upload ou cópia pelo MCP.

## Configuração

Use **Daemon > MiniApps** para preparar o gateway, HTTPS e, quando necessário, o Cloudflare Tunnel. O estado operacional, os registros de publicações, TLS e Tunnel ficam em `configs/daemon/services/mini-apps/`. O token do Tunnel é resolvido pelo vault MCP interno.

O módulo não tem perfis MCP independentes. Suas ferramentas administrativas são publicadas pelo servidor MCP; executar as operações depende da disponibilidade do daemon e da configuração do serviço. `mini_apps.status` e `mini_apps.diagnose` ajudam a verificar essa preparação.

## Ferramentas e dados

| Grupo | Ferramentas |
| --- | --- |
| Diagnóstico | `status`, `diagnose`, `list`, `inspect` e `events`. |
| Publicações | `create`, `update`, `publish`, `unpublish`, `reload` e `delete`. |
| Cloudflare Tunnel | `tunnel_status`, `tunnel_configure`, `tunnel_provision`, `tunnel_start`, `tunnel_stop`, `tunnel_restart` e `tunnel_install_cloudflared`. |
| TLS local | `tls_status` e `tls_install_trust`. |
| OAuth remoto | `oauth_callback_url`, `oauth_info`, `oauth_active`, `oauth_start`, `oauth_status`, `oauth_cancel` e `oauth_cancel_active`. |

Todos os nomes usam o prefixo `mini_apps.`. Operações que alteram estado exigem `confirm=true`.

Uma publicação informa `root_path`, `entrypoint` e as políticas de autenticação e duração. A raiz deve ser um diretório dentro do workspace; o entrypoint deve ser um arquivo Python dessa raiz que exporte `create_app`. O gateway valida novamente essas referências ao publicar e atender a aplicação.

## Publicações e callbacks

Atualizações nos arquivos publicados são consumidas no próprio workspace. Remover ou despublicar uma Mini App altera seu registro e disponibilidade, sem apagar seu código. URLs de claim e segredos de callback retornados na criação devem ser tratados como credenciais de acesso.

O broker interno também recebe callbacks OAuth de BTG e E-mail sem exigir uma aplicação de arquivos publicada. As ferramentas `mini_apps.oauth_*` existentes descrevem o fluxo BTG; a integração E-mail fornece suas próprias ferramentas de autorização e usa internamente o broker compartilhado. Consulte [Gateway Mini Apps](mini-apps.md) para armazenamento, limites de caminhos, credenciais do Tunnel e callbacks.
