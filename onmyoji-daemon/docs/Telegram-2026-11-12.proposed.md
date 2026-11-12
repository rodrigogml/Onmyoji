# Gateway Telegram

O Gateway Telegram recebe mensagens e anexos, inicia o Codex App Server quando
necessário e encaminha a conversa ao agente com o mesmo contexto de execução
configurado para a instância.

## Localização fixa

O Gateway recebe pontos fixos nativos do Telegram mesmo sem texto e entrega
ao agente latitude, longitude e, quando informada, precisão horizontal como
raio de incerteza em metros. Um venue é normalizado como ponto fixo único,
preservando título/endereço disponíveis como dados do remetente. Isso não
comprova GPS ou presença atual nem autoriza retransmitir o ponto.

Na DM, os dados seguem em `telegram_turn.location`; em grupos, no campo
`location` da mensagem no histórico estruturado. Coordenadas permanecem no
conteúdo de entrada, fora das developer instructions. Valem os mesmos owners,
disparos por menção/reply, tópicos, janela de contexto e isolamento da conversa.

Quando o pedido autorizado exigir devolver um ponto, o agente usa
`telegram_gateway.send_location`, sem arquivo/outbox, com mídia de saída
habilitada. A ferramenta envia um cartão nativo somente à conversa ativa.
Threads antigas preservam seu histórico e podem usar a capacidade temporária
de resposta obtida por `get_channel_state`, através de `telegram.send_location`.
Envios a outros destinos continuam sujeitos ao catálogo e à autorização externa.

Localização ao vivo, tracking e geocodificação ficam fora de escopo. Uma edição
recebida entra como nova revisão identificada da mensagem; não altera uma
entrada já aceita nem desfaz efeitos anteriores.
Um compartilhamento live recebe aviso de tipo não suportado nos contextos de
atendimento autorizados, sem acompanhamento. Veja os contratos, exemplos e
registro de desenvolvimento em [Localização Telegram](telegram-location.md).

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

## Pensamentos

O menu `Gateway Telegram` permite definir o padrão de pensamentos aplicado a
novas conversas e a conversas reiniciadas com `/new`: compartilhar ou não os
pensamentos do agente durante o turno e, quando compartilhados, excluí-los ou
mantê-los após a resposta. A configuração individual em `/config` continua
restrita à conversa atual e não é alterada ao modificar o padrão global.

## Configuração na conversa

`/config` confirma o clique antes de processar a alteração; a atualização do
controle não bloqueia o recebimento de novas mensagens do Telegram. A seção
**Execução** preserva o limite de modelos e esforços definido no setup e, ao
abrir a lista de modelos, consulta o catálogo atual da conta pelo App Server
do Codex. Assim, opções removidas da conta não são oferecidas à conversa.

Enquanto essa consulta ocorre, o controle mostra que está atualizando o
catálogo. Se a API do Telegram, o App Server ou a configuração não puderem
concluir uma ação, o Gateway fecha aquele controle e envia o motivo
operacional sanitizado, junto com a orientação para abrir `/config` novamente.
O diagnóstico também registra as etapas de confirmação, renderização e falha.

## Consumo e franquia do Codex

O owner pode enviar `/usage` na conversa privada, inclusive em um tópico privado,
para consultar a franquia da conta usada pelo Codex da instância. O comando
permanece disponível durante um turno em andamento e não cria uma thread nem
inicia geração. A resposta é protegida contra encaminhamento pelo Telegram.

O Gateway consulta a autenticação com `account/read` e os limites com
`account/rateLimits/read`, usando o App Server da instância. Exibe percentuais
consumidos e disponíveis, duração e próxima renovação das janelas retornadas,
além de plano, créditos adicionais e renovações extras quando esses dados forem
informados. O horário de renovação usa o fuso local do host do daemon e inclui
o deslocamento UTC. Limites de modelos diferentes permanecem separados.

Campos ausentes são apresentados como não informados. O comando não estima
quantidades de tokens, valores monetários ou acesso liberado a partir do
percentual restante, nem consome créditos de renovação. Limites de gastos ou de
uso indicados pelo servidor continuam visíveis mesmo com percentual restante.

