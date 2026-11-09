# Agendador e notificações ao agente no daemon

**Status:** rascunho vivo de concepção

**Início:** 2026-11-09

**Próximo tema de discussão:** acompanhamento da resposta no transporte local e cancelamento

> [!IMPORTANT]
> Este documento registra a discussão em andamento, inclusive hipóteses ainda prematuras. Ele não é uma especificação de implementação nem indica que as propostas preliminares foram aprovadas.

## Finalidade

Concentrar a concepção de duas capacidades persistentes e distintas no daemon do Onmyōji: um agendador e um gateway de notificações ao agente. “Gateway” é uma descrição funcional neste rascunho, não o nome definitivo do componente. As capacidades são discutidas em conjunto porque podem se integrar, mas nenhuma delas é parte obrigatória da outra. O rascunho deve evoluir incrementalmente: ideias podem ser corrigidas, promovidas a decisões ou descartadas à medida que a discussão avançar.

## Intenções já expressas

As seguintes capacidades orientam a discussão, embora seus contratos técnicos ainda precisem ser definidos:

- O daemon persistente deverá futuramente oferecer um serviço de agendamento, capaz de disparar tarefas em data e hora determinadas ou segundo uma recorrência configurada.
- O agendador terá contexto e menu próprios no setup do daemon. Futuramente, esse menu deverá permitir listar agendamentos existentes, modelos e definições atuais.
- Deverá existir, separadamente do agendador, um ingresso que receba eventos entendidos como solicitações ou notificações para que o agente processe determinada informação no mesmo gateway de execução usado pelo Telegram. O envelope JSON v1 terá os campos obrigatórios `schema_version`, `event_id`, `prompt`, `response_channel` e `thread_policy`.
- Um acontecimento externo — como novo e-mail, mensagem ou webhook — não será automaticamente um evento do gateway. A origem poderá tratá-lo integralmente sem envolver o agente.
- Somente quando a origem decidir que o agente deve participar ela emitirá um evento ou uma notificação ao gateway.
- O gateway poderá futuramente receber essas notificações por webhooks ou outras interfaces, sem se tornar um barramento genérico de todos os acontecimentos externos; essa exposição externa não integra a fase inicial.
- Na fase inicial, o ingresso será exclusivamente privado/local, sem autenticação, cadastro ou autorização de emissores. O receptor aceitará qualquer evento que passe pela validação do contrato JSON. Uma publicação externa dependerá de nova decisão específica sobre segurança e autenticação.
- O gateway receberá a notificação em JSON. Um esquema versionado definirá sua estrutura e o padrão válido, e a versão aplicável deverá estar identificável no próprio evento.
- O gateway validará o JSON antes de invocar o agente e retornará erro de validação quando a mensagem estiver fora do padrão, inclusive quando os campos obrigatórios do canal estiverem ausentes ou inválidos. Emissores poderão usar o mesmo esquema antecipadamente para verificar as mensagens que produzem.
- `schema_version` será um número inteiro com valor `1`; `event_id` será o UUIDv7 obrigatório; `prompt` será texto livre obrigatório e não vazio, sem referência obrigatória a template e sem limite máximo funcional definido neste momento.
- O emissor fornecerá um identificador UUID versão 7 obrigatório na chamada. O daemon reutilizará esse mesmo identificador durante processamento, entrega, diagnóstico e logs, sem criar um segundo ID de execução.
- Ao reenviar a mesma solicitação, o emissor manterá o mesmo identificador. Tentativas poderão ser diferenciadas por registros operacionais associados a esse ID, sem nova identidade obrigatória.
- O daemon não manterá cache ou histórico de resultados para deduplicar por UUIDv7. Cada recebimento, inclusive um reenvio com o mesmo ID, será processado novamente e produzirá uma nova resposta.
- Em cada chamada recebida, o daemon registrará no log o UUIDv7 fornecido pelo chamador e acrescentará um timestamp do momento da recepção com precisão de milissegundos. Esse timestamp será metadado operacional, não uma segunda identidade.
- Um agendamento poderá notificar o gateway ou executar outras ações que não o usem. Da mesma forma, uma notificação ao agente poderá ser emitida por fontes diferentes do agendador.
- A relação entre agendador e gateway será de composição e reutilização, não de dependência obrigatória nem de identidade arquitetural.
- A execução do evento será síncrona e permanecerá aguardando a resposta do App Server ou do Codex CLI. O chamador poderá esperar por essa resposta ou optar por não a acompanhar nem consumi-la; essa escolha não criará um modo assíncrono no gateway.
- Quando o chamador não acompanhar a resposta, o daemon ainda concluirá o processamento e tentará entregar o resultado ao canal configurado.
- Se o destino informado não existir, não for encontrado ou falhar na entrega, o daemon registrará a falha operacional em arquivo de log. Quando não houver chamador aguardando para receber o erro, esse registro preservará o diagnóstico; ele poderá conter integralmente a identificação do chamador, os dados de entrada do evento, a saída ou o resultado produzido e o erro ou a falha ocorrida.
- Os arquivos e registros de log do gateway serão retidos por 20 dias e excluídos automaticamente quando ultrapassarem esse prazo. Essa política não autoriza excluir e-mails, acontecimentos na origem nem outros conteúdos.
- O gateway terá uma configuração padrão para um canal de notificações operacionais e incidentes. Quando houver problema na resposta ou na entrega de um evento, ou quando ocorrer uma falha registrada em log, o daemon poderá enviar a esse canal uma notificação do problema ou do log.
- O canal de notificações operacionais será distinto do canal de resposta do evento e não receberá a resposta funcional normal.
- O canal operacional será opcional, mas deverá ser configurável pelo sistema de setup existente do Onmyōji, no contexto do gateway dentro do menu de configuração do daemon. Sem configuração, a ocorrência permanecerá no log local, e nem o setup nem a execução dependerão desse canal.
- Receber um evento no gateway significará solicitar a participação do agente. O gateway notificará ou invocará o agente para processar a informação pelo App Server ou pelo Codex CLI.
- `thread_policy` será obrigatório e aceitará `reuse` ou `new`. `reuse` usará a thread vigente associada ao destino. `new` iniciará uma nova thread para o evento e, depois da conclusão dessa troca, substituirá a associação anterior; o próximo `reuse` continuará na nova thread.
- O destino de resposta será o objeto obrigatório e aninhado `response_channel`, com `type` e `value` obrigatórios. `type` aceitará `telegram` ou `email`; `value` armazenará o destino.
- Os tipos iniciais considerados serão Telegram e e-mail. Para Telegram, o setup permitirá escolher um canal já registrado e o agendamento ou evento armazenará diretamente o ID escolhido. Para e-mail, o valor armazenado será o endereço que receberá a mensagem com o resultado.
- O destino de resposta será distinto do canal operacional global usado para incidentes e logs.
- Um evento sem tipo ou valor válido para o canal de saída será rejeitado na validação do esquema. Se os campos estiverem presentes, mas o destino não existir mais, não for encontrado ou falhar na entrega, o daemon registrará a falha no log e usará o canal operacional de incidentes quando ele estiver configurado.
- Os tipos iniciais de canal de saída considerados são Telegram e e-mail. Outros adaptadores poderão ser discutidos no futuro.
- Telegram e eventos do gateway compartilharão um único escalonador e o limite global já usado para turnos do Codex. O escalonador manterá os destinos ativos, pulará temporariamente candidatos cujo destino já esteja em execução e buscará o próximo elegível, sem criar filas individuais por canal nem bloqueio de cabeça de fila.
- Mensagens do Telegram terão prioridade sobre eventos do gateway que ainda aguardam capacidade, sem interromper execução já iniciada. A ordem de chegada será preservada entre itens comparáveis e elegíveis; tráfego contínuo do Telegram poderá causar starvation dos eventos do gateway.
- O evento será uma segunda origem do mesmo gateway de execução usado pelo Telegram. As duas origens compartilharão a mesma inbox persistente, o mesmo escalonador, o mesmo App Server, o mesmo limite global e o mesmo ciclo de terminalidade; não haverá executor, App Server, banco de execução ou fila paralelos para eventos.
- O processo compartilhado deverá permitir habilitar o ingresso de eventos sem exigir que a recepção Telegram esteja ativa. As origens e os adaptadores poderão ser habilitados separadamente, mas o núcleo de execução continuará único.
- O timeout de turno e a graça de interrupção já usados pelo Telegram também valerão para eventos. Não será criado um timeout próprio do novo ingresso. Continua em aberto como o chamador acompanhará a resposta sem bloquear o transporte RPC local e se poderá solicitar cancelamento.
- Um MCP futuro poderá permitir que o agente crie ou registre scripts e os associe a agendamentos. Esses scripts poderão executar localmente e, somente quando precisarem da participação do agente, emitir uma notificação ao gateway e receber o respectivo retorno.

