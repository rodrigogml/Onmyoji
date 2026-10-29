# Gateway Telegram

O Gateway Telegram recebe mensagens e anexos, inicia o Codex App Server quando
necessário e encaminha a conversa ao agente com o mesmo contexto de execução
configurado para a instância.

## Anexos no workspace

Anexos recebidos pelo Telegram são arquivos de trabalho do agente. Por isso,
o Gateway os mantém obrigatoriamente no workspace configurado do Shikigami, em
`<workspace>/.onmyoji/telegram/attachments/`, com staging transitório em
`<workspace>/.onmyoji/telegram/staging/`.

Eles não devem ser migrados para `configs/`: o agente precisa poder ler,
interpretar, criar, editar, substituir e excluir esses arquivos durante a
conversa, inclusive para devolver uma versão alterada ao usuário. O workspace
continua sujeito às permissões de execução do Codex; o Gateway não concede
acesso adicional fora dele.

Em contraste, o estado privado do serviço — configuração, credenciais,
sessões, logs e metadados operacionais — pertence a
`configs/daemon/services/telegram/` e não é versionado.

## Contexto e procedência

O Gateway separa o contexto confiável do conteúdo enviado ao agente. Para cada
turno, as developer instructions recebem apenas metadados locais: tipo de
conversa, rótulo de confiança da origem, presença de anexos e se o histórico
inclui conteúdo de terceiros não confiável. Esses metadados ficam na fila do
Gateway para sobreviver à execução assíncrona do turno.

Texto, legendas, nomes de arquivos e transcrições não são inseridos nas
developer instructions. Eles seguem como conteúdo de entrada, identificado
por procedência. Uma transcrição de voz informa que é automática e pode conter
erros de reconhecimento; nomes próprios, siglas e termos incomuns exigem
confirmação antes de orientar uma ação relevante.

## Respostas em texto e menus

Respostas do agente são convertidas de um subconjunto simples de Markdown para
HTML seguro do Telegram. O Gateway aceita negrito, itálico, sublinhado,
tachado, links e citações, escapa qualquer HTML de origem e divide mensagens
longas sem descartar o começo ou o fim da resposta. A [Bot API do
Telegram](https://core.telegram.org/bots/api#formatting-options) suporta
formatação HTML e o parâmetro `parse_mode` em mensagens de texto.

Menus interativos são publicados somente em conversas privadas, pois a
validação do callback exige o owner dessa conversa. Em grupos e tópicos, a
ferramenta não é entregue ao agente; envio de mensagens e arquivos continua
restrito à conversa ativa.

## Voz

Transcrição e síntese usam diretamente o serviço interno MCP EccoVox. O perfil
selecionado pelo Gateway fica em `configs/mcp/eccovox.toml`; ao habilitar voz,
o setup adiciona somente o staging do Telegram às raízes de leitura e escrita
desse perfil. Assim, a integração não depende de uma skill e continua limitada
aos arquivos transitórios do workspace.
