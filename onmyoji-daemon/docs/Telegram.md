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

## Voz

Transcrição e síntese usam diretamente o serviço interno MCP EccoVox. O perfil
selecionado pelo Gateway fica em `configs/mcp/eccovox.toml`; ao habilitar voz,
o setup adiciona somente o staging do Telegram às raízes de leitura e escrita
desse perfil. Assim, a integração não depende de uma skill e continua limitada
aos arquivos transitórios do workspace.
