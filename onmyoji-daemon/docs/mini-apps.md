# Gateway Mini Apps

O Gateway publica uma Mini App por referência: recebe `root_path`,
`entrypoint` e as políticas de publicação, mas não recebe, copia ou armazena
os arquivos da aplicação.

## Separação de responsabilidades

| Conteúdo | Localização |
| --- | --- |
| Código e assets publicados | Um subdiretório do workspace configurado do Shikigami (`codex.project_directory`) |
| Registro de aplicações, sessões, TLS e eventos | `configs/daemon/services/mini-apps/` |
| Registro compartilhado de listeners e TLS do proxy público | `configs/daemon/services/gateway/` |
| Configuração e executável Cloudflare Tunnel | `configs/daemon/services/cloudflare-tunnel/` |
| Perfis de vault | `configs/mcp/credentials.toml` |

O Gateway rejeita uma raiz fora do workspace, inclusive caminhos que escapem
por link simbólico. Ele também rejeita qualquer diretório de estado fora de
`configs/`. Em cada início, publicação, recarga e atendimento público, a raiz
registrada é conferida novamente contra o workspace vigente. Uma publicação que
deixe de ser válida é marcada com erro e não é servida.

Alterações no diretório publicado são consumidas diretamente pelo Gateway; não
há sincronização de arquivos. Para mover uma Mini App, atualize sua publicação
com a nova raiz e entrypoint válidos dentro do workspace.

## Publicação externa compartilhada

Mini Apps continua funcionando por HTTPS local quando o Gateway compartilhado
ou o Tunnel estão parados. Ele declara os prefixos `a` e `oauth` ao Supervisor,
que os associa ao processo conhecido e sincroniza o Gateway HTTP independente.
O proxy preserva o path completo: as aplicações mantêm `/a/{app_id}/...`.

O serviço não administra Cloudflare nem inicia `cloudflared`. Use **Daemon >
Publicação externa** para configurar a infraestrutura compartilhada. A
credencial do Tunnel é resolvida em memória pelo KeePass interno; sua configuração
guarda somente referências. Consulte [Publicação externa](publicacao-externa.md).

As respostas de publicação distinguem `local_url`, `public_url` configurada e
`public_available`. Uma origem pública configurada pode permanecer consultável
durante uma indisponibilidade. O diagnóstico separa prontidão local da pública.

## Callbacks OAuth de integrações MCP

Mini Apps recebe callbacks HTTPS para integrações explicitamente registradas no
broker interno: BTG e E-mail. Cada uma possui seu próprio caminho, adaptador,
vínculo de configuração e armazenamento autorizado. Nenhum miniapp de arquivos
precisa ser publicado para realizar o consentimento.

O callback do E-mail usa `/oauth/mcp/email/callback`; BTG usa
`/oauth/mcp/btg/callback`. O Gateway compartilhado encaminha ambos, além de
`/oauth/result/...`, pelo listener `oauth`. O broker continua em Mini Apps e
obtém do Supervisor a origem e a disponibilidade públicas, sem ler a configuração
interna do Tunnel. O setup permite cadastrar a mesma URI no cliente OAuth do provedor.
O broker encaminha o retorno ao runtime MCP, onde tokens e PKCE são tratados
internamente; credenciais e refresh tokens não são retornados ao navegador.
O modo local de OAuth do E-mail funciona diretamente no setup sem o Gateway.
