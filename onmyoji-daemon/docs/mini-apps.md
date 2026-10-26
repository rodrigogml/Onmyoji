# Gateway Mini Apps

O Gateway publica uma Mini App por referência: recebe `root_path`,
`entrypoint` e as políticas de publicação, mas não recebe, copia ou armazena
os arquivos da aplicação.

## Separação de responsabilidades

| Conteúdo | Localização |
| --- | --- |
| Código e assets publicados | Um subdiretório do workspace configurado do Shikigami (`codex.project_directory`) |
| Registro de publicações, sessões, TLS, Tunnel, eventos e uploads temporários | `configs/daemon/services/mini-apps/` |

O Gateway rejeita uma raiz fora do workspace, inclusive caminhos que escapem
por link simbólico. Ele também rejeita qualquer diretório de estado fora de
`configs/`. Em cada início, publicação, recarga e atendimento público, a raiz
registrada é conferida novamente contra o workspace vigente. Uma publicação que
deixe de ser válida é marcada com erro e não é servida.

Alterações no diretório publicado são consumidas diretamente pelo Gateway; não
há sincronização de arquivos. Para mover uma Mini App, atualize sua publicação
com a nova raiz e entrypoint válidos dentro do workspace.
