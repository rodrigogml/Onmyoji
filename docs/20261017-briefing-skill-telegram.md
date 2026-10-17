# Briefing — Skill de integração Telegram

**Data:** 2026-10-17  
**Status:** Briefing funcional consolidado — P1 a P8 resolvidas; arquitetura e implementação ainda não iniciadas.  
**Repositório responsável:** Onmyoji (upstream).  
**Escopo deste documento:** requisitos e regras de comportamento; não é ainda um plano de arquitetura nem autorização para iniciar a implementação.

## 1. Objetivo

Criar a skill de integração **telegram**, reutilizando o **Gateway Telegram existente** para fornecer aos agentes do Codex um canal de **comunicação de saída (somente envio)**, independentemente de onde o agente está executando (Codex UI, CLI externo, conversa Telegram ou futura execução disparada por evento).

A skill permite **listar/pesquisar canais explicitamente registrados**, consultar capacidades, **remover registros pelo owner** e **enviar texto e mídia/anexos** a esses destinos. Quando autorizado pelo Telegram, pode criar ou reutilizar um tópico para realizar a entrega. Ela **não recebe nem aguarda respostas, não disponibiliza menus interativos de conversa, não transporta threads Codex e não inicia processamento**.

Operações de envio retornam apenas **confirmação/metadados da entrega ou erro**, nunca o conteúdo de eventual resposta humana. Mensagem textual pode conter uma pergunta, mas o ato de enviar essa pergunta não cria uma interação bloqueante pela skill.

O **gateway** mantém, independentemente da skill, seus mecanismos nativos de recepção, resposta, menus e threads Codex. Se houver conversa Telegram vigente, o envio da skill é registrado no contexto **de destino**, sem alterar a thread de origem e sem iniciar um turno Codex. Quando alguém posteriormente falar no Telegram, o gateway decide se processa a mensagem segundo sua política usual.

**Execuções por eventos ficam fora do escopo:** futuramente, um evento poderá disparar script e/ou Codex CLI **pelo próprio gateway**. Nesse caso, a execução poderá ter thread associada nativamente ao gateway e utilizar os mecanismos **do próprio contexto de gateway** para interação; isso não torna a skill duplex e não exige compartilhar threads de UI/CLI externos.

## 2. Vocabulário e princípios acordados

- **Owner:** o mestre e administrador da instância; empregar sempre o termo owner.
- **Origem:** interface/canal de onde partiu a solicitação à execução atual (por exemplo, UI Codex ou tópico Telegram).
- **Canal:** unidade de comunicação Telegram que pode ser cadastrada: DM, grupo sem tópicos, grupo com tópicos, tópico de grupo ou tópico de DM. A granularidade de cadastro deverá ser explícita.
- **Catálogo:** lista de canais **registrados deliberadamente** para comunicação pela skill; não é a lista de todos os chats vistos pelo bot.
- **Destino:** canal selecionado para determinada entrega.
- **Thread Codex:** sessão/contexto do agente, que pode existir antes de qualquer contato via Telegram.
- **Associação:** vínculo administrado **pelo gateway** entre conversa Telegram e thread Codex, independente das operações de envio da skill.
- **Audiência:** quem pode receber o material enviado e, separadamente, quem pode interagir com o gateway; pessoas distintas e grupos não herdam o contexto privado do owner.

Princípios:
1. O **owner tem autoridade para usar a skill e enviar conteúdo a qualquer canal registrado**, sujeito apenas às limitações técnicas e permissões efetivas do Telegram; não se propõe restringir seus destinos com base no conteúdo do documento.
2. **Usuários não-owner não podem usar o agente para enviar documentos nem para provocar envio/início de comunicação em outros canais.** A interação deles fica limitada à conversa já vigente e às regras de atendimento do gateway; a política mais detalhada de usuários autorizados será definida em outra etapa.
3. Não ocorre descoberta indiscriminada de pessoas, grupos ou tópicos pelo bot.
4. **Receber mensagens de um canal não o cadastra automaticamente**. Registrar depende de ação explícita via Telegram.
5. A skill pode **listar e remover** canais registrados, sob autoridade do owner, mas **não pode registrar um canal novo**. A remoção solicitada ao agente deve ser explícita, identificando o canal sem ambiguidades.
6. Enviar a outro destino não altera a associação da conversa de origem.
7. Uma conversa do owner na UI local do Codex é tratada como originada pelo owner. Isso não autoriza compartilhar o histórico de sua thread com terceiros.
8. A skill é **unidirecional**: cadastro e gestão do catálogo são funções administrativas; a recepção e interação de mensagens são funções independentes do gateway.

