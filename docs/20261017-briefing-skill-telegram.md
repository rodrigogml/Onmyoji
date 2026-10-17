# Briefing — Skill de integração Telegram

**Data:** 2026-10-17  
**Status:** Em elaboração — decisões registradas; pendências abertas para discussão incremental.  
**Repositório responsável:** Onmyoji (upstream).  
**Escopo deste documento:** requisitos e regras de comportamento; não é ainda um plano de arquitetura nem autorização para iniciar a implementação.

## 1. Objetivo

Criar a skill de integração **telegram**, disponibilizada ao agente em qualquer contexto de execução do Codex (UI/CLI, conversa recebida pelo Telegram ou execução não interativa), para usar as capacidades de comunicação do **Gateway Telegram já existente**.

A skill deve permitir consultar os **canais explicitamente cadastrados**, enviar mensagens, perguntas, arquivos, imagens, áudio/voz, e, quando cabível, criar ou reutilizar tópicos e estabelecer continuidade de conversa com uma thread Codex.

**Enviar conteúdo** e **vincular uma conversa a uma thread** são decisões diferentes. Um envio nunca implica, por si só, a transferência do contexto privado da origem ao destinatário.

O evento que iniciou a execução é irrelevante para o contrato da skill. Agendamentos, webhooks, e-mails e seu processamento estão fora deste escopo; futuramente poderão chamar a mesma skill.

## 2. Vocabulário e princípios acordados

- **Owner:** o mestre e administrador da instância; empregar sempre o termo owner.
- **Origem:** interface/canal de onde partiu a solicitação à execução atual (por exemplo, UI Codex ou tópico Telegram).
- **Canal:** unidade de comunicação Telegram que pode ser cadastrada: DM, grupo sem tópicos, grupo com tópicos, tópico de grupo ou tópico de DM. A granularidade de cadastro deverá ser explícita.
- **Catálogo:** lista de canais **registrados deliberadamente** para comunicação pela skill; não é a lista de todos os chats vistos pelo bot.
- **Destino:** canal selecionado para determinada entrega.
- **Thread Codex:** sessão/contexto do agente, que pode existir antes de qualquer contato via Telegram.
- **Associação:** vínculo explícito de uma conversa Telegram com uma thread Codex para continuidade das respostas.
- **Audiência:** quem pode receber e interagir com o contexto daquela thread; pessoas distintas e grupos não herdam o contexto privado do owner.

Princípios:
1. O **owner tem autoridade para usar a skill e enviar conteúdo a qualquer canal registrado**, sujeito apenas às limitações técnicas e permissões efetivas do Telegram; não se propõe restringir seus destinos com base no conteúdo do documento.
2. **Usuários não-owner não podem usar o agente para enviar documentos nem para provocar envio/início de comunicação em outros canais.** A interação deles fica limitada à conversa já vigente e às regras de atendimento do gateway; a política mais detalhada de usuários autorizados será definida em outra etapa.
3. Não ocorre descoberta indiscriminada de pessoas, grupos ou tópicos pelo bot.
4. **Receber mensagens de um canal não o cadastra automaticamente**. Registrar depende de ação explícita via Telegram.
5. A skill pode **listar e remover** canais registrados, sob autoridade do owner, mas **não pode registrar um canal novo**. A remoção solicitada ao agente deve ser explícita, identificando o canal sem ambiguidades.
6. Enviar a outro destino não altera a associação da conversa de origem.
7. Uma conversa do owner na UI local do Codex é tratada como originada pelo owner. Isso não autoriza compartilhar o histórico de sua thread com terceiros.

## 3. Catálogo: cadastro intencional e manutenção

### 3.1. Cadastro exclusivamente pelo Telegram

Prever um comando nativo do gateway, provisoriamente **/channel**, executado no próprio contexto Telegram a registrar. O comando abre menu interativo contendo, pelo menos:
- **Registrar este canal** como destino permitido para comunicação em mão dupla.
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
- O tópico criado pode ser utilizado imediatamente **pela conversa que o originou**, inclusive para receber respostas e continuar sua thread Codex quando essa associação for compatível.
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

### 4.1. Descoberta do catálogo registrado

