# Daemon Onmyōji

O `onmyoji-daemon` é o único processo residente de uma instância Onmyōji. Ele atende somente o Shikigami cujo workspace foi configurado em `configs/onmyoji-system.toml`.

Os serviços são registrados pelo código, iniciados como subprocessos locais e administrados por RPC autenticado em loopback. O endpoint, o token efêmero, o estado de habilitação, logs, banco SQLite, contatos e staging ficam em `configs/daemon/`.

## Administração da instância

O menu `D. Daemon` distingue explicitamente três estados: daemon instalado na instância, processo local em segundo plano e serviço do sistema operacional. Instalar o daemon cria somente `configs/daemon/` e não inicia processos. O processo local e o serviço de SO são mutuamente exclusivos.

O processo manual é iniciado desacoplado do configurador e responde pelo endpoint RPC antes de o menu informar sucesso. Um lock e um registro com PID, raiz da instância e identificador efêmero impedem supervisores duplicados. A remoção da instância exige a confirmação literal `desinstalar`, para o processo, remove o serviço eventualmente administrado e então apaga apenas `configs/daemon/` daquela instância.

No Windows, a instalação do serviço requer privilégios administrativos e `pywin32` no Python que executará o daemon. No Linux, o mesmo menu gera e habilita uma unit `systemd`, também exigindo privilégios administrativos. O nome padrão é derivado da instância como `Shikigami-<IdentificadorCamelCase>`; a descrição padrão é `Shikigami <Identificador> Daemon`. Os recursos de serviço ficam registrados localmente para que outra instância nunca seja removida por engano.

## Telegram

Abra `D. Daemon → Gateway Telegram` para criar a configuração local e selecionar um perfil KeePass existente. O token não é solicitado nem armazenado pelo Onmyōji: o menu guarda somente uma referência, sugerida como `APIs/Telegram:<IdentificadorCamelCase>`. O teste de conexão obtém o token pelo KeePass e chama `getMe`, sem exibir o segredo.

A validação mostra cada dependência separadamente: instalação do daemon, TOML do gateway, perfil KeePass, referência do token, workspace do Shikigami, Codex-CLI e teste de conexão. O gateway não pode ser habilitado enquanto houver uma falha; itens pendentes são mostrados como tal e incluem a orientação de correção.

O gateway aceita inicialmente uma identidade Telegram. Pairing, owners e conversas ficam isolados na instância; mensagens de remetentes que não sejam owners em DM são ignoradas.
