# Daemon Onmyōji

O `onmyoji-daemon` é o único processo residente de uma instância Shikigami. Ele atende somente o workspace configurado localmente em `configs/onmyoji-system.toml`.

Os serviços são registrados pelo código, iniciados como subprocessos locais e administrados por RPC autenticado em loopback. O endpoint, o token efêmero, o estado de habilitação, logs, banco SQLite e contatos ficam em `configs/daemon/`. Arquivos de atividade do agente nunca ficam no `CODEX_HOME`: o gateway usa `<workspace>/.onmyoji/telegram/` e as saídas de processos agendados usam `<workspace>/.onmyoji/task-scheduler/`.

O menu **Daemon > Publicação externa** administra os serviços `gateway` e
`cloudflare-tunnel`, independentes de Mini Apps. Consulte a documentação de
[publicação externa e migração](../onmyoji-daemon/docs/publicacao-externa.md).

## Administração da instância

O menu `D. Daemon` distingue explicitamente três estados: daemon instalado na instância, processo local em segundo plano e serviço do sistema operacional. Instalar o daemon cria somente `configs/daemon/` e não inicia processos. O processo local e o serviço de SO são mutuamente exclusivos.

O runtime do daemon inclui `jsonschema` para validar contratos de notificações do agente. A instalação ou reparação verifica essa dependência antes de considerar o runtime pronto.

No wizard, a reparação verifica e instala os componentes mesmo quando o runtime já parecia válido. Se o teste controlado informar um módulo Python ausente, o wizard oferece reparar os componentes e repetir o teste uma vez; outras falhas são apresentadas para diagnóstico.

O teste controlado usa o Python do runtime da instância e, com isolamento Linux, executa sob a conta dedicada do Shikigami.

O processo manual é iniciado desacoplado do configurador e responde pelo endpoint RPC antes de o menu informar sucesso. Um lock e um registro com PID, raiz da instância e identificador efêmero impedem supervisores duplicados. A remoção da instância exige a confirmação literal `desinstalar`, para o processo, remove o serviço eventualmente administrado e então apaga apenas `configs/daemon/` daquela instância.

No Windows, a instalação do serviço requer privilégios administrativos e `pywin32` no Python que executará o daemon. No Linux, o mesmo menu gera e habilita uma unit `systemd`, também exigindo privilégios administrativos. O nome padrão é derivado da instância como `Shikigami-<IdentificadorCamelCase>`; a descrição padrão é `Shikigami <Identificador> Daemon`. Os recursos de serviço ficam registrados localmente para que outra instância nunca seja removida por engano.

## Notificações ao agente e agendador

`D. Daemon → N. Notificações ao agente` configura o ingresso privado/local, a conta SMTP padrão e o canal opcional de incidentes. Eventos entram na mesma inbox e no mesmo App Server do Telegram. O canal de retorno determina instruções, ferramentas e associação de thread: um evento com retorno Telegram segue o mesmo fluxo daquele chat/tópico, com `reuse` ou `new`. O ingresso pode funcionar com a recepção Telegram desligada. Não é publicado endpoint externo.

`D. Daemon → A. Agendador` consulta e ordena definições existentes. O serviço independente `task-scheduler` executa data única, intervalo e cron, com ações de notificação ou processo local. A administração das definições é feita por CLI/RPC; o setup não as cria nem edita. Novos recursos começam desabilitados.

Consulte [contratos, diagrama, comandos e recuperação](eventos-agendamentos.md).

## Telegram

Abra `D. Daemon → Gateway Telegram` para criar a configuração local em `configs/daemon/services/telegram/config.toml` e selecionar um perfil KeePass existente. O token não é solicitado nem armazenado pelo Onmyōji: o menu guarda somente uma referência, sugerida como `APIs/Telegram:<IdentificadorCamelCase>`. O teste de conexão obtém o token pelo KeePass e chama `getMe`, sem exibir o segredo.