> [!CAUTION]
> Exclusão automática na origem não é o comportamento padrão. Qualquer regra que exclua e-mails, mensagens ou outros conteúdos deverá ser deliberada, explícita e protegida. Essa triagem pode ocorrer sem emitir notificação ao gateway.

## Fronteiras entre os serviços

O agendador e o gateway de notificações ao agente pertencem ao daemon, mas têm responsabilidades e ciclos de configuração próprios:

- **Agendador:** administra definições temporais e recorrências e dispara ações configuradas. Seu catálogo de ações ainda não foi definido; notificar o gateway será uma possibilidade, não a finalidade exclusiva do serviço.
- **Gateway de execução:** recebe trabalhos das origens Telegram e evento, aplica o escalonamento global, invoca o agente por uma única instância App Server, acompanha a resposta e delega a entrega ao adaptador do canal.
- **Ingresso de eventos:** valida o envelope privado/local e insere o trabalho na mesma inbox do gateway; não possui executor nem fila próprios.
- **Processamento na origem:** serviços e scripts poderão detectar e tratar acontecimentos inteiramente sem gateway. Ignorar, arquivar, excluir sob proteção ou executar lógica local não exigirá criar uma notificação ao agente.
- **Composição:** um agendamento poderá notificar o gateway para solicitar processamento pelo agente, sem tornar as duas capacidades inseparáveis.
- **Setup:** o agendador terá seu próprio contexto e menu, e o gateway terá outro contexto e menu. Não haverá um submenu combinado denominado “Eventos e agendamentos”.

> [!IMPORTANT]
> Um acontecimento na origem e um evento do gateway não são sinônimos. Webhooks, interfaces e integrações somente emitirão um evento quando decidirem solicitar processamento pelo agente. O agendador também poderá executar ações que não notifiquem o gateway.

### Escopo inicial da interface do agendador

O primeiro escopo do contexto de agendamentos no setup do daemon será somente listar os agendamentos existentes, seus modelos e definições atuais. Criação e edição não foram aprovadas para esse escopo inicial.

A listagem será estruturada em colunas ou atributos exibíveis. Qualquer coluna apresentada poderá ser selecionada como critério de ordenação por um atalho na tela; a interação não ficará limitada a duas ordens fixas.

Os primeiros atributos mencionados para a listagem são:

- próxima execução;
- data e hora de criação;
- canal associado ao disparo ou ao resultado, entendido inicialmente como tipo e valor do destino de resposta;
- política de thread, indicando criação de nova thread ou reutilização de uma existente.

Esses dados deverão ser apresentados de modo que permitam compreender e ordenar os agendamentos, inclusive pelo tipo ou pelo valor do destino quando forem exibidos como colunas. Ainda não foram definidos teclas, direção ascendente ou descendente, nomes finais das colunas, larguras, paginação nem o conjunto completo de campos.

> [!NOTE]
> A futura interação deverá consultar e obedecer às [convenções da interface de setup](../setup-interface.md), inclusive a distinção entre menu navegável e apresentação em fluxo. Este rascunho não decide essa classificação nem autoriza implementação agora.

## Visão preliminar da integração e do fluxo de eventos

```text
agendador
    -> script ou outra ação sem gateway
    -> opcionalmente, notificação ao agente

origem detecta um acontecimento
    -> tratamento integral na origem, sem gateway
    -> ou decisão de envolver o agente
        -> emissor produz JSON conforme esquema versionado
        -> emissor pode validar com o mesmo esquema
        -> evento/notificação ao gateway
        -> registro da chamada: UUIDv7 recebido e timestamp de recepção em milissegundos
        -> validação do JSON pelo gateway
            -> inválido: erro de validação ao emissor
            -> válido
                -> reutilização do ID fornecido pelo emissor
                -> solicitação normalizada na inbox compartilhada
                -> escalonador global usado também pelo Telegram
                -> invocação na mesma instância App Server
                -> entrega ou retorno pelo canal obrigatório
                    -> destino ausente ou inválido no JSON: não chega à execução
                    -> destino inexistente, não encontrado ou com falha: registro em log
                    -> falha operacional registrada: notificação pelo canal de incidentes, se configurado
```

O bloco iniciado pela decisão de envolver o agente é uma proposta de decomposição conceitual do gateway. O caminho de falha sem chamador aguardando já expressa uma decisão funcional, mas o diagrama ainda não determina componentes, processos, filas, formatos de armazenamento, garantias de entrega, o catálogo de ações do agendador nem o contrato técnico das entradas do gateway.