- Listar canais disponíveis no cadastro, incluindo DMs do owner e de terceiros, grupos e tópicos que tenham sido explicitamente registrados.
- Buscar canais por nome/alias e desambiguar nomes coincidentes, sem escolher destinatário arbitrariamente.
- Consultar informações de um canal e suas capacidades, incluindo suporte à criação de tópicos, quando conhecido.
- Consultar/remover cadastro, restrito ao owner.
- Identificar a conversa Telegram atual quando a execução se originou no gateway, sem exigir IDs manuais.

### 4.2. Entrega de conteúdo

- Enviar texto e perguntas em formato de mensagem.
- Enviar documentos, arquivos, imagens/fotos e áudio.
- Aproveitar recursos existentes do gateway para voz, mensagens protegidas, mensagens temporárias e menus interativos, nos contextos onde sejam válidos.
- Permitir envio de arquivo produzido no workspace autorizado por uma execução **fora** do Telegram; não depender exclusivamente do outbox de um turno Telegram.
- Reutilizar o canal/tópico atual quando o destino for a própria conversa.
- Enviar a outro canal registrado **sem alterar a conversa nem a thread de origem**.
- Retornar resultado de envio e identificação inequívoca do destino usado, ou erro operacional compreensível.
- Respeitar limites de tamanho, formato e acesso aos arquivos já adotados pelo gateway.

**Distinção funcional:** “enviar uma pergunta” pode ser apenas entrega; “perguntar e esperar resposta” exige tratamento de resposta/estado, que não deve ser inferido automaticamente. O menu interativo do gateway hoje é contextual a um turno Telegram ativo; seu uso em outros canais exigirá validação específica.

### 4.3. Estabelecer/continuar conversa

Três intenções de alto nível, sem fixar ainda nomes de métodos ou formato da API:

| Intenção | Efeito esperado |
| --- | --- |
| **Somente enviar** | Entregar conteúdo; nenhuma nova associação à thread de origem. Respostas seguem a política vigente do canal. |
| **Nova conversa independente** | Criar ou reutilizar local de interação adequado e manter as respostas em um contexto Codex distinto da origem, se o destinatário puder interagir. |
| **Continuar thread atual** | Criar/reutilizar tópico/conversa e associá-lo à thread atual **somente quando a audiência for compatível** (por exemplo, UI do owner → DM do mesmo owner). |

Nos chats com tópicos habilitados, a skill deverá poder criar tópico quando autorizado e permitido pelo Telegram, ou reutilizar um já associado. Nos chats sem tópicos, deverá enviar normalmente, mas sem prometer correlação inequívoca entre várias conversas paralelas.

## 5. Regras de contexto e cenários aceitos

| Origem | Destino | Comportamento requerido |
| --- | --- | --- |
| UI local do Codex (owner) | DM registrada do owner sem tópicos | Enviar diretamente; respostas podem continuar a thread de origem **somente com correlação inequívoca**. |
| UI local do Codex (owner) | DM registrada do owner com tópicos | Criar/reutilizar tópico; permitir associação à thread de origem para continuidade. |
| UI local do Codex (owner) | DM registrada de Maria | Enviar; **não** associar à thread privada do owner. |
| UI local do Codex (owner) | Conversa solicitada com Maria | Nova thread/contexto independente e somente conteúdo compartilhável; respostas segundo a política da DM. |
| UI local do Codex (owner) | Grupo/tópico registrado | Enviar; **não** associar automaticamente à thread privada do owner. |
| Telegram | Mesmo chat/tópico ativo | Reutilizar fluxo e thread correntes; não criar novo tópico desnecessariamente. |
| Telegram | Outro chat/tópico registrado | Enviar sem mesclar ou alterar a origem; se abrir nova conversa, contexto independente. |
| Telegram (não-owner) | Outro canal ou nova conversa | **Proibido**, inclusive quando solicitado via prompt à execução do agente. |
| Execução não interativa (futura) | Destino previamente definido e compatível com a audiência | Poderá vincular a própria thread da execução ao destino e receber respostas nela. |
| Execução não interativa (futura) | Destino distinto da audiência da execução | Envio sem transferência automática da thread; nova conversa requer isolamento. |
| Qualquer origem | Sem conteúdo relevante a comunicar | **Nenhum envio obrigatório**. |

### 5.1. Regra especial: UI do owner e DM do owner

