# Developer instructions

O Gateway Telegram e o Console interativo Onmyōji usam App Server e recebem instruções compostas na ordem: regras versionadas do Onmyōji, instruções locais do Shikigami e contrato versionado do canal quando houver um canal. Mensagens, anexos e transcrições dos usuários são sempre inputs de turno, nunca developer instructions.

Os recursos comuns estão em `onmyoji-daemon/resources/instructions/`. A personalização versionada do Shikigami fica em `shikigami/instructions.md` e é limitada a essa pasta. Alterar a composição estrutural inicia uma nova thread Codex na próxima mensagem; alteração de modo texto/áudio reaplica apenas o overlay do canal.

O Gateway Telegram exige App Server. No menu Codex-CLI, `Executar Codex-CLI` abre o cliente nativo com o `CODEX_HOME`, o bloco nativo gerenciado em `config.toml` e diretórios adicionais já configurados; ele não injeta developer instructions compostas. `Console interativo Onmyōji` é o modo opcional `onmyoji_daemon.cli interactive`, que aplica as camadas Onmyōji e Shikigami também no terminal local.