## 3. Catálogo: cadastro intencional e manutenção

### 3.1. Cadastro exclusivamente pelo Telegram

Prever um comando nativo do gateway, provisoriamente **/channel**, executado no próprio contexto Telegram a registrar. O comando abre menu interativo contendo, pelo menos:
- **Registrar este canal** como destino permitido para **envio pela skill** (o registro não configura permissões de interação no gateway).
- **Remover este canal** do catálogo.
- **Consultar estado** do cadastro (recomendado).

O menu deve identificar claramente o canal que será registrado ou removido e exigir uma ação intencional. Não se deve registrar automaticamente canais devido a mensagens, menções, presença do bot no grupo, atualizações de tópicos ou descoberta passiva.

O canal cadastrado fica elegível para ser exibido nas listagens da skill. A capacidade de enviar ou criar tópico precisa respeitar as condições reais do Telegram (permissões, bot bloqueado, modo de tópicos etc.).

**Decisão P1 — Aprovada em 2026-10-17: cadastro de DMs de terceiros mediante solicitação e aprovação no Telegram.** O owner não precisa acessar a DM privada entre o terceiro e o bot. O fluxo acordado é:

1. Um usuário terceiro (ex.: Maria) abre sua própria DM com o bot e executa `/channel`.
2. O gateway apresenta um menu com **Solicitar cadastro deste canal**.
3. Ao selecionar essa ação, o gateway encaminha ao **owner, pela DM dele com o bot**, uma solicitação identificando o usuário e o canal de origem.
4. O owner recebe um menu com **Aprovar** e **Rejeitar**.
5. Somente **após aprovação explícita pelo owner** o gateway registra a DM de Maria no catálogo de destinos utilizáveis pela skill.
6. Solicitação pendente ou rejeitada **não** torna o canal elegível para envios proativos pela skill. O usuário solicitante deve receber retorno compreensível sobre o resultado.

O cadastro é uma ação administrativa realizada pelo Telegram, **nunca pela skill**. Mensagens ou `/channel` isoladamente não registram a DM; o registro depende da aprovação do owner. Este fluxo define o cadastro do canal, **não** concede automaticamente ao terceiro permissão de acionar a skill ou de iniciar comunicações para outros canais. Detalhes de expiração, cancelamento, deduplicação e revogação de solicitações serão especificados na etapa de desenho do comportamento do comando.

### 3.2. Granularidade do canal

Um grupo e seus tópicos, ou uma DM com tópicos e seus tópicos individuais, não devem ser tratados como o mesmo endereço sem regra explícita. O catálogo precisa distinguir:
- Cadastro do chat/base (quando aplicável);
- Cadastro de um tópico específico;
- Tópico criado pelo agente para uma conversa, mas não necessariamente cadastrado como destino autônomo no catálogo.

**Decisão P2 — Aprovada em 2026-10-17: cadastro granular no contexto exato do comando.**

| Contexto onde `/channel` é executado | O que pode ser registrado |
| --- | --- |
| DM tradicional, sem tópicos | Somente a DM inteira |
| DM com tópicos, dentro de um tópico | Somente aquele tópico |
| Grupo sem tópicos | Somente o grupo inteiro |
| Grupo com tópicos, dentro do tópico Geral | Somente o tópico Geral |
| Grupo com tópicos, dentro de um tópico específico | Somente aquele tópico |

Registrar um grupo, DM ou tópico **não** registra automaticamente outros tópicos existentes ou futuros. Cada contexto é uma unidade independente do catálogo.

**Autorização do comando:**
- **Grupos e tópicos (inclusive tópico em DM):** somente o **owner** pode executar `/channel` para gerir o cadastro. Usuários não-owner **não** podem solicitar aprovação para esses contextos. Ao tentarem, o gateway deve responder explicitamente que a operação é exclusiva do **proprietário** (tradução exibida ao usuário para `owner`, mantendo a terminologia interna `owner`).
- **DM tradicional de terceiro, sem tópico:** permanece a exceção aprovada em P1: a pessoa pode solicitar cadastro na própria DM, que só será efetivado após aprovação do owner no Telegram.
- O comando identifica o contexto a partir de onde foi executado; não aceita que o usuário cadastre outro grupo/tópico apontando um ID arbitrário.

