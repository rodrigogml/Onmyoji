# Daemon Onmyōji

O `onmyoji-daemon` é o único processo residente de uma instância Onmyōji. Ele atende somente o Shikigami cujo workspace foi configurado em `configs/onmyoji-system.toml`.

Os serviços são registrados pelo código, iniciados como subprocessos locais e administrados por RPC autenticado em loopback. O endpoint, o token efêmero, o estado de habilitação, logs, banco SQLite e contatos ficam em `configs/daemon/`. Arquivos de atividade do agente nunca ficam no `CODEX_HOME`: o gateway usa exclusivamente `<workspace>/.onmyoji/telegram/`.

## Administração da instância

O menu `D. Daemon` distingue explicitamente três estados: daemon instalado na instância, processo local em segundo plano e serviço do sistema operacional. Instalar o daemon cria somente `configs/daemon/` e não inicia processos. O processo local e o serviço de SO são mutuamente exclusivos.

O processo manual é iniciado desacoplado do configurador e responde pelo endpoint RPC antes de o menu informar sucesso. Um lock e um registro com PID, raiz da instância e identificador efêmero impedem supervisores duplicados. A remoção da instância exige a confirmação literal `desinstalar`, para o processo, remove o serviço eventualmente administrado e então apaga apenas `configs/daemon/` daquela instância.

No Windows, a instalação do serviço requer privilégios administrativos e `pywin32` no Python que executará o daemon. No Linux, o mesmo menu gera e habilita uma unit `systemd`, também exigindo privilégios administrativos. O nome padrão é derivado da instância como `Shikigami-<IdentificadorCamelCase>`; a descrição padrão é `Shikigami <Identificador> Daemon`. Os recursos de serviço ficam registrados localmente para que outra instância nunca seja removida por engano.

## Telegram

Abra `D. Daemon → Gateway Telegram` para criar a configuração local e selecionar um perfil KeePass existente. O token não é solicitado nem armazenado pelo Onmyōji: o menu guarda somente uma referência, sugerida como `APIs/Telegram:<IdentificadorCamelCase>`. O teste de conexão obtém o token pelo KeePass e chama `getMe`, sem exibir o segredo.

Ao definir a referência, o setup tenta ler a entrada. Quando já há token, permite mantê-lo, substituí-lo ou corrigir o caminho. Quando a entrada não existe, permite corrigir o caminho, criar a entrada com token informado por prompt oculto e enviado ao KeePass somente via stdin, ou salvar a referência para preenchimento posterior. A validação também informa a quantidade de owners pareados.

A validação mostra cada dependência separadamente: instalação do daemon, TOML do gateway, perfil KeePass, referência do token, owners pareados, workspace do Shikigami, Codex-CLI, teste de conexão e execução controlada do agente. O gateway só pode ser habilitado após os testes Telegram e Codex; falhas recentes do gateway também são exibidas sem incluir segredos.

Quando TOTP é configurado e habilitado, os comandos privados de cada owner incluem `/new`, `/config` e `/totp`. O fluxo TOTP pede uma senha real ou falsa mantida no KeePass, lista entradas paginadas, devolve o código em mensagem copiável e agenda a exclusão das mensagens auxiliares.

`/config` abre um menu efêmero e privado por conversa para controlar `Compartilha Pensamentos` e `Excluir Pensamentos`; ambas começam habilitadas e são persistidas no SQLite do gateway. O executor CLI atual ainda não produz eventos públicos de pensamento, portanto as preferências já são persistidas para a migração do App Server, mas não há pensamentos a exibir ou excluir nesta fase. Comandos desconhecidos, TOTP desabilitado, sessões expiradas e configurações TOTP incompletas recebem uma resposta descritiva em vez de descarte silencioso.

O gateway aceita inicialmente uma identidade Telegram. Pairing, owners e conversas ficam isolados na instância; mensagens de remetentes que não sejam owners em DM são ignoradas.

## Anexos Telegram

Fotos, documentos e mensagens de voz são baixados de forma limitada e autenticada, com máximo de 20 MiB por arquivo, 50 MiB por mensagem e 250 MiB retidos por conversa, valores ajustáveis em `telegram.toml`. A fila é persistida no SQLite por conversa; um processo interrompido devolve itens em execução à fila ao iniciar novamente, e cada conversa é atendida serialmente.

Os arquivos retidos ficam em `<workspace>/.onmyoji/telegram/attachments/<chat>/<geração>/`. O App Server recebe fotos como `localImage`; documentos e vozes recebem um caminho temporário. Quando uma configuração EccoVox local autoriza essa área de staging, mensagens de voz também recebem transcrição local. O agente pode listar os metadados da conversa e materializar um anexo de forma explícita pela ferramenta dinâmica `telegram_gateway`, sempre restrita ao owner, à conversa e à geração atuais. O staging é removido ao fim do turno, e `/new` remove a fila e todos os anexos retidos da geração anterior.

O envio de anexos requer o modo App Server habilitado. No modo `codex exec`, o gateway recusa o anexo explicitamente, em vez de expor caminhos ou executar uma importação insegura.

## Respostas em áudio e mídia de saída

O menu `Gateway Telegram → Configurar respostas em áudio` seleciona um perfil EccoVox, o desligamento automático por inatividade e, separadamente, a permissão de mídia enviada pelo agente. O modo texto/áudio é persistido por conversa e volta para texto após o tempo configurado sem nova mensagem do owner. O menu `/config` alterna esse modo; o agente também pode consultar e alterá-lo pela ferramenta dinâmica `telegram_gateway.set_reply_mode` na conversa ativa.

Quando o modo é áudio, o gateway inclui uma orientação transitória para resposta falável no turno e sintetiza a resposta final localmente pelo EccoVox. A falha de síntese é informada e, por padrão, entrega a resposta em texto. O áudio e qualquer arquivo de saída vivem somente no staging/outbox do turno, dentro do workspace, e são apagados ao fim.

Se `agent_outbound_media` estiver habilitado, o agente pode solicitar `get_outbox` e `send_file`; o gateway aceita exclusivamente arquivos regulares do outbox daquele turno, limita tamanho e envia somente à DM ativa. O modelo nunca informa chat, owner ou um caminho arbitrário ao gateway.