É aceitável que a UI do Codex e um tópico Telegram do owner representem **a mesma thread**. O conflito de duas interfaces é do mesmo owner; a falta de sincronização visual imediata entre interfaces é tolerável.

**Não é aceitável executar turnos incompatíveis simultaneamente na mesma thread.** Mensagens recebidas enquanto ela está ocupada devem ser serializadas ou receber tratamento explícito, nunca resultar em troca silenciosa de thread.

**Pendência P5 (viabilidade técnica):** validar como identificar a thread da UI/CLI e se pode ser retomada pelo App Server do gateway enquanto outro cliente a utiliza; definir o comportamento quando não for possível. Nesse caso, o envio simples deve continuar possível, sem prometer uma associação inválida.

### 5.2. Regra de isolamento: Maria e grupos

Quando a audiência não for a mesma da thread privada de origem:
- Nunca associar automaticamente a thread privada.
- Não compartilhar histórico, instruções privadas ou artefatos não selecionados para envio.
- Se solicitada conversa em mão dupla, criar um contexto separado com a mensagem, anexos e demais informações **explicitamente destinadas ao novo público**.
- Respostas serão processadas somente se o gateway permitir atendimento àquele usuário/grupo. O atendimento atual a não-owner ainda é limitado; a ampliação das permissões de terceiros **não** faz parte desta skill.

### 5.3. Chats sem tópicos e respostas ambíguas

Em uma DM ou grupo sem tópicos, várias comunicações podem compartilhar a mesma linha temporal. Uma resposta à mensagem específica do bot pode ajudar na correlação, mas uma mensagem espontânea não deve ser atribuída arbitrariamente a outra thread.

**Pendência P6:** definir a regra final de associação de respostas em chats sem tópicos e a política para mensagens espontâneas quando houver mais de uma interação potencial.

## 6. Autorizações

1. **Owner:** pode listar, enviar a qualquer canal **registrado**, solicitar tópicos/conversas quando permitidos e remover registros. Não há restrição artificial de destino por tipo ou conteúdo do documento; o owner administra o sistema.
2. **Não-owner:** não recebe capacidade geral de operação da skill; não pode solicitar envio de documentos nem iniciar comunicações para canais diferentes da conversa vigente. O tratamento de suas mensagens no próprio canal continua sujeito à política atual/futura do gateway.
3. **Cadastro:** não é permitido pela skill. `/channel` é a porta de entrada obrigatória de registro no próprio Telegram. Nas DMs tradicionais de terceiros, solicita-se aprovação do owner (P1); nos grupos e tópicos, só o owner pode executar o comando, sem fluxo de solicitação por terceiros (P2).
4. **Plataforma:** o bot só pode enviar quando a Bot API e as permissões efetivas do chat permitirem.
5. **Isolamento:** o direito do owner de **enviar** material a qualquer canal registrado não concede a terceiros acesso à thread/contexto privado do owner.

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
- Criação proativa de tópicos com associação de contexto de origem.
- Regras completas para permissões e respostas de usuários não-owner.
- Garantia de retomada segura da **mesma** thread usada simultaneamente por UI/CLI e gateway.

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
| R08 | Enviar texto, arquivos, imagem, áudio e perguntas usando recursos do gateway | Decidido |
| R09 | Enviar de UI/CLI sem depender de conversa Telegram ativa | Decidido |
| R10 | Enviar a outro canal sem mesclar/alterar a conversa de origem | Decidido |
| R11 | Criar/reutilizar tópicos quando habilitados e permitidos | Decidido — P3 resolvida |
| R12 | Associar tópico do owner à thread da origem quando compatível | Objetivo aceito; viabilidade P5 |
| R13 | Em destinatário terceiro/grupo, isolar thread e contexto da origem | Decidido |
| R14 | Respostas de não-owner obedecem às permissões do gateway | Decidido; políticas futuras |
| R15 | Execução futura pode declarar destino/audiência prévia, sem enviar obrigatoriamente | Diretriz futura, fora do desenvolvimento atual |
| R16 | Remover cadastro impede novos envios proativos da skill para o destino, mas preserva chat/tópico, histórico, thread, associação e respostas do gateway à conversa vigente | Decidido — P4 resolvida |
| R17 | Suportar perguntas/menus; espera síncrona somente quando houver suporte à execução | Decidido; viabilidade a testar |
| R18 | `/channel` cadastra só o chat/tópico onde foi executado, nunca canais irmãos | Decidido — P2 resolvida |
| R19 | Em grupos e tópicos só o owner pode executar `/channel`; não-owner recebe negativa explícita usando “proprietário” | Decidido — P2 resolvida |
| R20 | Tópico criado pela skill pode continuar a conversa de origem, mas não entra no catálogo até `/channel` explícito pelo owner naquele tópico | Decidido — P3 resolvida |