Exemplo de resposta de acesso negado nos grupos/tópicos: **"Esta operação é permitida apenas ao proprietário."**

Esse controle se aplica ao menu de consulta, registro e remoção administrado por `/channel` no contexto correspondente. Não confundir a possibilidade de receber mensagens no grupo com autorização para cadastrá-lo.

**Decisão P3 — Aprovada em 2026-10-17: criação de tópico não é cadastro no catálogo.**

- O owner pode solicitar à skill a criação de um tópico em um chat previamente registrado, desde que o bot possua suporte e permissões efetivas.
- O tópico criado pode ser utilizado imediatamente como conversa Telegram de destino, inclusive para receber respostas e continuar a **thread própria desse tópico** (ou criá-la na primeira interação autorizada). Nunca herda a thread Codex da aplicação que solicitou sua criação.
- A criação **não** inclui o tópico automaticamente no catálogo permanente de destinos da skill, nem registra outros tópicos.
- Para que o tópico apareça como destino independente nas listagens e possa ser selecionado em envios futuros não associados à conversa original, o **owner precisa executar `/channel` dentro desse tópico**.
- Enquanto não cadastrado, o tópico pode continuar como conversa operacional já associada, mas **não** deve aparecer como canal registrado na busca/listagem geral.

Esta decisão reforça a distinção entre **criar uma conversa** e **autorizar um canal no catálogo**.

### 3.3. Listagem e remoção pela skill

A skill deve:
- Listar canais cadastrados e pesquisar por nome/alias/tipo.
- Expor identificação inequívoca e capacidades conhecidas (texto, mídia, tópicos, limitações/estado conhecido).
- Diferenciar “registrado” de “entrega garantida”; verificar permissões/resultado efetivo ao enviar.
- Permitir remover um canal cadastrado quando solicitado pelo owner; a operação deve ser inequívoca e retornar confirmação.
- **Não** oferecer operação de inclusão/cadastro pelo agente, nem um caminho alternativo para contornar o fluxo do Telegram.

**Decisão P4 — Aprovada em 2026-10-17: remoção revoga o destino no catálogo, não encerra a conversa.**

Ao remover um canal do catálogo pelo `/channel` ou pela skill, sob autoridade do owner:

| Elemento | Comportamento após remoção |
| --- | --- |
| Catálogo | Remover o registro imediatamente; não apresentar o canal como destino cadastrado nas buscas/listagens. |
| Envio proativo pela skill a partir de outra origem | Não permitir **novos envios direcionados** ao canal removido como destino do catálogo. |
| Chat ou tópico no Telegram | Permanecer intacto; não apagar/fechar o chat ou tópico. |
| Histórico de mensagens | Preservar. |
| Thread Codex já associada | Preservar, sem excluir ou reiniciar. |
| Associação Telegram ↔ Codex | Preservar. |
| Mensagens recebidas nesse mesmo contexto | Continuar seguindo as regras normais de atendimento do gateway, inclusive a continuação da thread associada quando autorizada. |
| Respostas do agente na conversa vigente | Continuar pelo fluxo normal do gateway; a remoção do catálogo não bloqueia respostas contextuais a mensagens recebidas. |

**Distinção:** remover do catálogo equivale a revogar a elegibilidade para **novas comunicações iniciadas pela skill a partir de outras origens**. Não é revogação do atendimento naquele chat nem encerramento da associação existente.

A remoção não deve afetar automaticamente outros tópicos/canais cadastrados individualmente. Se o owner quiser registrar novamente, deverá seguir o fluxo do `/channel`; a skill não pode recadastrar.

## 4. Capacidades esperadas da skill

### 4.1. Consulta e manutenção do catálogo registrado

- Listar apenas os destinos cadastrados explicitamente: DMs do owner e de terceiros, grupos e tópicos, conforme granularidade P2.
- Buscar por nome/alias/tipo e desambiguar nomes coincidentes; consultar capacidades conhecidas do destino (texto, mídia, criação de tópico e estado/permissões).
- Consultar e **remover** registros pelo owner, com indicação inequívoca do destino e confirmação da alteração.
- **Nunca cadastrar um canal novo pela skill**. Cadastro é exclusivamente via `/channel` no Telegram, com as regras P1/P2.
- Identificar o próprio canal quando uma execução estiver no gateway, sem expor IDs técnicos ao modelo.