Ao definir a referência, o setup tenta ler a entrada. Quando já há token, permite mantê-lo, substituí-lo ou corrigir o caminho. Quando a entrada não existe, permite corrigir o caminho, criar a entrada com token informado por prompt oculto e enviado ao KeePass somente via stdin, ou salvar a referência para preenchimento posterior. A validação também informa a quantidade de owners pareados.

A validação mostra cada dependência separadamente: instalação do daemon, TOML do gateway, perfil KeePass, referência do token, owners pareados, workspace do Shikigami, Codex-CLI, teste de conexão e execução controlada do agente. O gateway só pode ser habilitado após os testes Telegram e Codex; falhas recentes do gateway também são exibidas sem incluir segredos.

Quando TOTP é configurado e habilitado, o setup usa o mesmo perfil KeePass do bot para conferir as entradas das senhas real e falsa. Para cada uma, permite reutilizar uma senha existente, informar outra e substituí-la, ou criar a entrada; o gateway só inicia se ambas continuarem existentes e com senha utilizável. Os comandos privados de cada owner incluem `/new`, `/config` e `/totp`. O fluxo TOTP pede uma senha real ou falsa mantida no KeePass, lista entradas paginadas, devolve o código em mensagem copiável e agenda a exclusão das mensagens auxiliares.

`/config` abre um menu efêmero e privado por conversa para controlar `Compartilha Pensamentos` e `Excluir Pensamentos`; ambas começam habilitadas e são persistidas no SQLite do gateway. Durante um turno App Server, comentários e resumos disponibilizados pelo Codex são enviados como mensagens protegidas iniciadas por `💭`, com deduplicação por item e execução. Isso não promete acesso ao raciocínio interno completo. Quando a exclusão está habilitada, a limpeza pertence ao turno concluído e aguarda a confirmação da resposta final ou do aviso terminal. Nova mensagem, fila ou steering não apaga essas atualizações; entrega incerta preserva os pensamentos até resolução. Comandos desconhecidos, TOTP desabilitado, sessões expiradas e configurações TOTP incompletas recebem uma resposta descritiva em vez de descarte silencioso.

O gateway aceita inicialmente uma identidade Telegram. Pairing, owners e conversas ficam isolados na instância; mensagens de remetentes que não sejam owners em DM são ignoradas.

## Anexos Telegram

Fotos, documentos e mensagens de voz são baixados de forma limitada e autenticada, com máximo de 20 MiB por arquivo, 50 MiB por mensagem e 250 MiB retidos por conversa, valores ajustáveis em `configs/daemon/services/telegram/config.toml`. A entrada é registrada e deduplicada no SQLite antes de avançar o checkpoint do polling. A ordem é preservada por conversa; com política `steer` e capacidade certificada, novas entradas elegíveis podem direcionar o turno ativo. Reiniciar o Gateway não recoloca cegamente solicitações em execução na fila: operações que podem ter sido aceitas ficam incertas até reconciliação ou decisão.

Os arquivos retidos ficam em `<workspace>/.onmyoji/telegram/attachments/<identificador-local>/<geração>/`. O App Server recebe fotos como `localImage`; documentos e vozes recebem um caminho temporário. Quando o perfil MCP EccoVox autoriza essa área de staging, mensagens de voz também recebem transcrição local. O agente pode listar os metadados da conversa e materializar um anexo de forma explícita pela ferramenta dinâmica `telegram_gateway`, sempre restrita ao owner, à conversa e à geração atuais. O staging é liberado ao fechar o turno e seus consumidores. `/new` isola a geração seguinte imediatamente, invalida trabalho antigo e adia a remoção de anexos ainda usados até confirmar encerramento ou perda do processo.

O Gateway exige App Server, inclusive para mensagens somente textuais; não há execução alternativa por `codex exec`. CLI nativo e Console são entradas locais independentes e usam o mesmo contrato central de configuração.

## Respostas em áudio e mídia de saída