App Server desabilitado, falta de login, autenticação por chave de API ou
Amazon Bedrock e versão do Codex sem suporte recebem orientações próprias.
Falhas de comunicação não expõem respostas brutas, e-mails ou credenciais.
Ao atualizar o Onmyōji, reinicie o Gateway para carregar o novo código e
sincronizar o catálogo de comandos dos owners. Se `/usage` ainda receber
literalmente `Comando inválido` na DM do owner, confira a atualização e o
processo que está atendendo o bot.

Contrato de referência: [Codex App Server — Rate limits](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt).

## Login remoto do Codex

O owner pode enviar `/login` na DM privada (inclusive tópicos privados) para iniciar
autenticação ChatGPT por código de dispositivo no Codex App Server da instância.
O Gateway transmite somente URL HTTPS validada e código temporário, com proteção
de encaminhamento, sem acesso a tokens, senhas ou arquivos de credenciais.
A confirmação é recebida de `account/login/completed` e a sessão é cancelável
por `/login cancelar`. `/login status` informa o modo de autenticação sem
mostrar e-mails, tokens ou credenciais.

A troca da conta não é iniciada enquanto houver execuções ativas ou operações
incertas e suspende novos despachos até seu término. Para essa função, o App
Server deve estar habilitado e a autenticação por código de dispositivo
permitida pela conta/workspace ChatGPT.

## Matriz de acesso aos comandos do Telegram

Esta matriz descreve as permissões **efetivamente verificadas pelo Gateway**,
independentemente dos comandos exibidos no menu do bot. **Owner** é o usuário
pareado e cadastrado com esse papel; **Outros** são os demais usuários do
Telegram. `DM` significa conversa privada com o bot.

| Comando | Owner | Outros |
| --- | --- | --- |
| `/new` | DM e grupos: reiniciar a conversa correspondente. | Somente DM: reiniciar a própria conversa. |
| `/config` | DM: abrir preferências; grupos: ajustar a janela de contexto com argumentos numéricos. | Não permitido. |
| `/channel` | DM e grupos: cadastrar, identificar e gerenciar canais. | Não permitido, inclusive para solicitar cadastro. |
| `/usage` | Somente DM (inclusive tópicos privados): consultar os limites e o consumo da conta Codex. | Não permitido; nenhuma informação da franquia é consultada ou enviada. |
| `/login` | Somente DM (inclusive tópicos privados): iniciar, consultar ou cancelar autenticação ChatGPT do Codex. | Não permitido. |
| `/fila <texto>` | Somente DM: enfileirar uma entrada para o agente. | Não permitido. |
| `/direcionar <texto>` | Somente DM: solicitar direcionamento, se o runtime permitir. | Não permitido. |
| `/cancel` | Somente DM: cancelar o turno atual, preservando a fila. | Não permitido. |
| `/resolver` | DM e grupos: reconciliar ou descartar operações incertas da conversa correspondente. | Somente DM: reconciliar ou descartar operações incertas da própria conversa. |
| `/totp` | Somente DM, quando TOTP estiver habilitado. | Não permitido. |
| `/pair <PIN>` | Somente DM, com PIN temporário válido. | Somente DM, com PIN temporário válido: realiza o pareamento e concede o papel de owner. |

A relação acima inclui `/pair`, embora ele não seja divulgado no menu permanente.
O PIN é gerado pelo setup, expira e só pode ser consumido uma vez; a sua
validação e consumo são atômicos para impedir pareamentos simultâneos com o
mesmo código. Outros usuários recebem apenas `/new` e `/resolver` no menu
privado; o menu do owner prevalece para usuários pareados. A exibição no
Telegram não substitui a autorização no servidor.

O Gateway restringe `/new` e `/resolver` de outros usuários à própria DM,
sem permitir alterar o estado do owner ou de terceiros. O argumento
`/resolver descartar` também é isolado por conversa. `/channel` não pode
ser executado por outros usuários: menus antigos de solicitação não podem
ser confirmados por callbacks. Em grupos, mensagens de terceiros podem
compor o contexto não confiável, mas não autorizam comandos de controle.
O Gateway rejeita `/usage` fora da DM do owner antes de consultar
`account/rateLimits/read`.