### 4.2. Operações de saída

- Enviar texto, documentos/arquivos, imagens/fotos e áudio; envio de voz sintetizada é possível se aproveitado como **mídia de saída**, sem expectativa de resposta.
- Mensagem de texto pode ser formulada como pergunta, mas a skill **não aguarda nem recupera respostas**.
- Aproveitar recursos de **envio** já presentes no gateway (proteção, expiração quando aplicável, mídia, limites); **não expor por essa skill `ask_menu` ou outras ferramentas de diálogo**.
- Permitir enviar arquivos de workspace autorizado a partir de UI/CLI externo, sem depender de outbox de um turno ativo do Telegram.
- Enviar no contexto Telegram atual ou para outro destino registrado sem alterar a conversa/thread de origem.
- Criar ou reutilizar tópico Telegram quando solicitado e permitido, apenas para selecionar o local de entrega; sua criação **não o cadastra** no catálogo (P3).
- Retornar somente sucesso/metadados operacionais (`sent`, destino, identificador de mensagem/tópico quando aplicável) ou **erro de envio**, sem payload de respostas futuras.
- Preservar limites de tamanho/formato/acesso aos arquivos e regras de autorização do gateway.

### 4.3. Registro da entrega no contexto de destino

Um envio realizado pela skill deve ser registrado no **histórico da conversa Telegram destinatária** como mensagem externa, para ser apresentado ao próximo turno **que o gateway decidir executar** naquela conversa (P5). O registro não provoca execução, não cria thread por si só, não envia respostas para o cliente de origem e não importa o contexto privado desse cliente.

- **Destino com thread existente:** manter a associação do gateway e, no próximo turno, apresentar a mensagem externa relevante ao modelo.
- **Destino sem thread:** registrar a entrega; eventual thread será criada pelo gateway **sob demanda**, quando uma interação recebida exigir processamento.
- **Destino já atendido pelo gateway:** envio integra o contexto existente, sem criar outra conversa nem reconfigurar a thread.
- **Destino sem tópicos:** segue a única thread vigente por chat (P6).
- **Execução de evento no futuro:** quando o Codex CLI for iniciado **pelo gateway**, sua thread/diálogo e eventual interatividade pertencem ao **fluxo nativo do gateway**, não à skill.

O cadastro de um canal habilita **novos envios iniciados pela skill**. Não concede automaticamente permissões de atendimento de mensagens recebidas; isso continua sendo política do gateway (P8).

## 5. Regras de contexto e cenários aceitos

| Origem | Destino | Comportamento da **skill** / do **gateway** |
| --- | --- | --- |
| Codex UI/CLI externo (owner) | DM registrada do owner sem tópicos | Skill envia e registra no contexto Telegram; gateway continua sua thread própria conforme P6. Nenhum retorno à UI. |
| Codex UI/CLI externo (owner) | DM registrada do owner com tópicos | Skill envia ao tópico escolhido/criado; gateway preserva thread de destino ou cria sob demanda. Não herda thread da UI. |
| Codex UI/CLI externo (owner) | DM registrada de Maria | Skill envia e registra somente o conteúdo compartilhado; não transfere contexto do owner. Eventuais respostas são do gateway, sob sua política. |
| Codex UI/CLI externo (owner) | Grupo ou tópico registrado | Skill envia, sem transportar a thread de origem; gateway aplica suas próprias permissões de recepção. |
| Telegram (gateway, owner) | Mesmo chat/tópico | Skill pode enviar no próprio destino; a conversa/thread vigente do gateway permanece intacta. |
| Telegram (gateway, owner) | Outro chat/tópico registrado | Skill envia e registra no destino, sem afetar a conversa/thread de origem. |
| Telegram (não-owner) | Outro canal | Não pode utilizar a skill para enviar anexos ou iniciar comunicação fora da conversa vigente. |
| Execução futura iniciada **pelo gateway** | Canal associado à própria execução | Gateway gerencia nativamente thread, respostas e interações; a skill, se usada, somente envia. |
| Execução futura externa ao gateway | Canal registrado | Skill somente envia, sem transferir thread; qualquer interação subsequente é responsabilidade do gateway. |
| Qualquer origem | Sem conteúdo relevante | Nenhum envio obrigatório. |

