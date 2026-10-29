# Developer instructions

O Gateway Telegram e o Console interativo Onmyōji usam App Server e recebem instruções compostas na ordem: regras versionadas do Onmyōji, instruções locais do Shikigami e contrato versionado do canal quando houver um canal. Mensagens, anexos e transcrições dos usuários são sempre inputs de turno, nunca developer instructions.

Os recursos comuns estão em `onmyoji-daemon/resources/instructions/`. A personalização opcional do Shikigami fica em `instructions.md`, na raiz da instância. Quando o arquivo não existir, a composição segue somente com as regras comuns e do canal. Alterar a composição estrutural inicia uma nova thread Codex na próxima mensagem; alteração de modo texto/áudio reaplica apenas o overlay do canal.

O Gateway Telegram exige App Server. No menu Codex-CLI, `Executar Codex-CLI` abre o cliente nativo com o `CODEX_HOME`, o bloco nativo gerenciado em `config.toml` e diretórios adicionais já configurados; ele não injeta developer instructions compostas. `Console interativo Onmyōji` é o modo opcional `onmyoji_daemon.cli interactive`, que aplica as camadas Onmyōji e Shikigami também no terminal local.

Quando a memória MCP está habilitada, o servidor publica no handshake MCP e o Composer injeta no App Server somente as rotas curtas de workflow. Ao ocorrer um gatilho, o agente chama `workflows.read` para receber o procedimento completo aplicável, como `memory-decision` ou `memory-learning`. Os documentos canônicos ficam no daemon, não são copiados para o workspace e não podem ser alterados pelo agente. Essa superfície vale tanto para o CLI nativo quanto para o App Server; uma nova abertura obtém as rotas vigentes.