A futura operação de consumo de renovação, `/resetusage`, deve seguir a mesma
permissão de `/usage` e exigir confirmação explícita; ela ainda não é um
comando implementado nem faz parte do catálogo atual. A orientação para
`/resetusage` só deve aparecer na resposta de `/usage` depois que o comando
estiver disponível.

## Menu de comandos do bot

O catálogo central em `telegram_commands.py` publica o conjunto completo de
comandos no chat privado de cada owner:

| Comando | Função |
| --- | --- |
| `/new` | Iniciar uma nova conversa. |
| `/config` | Configurar a conversa atual. |
| `/channel` | Gerenciar canal de envio. |
| `/usage` | Consultar consumo e franquia do Codex. |
| `/login` | Autenticar ou trocar conta Codex. |
| `/fila <texto>` | Enfileirar uma mensagem. |
| `/direcionar <texto>` | Orientar o turno em andamento. |
| `/cancel` | Interromper o turno atual, preservando a fila. |
| `/resolver` | Consultar e reconciliar solicitações sem confirmação. |
| `/totp` | Obter código de autenticação, quando TOTP estiver habilitado. |

`/pair <PIN>` pertence ao pareamento temporário e não entra no menu permanente.
Variações como `/resolver descartar` são argumentos do comando correspondente.
O teste do catálogo compara os handlers com essa relação para detectar novos
comandos permanentes que ainda não tenham sido incluídos no menu.

Para **outros usuários**, o menu na DM contém somente `/new` e `/resolver`.
O Gateway publica esse catálogo pelo escopo `all_private_chats`, sem divulgar
comandos administrativos no escopo global do Telegram. O catálogo individual
do owner tem prioridade sobre o escopo geral de DMs.

A sincronização configura o escopo do chat do owner sem idioma e também os
idiomas conhecidos daquele owner, normalizados para duas letras. Isso evita
que uma lista antiga por idioma prevaleça sobre o catálogo geral do chat.
O Gateway preserva os idiomas já observados; quando recebe um novo idioma,
solicita nova sincronização. A API não permite enumerar catálogos de idiomas
desconhecidos, portanto o owner precisa interagir com o bot após trocar de
idioma para que essa variante seja observada. O botão do menu também é
confirmado como menu de comandos. A reconciliação do catálogo dos outros
usuários verifica também a leitura de volta de `all_private_chats`. Nenhuma
dessas operações altera o menu global ou o dos grupos do bot.

A confirmação compara nomes, descrições e ordem dos catálogos retornados
pelo Telegram. O recibo local inclui a revisão conjunta dos catálogos, a
configuração TOTP, owners confirmados, o resultado para outras DMs e pendências. O setup e a CLI só informam sucesso quando
esse recibo corresponde ao catálogo da versão local; um recibo antigo ou uma
tentativa ainda em andamento permanece pendente.

Um worker verifica o menu ao iniciar o Gateway, após reconectar o polling,
ao parear um owner, ao observar novo idioma e a cada cinco minutos. Falhas
têm nova tentativa após um minuto, respeitando um prazo maior informado pelo
Telegram em `retry_after`. Cada rodada tem orçamento de 30 segundos e não
ocupa o recebimento normal de updates. Rodadas parciais retomam os owners
pendentes e o catálogo das demais DMs, preservando confirmações da mesma revisão
até concluir o ciclo. Owners lentos não impedem a tentativa dos demais. O ciclo seguinte
verifica novamente o catálogo de todos os owners. Um ciclo parcial também
expira após cinco minutos, para uma falha persistente não impedir a nova
verificação de quem já havia sido confirmado.

Para solicitar uma verificação imediata, abra **Daemon → Telegram → Owners e
comandos → Atualizar comandos privados**. O diagnóstico lê o recibo em
`state/daemon/services/telegram/gateway-status.json`.

Depois de atualizar os arquivos do Onmyōji, reinicie o daemon completo para
carregar o Gateway e o supervisor novos. Em **Daemon → Processo/Serviço do
Daemon**, use **Processo local → Finalizar/Iniciar** ou **Serviço do sistema
operacional → Reiniciar serviço**, conforme a instalação. A retomada do
processo local pelo atualizador não substitui o reinício de um serviço do SO.