O perfil MCP EccoVox define a voz padrão usada pelas ferramentas MCP de síntese. O menu `Gateway Telegram → Configurar respostas em áudio` seleciona esse perfil, permite uma voz específica para o Telegram (vazia para herdar a voz do perfil MCP), configura o desligamento automático por inatividade e, separadamente, a permissão de mídia enviada pelo agente. O modo texto/áudio é persistido por conversa e volta para texto após o tempo configurado sem nova mensagem do owner. O menu `/config` alterna esse modo; o agente também pode consultar e alterá-lo pela ferramenta dinâmica `telegram_gateway.set_reply_mode` na conversa ativa.

Quando o modo é áudio, o gateway inclui uma orientação transitória para resposta falável no turno e sintetiza a resposta final localmente pelo EccoVox. A falha de síntese é informada e, por padrão, entrega a resposta em texto. O áudio e qualquer arquivo de saída vivem somente no staging/outbox do turno, dentro do workspace, e são apagados ao fim.

Se `agent_outbound_media` estiver habilitado, o agente pode solicitar `get_outbox` e `send_file`; o gateway aceita exclusivamente arquivos regulares do outbox daquele turno, limita tamanho e envia somente à DM ativa. O modelo nunca informa chat, owner ou um caminho arbitrário ao gateway.

## Execução assíncrona e recuperação do Gateway

`[agent].busy_input_policy = "queue"` mantém o padrão compatível. A opção `"steer"`
permite novas entradas ao turno ativo somente quando o runtime, o tipo de input e
a origem forem elegíveis. `/fila <texto>` pede processamento posterior;
`/direcionar <texto>` pede direcionamento, com fallback informado para fila.
`/cancel` solicita interrupção do turno e preserva a fila. `/resolver` consulta e
tenta reconciliar incertezas; `/resolver descartar` libera solicitações incertas
localmente, sem reenviar nem desfazer efeitos externos. Esses controles são do
owner na conversa privada.

Processo, thread e turno têm ciclos diferentes: um item de resposta final não
encerra o turno, `turn/completed` não encerra necessariamente o processo, e
`thread/resume` não inicia geração. Nova mensagem com processo parado exige
handshake, retomada da thread e `turn/start`. `/new` abre nova geração, mas seu
primeiro despacho aguarda a execução antiga deixar de estar não terminal.

Antes da geração, o Gateway salva uma identidade nativa do processo em estado
privado. Após crash, confere PID, criação, executável e instância antes de
encerrar um órfão conhecido ou abrir outro App Server. PID reutilizado é
preservado; identidade inacessível mantém a abertura bloqueada para não deixar
duas execuções sem coordenação. O mecanismo não reconecta stdio perdido.

`[agent].post_turn_results` começa `false`. Quando habilitada e certificada, a
continuidade pós-turno acompanha somente filhos ligados ao principal pelo
runtime e entrega resultados como dados para uma continuação coordenada. O
runtime validado permite filho terminar depois do turno enquanto o processo
vive, mas perde a execução ativa do filho ao encerrar o App Server. Não há
promessa de worker durável ou retomada automática depois dessa perda.

Uma outbox local registra tentativas e confirmações; não cria garantia de
entrega única em Telegram ou serviços externos. Timeout após possível envio
permanece incerto e não causa reenvio automático. Consulte os
[contratos detalhados e controles](../onmyoji-daemon/docs/Telegram.md) e o
[relatório de compatibilidade](../onmyoji-daemon/docs/validation/delegacao-app-server.md).

A migração eleva a configuração do Gateway para esquema 2 antes de gravar o
banco novo, preservando backup privado do TOML e snapshot SQLite anterior quando
há dados. O binário antigo recusa a configuração nova. A volta de versão deve
ser feita por modo fila/recuperação ou por restauração consistente com o serviço
parado, após reconciliar efeitos externos; restaurar banco não desfaz envios.

O banco integrado está no esquema 3, preservando localização estruturada na
fila durável e no redirecionamento. Isso não muda o esquema 2 da configuração.
O [registro de integração](../onmyoji-daemon/docs/implementation/integracao-delegacao-gateway.md)
distingue a validação do projeto da aplicação posterior à instância.