### 1. Acontecimento na origem e decisão de notificar

Uma origem poderá detectar um e-mail, mensagem, webhook ou outro fato e tratá-lo integralmente sem emitir evento ao gateway. Somente quando decidir que o agente deve participar ela produzirá a notificação entregue ao gateway. Exemplos de emissores considerados até agora incluem:

- agendamento que escolhe emitir um evento;
- integração que selecionou um e-mail ou uma mensagem para processamento pelo agente;
- webhook ou outra interface que solicita deliberadamente a participação do agente;
- script local que precisa encaminhar informação ao agente;
- futuras integrações capazes de notificar o gateway.

Na fase inicial, qualquer origem com acesso ao ingresso privado/local poderá emitir uma notificação, sem cadastro, autenticação ou autorização de emissor; basta que o JSON passe pelo esquema. Isso não autoriza endpoint externo. Webhook ou outra publicação futura dependerá de nova decisão de segurança e autenticação. A presença do agendador nessa lista representa somente uma integração possível, e não torna todo disparo agendado um evento.

### 2. Notificação normalizada ao agente

O gateway receberá a notificação em JSON. Um esquema versionado definirá a estrutura e o padrão válido do evento; a versão do esquema deverá estar representada ou ser identificável para permitir evolução do contrato.

Antes de invocar o agente, o gateway validará a mensagem contra o esquema aplicável. Uma mensagem fora do padrão produzirá erro de validação. Quem cria ou emite o evento também poderá usar o mesmo esquema para validar previamente o JSON produzido.

O envelope v1 possui estes campos obrigatórios:

- `schema_version`: número inteiro com valor `1`;
- `event_id`: UUID versão 7 fornecido pelo emissor;
- `prompt`: texto livre não vazio para o agente;
- `response_channel`: objeto aninhado com `type` igual a `telegram` ou `email` e `value` obrigatório;
- `thread_policy`: literal `reuse` ou `new`.

Tipo de evento, payload separado, timestamp, parâmetros adicionais, proveniência e correlações adicionais não foram aprovados como campos da v1 e permanecem como dúvidas para versões futuras. Também não foi escolhida a tecnologia do esquema: JSON Schema, OpenAPI, esquema próprio ou outra alternativa continuam em avaliação. Esse envelope descreve uma solicitação de processamento pelo agente, não um acontecimento externo genérico.

O prompt será texto livre e não precisará referenciar um template. Não haverá, por enquanto, limite funcional máximo definido para seu tamanho. Eventuais limites técnicos de implementação permanecem para discussão futura e não alteram essa decisão funcional.

