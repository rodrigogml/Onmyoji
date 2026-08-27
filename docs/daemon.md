# Daemon Onmyōji

O `onmyoji-daemon` é o único processo residente de uma instância Onmyōji. Ele atende somente o Shikigami cujo workspace foi configurado em `configs/onmyoji-system.toml`.

Os serviços são registrados pelo código, iniciados como subprocessos locais e administrados por RPC autenticado em loopback. O endpoint, o token efêmero, o estado de habilitação, logs, banco SQLite, contatos e staging ficam em `configs/daemon/`.

## Telegram

Inicialize a configuração pelo menu `D. Daemon` ou com `py -3 onmyoji-daemon/setupDaemon.py --onmyoji-root <CODEX_HOME> --action bootstrap`. Edite somente o arquivo local `configs/daemon/services/telegram/telegram.toml`. Ele referencia o perfil KeePass e a entrada que contém o token; não armazena token ou senha.

Instale o projeto para uso fora do checkout com `py -3 -m pip install -e onmyoji-daemon`. O adaptador Windows requer o extra `.[windows]`; no Linux use o modelo `onmyoji-daemon/systemd/onmyoji-daemon.service.model`, substituindo os marcadores pelo usuário, Python e `CODEX_HOME` corretos. Registre o serviço somente depois de validar a execução em foreground.

O gateway aceita inicialmente uma identidade Telegram. Pairing, owners e conversas ficam isolados na instância; mensagens de remetentes que não sejam owners em DM são ignoradas.
