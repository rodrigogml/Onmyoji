# Developer instructions

Toda execução iniciada pelo Onmyōji usa App Server e recebe instruções compostas na ordem: regras versionadas do Onmyōji, instruções locais do Shikigami e contrato versionado do canal quando houver um canal. Mensagens, anexos e transcrições dos usuários são sempre inputs de turno, nunca developer instructions.

Os recursos comuns estão em `onmyoji-daemon/resources/instructions/`. A personalização local fica em `configs/daemon/instructions/shikigami.md`, é ignorada pelo Git e é limitada à própria raiz. Alterar a composição estrutural inicia uma nova thread Codex na próxima mensagem; alteração de modo texto/áudio reaplica apenas o overlay do canal.

O Gateway Telegram exige App Server. O menu Codex-CLI inicia o launcher interativo `onmyoji_daemon.cli interactive`, que aplica as camadas Onmyōji e Shikigami também no terminal local.