O identificador será um UUID versão 7 conforme a [RFC 9562](https://www.rfc-editor.org/rfc/rfc9562.html), fornecido pelo emissor e reutilizado pelo daemon em todo o processamento, entrega, diagnóstico e logs. A escolha oferece um identificador padronizado, descentralizado e ordenável temporalmente, adequado a índices, listagens cronológicas e investigação de logs sem contador central. O daemon não criará um segundo ID de execução. Em caso de reenvio do mesmo evento lógico, o emissor deverá preservar o UUIDv7 original. Registros operacionais poderão distinguir tentativas associadas ao mesmo ID, sem introduzir nova identidade obrigatória.

O UUIDv7 servirá para correlação, rastreamento e logs, não como chave de idempotência. O daemon não manterá cache nem histórico de resultados para deduplicar eventos, não retornará um resultado anterior por causa do ID e não exigirá persistência de resultados para essa finalidade. Se o mesmo UUIDv7 for recebido novamente, o evento será processado outra vez e uma nova resposta será produzida e entregue. O emissor deverá considerar que reenviar repete o processamento.

Em cada chamada recebida, o daemon registrará o UUIDv7 fornecido pelo chamador e agregará um timestamp do momento da recepção ou chamada com precisão de milissegundos. Assim, chamadas repetidas com o mesmo UUIDv7 terão ocorrências operacionais separadas sem ID de execução adicional e sem deduplicação. A dupla UUIDv7 e timestamp não foi definida como chave única. Formato do timestamp, timezone e uso de relógio monotônico ainda não foram decididos.

### 3. Decisão na origem

Antes de notificar o gateway, a origem poderá avaliar o acontecimento e decidir seu tratamento. A intenção atual admite pelo menos estes resultados:

| Resultado | Comportamento pretendido |
| --- | --- |
| Ignorar | Encerrar o tratamento na origem sem emitir notificação ao gateway. |
| Arquivar | Preservar o item conforme a capacidade e a política da origem, sem exigir agente. |
| Excluir | Exigir regra explícita e proteção adicional; nunca ser o padrão implícito nem consequência do gateway. |
| Executar localmente | Rodar script ou outra lógica na origem sem envolver o agente. |
| Notificar o agente | Emitir um evento ao gateway para solicitar processamento pelo agente. |

Ainda não foi definido onde essa decisão residirá em cada integração, como seus critérios serão declarados nem quais responsabilidades serão comuns ou específicas da origem.

### 4. Invocação do agente e contexto

Ao receber uma notificação válida, o ingresso a persistirá na mesma inbox do gateway de execução usada pelo Telegram. O gateway deverá invocar o agente pela mesma instância App Server; um adaptador Codex CLI só poderá ser considerado se preservar a mesma autoridade de admissão. A execução respeitará `thread_policy`:

- `reuse`: usar a thread vigente associada ao `response_channel`;
- `new`: iniciar uma nova thread para o evento e, depois da conclusão dessa troca, substituir a associação anterior do mesmo destino.

Depois da substituição, eventos posteriores com `reuse` continuarão na nova thread, e a anterior não será reutilizada automaticamente. Ainda não foram definidos exclusão da thread anterior, comportamento se a criação ou o primeiro turno falhar antes de concluir a troca, retenção nem retomada após falha.

Telegram e eventos compartilharão a mesma inbox, um único escalonador e o limite global de execuções simultâneas do Codex já configurado no daemon. O escalonador manterá o conjunto de destinos em execução e examinará os itens pendentes por prioridade e ordem de chegada. Quando um candidato usar destino ativo, ele permanecerá pendente e será pulado temporariamente para permitir a seleção do próximo elegível; voltará a ser considerado após nova oportunidade ou liberação do destino. Não haverá filas separadas por origem/canal nem bloqueio de cabeça de fila.

Telegram terá prioridade sobre eventos do gateway pendentes. A prioridade preservará FIFO entre itens comparáveis e elegíveis e não interromperá execução já iniciada. Tráfego contínuo do Telegram poderá causar starvation dos eventos do gateway; quota, aging ou outra compensação não foram aprovados.

O limite compartilhado representa turnos do Codex. Depois da terminalidade confirmada, o slot global pode ser liberado, enquanto o destino continua ativo até a entrega e o encerramento do trabalho. Se a terminalidade ficar incerta, o slot permanece retido até reconciliação ou perda comprovada do processo. Essas regras já existem no caminho Telegram e devem ser preservadas na extração do núcleo multiorigem.

### 5. Destino de resposta

O destino de resposta será representado pelo objeto aninhado `response_channel`:

- `type`: literal `telegram` ou `email`;
- `value`: valor obrigatório usado para entregar a resposta.

Para Telegram, a configuração permitirá escolher entre destinos já registrados, como canal, grupo ou thread. O agendamento ou a notificação ao gateway armazenará diretamente o ID do destino escolhido, e não uma referência indireta ou a posição ocupada na lista de seleção.

Para e-mail, o valor armazenado será o endereço de e-mail para o qual será enviada a mensagem com o resultado.

> [!IMPORTANT]
> O destino de resposta pertence ao resultado funcional do agendamento ou da notificação ao agente. Ele é diferente do canal operacional global destinado a incidentes, problemas de entrega e logs.

Ainda não foram definidos o formato do ID, as regras específicas de validação dos valores, múltiplos destinatários ou migração quando um canal registrado for removido.

O canal de saída será obrigatório no evento. A ausência ou invalidade do tipo ou do valor fará o JSON falhar na validação do esquema e ser rejeitado com erro de validação.

Quando os campos estiverem presentes, mas o destino informado não existir mais, não for encontrado ou falhar durante a entrega, a ocorrência será uma falha operacional. O daemon a registrará no log conforme a política já definida e usará o canal operacional de incidentes quando ele estiver configurado.

### 6. Entrega ou retorno

O resultado será entregue pelo canal de saída obrigatório e poderá também ser devolvido diretamente ao originador que estiver aguardando. Os tipos iniciais de canal são:

- Telegram;
- e-mail;

Outros adaptadores ainda não foram definidos. A possibilidade de o chamador aguardar a resposta é um comportamento da execução e não substitui os campos obrigatórios do canal de saída no evento.

O gateway manterá uma única semântica de execução: o evento continuará em processamento síncrono enquanto aguarda a resposta do App Server ou do Codex CLI. O chamador poderá permanecer aguardando o resultado ou decidir não acompanhar nem consumir essa resposta.

Quando o chamador não acompanhar a resposta, o daemon ainda concluirá o processamento e tentará entregar o resultado ao canal configurado. Se o destino informado não existir, não for encontrado ou falhar na entrega, o daemon registrará a falha operacional em arquivo de log. Quando não houver um chamador aguardando para receber o erro, esse registro preservará o diagnóstico e poderá conter integralmente:

- a identificação do chamador;
- o identificador único do evento;
- o timestamp de recepção agregado pelo daemon com precisão de milissegundos;
- os dados de entrada do evento;
- a saída ou o resultado produzido;
- o erro ou a falha ocorrida.

O timeout de execução não será novo: eventos reutilizarão `turn_timeout_seconds` e `interrupt_grace_seconds` do gateway vigente, inclusive o estado incerto quando a interrupção não confirma terminalidade. Permanecem indefinidos o contrato comum dos adaptadores, respostas parciais, tentativas/falhas de entrega, o mecanismo pelo qual o chamador local acompanha o resultado e a possibilidade de cancelamento pelo chamador.

> [!NOTE]
> A opção do chamador de não esperar pela resposta não transforma o gateway em assíncrono nem cria um segundo modo de execução. O RPC local atual atende uma chamada por vez e usa timeout curto no Supervisor, portanto não pode aguardar um turno longo sem bloquear o serviço. Ainda precisam ser definidos o acompanhamento da resposta, a eventual retenção do resultado e o cancelamento; não deve ser criado um timeout de evento para contornar essa lacuna.

> [!IMPORTANT]
> Na premissa atual, a instância e seus dados são acessíveis somente ao próprio dono da informação. Por isso, não há categorias de conteúdo proibidas no log por privacidade e não existe requisito funcional de redaction ou mascaramento. Essa premissa de acesso exclusivo do proprietário fundamenta a permissão de registrar integralmente entrada, saída e falha.

Os arquivos e registros de log do gateway terão retenção de 20 dias. Quando ultrapassarem 20 dias, serão excluídos automaticamente.

> [!CAUTION]
> A exclusão automática após 20 dias é uma política restrita ao ciclo de vida desses logs. Ela não autoriza excluir e-mails, mensagens, acontecimentos na origem nem qualquer outro conteúdo processado ou referenciado.

O gateway terá uma configuração padrão para um canal de notificações operacionais e incidentes. Quando um evento tiver problema na resposta ou na entrega, ou produzir a ocorrência de falha registrada em log, o daemon poderá enviar a esse canal uma notificação do problema ou do log.

> [!IMPORTANT]
> O canal de notificações operacionais é distinto do canal de resposta do evento. Ele não deverá receber a resposta funcional normal produzida pelo evento.

O canal operacional será opcional, mas deverá ser configurável pelo sistema de setup existente do Onmyōji, no contexto e menu próprios do gateway dentro da configuração do daemon. A localização exata dentro desse contexto ainda não foi definida. Se o canal não estiver configurado, a ocorrência continuará registrada no log local; sua ausência não impedirá nem condicionará o setup ou a execução de eventos.

> [!NOTE]
> Esta é uma decisão de concepção, não uma autorização para alterar os scripts de setup agora. A implementação futura dessa interação deverá consultar e obedecer às [convenções da interface de setup](../setup-interface.md).

Uma CLI isolada, variável de ambiente ou edição manual de arquivo não está definida como interface principal para essas configurações.

Ainda não foram decididos os adaptadores aceitos, o formato e o conteúdo exato da notificação, tentativas de envio, o comportamento caso o próprio canal operacional falhe, a opção exata no contexto do gateway no setup, a tela ou o prompt, um adaptador padrão nem o armazenamento da configuração.

## Exemplo exploratório: triagem de e-mail

Um serviço integrado ao MCP de e-mail poderia verificar uma caixa periodicamente. Para cada mensagem encontrada, aplicaria filtros por remetente ou por outras condições e decidiria se resolve o acontecimento na própria origem ou se solicita a participação do agente pelo gateway.

Possíveis resultados desse exemplo:

- ignorar uma mensagem fora das regras, sem emitir evento;
- arquivar uma mensagem que não exige agente, sem emitir evento;
- executar um script local e concluir o tratamento na origem;
- excluir somente quando uma regra específica, explícita e protegida autorizar a operação, sem atribuir a exclusão ao gateway;
- emitir uma notificação ao gateway quando a mensagem exigir processamento pelo agente e entregar o resultado por e-mail ou por outro canal escolhido.

> [!NOTE]
> O exemplo ilustra a fronteira entre acontecimento na origem e notificação ao agente. Ele não fecha a periodicidade, a API de e-mail, os filtros, a política de leitura, o modelo de autorização ou a semântica de arquivamento e exclusão.

## Scripts e MCP futuro

Uma possibilidade para evolução é oferecer um MCP pelo qual o agente possa criar ou registrar scripts e associá-los ao serviço de agendamento. Nesse desenho preliminar, o uso do gateway permanece opcional:

1. o script é registrado para execução local;
2. o agendador dispara o script segundo a configuração;
3. o script pode concluir seu trabalho sem envolver o agente;
4. somente se precisar da participação do agente, o script emite uma notificação ao gateway;
5. quando aplicável, recebe um retorno síncrono ou por um canal configurado.

Esse exemplo não torna o gateway parte obrigatória do agendador: o script poderá concluir seu trabalho sem emitir evento. Ainda devem ser discutidos os limites de criação e alteração de scripts, permissões, isolamento, validação, auditoria, tempo máximo de execução e tratamento de resultados.

## Propostas preliminares, não decididas

- Separar conceitualmente acontecimento na origem, decisão de envolver o agente, normalização da notificação, invocação e entrega.
- Definir campos adicionais somente em versão futura, se houver necessidade comprovada.

Esses pontos são direção de investigação. Podem mudar quando forem definidas as garantias operacionais e as fronteiras futuras de exposição externa.

## Pendências prioritárias e outras dúvidas

As próximas lacunas devem ser discutidas prioritariamente, sem decisão antecipada neste rascunho:

1. mecanismo de acompanhamento da resposta no transporte local e cancelamento pelo chamador;
2. persistência e retomada após reinício do daemon;
3. tentativas de entrega do resultado;
4. ciclo de vida e falhas das threads;
5. contrato próprio do agendador, independente do contrato do gateway.

### Contrato da notificação ao agente

- Se uma versão futura precisar de campos adicionais, quais justificam uma v2?
- Como sinalizar conteúdo não confiável e impedir que dados externos sejam tratados como instruções privilegiadas?
- Qual será o contrato técnico para uma origem notificar o gateway por webhook ou outra interface?
- Qual tecnologia representará o esquema versionado: JSON Schema, OpenAPI, esquema próprio ou outra?
- Como versões compatíveis ou incompatíveis serão tratadas durante a evolução do contrato?
- Como o mesmo esquema será disponibilizado ao gateway e aos emissores?
- Qual será a representação do erro de validação devolvido ao emissor?

### Agendador

- Quais ações o agendador poderá disparar além de notificar o gateway?
- Como serão representados modelos e definições atuais na listagem?
- Quais outras colunas ou atributos serão exibidos?
- Quais atalhos selecionarão cada coluna como critério de ordenação?
- Haverá direção ascendente e descendente, e como ela será escolhida?
- Quais serão os nomes finais, larguras e regras de paginação da listagem?
- A listagem interativa será classificada como menu navegável ou apresentação em fluxo conforme `docs/setup-interface.md`?

### Operação e confiabilidade do gateway

- As notificações ao agente serão persistidas antes da invocação? Em quais situações?
- Quais estados e garantias de entrega serão necessários?
- Qual será o ciclo de vida da execução quando o chamador optar por não esperar nem acompanhar a resposta?
- O resultado será retido quando o chamador não o consumir? Se for, por quanto tempo e para quem ficará disponível?
- Como o chamador poderá acompanhar a resposta sem bloquear o RPC serial e o lock de encaminhamento do Supervisor durante todo o turno?
- O chamador poderá solicitar cancelamento, ou somente abandonar o acompanhamento sem cancelar o trabalho aceito?
- Como tratar retomada e falhas parciais, preservando `turn_timeout_seconds` e `interrupt_grace_seconds` já existentes?
- Qual será o formato do timestamp de recepção, seu timezone e a eventual relação com relógio monotônico?
- Qual será o formato, o caminho, a periodicidade da limpeza, o mecanismo de rotação e o eventual limite de tamanho dos logs de falha?

### Decisão na origem e segurança

- Onde residirão os critérios que decidem tratar um acontecimento localmente ou notificar o agente?
- Como esses critérios serão declarados, ordenados, validados e testados em cada integração?
- Quais autorizações serão exigidas para criar agendamentos, scripts e critérios de seleção?
- Que proteção adicional tornará uma exclusão automática na origem deliberada e segura?
- Quais limites de recursos e isolamento serão aplicados a scripts locais?
- Se houver proposta futura de exposição externa, qual segurança e autenticação serão exigidas antes de publicá-la?

### Threads e canais

- O que caracteriza a thread associada a cada tipo de canal?
- Quando uma associação deixa de ser válida ou precisa ser recriada?
- Como tratar falha de criação ou do primeiro turno `new` antes da conclusão da troca, sem definir implicitamente exclusão ou rollback?

### Notificações operacionais

- A configuração do canal de notificações operacionais será somente global ou poderá ser sobrescrita por notificação do gateway ou por origem?
- Em qual opção do contexto próprio do gateway essa configuração ficará no setup do daemon?
- Como o setup apresentará e armazenará essa configuração opcional, respeitando `docs/setup-interface.md`?

## Próximos passos

1. Definir o mecanismo de acompanhamento da resposta no transporte local e o cancelamento pelo chamador, sem criar executor ou timeout paralelos.
2. Definir persistência e retomada após reinício do daemon.
3. Definir tentativas de entrega do resultado.
4. Definir falhas e ciclo de vida das threads.
5. Definir o contrato próprio do agendador sem pressupor uso obrigatório do gateway.
6. Decidir se a configuração do canal de notificações operacionais será somente global ou poderá ser sobrescrita por notificação do gateway ou por origem.
7. Completar o desenho da listagem ordenável de agendamentos e decidir seus detalhes de interação.
8. Escolher a tecnologia do esquema JSON versionado e detalhar sua evolução, distribuição e erros de validação.
9. Delimitar responsabilidades entre origem, gateway, invocação do agente e adaptadores de entrega.
10. Converter propostas em decisões explícitas antes de iniciar um plano de implementação.

## Registro evolutivo

Esta seção acompanha a evolução do rascunho sem transformá-lo em documentação definitiva. Uma entrada deve indicar se apenas registrou uma ideia, corrigiu entendimento, tomou uma decisão ou descartou uma hipótese.

| Data | Estado | Registro |
| --- | --- | --- |
| 2026-11-09 | Concepção inicial | Registradas as intenções de agendamento, notificação ao agente, estratégias de thread, canais de saída e integração futura por MCP. Nenhuma arquitetura de implementação foi aprovada. |
| 2026-11-09 | Decisão funcional | A execução da notificação recebida pelo gateway será síncrona e aguardará a resposta do App Server ou do Codex CLI. O chamador poderá esperar ou não acompanhar nem consumir a resposta, sem que isso crie um modo assíncrono no gateway. Ciclo de vida, retenção do resultado, timeout e cancelamento permanecem em aberto. |
| 2026-11-09 | Decisão funcional | Sem um chamador acompanhando a resposta, o daemon ainda concluirá o processamento pelo agente e tentará entregar o resultado ao canal configurado. Se a entrega falhar e ninguém estiver aguardando para receber o erro, registrará a falha em arquivo de log. Para diagnóstico e resolução futura, o log poderá conter integralmente identificação do chamador, entrada da notificação, saída ou resultado e erro ou falha. Não há categorias proibidas por privacidade sob a premissa de acesso exclusivo do proprietário. |
| 2026-11-09 | Decisão funcional | Os arquivos e registros de log do gateway terão retenção de 20 dias e serão excluídos automaticamente ao ultrapassar esse prazo. A política se aplica somente aos logs e não autoriza excluir e-mails, mensagens, acontecimentos na origem ou outros conteúdos. |
| 2026-11-09 | Decisão funcional | O gateway terá uma configuração padrão para um canal de notificações operacionais e incidentes. Problemas de resposta ou entrega e falhas registradas em log poderão gerar uma notificação do problema ou do log nesse canal, que será distinto do canal de resposta e não receberá a resposta funcional normal do agente. |
| 2026-11-09 | Decisão funcional | O canal operacional será opcional, mas configurável pelo setup do Onmyōji. Sem configuração, a ocorrência permanecerá no log local, sem tornar o setup ou a execução dependentes do canal. A interação futura deverá obedecer a `docs/setup-interface.md`; seu desenho e armazenamento ainda não foram definidos. |
| 2026-11-09 | Decisão de integração | O agendador e o gateway terão contextos e menus distintos na configuração do daemon; não haverá submenu combinado. Uma CLI isolada, variável de ambiente e edição manual de arquivo não foram definidas como interface principal. Qualquer interação futura deverá seguir `docs/setup-interface.md`. |
| 2026-11-09 | Correção arquitetural | Agendador e gateway são capacidades distintas ligadas por composição. Um agendamento pode notificar o gateway ou executar outra ação; o gateway também pode ser notificado por outras origens. Nenhum dos dois depende obrigatoriamente do outro. |
| 2026-11-09 | Correção conceitual | Evento do gateway passou a significar uma solicitação deliberada para envolver o agente, não qualquer acontecimento externo. Origens podem tratar e-mails, mensagens, webhooks ou outros fatos integralmente sem gateway. |
| 2026-11-09 | Decisão de interface | O primeiro escopo do menu do agendador será somente listar agendamentos existentes, modelos e definições atuais. Criação e edição não foram aprovadas. A futura interação deverá obedecer a `docs/setup-interface.md`. |
| 2026-11-09 | Decisão de interface | A listagem terá colunas ou atributos exibíveis, e qualquer coluna poderá ser escolhida como critério de ordenação por atalho. Os primeiros atributos mencionados são próxima execução, criação, canal associado e política de thread; teclas, direção, nomes finais, layout, paginação e demais campos permanecem em aberto. |
| 2026-11-09 | Decisão funcional | O destino de resposta será representado por tipo e valor do canal. Os tipos iniciais serão Telegram e e-mail: Telegram armazenará diretamente o ID do canal registrado escolhido, e e-mail armazenará o endereço destinatário. Esse destino funcional é distinto do canal operacional global de incidentes e logs. |
| 2026-11-09 | Correção de contrato | O canal de saída passou a ser obrigatório no evento. Ausência ou invalidade de tipo ou valor causa rejeição por erro de validação; destino informado que não existe, não é encontrado ou falha na entrega produz falha operacional, log e notificação pelo canal de incidentes quando configurado. |
| 2026-11-09 | Decisão de contrato | Qualquer integração cadastrada poderá emitir notificações ao gateway. A mensagem será JSON validado contra um esquema versionado identificável; entradas inválidas produzirão erro de validação, e emissores poderão usar o mesmo esquema preventivamente. A tecnologia do esquema ainda não foi escolhida. |
| 2026-11-09 | Decisão de contrato | Os campos mínimos conceituais do evento serão versão do esquema, identificador UUID versão 7 fornecido pelo emissor, prompt para o agente, tipo e valor do canal de saída e política de thread para reutilizar a thread associada ao destino ou forçar uma nova. Nomes JSON finais e outros campos ainda não foram definidos. |
| 2026-11-09 | Decisão de contrato | O prompt do evento será texto livre, sem referência obrigatória a template e sem limite máximo funcional definido neste momento. Eventuais limites técnicos permanecem assunto de implementação futura. |
| 2026-11-09 | Decisão de identidade | O emissor fornecerá o identificador único do evento e o manterá em reenvios da mesma solicitação. O daemon reutilizará esse ID em processamento, entrega, diagnóstico e logs, sem criar outro ID de execução; tentativas poderão ter registros operacionais próprios associados à mesma identidade. |
| 2026-11-09 | Decisão de identidade | O identificador do evento será UUID versão 7, conforme a RFC 9562. A escolha é padronizada, descentralizada e ordenável temporalmente, favorecendo índices, listagens cronológicas e investigação de logs sem contador central. |
| 2026-11-09 | Decisão de idempotência | O UUIDv7 será usado para correlação, rastreamento e logs, não para deduplicação. Cada recebimento será processado novamente e produzirá nova resposta, inclusive quando repetir o mesmo ID; o daemon não manterá nem retornará resultado anterior para esse fim. |
| 2026-11-09 | Decisão de rastreamento | Cada chamada será registrada com o UUIDv7 fornecido e um timestamp de recepção agregado pelo daemon com precisão de milissegundos. Chamadas com o mesmo ID terão registros separados, sem ID adicional e sem definir a dupla como chave única. |
| 2026-11-09 | Decisão de contrato | O envelope JSON v1 foi fechado com cinco campos obrigatórios: `schema_version` inteiro constante `1`, `event_id` UUIDv7, `prompt` não vazio, `response_channel` aninhado com `type` (`telegram` ou `email`) e `value`, e `thread_policy` (`reuse` ou `new`). Nenhum outro campo integra a v1. |
| 2026-11-09 | Decisão de ingresso | A fase inicial será estritamente privada/local, sem endpoint externo e sem autenticação, cadastro ou autorização de emissores. Qualquer evento válido pelo esquema será aceito. Exposição externa dependerá de decisão futura específica sobre segurança e autenticação. |
| 2026-11-09 | Decisão de thread | `new` cria uma nova thread para o evento e, após a conclusão da troca, substitui a associação vigente do mesmo destino. O próximo `reuse` usa a nova thread; a anterior deixa de ser associação ativa e não é reutilizada automaticamente. Falha antes da conclusão continua sem política decidida. |
| 2026-11-09 | Decisão de concorrência | Telegram e eventos compartilharão um único escalonador e o limite global do Codex. Destinos ativos serão pulados temporariamente sem bloquear os próximos elegíveis; FIFO será preservado entre comparáveis. Telegram terá prioridade sem preempção, com risco aceito de starvation de eventos. |
| 2026-11-09 | Correção arquitetural | Evento não terá executor paralelo: será outra origem do mesmo gateway de execução usado pelo Telegram, compartilhando inbox persistente, escalonador, App Server, limite, timeout, interrupção e terminalidade. Diferem apenas contrato de entrada, instruções/capacidades e entrega. |
| 2026-11-09 | Decisão de lifecycle | O processo compartilhado poderá manter o ingresso de eventos ativo sem ativar a recepção Telegram. Origens e adaptadores têm habilitação própria, mas não instanciam outro núcleo de execução. |
| 2026-11-09 | Diagnóstico técnico | O caminho Telegram já usa `turn_timeout_seconds` e `interrupt_grace_seconds` e retém o permit quando falta terminalidade; esses valores serão reutilizados. O RPC local atual é serial e tem timeout curto no Supervisor, por isso o acompanhamento de uma resposta longa permanece uma decisão aberta, sem criação de timeout próprio do evento. |

### Decisões consolidadas

#### Fronteiras arquiteturais

- O agendador e o gateway de notificações ao agente serão capacidades distintas do daemon.
- A relação entre ambos será de composição e reutilização, sem dependência obrigatória nem identidade arquitetural.
- O agendador poderá notificar o gateway ou executar scripts e outras ações que não o utilizem.
- O gateway poderá receber notificações de agendamentos, webhooks, interfaces, scripts e integrações.
- Na fase inicial, qualquer origem com acesso ao ingresso privado/local poderá emitir uma notificação válida, sem autenticação, cadastro ou autorização de emissor.
- A fase inicial não publicará endpoint externo. Webhook ou outra exposição externa exigirá nova decisão específica de segurança e autenticação.
- Um acontecimento detectado na origem não será automaticamente um evento do gateway.
- Somente a decisão de solicitar participação do agente produzirá uma notificação ou um evento para o gateway.
- O gateway não será um receptor ou barramento genérico de todos os acontecimentos externos.
- Agendador e gateway terão contextos e menus separados no setup do daemon; não haverá submenu combinado “Eventos e agendamentos”.

#### Interface inicial do agendador

- O contexto do agendador no setup inicialmente permitirá somente listar agendamentos existentes, seus modelos e definições atuais.
- Criação e edição não fazem parte desse primeiro escopo aprovado.
- A listagem será estruturada em colunas ou atributos que permitam compreender os agendamentos.
- Qualquer coluna exibida poderá ser selecionada como critério de ordenação por um atalho na tela.
- Os primeiros atributos mencionados são próxima execução, data e hora de criação, canal associado ao disparo ou ao resultado e política de thread para criar nova ou reutilizar existente.
- O canal associado será entendido inicialmente como o tipo e o valor do destino de resposta; quando exibidos como colunas, esses dados também poderão ser usados para ordenação.
- Teclas, direção da ordenação, nomes finais, larguras, paginação e conjunto completo de campos ainda não foram definidos.
- A interação futura deverá consultar e obedecer a `docs/setup-interface.md`, inclusive a distinção entre menu navegável e apresentação em fluxo; nenhuma implementação foi autorizada agora.

#### Contrato e validação do gateway

- O gateway receberá as notificações em JSON.
- Um esquema versionado definirá a estrutura e o padrão válido do evento.
- A versão do esquema deverá estar representada ou ser identificável para permitir evolução do contrato.
- O envelope v1 terá exatamente os campos obrigatórios `schema_version`, `event_id`, `prompt`, `response_channel` e `thread_policy`.
- `schema_version` será inteiro constante `1`; `event_id` será UUIDv7; `prompt` será texto livre não vazio.
- `response_channel` permanecerá aninhado e conterá obrigatoriamente `type` (`telegram` ou `email`) e `value`.
- `thread_policy` aceitará somente `reuse` ou `new`.
- O UUIDv7 seguirá a [RFC 9562](https://www.rfc-editor.org/rfc/rfc9562.html).
- A escolha do UUIDv7 fornece identidade padronizada, descentralizada e ordenável temporalmente, adequada a índices, listagens cronológicas e investigação de logs sem contador central.
- O daemon reutilizará o identificador fornecido pelo emissor durante processamento, entrega, diagnóstico e logs.
- O daemon não criará um segundo ID de execução.
- Em reenvios da mesma solicitação, o emissor deverá manter o mesmo identificador.
- Tentativas poderão ser diferenciadas por registros operacionais associados ao mesmo ID, sem nova identidade obrigatória.
- O UUIDv7 não será chave de idempotência nem impedirá reprocessamento.
- O daemon não manterá cache ou histórico de resultados para deduplicar eventos pelo ID e não retornará resultado anterior por esse motivo.
- Cada recebimento será processado novamente e produzirá uma nova resposta, mesmo quando o UUIDv7 já tiver sido recebido.
- O emissor deverá considerar que um reenvio repete o processamento.
- O prompt será texto livre e não exigirá referência a template.
- Não haverá, por enquanto, limite funcional máximo definido para o tamanho do prompt; eventuais limites técnicos permanecem para implementação futura.
- `reuse` usará a associação vigente do destino. `new` criará uma nova thread e, após concluir a troca, substituirá a associação; o próximo `reuse` usará a nova thread e a anterior não será retomada automaticamente.
- O gateway validará a mensagem contra o esquema antes de invocar o agente.
- Uma mensagem fora do padrão, inclusive sem tipo ou valor válido de canal, produzirá erro de validação para o emissor e não invocará o agente.
- Quem cria ou emite a notificação poderá usar o mesmo esquema antecipadamente para verificar o JSON produzido.
- A tecnologia do esquema ainda não foi definida; JSON Schema, OpenAPI, esquema próprio e outras alternativas permanecem possíveis.
- Campos adicionais não integram a v1; eventual ampliação exigirá evolução do esquema.

#### Execução e entrega pelo gateway

- Receber um evento no gateway significará solicitar que o agente processe a informação.
- O evento será outra origem do mesmo gateway de execução usado pelo Telegram, e não um executor paralelo.
- Telegram e eventos compartilharão a mesma inbox persistente, o mesmo escalonador, a mesma instância App Server, o mesmo limite global e o mesmo ciclo de terminalidade.
- O ingresso de eventos poderá ser habilitado sem ativar a recepção Telegram; essa independência de adaptadores não cria outro processo executor.
- O gateway poderá futuramente usar Codex CLI somente se ele permanecer sob a mesma autoridade de admissão e oferecer contrato equivalente.
- A execução será síncrona e permanecerá aguardando a resposta do App Server ou do Codex CLI.
- O chamador poderá escolher esperar pela resposta ou não a acompanhar nem consumi-la.
- A escolha de não esperar não criará um modo assíncrono no gateway.
- Quando o agente for invocado, será possível reutilizar a thread vigente associada ao canal de saída ou criar uma nova que substitui a associação após a conclusão da troca.
- Sem acompanhamento do chamador, o daemon ainda concluirá o processamento e tentará entregar o resultado ao canal configurado.
- O mesmo identificador do evento acompanhará a invocação, a entrega e eventuais tentativas registradas.
- Recebimentos repetidos com o mesmo UUIDv7 iniciarão novo processamento e produzirão nova resposta.
- O destino de resposta será o objeto aninhado obrigatório `response_channel`, com `type` e `value`.
- Os tipos iniciais considerados serão Telegram e e-mail.
- Para Telegram, a configuração permitirá escolher um destino já registrado, como canal, grupo ou thread, e o agendamento ou a notificação armazenará diretamente o ID escolhido, não uma referência indireta nem sua posição na lista.
- Para e-mail, o valor armazenado será o endereço ao qual será enviada a mensagem com o resultado.
- O destino de resposta será distinto do canal operacional global de incidentes e logs.
- O tipo e o valor do canal de saída serão obrigatórios no evento.
- Campos de canal ausentes ou inválidos farão o JSON falhar na validação do esquema e ser rejeitado.
- Se os campos estiverem presentes, mas o destino não existir mais, não for encontrado ou falhar na entrega, a ocorrência será tratada como falha operacional.
- Um escalonador único selecionará pendentes por prioridade e chegada, pulará destinos ativos e reconsiderará os itens depois, sem filas separadas por origem e sem head-of-line blocking.
- Telegram terá prioridade sobre eventos pendentes sem preempção. FIFO será preservado entre itens comparáveis e elegíveis; starvation dos eventos sob tráfego contínuo do Telegram é um risco aceito sem quota, aging ou alternância aprovados.
- Eventos reutilizarão `turn_timeout_seconds` e `interrupt_grace_seconds`; terminalidade incerta continuará retendo o permit até reconciliação ou perda comprovada do App Server.

#### Logs e notificações operacionais do gateway

- Se o destino informado não existir, não for encontrado ou falhar na entrega, o daemon registrará a falha operacional em arquivo de log.
- Cada chamada recebida terá registro com o UUIDv7 fornecido pelo chamador e um timestamp do momento da recepção agregado pelo daemon com precisão de milissegundos.
- O timestamp será metadado operacional da ocorrência e não um ID de execução adicional.
- Chamadas com o mesmo UUIDv7 terão registros separados; a dupla UUIDv7 e timestamp não foi definida como chave única.
- Formato do timestamp, timezone e uso de relógio monotônico ainda não foram definidos.
- Para diagnóstico e resolução futura, o log poderá conter integralmente a identificação do chamador, o UUIDv7 do evento, o timestamp de recepção, os dados de entrada da notificação, a saída ou o resultado produzido e o erro ou a falha ocorrida.
- Não haverá categorias de conteúdo proibidas no log por privacidade, sob a premissa de que a instância e os dados são acessíveis somente ao próprio proprietário.
- Os arquivos e registros de log do gateway terão retenção de 20 dias e serão excluídos automaticamente quando ultrapassarem esse prazo.
- A política de exclusão automática se restringirá ao ciclo de vida dos logs e não autorizará excluir e-mails, mensagens, acontecimentos na origem ou outros conteúdos.
- O gateway terá uma configuração padrão para um canal de notificações operacionais e incidentes.
- Quando houver problema na resposta ou entrega, ou uma falha registrada em log, o daemon poderá enviar a esse canal uma notificação do problema ou do log.
- Um destino de saída informado que não exista, não seja encontrado ou falhe na entrega será registrado no log e poderá gerar notificação nesse canal operacional quando ele estiver configurado.
- O canal de notificações operacionais será distinto do canal de resposta e não receberá a resposta funcional normal do agente.
- O canal operacional será opcional, mas deverá ser configurável pelo sistema de setup existente do Onmyōji, no contexto próprio do gateway dentro do menu do daemon.
- Sem o canal configurado, a ocorrência permanecerá no log local, e nem o setup nem a execução dependerão desse canal.
- Uma CLI isolada, variável de ambiente ou edição manual de arquivo não será tratada como interface principal já decidida para essas configurações.
- A implementação futura da interação de configuração deverá consultar e obedecer a `docs/setup-interface.md`.

Essas são decisões funcionais e arquiteturais de concepção. O nome definitivo do gateway e a tecnologia do esquema versionado ainda não foram definidos; o envelope v1, porém, está fechado e não contém campos adicionais. As pendências prioritárias são o mecanismo de acompanhamento da resposta no transporte local, cancelamento pelo chamador, persistência e retomada, tentativas de entrega, falha de `new` antes da troca de associação e contrato próprio do agendador. Autenticação de emissores não integra a fase privada/local; só será discutida se houver proposta futura de exposição externa. Para o agendador, permanecem abertos o catálogo de ações e os detalhes da listagem já enumerados. Para os logs, permanecem em aberto formato geral, caminho, periodicidade da limpeza, mecanismo de rotação, eventual limite de tamanho, formato do timestamp, timezone e uso de relógio monotônico. Para as notificações operacionais, ainda não foram decididos adaptadores, formato, conteúdo exato, tentativas, tratamento da falha do próprio canal, opção exata no contexto do gateway, tela ou prompt, adaptador padrão nem armazenamento da configuração.