Referência: [Telegram Bot API — comandos e escopos](https://core.telegram.org/bots/api#determining-list-of-commands).

## Paralelismo de respostas

O menu `Gateway Telegram` permite definir de 1 a 16 conversas processadas em
paralelo. O limite distribui capacidade entre conversas distintas; cada conversa
conserva um único turno coordenador não terminal, sua ordem de entradas e seu
contexto. Entradas elegíveis podem direcionar esse mesmo turno conforme abaixo.
Reinicie o Gateway depois de alterar esse valor para aplicar o novo limite.

## Mensagens durante trabalho em andamento

O Gateway recebe e registra mensagens mesmo enquanto há um turno em execução.
Downloads e preparação de voz/anexos usam capacidade limitada, preservando a
ordem por conversa; callbacks e controles têm caminho próprio. A fila durável
é deduplicada pela origem do update antes de avançar o checkpoint do polling.

Em `configs/daemon/services/telegram/config.toml`:

```toml
[agent]
busy_input_policy = "queue"
post_turn_results = false
interrupt_grace_seconds = 15

[limits]
preparation_workers = 4
```

`queue` é o padrão compatível. `steer` permite acrescentar entradas ao turno
ativo quando a capacidade for certificada para o executável efetivo, o input
for suportado e a mensagem vier do owner na DM privada elegível. Grupos e
tópicos continuam com fila nesta evolução. Mudança de modelo/esforço e uma
entrada explicitamente enfileirada estabelecem barreira de próximo turno.
Steering aceito não significa que uma ferramenta já enviada foi interrompida.

| Controle privado do owner | Efeito |
| --- | --- |
| `/fila <texto>` | Preserva a entrada para depois do turno atual, inclusive com política steer |
| `/direcionar <texto>` | Solicita steering; sem suporte ou elegibilidade, fica na fila com indicação da limitação |
| `/cancel` | Solicita interrupção do turno ativo; preserva thread e fila |
| `/resolver` | Consulta e tenta reconciliar operações sem confirmação pela história persistida |
| `/resolver descartar` | Descarta localmente solicitações incertas para liberar a conversa; não repete nem desfaz ações ou entregas externas |
| `/new` | Abre nova geração, invalida trabalho antigo e descarta o contexto para os próximos turnos |

`/fila` e `/direcionar` exigem texto, que pode ser legenda de uma mensagem com
anexos. Os comandos não alteram a política global. Conteúdo, anexos e transcrição
continuam inputs com procedência, sem alterar instruções privilegiadas. Se o
turno terminar durante um steering, somente recusa conclusiva permite levar a
mesma entrada ao próximo turno. Timeout de ACK não é prova de recusa.

## Respostas, pensamentos e conclusão

Comentários e resumos disponibilizados pelo Codex são os “pensamentos” exibidos
pelo canal; não há promessa de acesso ao raciocínio interno completo. Entrar
uma mensagem nova, enfileirar ou direcionar não apaga atualizações do turno.
Desligar compartilhamento impede novas publicações sem apagar retroativamente
as existentes. A preferência de exclusão é aplicada ao fechar aquela execução.

Uma resposta final é um item; a conclusão da execução é `turn/completed`. A
limpeza espera o fechamento do turno e a confirmação da saída final ou aviso
terminal. Se uma entrega estiver incerta, as atualizações permanecem até a
resolução. IDs e limpeza são limitados à conversa, geração, thread e turno
corretos; não usam a geração corrente depois de uma chamada de rede tardia.

O principal pode usar comentários e `telegram_gateway.send_message` para
atualizações ou respostas intermediárias. Ele coordena dependências, acompanha
subagentes e consolida resultados. A conclusão de um filho não é uma nova
autorização do usuário e não encaminha sua fala diretamente ao Telegram.

## Perda de processo e entregas incertas

O App Server é persistente enquanto necessário; thread de histórico e turno de
execução são objetos distintos. Se o processo estiver vivo e o turno anterior
tiver terminado, a próxima entrada inicia outro turno na mesma thread. Se o
processo tiver parado, o Gateway inicializa a conexão, retoma a thread e só
então inicia o turno. Retomar histórico sozinho não gera resposta.

O checkpoint privado também registra PID, criação nativa, executável e epoch
do App Server antes de iniciar geração. Após crash do Gateway, uma nova abertura
reconcilia esse registro: encerra somente o órfão cuja identidade e instância
coincidam, preserva PID reutilizado e bloqueia se a identidade estiver incerta.
Isso não reconecta stdio nem reinicia uma operação de modelo sem confirmação.

O estado durável registra operações de início/steering, entregas e referências
de recursos. A saída terminal salva passa por `pending`, confirmação `sent` e
`done` após entrega e limpeza concluídas; uma execução encerrada não precisa
iniciar geração novamente para recuperar somente sua entrega. Após crash, uma operação possivelmente aceita não volta cegamente
à fila; fica incerta até reconciliação ou decisão. `/resolver descartar` não
afirma que nada ocorreu: confira os efeitos externos antes de pedir novamente.

> [!IMPORTANT]
> A outbox local não oferece exatamente uma entrega em Telegram ou em MCPs.
> Envio confirmado pode ser reaproveitado pelo recibo; timeout depois de possível
> envio permanece incerto e não provoca reenvio automático.

`/new` incrementa a geração e impede resultados antigos de entrar no contexto
novo. O primeiro despacho da geração seguinte aguarda terminalidade da anterior;
anexos ainda consumidos são liberados somente depois. Uma chamada HTTP já em
voo pode terminar após o reset e continua atribuída à geração antiga.

`/cancel` preserva as entradas ainda na fila e impede continuações de resultados
que aguardavam entrega, mesmo quando o filho já terminou. Se ocorrer durante
síntese de voz ou antes da admissão do envio final, o Gateway entrega o aviso
de cancelamento. Um envio externo já iniciado pode exigir conferência.

`post_turn_results = true` é opcional e ainda depende dos gates G3, G5 e G7.
Quando habilitado, resultados tardios de filhos conhecidos são acompanhados
com prazo e enviados como dados para continuação do principal na mesma geração.
O certificado atual reprova G6: filhos ativos não sobrevivem à perda do
processo; a próxima interação pode reconciliar o estado interrompido, sem replay
automático. Um resultado já consultado e salvo no Gateway é dado durável: pode
ser apresentado por uma continuação após retomar a thread, sem afirmar que a
execução do filho sobreviveu. Atualizar MCPs/skills/perfis e fechar por ociosidade
só deve ocorrer em ponto seguro, respeitando o trabalho realmente acompanhado.

Consulte o [relatório de capacidades](validation/delegacao-app-server.md) e o
[inventário de implementação e aceite](implementation/delegacao-gateway-subagentes.md)
para distinguir código disponível, prova do runtime e pendências de verificação.

## Continuidade de contexto

Uma conversa mantém a thread persistida do Codex através de reinicializações
do App Server, atualizações de skills, instruções e MCPs. Essas alterações
podem reiniciar o processo para recarregar recursos, mas não iniciam uma
conversa nova. O Gateway atualiza a revisão das instruções após retomar a
thread e só descarta sua referência quando o App Server confirma que ela não
existe mais. Falhas transitórias de retomada ficam registradas para diagnóstico
e são tentadas novamente no próximo turno.

O Gateway também limita o histórico serializado de cada turno por mensagens,
idade e bytes UTF-8. Por padrão, a janela ocupa no máximo 512 KiB, preservando
as mensagens mais recentes, antes do limite de frame de 4 MiB do App Server.
Após 16 novas mensagens, ele agenda a compactação nativa da thread Codex; isso
reduz o contexto acumulado sem abrir outra conversa. Os dois valores podem ser
ajustados em `[limits]` por `context_max_bytes` e
`context_compaction_message_interval` (`0` desabilita a compactação preventiva).
Uma compactação recusada não repete nem invalida o turno já concluído.

No Linux, o checkpoint também aceita o lançador JavaScript do Codex sob Node. Antes de registrar ou recuperar, verifica o script e seus argumentos, além da identidade nativa, criação e hash do processo. Se o wrapper ainda estiver vivo após uma interrupção, a recuperação bloqueia outra abertura e requer intervenção do operador: encerrar apenas o Node não comprova o término de eventual filho nativo. Se a identidade estiver inacessível ou alterada, o encerramento automático também é bloqueado. O diagnóstico registra apenas a etapa e o código operacional, sem conteúdo de mensagens ou erro bruto.

`/new` é a forma explícita de descartar o contexto. Cada tópico direto do
Telegram também é uma conversa independente, embora pertença ao mesmo DM.

Se a conexão autenticada com o Telegram falhar durante a inicialização, o
supervisor marca o Gateway como indisponível e tenta iniciá-lo novamente em
segundo plano. Uma inicialização que não ficou pronta é encerrada antes da nova
tentativa, para não deixar processos sem endpoint de controle.

## Migração e rollback local

Na primeira abertura desta evolução, a configuração do Gateway passa de
`schema_version = 1` para `2` antes de introduzir o banco novo. O original é
preservado em `config-before-delegation-v1.toml`, ao lado da configuração. Quando
o banco anterior contém dados, um snapshot consistente
`gateway-before-delegation-v<versão>.sqlite3` é preservado junto ao banco em
`state/`. Esses arquivos são estado privado da instância, sem credenciais novas
nem versionamento. A publicação transitória da configuração usa somente `tmp/`
da instância e conserva os demais bytes, comentários e encoding.

A abertura reserva exclusividade antes de migrar e recusa um banco de versão
futura antes de alterar configuração ou tabelas. No desligamento, essa posse
permanece até a saída do processo: callbacks, preparadores e HTTP antigos ainda
podem terminar. Uma nova instância deve aguardar essa saída. Entradas reservadas
que comprovadamente não chegaram ao transporte permanecem na fila para a retomada.

O banco integrado usa esquema 3 para preservar também localização estruturada
na fila, inclusive após reinício e no redirecionamento ao turno ativo. A
configuração continua no esquema 2. A migração de um banco 2 com entradas
preserva seu snapshot antes de acrescentar essa coluna; um leitor anterior
deve recusar o esquema 3. Localizações de entradas encerradas seguem o expurgo
de payloads após sete dias, sem eliminar registros incertos necessários à
reconciliação.

A versão nova aceita configurações 1/2 durante a transição; a versão antiga
recusa esquema 2 em vez de transformar operações incertas em replay. Prefira
rollback funcional desabilitando steering e pós-turno na versão capaz de ler o
banco novo. Downgrade exige parar admissão, reconciliar operações e restaurar
configuração e banco consistentes, sob controle do operador. Restaurar snapshot
não desfaz ações externas e pode reapresentar updates antigos: não reative
consumo antes dessa reconciliação.

## Figurinhas do agente

O Gateway pode disponibilizar figurinhas pré-cadastradas ao agente na conversa
ativa. Os arquivos e o catálogo versionável ficam em
`configs/daemon/services/telegram/stickers/`. O catálogo associa um ID estável,
o nome local do arquivo e o contexto que explica quando a figurinha representa
uma resposta adequada.

O agente consulta somente IDs e contextos e envia uma opção cadastrada pelo
ID; não recebe acesso a caminhos locais, arquivos arbitrários ou `file_id` do
Telegram. Arquivos sem contexto, links, caminhos externos, arquivos ausentes
ou formatos não suportados não são publicados. O Gateway aceita `.webp`,
`.tgs` e `.webm`, envia a mídia por `sendSticker` e mantém o mesmo controle de
autorização aplicado à mídia de saída.

Por padrão, a figurinha complementa a resposta textual. Quando ela for a
resposta completa, o agente informa `delivery: "only"` em `send_sticker`; o
Gateway suprime o texto final somente depois de confirmar o envio da
figurinha. Isso evita que uma falha ou ausência acidental de resposta seja
interpretada como uma resposta somente visual.

O menu também pode converter imagens estáticas PNG, JPG, JPEG ou WebP para uma
nova saída WebP. A conversão preserva o original, centraliza a imagem em uma
tela transparente de 512×512 pixels e procura atingir 256 KiB sem substituir
um arquivo existente. A saída ainda exige cadastro e contexto antes de ser
oferecida ao agente. Arquivos `.tgs` e `.webm` não são convertidos pelo menu,
pois animações vetoriais e vídeo têm requisitos próprios.

O cadastro e a edição selecionam uma figurinha por vez. O setup valida o ID e
o contexto, de até 240 caracteres, antes de gravar imediatamente o catálogo;
uma entrada inválida não afeta os demais arquivos pendentes.

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