## 9. Fora de escopo agora

- Recebimento/disparo de agendamentos, webhooks, notificações de e-mail ou qualquer mecanismo de eventos.
- Executor automático de tarefas e auditoria desses eventos.
- Sistema completo de novas permissões para usuários não-owner.
- Sincronização visual da UI Codex com Telegram.
- Compartilhamento automático de histórico entre audiências distintas.
- Descoberta aberta de contatos e grupos externos ao catálogo.
- Implementação antecipada de infraestrutura genérica multicanal.

## 10. Pendências para decisão — uma por vez

1. **P1 — RESOLVIDA (2026-10-17):** terceiro solicita pelo `/channel` em sua própria DM; gateway encaminha pedido ao owner via Telegram; owner aprova/rejeita no menu; somente a aprovação cadastra a DM. Não confere novas permissões ao terceiro.
2. **P2 — RESOLVIDA (2026-10-17):** `/channel` gerencia apenas o chat/tópico exato em que foi executado, sem cadastrar irmãos. Em grupos e tópicos, somente owner; não-owner recebe negativa explícita (“proprietário”), sem fluxo de aprovação. Mantida a exceção P1 para solicitação em DM tradicional de terceiros.
3. **P3 — RESOLVIDA (2026-10-17):** criação de tópico pela skill não o registra como destino permanente. O tópico pode servir à conversa/associação que o criou; só passa ao catálogo de destinos após `/channel` executado pelo owner naquele tópico.
4. **P4 — RESOLVIDA (2026-10-17):** remover um registro retira o destino do catálogo e impede novos envios proativos a ele via skill, sem apagar chat/tópico, histórico, thread Codex ou vínculo; gateway continua atendendo normalmente a conversa existente.
5. **P5 — Thread compartilhada:** identificação da thread atual e retomada segura entre Codex UI/CLI e App Server; ordenação de turnos.
6. **P6 — Chats sem tópicos:** roteamento de respostas a mensagens e concorrência de conversas distintas.
7. **P7 — Perguntas e menus fora do turno Telegram:** comportamento assíncrono, validade e tratamento de respostas.
8. **P8 — Política do canal registrado como “mão dupla”:** delimitar a autorização de envio proativo da autorização de interagir/responder no gateway, especialmente para não-owner.

Pendências **não** impedem que o briefing seja versionado agora. Elas impedem apenas considerar os respectivos comportamentos como especificação fechada.

## 11. Critérios de aceitação de alto nível

A skill estará aderente ao objetivo quando, em testes controlados:
1. Um owner na UI do Codex conseguir listar os canais cadastrados, escolher Maria/grupo/tópico e enviar arquivo sem conhecer IDs Telegram.
2. Uma conversa Telegram conseguir enviar conteúdo para seu próprio canal ou para outro registrado, sem modificar o contexto original.
3. O owner conseguir registrar/remover explicitamente o canal pelo Telegram, e consultar/remover pelo agente; mensagem recebida isoladamente nunca cadastrar o canal.
4. Um envio da thread privada do owner para Maria ou grupo não associar o destinatário à thread privada.
5. Um tópico do owner poder continuar a thread originada na UI **se** a integração de sessões entre clientes for validada; caso contrário, informar a limitação e preservar envio simples.
6. Usuários não-owner não conseguirem usar a skill para enviar documentos ou iniciar outra conversa fora da vigente.
7. Ausência de destino registrado, ambiguidade ou bloqueio do bot resultar em erro explícito, sem envio inesperado.
8. Nenhuma implementação de eventos/agendamentos ser exigida para a skill funcionar.

---

**Próxima discussão:** P5, compartilhamento de thread entre UI/CLI Codex e Telegram: identificar a thread de origem, validar recuperação segura pelo App Server e definir o fallback se a associação não for tecnicamente possível. Atualizar este briefing a cada decisão antes de iniciar a implementação.