**Princípio:** a existência de canal de retorno no Telegram não faz da skill uma integração duplex. A skill só conhece o resultado da **operação de envio**. O recebimento e o processamento de respostas são do gateway, independentemente dela.

### 5.1. P5 — Decisão final: mensagem inserida no contexto de destino; threads independentes

**Decisão aprovada pelo owner em 2026-10-17.**

**Regra:** a skill `telegram` envia o conteúdo ao canal de destino e o gateway o registra no contexto daquela **conversa Telegram**, sem compartilhar a thread da origem (Codex UI, CLI externo ou outra conversa Telegram).

1. **Destino com thread Codex associada:** preservar sua thread; registrar o envio externo para inclusão no **próximo turno** dessa conversa. Não executar um novo turno só para registrar um envio.
2. **Destino sem thread:** registrar o envio e criar uma thread própria somente quando chegar a primeira interação autorizada que exija processamento.
3. **Origem independente:** a thread da execução que pediu o envio permanece intocada; não há retorno automático de respostas do Telegram à UI/CLI.
4. **Conteúdo e proveniência:** registrar texto, legenda, mensagem Telegram, data, anexos e origem interna como evento `external_outbound`. Apresentar ao modelo apenas material efetivamente enviado e autorizado, **sem importar histórico privado nem tratar o envio externo como instrução do owner**.
5. **Mensagem recebida depois:** o gateway inclui os eventos externos relevantes e ainda não incorporados aos inputs/contexto do próximo turno da thread **do destino**. Evitar duplicação e não transformar uma mensagem do bot em solicitação autônoma de execução.
6. **Envios para o mesmo contexto Telegram em que o gateway já atende:** manter o fluxo e a thread existentes. Se a mensagem vier de outro contexto, ela apenas entra na história do destino.
7. **Texto em forma de pergunta:** é apenas mensagem de saída. A skill não aguarda, recupera ou devolve respostas. Se houver mensagem posterior, seu tratamento pertence exclusivamente ao gateway, sem suspensão da thread de origem.

**Distinção técnica:** “inserir no contexto” significa **persistir no histórico do gateway e apresentar ao Codex no próximo turno**. Não significa injetar diretamente um item no histórico interno do Codex sem executar um turno. O código atual já persiste conversas e associa threads; precisará estender o registro e a apresentação dos envios externos, pois o fluxo de DMs não usa o mesmo snapshot que os grupos.

**Limites:** anexos exigem metadados/referências válidas e política de retenção; chats sem tópicos seguem a decisão P6. Menus, espera de respostas e interação bidirecional **não pertencem à skill** (P7). Quando um evento futuro for processado pelo próprio gateway, poderá utilizar o contexto interativo nativo do gateway, sem ampliar a skill.

### 5.2. Regra de isolamento: Maria e grupos

Quando a audiência não for a mesma da thread privada de origem:
- Nunca associar automaticamente a thread privada.
- Não compartilhar histórico, instruções privadas ou artefatos não selecionados para envio.
- Se houver envio para um terceiro, registrar no contexto de destino apenas a mensagem, anexos e demais informações **explicitamente destinadas ao novo público**; a skill não estabelece um fluxo de pergunta/resposta.
- Respostas serão processadas somente se o gateway permitir atendimento àquele usuário/grupo. O atendimento atual a não-owner ainda é limitado; a ampliação das permissões de terceiros **não** faz parte desta skill.

### 5.3. P6 — Decisão final: uma thread por chat sem tópicos

**Decisão aprovada pelo owner em 2026-10-17.**

- Cada **DM ou grupo sem tópicos** tem uma **única conversa Telegram e uma única thread Codex associada** no gateway (criada sob demanda na primeira interação autorizada que exigir processamento).
- Todos os envios feitos pela skill àquele chat, independentemente de qual execução ou thread Codex os originou, são registrados no **mesmo contexto do destino**, de acordo com a P5.
- Respostas diretas a mensagens específicas do bot e mensagens espontâneas **seguem a mesma thread do chat**, sem tentar rotear a execução a uma thread externa que tenha originado algum envio.
- A identificação de uma mensagem respondida pode ajudar o agente a entender o assunto, mas **não muda a thread de processamento**.
- A chegada de um envio externo **não dispara** por si só uma nova execução do Codex. O processamento de mensagens recebidas depende das autorizações vigentes no gateway.
- Quando vários envios externos tratarem de assuntos distintos, a conversa compartilha o mesmo contexto: eventual ambiguidade temática é uma característica aceita dos chats sem tópicos, não motivo para multiplicar threads ocultas.
- Chats com tópicos continuam isolados pelo **identificador de cada tópico**, mantendo cada tópico sua própria conversa/thread, conforme P2, P3 e P5.
- O comando `/new` e eventuais reinícios explícitos do contexto continuam seguindo as regras existentes do gateway; "uma thread por chat" significa **uma associação vigente por vez**, não uma thread imutável para sempre.

