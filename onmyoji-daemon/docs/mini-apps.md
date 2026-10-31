# Gateway Mini Apps

O Gateway publica uma Mini App por referência: recebe `root_path`,
`entrypoint` e as políticas de publicação, mas não recebe, copia ou armazena
os arquivos da aplicação.

## Separação de responsabilidades

| Conteúdo | Localização |
| --- | --- |
| Código e assets publicados | Um subdiretório do workspace configurado do Shikigami (`codex.project_directory`) |
| Registro de publicações, sessões, TLS, Tunnel, eventos e uploads temporários | `configs/daemon/services/mini-apps/` |
| Perfis de vault e referências de credenciais | `configs/mcp/credentials.toml` e `configs/daemon/services/mini-apps/tunnel.toml` |

O Gateway rejeita uma raiz fora do workspace, inclusive caminhos que escapem
por link simbólico. Ele também rejeita qualquer diretório de estado fora de
`configs/`. Em cada início, publicação, recarga e atendimento público, a raiz
registrada é conferida novamente contra o workspace vigente. Uma publicação que
deixe de ser válida é marcada com erro e não é servida.

Alterações no diretório publicado são consumidas diretamente pelo Gateway; não
há sincronização de arquivos. Para mover uma Mini App, atualize sua publicação
com a nova raiz e entrypoint válidos dentro do workspace.

## Credencial do Cloudflare Tunnel

O Tunnel seleciona um perfil de vault MCP e uma entrada KeePass. O token é
resolvido diretamente pelo serviço interno `KeePassCredentialResolver`, somente
em memória, sem expor uma ferramenta de vault, scripts intermediários, sessões
ou arquivos temporários. A configuração do Tunnel persiste apenas
`credential_profile`, `credential_entry_path` e o campo protegido utilizado.

## Callbacks OAuth de integrações MCP

O Gateway recebe callbacks HTTPS para integrações explicitamente registradas no
broker interno: BTG e E-mail. Cada uma possui seu próprio caminho, adaptador,
vínculo de configuração e armazenamento autorizado. Nenhum miniapp de arquivos
precisa ser publicado para realizar o consentimento.

O callback do E-mail usa `/oauth/mcp/email/callback`. O setup verifica a origem
do Tunnel ativo e permite cadastrar a mesma URI no cliente OAuth do provedor.
O broker encaminha o retorno ao runtime MCP, onde tokens e PKCE são tratados
internamente; credenciais e refresh tokens não são retornados ao navegador.
O modo local de OAuth do E-mail funciona diretamente no setup sem o Gateway.