**Resultado:** sem mecanismos de correlação por thread de origem, sem roteamento de respostas entre aplicações e sem concorrência de várias threads associadas ao mesmo chat sem tópicos.

## 6. Autorizações

1. **Owner:** pode listar, enviar a qualquer canal **registrado**, solicitar tópicos/conversas quando permitidos e remover registros. Não há restrição artificial de destino por tipo ou conteúdo do documento; o owner administra o sistema.
2. **Não-owner:** não recebe capacidade geral de operação da skill; não pode solicitar envio de documentos nem iniciar comunicações para canais diferentes da conversa vigente. O tratamento de suas mensagens no próprio canal continua sujeito à política atual/futura do gateway.
3. **Cadastro:** não é permitido pela skill. `/channel` é a porta de entrada obrigatória de registro no próprio Telegram. Nas DMs tradicionais de terceiros, solicita-se aprovação do owner (P1); nos grupos e tópicos, só o owner pode executar o comando, sem fluxo de solicitação por terceiros (P2).
4. **Plataforma:** o bot só pode enviar quando a Bot API e as permissões efetivas do chat permitirem.
5. **Isolamento:** o direito do owner de **enviar** material a qualquer canal registrado não concede a terceiros acesso à thread/contexto privado do owner.
6. **Decisão P8 — Aprovada em 2026-10-17:** registrar um canal via `/channel` habilita **somente seu uso como destino de envio pela skill**. O cadastro **não modifica as permissões de recepção, atendimento ou execução** do gateway, não promove usuários não-owner e não autoriza terceiros a operarem a skill. Respostas recebidas são tratadas exclusivamente pelas regras de atendimento já vigentes (ou futuramente configuradas) no gateway. A remoção do cadastro também não desativa automaticamente o atendimento normal (P4).

## 7. Reaproveitamento atual e lacunas constatadas no código

Referências no upstream:
- [Gateway Telegram](../onmyoji-daemon/src/onmyoji_daemon/telegram.py)
- [Supervisor](../onmyoji-daemon/src/onmyoji_daemon/supervisor.py)
- [RPC](../onmyoji-daemon/src/onmyoji_daemon/rpc.py)
- [Documentação do daemon](daemon.md)
- [Arquitetura da instância](architecture.md)

O gateway **já oferece**:
- RPC interno com operação de envio simples; ainda recebe chat_id explícito e não realiza resolução via catálogo.
- Envio de mensagem e mídia via TelegramApi.
- Ferramentas dinâmicas contextuais para mensagem adicional, menu interativo, mídia/outbox, anexos e modo de resposta.
- Identificadores diferentes para DM comum, DM com tópico e grupo com tópico.
- Persistência por conversa e vínculo com codex_thread_id; recuperação/início de threads via Codex App Server.
- Reconhecimento de grupos/tópicos recebidos e processamento sob política atual.
- Cadastro de contatos centrado em owners pareados; **não** existe ainda catálogo intencional de todos os canais autorizados.

O gateway **não oferece ainda**, no contrato exigido:
- /channel e ciclo de vida explícito de registro/remoção de canais.
- Listagem de destinos autorizados a partir de um catálogo.
- Envio da skill por destinatário lógico (sem expor chat_id).
- Envio genérico de arquivos fora de um turno Telegram ativo.
- Criação proativa de tópicos pela skill com registro da entrega no contexto de destino, **sem associação à thread de origem**.
- Regras completas para permissões e respostas de usuários não-owner.
- Registro dos envios externos no contexto Telegram para apresentação ao próximo turno do gateway, quando houver.

O reconhecimento passivo de chats/tópicos **não** deverá alimentá-los automaticamente como destinos registrados.

## 8. Requisitos funcionais consolidados

| ID | Requisito | Estado |
| --- | --- | --- |
| R01 | Skill denominada **telegram**, pertencente ao Onmyoji | Decidido |
| R02 | Listar/buscar apenas canais explicitamente registrados | Decidido |
| R03 | Registro novo somente via interação Telegram (/channel ou comando equivalente), com aprovação do owner para DM de terceiros | Decidido — P1 resolvida |
| R04 | Não cadastrar canais automaticamente ao receber conteúdo | Decidido |
| R05 | Remoção de registro via Telegram e via skill pelo owner | Decidido |
| R06 | Owner pode enviar para qualquer canal registrado | Decidido |
| R07 | Não-owner não pode provocar envio de documentos nem abrir comunicações em outros canais | Decidido |
| R08 | Enviar texto (inclusive perguntas como texto), arquivos, imagens e áudio, **sem diálogo/retorno à skill** | Decidido — P7 resolvida |
| R09 | Enviar de UI/CLI sem depender de conversa Telegram ativa | Decidido |
| R10 | Enviar a outro canal sem mesclar/alterar a conversa de origem | Decidido |
| R11 | Criar/reutilizar tópicos quando habilitados e permitidos | Decidido — P3 resolvida |
| R12 | Envios de qualquer origem entram no contexto Telegram de destino; preservam sua thread existente ou criam sob demanda, sem associar a thread da UI/CLI de origem | Decidido — P5 resolvida |
| R13 | Em destinatário terceiro/grupo, isolar thread e contexto da origem | Decidido |
| R14 | Respostas de não-owner obedecem às permissões do gateway | Decidido; políticas futuras |
| R15 | Execução futura iniciada **pelo gateway** pode ter thread própria associada e recursos nativos de interação; a skill permanece somente de saída, sem envio obrigatório | Diretriz futura, fora do desenvolvimento atual |
| R16 | Remover cadastro impede novos envios proativos da skill para o destino, mas preserva chat/tópico, histórico, thread, associação e respostas do gateway à conversa vigente | Decidido — P4 resolvida |
| R17 | Skill é **somente envio**; não implementa menus interativos, espera por seleção/resposta nem retorno de mensagens. Apenas confirmação/metadados de envio ou erro | Decidido — P7 resolvida |
| R18 | `/channel` cadastra só o chat/tópico onde foi executado, nunca canais irmãos | Decidido — P2 resolvida |
| R19 | Em grupos e tópicos só o owner pode executar `/channel`; não-owner recebe negativa explícita usando “proprietário” | Decidido — P2 resolvida |
| R20 | Tópico criado pela skill pode receber e continuar a conversa própria do destino, sem compartilhar thread externa; não entra no catálogo até `/channel` explícito pelo owner naquele tópico | Decidido — P3/P5 resolvidas |
| R21 | DM/grupo sem tópicos mantém uma única thread Codex vigente por chat; envios externos entram nesse contexto, e respostas não são roteadas para threads externas | Decidido — P6 resolvida |
| R22 | Menus e interação do gateway existente continuam disponíveis **apenas pelo próprio gateway**; não são expostos pela skill | Decidido — P7 resolvida |
| R23 | Registrar um canal via `/channel` autoriza apenas novos envios pela skill, **sem alterar permissões de atendimento/interação do gateway nem conceder acesso a não-owner** | Decidido — P8 resolvida |

## 9. Fora de escopo agora

- Recebimento/disparo de agendamentos, webhooks, notificações de e-mail ou qualquer mecanismo de eventos.
- Executor automático de tarefas e auditoria desses eventos.
- Sistema completo de novas permissões para usuários não-owner.
- Sincronização visual da UI Codex com Telegram.
- Interface de retorno, consulta de respostas, prompts bloqueantes ou menus de interação pela skill.
- Reimplementação ou extensão dos mecanismos nativos de diálogo do gateway dentro da skill.
- Compartilhamento automático de histórico entre audiências distintas.
- Descoberta aberta de contatos e grupos externos ao catálogo.
- Implementação antecipada de infraestrutura genérica multicanal.

## 10. Pendências para decisão — uma por vez

1. **P1 — RESOLVIDA (2026-10-17):** terceiro solicita pelo `/channel` em sua própria DM; gateway encaminha pedido ao owner via Telegram; owner aprova/rejeita no menu; somente a aprovação cadastra a DM. Não confere novas permissões ao terceiro.
2. **P2 — RESOLVIDA (2026-10-17):** `/channel` gerencia apenas o chat/tópico exato em que foi executado, sem cadastrar irmãos. Em grupos e tópicos, somente owner; não-owner recebe negativa explícita (“proprietário”), sem fluxo de aprovação. Mantida a exceção P1 para solicitação em DM tradicional de terceiros.
3. **P3 — RESOLVIDA (2026-10-17):** criar tópico pela skill não o registra como destino permanente. Ele pode ser utilizado pela conversa Telegram de destino, com thread própria conforme P5; só entra no catálogo de destinos após `/channel` executado pelo owner naquele tópico.
4. **P4 — RESOLVIDA (2026-10-17):** remover um registro retira o destino do catálogo e impede novos envios proativos a ele via skill, sem apagar chat/tópico, histórico, thread Codex ou vínculo; gateway continua atendendo normalmente a conversa existente.
5. **P5 — RESOLVIDA (2026-10-17):** a skill envia e registra conteúdo externo no contexto da conversa Telegram de destino, sem compartilhar threads entre clientes. A thread de destino é preservada ou criada no primeiro processamento autorizado; mensagens externas são oferecidas ao próximo turno, sem executar Codex só para registrar o envio. O retorno Telegram permanece no gateway.
6. **P6 — RESOLVIDA (2026-10-17):** cada DM/grupo sem tópicos mantém uma thread Codex vigente para todo o chat, criada sob demanda. Todos os envios externos entram nesse contexto; respostas, inclusive replies, seguem a thread do chat e nunca uma thread externa originadora. Tópicos permanecem separados.
7. **P7 — RESOLVIDA (2026-10-17):** a skill é unidirecional: envia mensagens e anexos e retorna apenas confirmação/metadados ou erro. Não cria/aguarda menus interativos nem retorna respostas. O gateway conserva seus próprios recursos de diálogo; futuras execuções Codex disparadas pelo gateway podem utilizá-los no contexto nativo.
8. **P8 — RESOLVIDA (2026-10-17):** cadastro via `/channel` habilita somente o canal como destino de envios da skill; não altera permissões de recepção, atendimento ou execução do gateway, nem concede novas capacidades a não-owner. Remover registro não encerra o atendimento existente (P4).

**Todas as decisões funcionais P1–P8 estão encerradas.** O briefing está consolidado para apoiar o planejamento de arquitetura e testes; detalhes técnicos de implementação, limites da Bot API e validação de integração permanecem para a próxima fase, sem reabrir decisões funcionais automaticamente.

## 11. Critérios de aceitação de alto nível

A skill estará aderente ao objetivo quando, em testes controlados:
1. Um owner na UI do Codex conseguir listar os canais cadastrados, escolher Maria/grupo/tópico e enviar arquivo sem conhecer IDs Telegram.
2. Uma conversa Telegram conseguir enviar conteúdo para seu próprio canal ou para outro registrado, sem modificar o contexto original.
3. O owner conseguir registrar/remover explicitamente o canal pelo Telegram, e consultar/remover pelo agente; mensagem recebida isoladamente nunca cadastrar o canal.
4. Um envio da thread privada do owner para Maria ou grupo não associar o destinatário à thread privada.
5. Ao enviar de outro contexto, registrar conteúdo e proveniência no histórico do destino. Próxima interação autorizada usa thread Telegram existente ou cria uma se ausente, com contexto externo relevante, sem interferir na thread da origem (P5 resolvida).
6. Usuários não-owner não conseguirem usar a skill para enviar documentos ou iniciar outra conversa fora da vigente.
7. Ausência de destino registrado, ambiguidade ou bloqueio do bot resultar em erro explícito, sem envio inesperado.
8. Enviar mensagem em forma de pergunta ou anexo pela skill retornar **somente resultado do envio**, sem aguardar nem consultar a resposta de pessoa ou seleção de menu.
9. Envio em conversa que já possui thread ser registrado para o próximo turno **nativo do gateway**, sem criar thread/turno nem transportar a thread do cliente de origem.
10. Nenhuma implementação de eventos/agendamentos ser exigida para a skill funcionar; interatividade de eventuais execuções iniciadas pelo próprio gateway permanece fora da skill.

---

**Próxima etapa:** planejar a arquitetura e a implementação da skill `telegram` e das extensões mínimas necessárias no Gateway Telegram, respeitando P1–P8. Nenhuma implementação foi iniciada por este briefing.
