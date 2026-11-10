# Plano de implementação do gateway de notificações e do agendador do daemon

**Estado:** plano fechado para entrega ao desenvolvimento; a publicação deste documento não implementa as capacidades descritas.

**Revisão:** 2026-11-09.

**Baseline inspecionado:** `5eed184`.

**Componente responsável:** `onmyoji-daemon`.

**Fonte funcional:** [rascunho de concepção](infraestrutura-eventos-daemon.md).

> [!IMPORTANT]
> Este documento é autocontido e separa definições do usuário, decisões técnicas deste plano, inferências sobre o código atual e perguntas bloqueantes. O rascunho de concepção permanece a fonte histórica das decisões funcionais; divergências futuras devem ser resolvidas nele antes de alterar o contrato implementado.

> [!CAUTION]
> A fase inicial é exclusivamente privada/local e não publica endpoint externo. Nesse ingresso, o receptor aceita qualquer evento que passe pela validação do contrato, sem autenticação, cadastro ou autorização de emissores. Uma exposição externa exigirá nova decisão sobre segurança e autenticação. O plano também não autoriza criar ou editar agendamentos no setup: o primeiro escopo aprovado do agendador é somente consulta e ordenação.

## Navegação

1. [Legenda de autoridade](#autoridade)
2. [Objetivo, escopo e não objetivos](#objetivo)
3. [Baseline confirmado](#baseline)
4. [Arquitetura alvo](#arquitetura)
5. [Contrato JSON e validação compartilhada](#contrato)
6. [Recepção e semântica síncrona](#recepcao)
7. [Execução pelo agente e threads](#execucao)
8. [Adaptadores de entrega](#entrega)
9. [Logs, retenção e incidentes](#logs)
10. [Persistência e recuperação](#persistencia)
11. [Ingresso inicial e publicação futura](#seguranca)
12. [Agendador como fase independente](#agendador)
13. [Setup e experiência operacional](#setup)
14. [Impacto por componente](#impacto)
15. [Fases e dependências](#fases)
16. [Primeiro corte vertical](#vertical)
17. [Testes e critérios de aceite](#testes)
18. [Migração, rollout e rollback](#rollout)
19. [Riscos e mitigação](#riscos)
20. [Perguntas bloqueantes](#bloqueios)
21. [Checklist de conclusão](#checklist)

<a id="autoridade"></a>
## 1. Legenda de autoridade

As marcações abaixo evitam promover uma hipótese técnica a requisito do usuário:

| Marcação | Significado |
| --- | --- |
| **[U] Definição do usuário** | Comportamento consolidado no rascunho de concepção e obrigatório até revisão explícita. |
| **[T] Decisão técnica do plano** | Escolha recomendada a partir da arquitetura atual. Pode ser substituída por decisão técnica equivalente sem alterar o requisito funcional. |
| **[I] Inferência a validar** | Conclusão baseada no código atual; exige teste ou prova antes de ativação em produção. |
| **[B] Bloqueio** | Resposta necessária antes da fase indicada. Não deve ser preenchida por conveniência do implementador. |

Os nomes `agent_notification` e `task_scheduler` usados neste plano são **[T] nomes de trabalho**, não nomes funcionais aprovados pelo usuário. `agent_notification` identifica o contrato e os adaptadores do novo ingresso; não identifica um segundo executor nem um segundo processo App Server. Renomeá-los antes da implementação não reabre a arquitetura, desde que os contextos continuem separados.

<a id="objetivo"></a>
## 2. Objetivo, escopo e não objetivos

### 2.1 Objetivo

Implementar duas capacidades persistentes e independentes do daemon:

1. um novo ingresso de eventos que recebe uma solicitação deliberada de participação do agente, valida seu contrato e a encaminha ao mesmo gateway de execução usado pelo Telegram, com entrega no destino obrigatório; e
2. um agendador que administra disparos temporais e pode executar ações locais ou, opcionalmente, emitir uma solicitação ao gateway.

### 2.2 Escopo obrigatório

- **[U]** Gateway e agendador são serviços distintos, ligados por composição e sem dependência obrigatória.
- **[U]** A origem pode tratar um acontecimento integralmente sem gateway.
- **[U]** O gateway recebe JSON conforme esquema versionado e compartilhável com emissores.
- **[U]** O envelope v1 contém `schema_version`, `event_id`, `prompt`, `response_channel` e `thread_policy`, com a estrutura fechada na seção 5.
- **[U]** A fase inicial aceita, somente pelo ingresso privado/local, qualquer evento que passe pela validação do contrato, sem autenticação, cadastro ou autorização de emissores.
- **[U]** Cada recebimento é processado novamente, mesmo que repita o UUIDv7.
- **[U]** Telegram e eventos entram no mesmo gateway de execução, na mesma inbox/escalonador, no mesmo App Server e sob o mesmo limite global; não existe executor paralelo para eventos.
- **[U]** A execução aguarda App Server ou Codex CLI; abandonar a resposta não cancela o trabalho aceito.
- **[U]** O resultado funcional é entregue por Telegram ou e-mail; falhas operacionais vão para log e, se configurado, canal de incidentes.
- **[U]** Logs do gateway têm retenção de 20 dias.
- **[U]** Gateway e agendador têm contextos próprios no setup.
- **[U]** A primeira interface do agendador lista definições existentes e permite ordenar por qualquer coluna exibida; não cria nem edita agendamentos.

### 2.3 Fora do escopo inicial

- barramento genérico de webhooks, e-mails ou mensagens;
- captura automática de todo acontecimento externo;
- exposição externa do receptor e mecanismos de autenticação, cadastro, permissão, rotação ou revogação de emissores;
- exclusão automática de conteúdo na origem;
- deduplicação, cache ou consulta de resultado anterior por UUIDv7;
- múltiplos destinatários no mesmo evento;
- editor de agendamentos no setup;
- catálogo definitivo de ações do agendador;
- garantia de entrega exatamente uma vez;
- novo broker externo, fila distribuída ou dependência operacional em `.github/`;
- segundo processo App Server, executor próprio, inbox paralela ou semáforo exclusivo para eventos.

<a id="baseline"></a>
## 3. Baseline confirmado

### 3.1 Componentes existentes e implicações

| Fonte vigente | Comportamento confirmado | Implicação para a implementação |
| --- | --- | --- |
| [`registry.py`](../../onmyoji-daemon/src/onmyoji_daemon/registry.py) | Registra um namespace por `ServiceSpec`; `telegram.` aponta para o processo que hoje hospeda receptor, inbox, execução e entrega. O namespace `task.` já está reservado. | **[T]** Evoluir o mesmo processo para aceitar também o namespace do novo ingresso, ou generalizar o roteamento para mais de um prefixo por serviço. Não registrar outro serviço executor. O agendador continua sendo serviço separado. |
| [`supervisor.py`](../../onmyoji-daemon/src/onmyoji_daemon/supervisor.py) | Inicia processos com diretório próprio em `configs/daemon/services/`, RPC com token efêmero, logs de stdout e recuperação de serviços habilitados. | Reutilizar ciclo de vida, estado `services.json`, temporários em `tmp/daemon/` e roteamento por namespace; não criar outro supervisor. |
| [`rpc.py`](../../onmyoji-daemon/src/onmyoji_daemon/rpc.py) | RPC local em loopback, autenticado pelo token interno do Supervisor, leitura limitada a 1 MiB e servidor que atende uma conexão por vez. O cliente usa 5 segundos por padrão; o Supervisor também serializa chamadas por serviço. | Serve para admissão local curta, mas não pode aguardar um turno longo no handler sem bloquear status, controle e shutdown. A forma pela qual o chamador acompanha a resposta é um bloqueio explícito; não resolvê-la aumentando timeout arbitrariamente. A ausência de autenticação de emissores não remove a proteção interna do transporte. |
| [`gateway/service.py`](../../onmyoji-daemon/src/onmyoji_daemon/gateway/service.py) e [`supervision/publication.py`](../../onmyoji-daemon/src/onmyoji_daemon/supervision/publication.py) | O Gateway HTTP compartilhado é proxy HTTPS; o Supervisor valida owner, prefixo, porta, TLS e geração dos listeners. | O gateway de notificações não deve ser incorporado ao proxy. O mecanismo de publicação só será considerado numa fase externa futura, após nova decisão de segurança. |
| [`codex_execution.py`](../../onmyoji-daemon/src/onmyoji_daemon/codex_execution.py) | `CodexLaunch` centraliza executável, workspace, sandbox, aprovação, instruções, skills, agentes e MCP. | Toda execução nova deve partir de `CodexLaunch`; não remontar ambiente ou comando em outro módulo. |
| [`codex_app_server.py`](../../onmyoji-daemon/src/onmyoji_daemon/codex_app_server.py) | Transporte persistente único no processo, com handshake, RPCs correlacionados, filas limitadas, eventos ordenados por thread e estados de perda. O `clientInfo` ainda identifica o Gateway Telegram. | Manter uma única instância e generalizar a identidade para o gateway multiorigem. O novo ingresso não instancia outro transporte. |
| [`telegram_execution.py`](../../onmyoji-daemon/src/onmyoji_daemon/telegram_execution.py) e [`telegram.py`](../../onmyoji-daemon/src/onmyoji_daemon/telegram.py) | Persistem a inbox, criam um worker por conversa, executam `thread/start`/`resume` e `turn/start`, esperam terminalidade, entregam a resposta e mantêm a associação. `_slots` impede sobreposição por conversa. | Extrair do caminho vigente um núcleo multiorigem no mesmo processo, mantendo receptor e entrega Telegram como adaptadores. Eventos entram na mesma persistência, no mesmo escalonador e no mesmo ciclo de execução; diferenças ficam no contrato de entrada, instruções/capacidades e entrega. |
| `GatewayStore.enqueue()`/`next_input()` em [`telegram_persistence.py`](../../onmyoji-daemon/src/onmyoji_daemon/telegram_persistence.py) | A ordem é FIFO por conversa (`sequence` por `chat_id`). Cada worker reserva o próximo item da própria conversa antes de disputar o semáforo global. | A persistência precisa ganhar origem, prioridade e sequência global estável. Um escalonador único deve escolher o próximo item elegível antes da reserva, pular destinos ativos e só então iniciar o worker admitido. |
| `Settings.load()`, `Gateway.work` e `_turn_serial()` | `agent.max_parallel_conversations` alimenta um `BoundedSemaphore` no mesmo processo. O permit é liberado após terminalidade do Codex e antes da entrega; o destino continua ocupado em `_slots` até entrega/cleanup. Se a terminalidade é incerta, o permit fica retido até reconciliação ou perda comprovada do App Server. | Preservar exatamente essas duas fronteiras: orçamento global para turnos e exclusão do destino até o fim do trabalho. Mudar a seleção, não duplicar o limite nem liberar capacidade incerta. |
| `_app_turn()` em [`telegram_execution.py`](../../onmyoji-daemon/src/onmyoji_daemon/telegram_execution.py) | Aguarda `turn_timeout_seconds`; ao vencer, solicita `turn/interrupt`, espera `interrupt_grace_seconds` e marca incerteza se não houver terminalidade. | Reutilizar esses prazos e estados para eventos. Não criar timeout próprio do novo ingresso. |
| [`telegram_channels.py`](../../onmyoji-daemon/src/onmyoji_daemon/telegram_channels.py) e [`mcp/telegram/service.py`](../../onmyoji-daemon/src/onmyoji_daemon/mcp/telegram/service.py) | Mantêm catálogo de destinos e envio externo por RPC. Sem chave de idempotência, cada chamada envia novamente; resultado incerto não é repetido automaticamente. | O adaptador Telegram deve consultar o catálogo vigente e enviar sem usar o UUIDv7 como chave de idempotência. |
| [`mcp/email/service.py`](../../onmyoji-daemon/src/onmyoji_daemon/mcp/email/service.py) e [`mcp/email/store.py`](../../onmyoji-daemon/src/onmyoji_daemon/mcp/email/store.py) | Envio SMTP exige conta, confirmação e `request_id`; a mesma chave consulta a submissão anterior e não reenvia. | O UUIDv7 sozinho não pode ser o `request_id`, pois um reenvio do evento precisa gerar nova submissão. Seleção da conta remetente permanece bloqueante. |
| [`telegram_persistence.py`](../../onmyoji-daemon/src/onmyoji_daemon/telegram_persistence.py) | SQLite versionado, lease, recuperação, estados incertos, inbox, execuções e entregas do gateway vigente. | Evoluir por migração para o estado multiorigem; não criar um segundo banco de execução que possa divergir da fila global. Logs específicos do contrato podem continuar separados do estado transacional. |
| [`setupOnmyoji.py`](../../setupOnmyoji.py) | `D. Daemon` já separa Telegram, MiniApps e Publicação externa; menus usam `screen()`, assistentes e diagnósticos usam fluxo. | Acrescentar dois contextos independentes sem combinar “eventos e agendamentos”. |
| [`docs/setup-interface.md`](../setup-interface.md) | Define moldura, fluxo, escolhas, padrões, confirmações e uso de `X`. | Toda interação nova deve ter sua classificação registrada e testada antes de implementação. |
| [`conftest.py`](../../conftest.py) | Redireciona temporários e bytecode de testes para `tmp/`. | Executar a suíte com `python -B -m pytest`; não criar temporários fora de `tmp/`. |

Os valores compartilhados vivem hoje em `configs/daemon/services/telegram/config.toml`, seção `[agent]`: `max_parallel_conversations`, `turn_timeout_seconds` e `interrupt_grace_seconds`. `setupOnmyoji.configure_parallel_responses()` edita o primeiro por `setupDaemon.py --action set-max-parallel-conversations`; a configuração de execução edita a graça de interrupção. A extração do gateway deve migrar ou reler esses valores sem duplicá-los, mantendo compatibilidade com instalações existentes e sem criar configuração paralela para eventos.

O ponto real de entrada da fila Telegram é: `_receive_update()` persiste em `ingress_updates`; `_pump_ingress()` prepara a mensagem; `_enqueue_turn()` insere em `inbox_items`; `_worker()` escolhe `next_input()` por conversa, chama `reserve()` e inicia `_execute_inbox()`; `_turn_serial()` adquire `Gateway.work` e chama `_app_turn()`. O novo evento deve convergir em `inbox_items` por uma porta neutra antes da seleção global. Ele não deve chamar `_turn_serial()` diretamente nem criar um worker fora dessa cadeia.

### 3.2 Restrições observadas

1. O processo Telegram é simultaneamente receptor conversacional, gateway de execução e entregador. A evolução deve separar responsabilidades dentro do processo, não herdar `Gateway` nem criar processo concorrente.
2. O `CodexAppServer`, a agregação de resposta, a reconciliação e os permits incertos já formam um único ciclo operacional; extraí-los para componentes neutros deve preservar uma única instância.
3. O Gateway HTTP poderia transportar uma futura interface externa, mas essa publicação e sua autenticação estão fora do escopo inicial privado/local.
4. A configuração de e-mail possui contas SMTP habilitadas, porém o evento conceitual informa somente o endereço destinatário.
5. Os logs de stdout do Supervisor não possuem rotação nem retenção de 20 dias; a política nova precisa de armazenamento próprio.
6. O RPC local atual não comporta espera longa sem bloquear todo o serviço; a interface de acompanhamento do chamador precisa ser decidida antes do corte vertical.
7. Não há contrato de agendamentos nem origem aprovada para criar suas definições.

<a id="arquitetura"></a>
## 4. Arquitetura alvo

### 4.1 Visão de componentes

```text
origem Telegram --------------------------+
                                          |
emissor pelo ingresso privado/local       |
    -> contrato JSON + validação           |
    -> registro (UUIDv7 + timestamp)       |
                                          v
                             gateway de execução único
                                 -> inbox/escalonador global
                                 -> destinos ativos
                                 -> limite max_parallel_conversations
                                 -> uma instância do App Server
                                 -> thread/turno/terminalidade
                                          |
                       +------------------+------------------+
                       v                                     v
               entrega Telegram                         entrega e-mail
                       |
               log e incidente opcional

agendador
    -> ação local independente
    -> ou emissor local do mesmo contrato de eventos

Gateway HTTP compartilhado
    -> publicação futura, fora do escopo inicial
```

### 4.2 Limites de responsabilidade

| Componente | Responsabilidade | Não deve fazer |
| --- | --- | --- |
| Contrato | Carregar versão, validar schema e normalizar erro de validação. | Autenticar, executar agente ou consultar destino. |
| Ingresso de evento | No escopo inicial privado/local, limitar transporte, decodificar JSON, registrar a recepção e inserir a solicitação na inbox vigente. | Autenticar ou cadastrar emissores, executar fora do gateway, decidir acontecimentos na origem ou expor o receptor publicamente. |
| Gateway de execução | Consumir a inbox única, resolver thread, executar no App Server compartilhado, coordenar entrega, registrar falha e acionar incidente. | Instanciar um executor por origem ou acoplar o núcleo neutro a detalhes de Telegram/SMTP. |
| Escalonador global | Compartilhar a admissão entre as duas origens, manter destinos ativos, selecionar por prioridade/chegada e impedir simultaneidade no mesmo destino. | Criar filas independentes por origem/canal, bloquear toda a fila por um candidato inelegível ou ultrapassar o limite vigente. |
| Núcleo de turno | Resolver thread, iniciar turno, aguardar terminalidade e devolver resultado dentro do gateway único. | Criar outro App Server ou entregar diretamente ao canal funcional. |
| Adaptador de saída | Validar existência operacional do destino e efetuar uma tentativa controlada. | Deduplicar eventos pelo UUIDv7. |
| Registro operacional | Preservar ocorrência e limpar somente logs vencidos. | Excluir conteúdo na origem ou servir como cache de respostas. |
| Agendador | Calcular disparos e executar a ação definida. | Ser requisito para o gateway ou transformar toda ação em evento. |

### 4.3 Organização proposta de pacotes

**[T]** Organizar por funcionalidade, sem `util` ou `common` genéricos:

```text
onmyoji_daemon/
    execution_gateway/
        scheduler.py            # seleção global por origem, chegada e destino
        execution.py            # ciclo de thread/turno extraído do gateway vigente
        repository.py           # inbox e estado transacional multiorigem
        exception/
    agent_notification/
        contract.py             # seleção de versão e validação
        ingress.py              # adaptador privado/local para a inbox única
        logging.py              # JSONL, retenção e limpeza
        delivery/
            telegram.py
            email.py
            incident.py
        exception/
        schemas/
            event-v1.schema.json
    task_scheduler/
        service.py
        repository.py
        action/
        view/
        exception/
```

`execution_gateway` representa componentes extraídos do caminho já executado pelo Telegram, não um novo serviço. O processo mantém uma única instância desses componentes e uma única instância de `CodexAppServer`. O receptor Telegram e `agent_notification.ingress` são adaptadores de entrada desse processo; as entregas são adaptadores de saída. Nomes finais podem mudar, mas nenhum pacote de notificações pode conter executor, semáforo ou fila próprios.

O lifecycle também precisa tornar-se multiorigem: o processo do gateway deve poder permanecer ativo quando o ingresso de eventos estiver habilitado mesmo que a recepção Telegram esteja desabilitada. O namespace `telegram.` e o estado habilitado existente devem ser migrados sem regressão; receptor Telegram, ingresso de eventos e adaptadores de entrega têm habilitação própria dentro do único processo. Isso não autoriza duas instâncias do núcleo.

<a id="contrato"></a>
## 5. Contrato JSON e validação compartilhada

### 5.1 Envelope funcional consolidado

| Campo v1 | Contrato **[U]** | Validação |
| --- | --- | --- |
| `schema_version` | Obrigatório. | Número inteiro com valor constante `1`. |
| `event_id` | Obrigatório; UUID versão 7 gerado pelo emissor e preservado em reenvios. | Validar sintaxe e `UUID.version == 7`; não gerar substituto. |
| `prompt` | Obrigatório; texto livre não vazio, sem template obrigatório e sem máximo funcional definido. | Limite de bytes do transporte permanece uma proteção operacional separada e ainda aberta. |
| `response_channel` | Objeto obrigatório e aninhado. | Contém obrigatoriamente apenas os campos v1 `type` e `value`. |
| `response_channel.type` | Tipo do destino funcional. | Literal `telegram` ou `email`. |
| `response_channel.value` | Valor obrigatório do destino. | A estrutura é validada no schema; existência do destino é verificação operacional posterior. |
| `thread_policy` | Política obrigatória da thread. | Literal `reuse` ou `new`. |

Exemplo canônico do envelope v1, sem campos adicionais:

```json
{
  "schema_version": 1,
  "event_id": "019a6ff0-4c3b-7a2e-8f10-123456789abc",
  "prompt": "Analise a informação recebida e produza a resposta solicitada.",
  "response_channel": {
    "type": "telegram",
    "value": "group:-1001234567890:42"
  },
  "thread_policy": "new"
}
```

> [!IMPORTANT]
> O canal permanece agrupado em `response_channel`; `type` e `value` não devem ser promovidos ao nível raiz. Campos adicionais não foram aprovados para a v1.

`thread_policy = "reuse"` continua na thread vigente associada ao par `response_channel.type` e `response_channel.value`. `thread_policy = "new"` inicia uma nova thread para o evento e, depois da conclusão dessa troca, substitui a associação anterior do mesmo destino. Eventos posteriores com `reuse` continuam nessa nova thread; a anterior deixa de ser a associação ativa e não é reutilizada automaticamente.

### 5.2 Escalonador global e exclusão por destino

- **[U]** Telegram e gateway de eventos compartilham um único escalonador/fila e o mesmo orçamento global de turnos do Codex.
- **[U]** O escalonador mantém o conjunto de canais/destinos atualmente em execução.
- **[U]** Ao selecionar o próximo trabalho, examina os itens segundo prioridade e ordem de chegada.
- **[U]** Se o candidato usa um destino ativo, ele permanece pendente e é pulado temporariamente; o escalonador procura o próximo item elegível.
- **[U]** O item pulado volta a ser considerado quando houver nova oportunidade ou liberação do destino.
- **[U]** Essa seleção impede simultaneidade no mesmo destino sem criar filas individuais por canal e sem causar bloqueio de cabeça de fila.
- **[U]** Destinos diferentes podem executar em paralelo até o limite global; quando todos os slots estão ocupados, os demais trabalhos aguardam.
- **[U]** Telegram tem prioridade sobre eventos do gateway pendentes, mas não interrompe nem cancela execução já iniciada.
- **[U]** A ordem FIFO é preservada entre itens comparáveis e elegíveis da mesma classe de prioridade.
- **[U]** Para um mesmo destino, um evento `new` conclui a substituição da associação antes de o `reuse` seguinte tornar-se elegível.

O limite existente foi confirmado no código: `agent.max_parallel_conversations` é lido por `Settings.load()` e aplicado em `Gateway.work` por `threading.BoundedSemaphore(settings.parallel)`. O setup o apresenta como “limite global de turnos simultâneos em conversas distintas”. Hoje, porém, cada conversa cria seu worker, reserva sua entrada e disputa o semáforo; essa corrida entre workers não expressa prioridade global nem permite provar a ordem entre origens.

**[T]** Antes de adicionar o ingresso de eventos, substituir a disputa entre workers por um escalonador único dentro do mesmo processo. A inbox persistente passa a registrar origem (`telegram` ou `event`), classe de prioridade e sequência global monotônica. O escalonador consulta os pendentes por prioridade e sequência, ignora temporariamente destinos presentes em `_slots`, admite até `max_parallel_conversations` e só então reserva/inicia o trabalho. Não há RPC de lease, coordenador em outro processo, segunda inbox ou segundo semáforo.

O permit global continua representando apenas um turno do Codex: é liberado após terminalidade confirmada, antes da entrega. O destino continua ativo até entrega e cleanup terminarem, impedindo o próximo item do mesmo destino. Se a terminalidade ficar incerta, o permit permanece retido em `_uncertain_permits` até reconciliação ou perda comprovada do processo, como ocorre hoje. Sucesso, falha e cancelamento conclusivos liberam o permit uma vez; nenhum adaptador de entrega libera ou adquire capacidade de turno.

Essa prioridade pode causar starvation dos eventos do gateway sob tráfego contínuo do Telegram. Não adicionar quota, aging, alternância ou limite de prioridade sem nova decisão funcional.

### 5.3 Tecnologia recomendada

- **[T]** Usar JSON Schema Draft 2020-12, já disponível por `jsonschema>=4,<5` e já usado nos contratos MCP.
- **[T]** Manter o schema como recurso versionado do pacote, com `additionalProperties: false` em cada objeto fechado.
- **[T]** Criar `EventContract` que selecione a versão, valide o próprio schema com `Draft202012Validator.check_schema()` e devolva erros estáveis por caminho.
- **[T]** Registrar um `FormatChecker` próprio para UUIDv7; o formato genérico `uuid` não garante a versão.
- **[T]** Expor a mesma função de validação para receptor, agendador, scripts locais e futuros emissores Python. O arquivo JSON é a fonte única; não manter validação manual divergente.
- **[T]** Não aplicar coerção silenciosa. Número não vira texto, espaços não são removidos de identificadores e campos desconhecidos falham.

### 5.4 Evolução do contrato

1. Uma versão publicada é imutável.
2. O receptor escolhe o validador pela versão declarada antes de ler campos dependentes dela.
3. Versão desconhecida retorna erro específico e não invoca o agente.
4. Mudança incompatível cria novo arquivo e nova fixture de conformidade.
5. O serviço pode aceitar mais de uma versão durante migração, mas normaliza para um Value Object interno.
6. Emissores recebem schema e casos válidos/inválidos sem importar módulos internos do daemon.
7. O schema não promete existência de canal, disponibilidade do agente, autenticação ou entrega.

### 5.5 Identidade e reenvio

- **[U]** O UUIDv7 é a única identidade de domínio do evento.
- **[U]** O daemon não cria segundo ID de execução nem usa o UUIDv7 como chave de idempotência.
- **[U]** Duas recepções com o mesmo UUIDv7 executam duas vezes e produzem duas respostas.
- **[T]** Chaves internas de banco, índices de linha ou tokens de submissão podem existir como detalhes de armazenamento, mas nunca são expostos como identidade do evento nem alteram a correlação apresentada ao operador.
- **[T]** Todos os registros, erros, chamadas de execução e entregas carregam o UUIDv7 recebido.

### 5.6 Erros de contrato

Definir códigos estáveis, sem traceback:

| Código proposto **[T]** | Situação |
| --- | --- |
| `invalid_json` | Corpo não é JSON decodificável ou não é objeto. |
| `schema_version_required` | Não é possível selecionar o schema. |
| `unsupported_schema_version` | Versão não suportada. |
| `event_validation_failed` | Estrutura não corresponde ao schema; inclui caminhos sanitizados. |
| `invalid_event_id` | UUID ausente, malformado ou não é versão 7. |

Os nomes dos códigos são técnicos e podem mudar antes da publicação do contrato; testes devem congelá-los quando a fase 1 for aprovada.

<a id="recepcao"></a>
## 6. Recepção e semântica síncrona

### 6.1 Ordem obrigatória

1. receber o corpo pelo ingresso privado/local;
2. aplicar o limite técnico do transporte, quando definido;
3. decodificar JSON;
4. selecionar e validar o schema;
5. capturar o timestamp de recepção definido;
6. registrar a ocorrência aceita com o UUIDv7;
7. persistir a solicitação na inbox única do gateway com origem, prioridade, sequência global e destino;
8. o escalonador vigente admitir o item e invocar o agente pelo mesmo App Server usado pelo Telegram;
9. entregar o resultado ao canal obrigatório;
10. finalizar a resposta ao chamador, se o mecanismo de acompanhamento aprovado ainda estiver ativo;
11. em falha, registrar log e tentar incidente sem recursão.

Na fase inicial não existe autenticação, cadastro ou autorização do emissor. JSON inválido deve gerar registro de rejeição conforme a política definida para logs e nunca invocar o agente.

### 6.2 Síncrono não significa dependente da conexão

**[U]** A operação lógica aguarda a resposta do agente. O chamador pode acompanhar a resposta ou abandoná-la; abandonar não cria outro modo de execução e não cancela o processamento aceito.

**[T]** Depois do passo 7, a execução pertence ao gateway, não ao socket. Cancelamento do chamador deve ser isolado do trabalho aceito. Isso exige:

- estado em memória ou persistente controlado pelo serviço;
- desligamento gracioso que pare de admitir eventos e trate os ativos conforme a política de recuperação;
- teste de desconexão antes da resposta;
- nenhuma fila pública ou endpoint de consulta de resultado apenas para simular assincronicidade.

> [!WARNING]
> O `RpcServer` atual processa uma conexão por vez, e o Supervisor segura `outbound_lock` durante a chamada encaminhada. O timeout padrão de 5 segundos é menor que `turn_timeout_seconds` (900 segundos por padrão). Portanto, o mesmo handler RPC não pode ficar aguardando a execução completa. A forma de oferecer ao chamador a opção de acompanhar a resposta permanece bloqueante; não se deve contornar a lacuna com um timeout arbitrário nem com um segundo modo de execução.

### 6.3 Resposta do ingresso

**[B]** O formato de sucesso/erro do ingresso privado e a presença do resultado na resposta ainda precisam ser fechados. O contrato deve distinguir:

- rejeição antes da aceitação;
- execução recusada antes de iniciar o turno;
- execução com terminalidade incerta;
- resultado produzido e entrega confirmada;
- resultado produzido e entrega falha ou incerta.

Não retornar “sucesso” apenas porque o JSON foi aceito quando o chamador permaneceu aguardando o processamento completo.

<a id="execucao"></a>
## 7. Execução pelo agente e threads

### 7.1 Porta interna do gateway

**[T]** Definir uma porta interna semelhante a:

```text
submit(origin, prompt, destination_key, thread_policy, event_id) -> GatewayWork
```

`GatewayWork` é a representação interna persistida na mesma inbox usada pelo Telegram. O resultado contém estado terminal, texto final e metadados mínimos de diagnóstico; não contém credenciais nem cria outra identidade de evento. A porta não executa em linha nem possui semáforo próprio.

### 7.2 App Server compartilhado

1. Manter uma única instância de `CodexAppServer`, criada pelo gateway de execução vigente por `CodexLaunch.from_system(...).app_server_launch()`.
2. Generalizar `clientInfo` para o gateway multiorigem sem iniciar outro processo.
3. Selecionar instruções e capacidades pela origem: Telegram preserva o contrato atual; eventos usam `CodexLaunch.developer_instructions()` e não recebem ferramentas Telegram por acidente.
4. Para nova thread, chamar `thread/start` com cwd, aprovação, sandbox e instruções.
5. Para reutilização, buscar vínculo do destino na persistência do mesmo gateway e chamar `thread/resume`.
6. Se a ausência da thread for conclusiva, aplicar a política aprovada de recriação; falha transitória não apaga vínculo.
7. Chamar `turn/start` com o prompt como input do usuário e aguardar `turn/completed` correlacionado pelo ciclo já usado pelo Telegram.
8. Coletar somente resposta final correlacionada; comentário intermediário não é terminalidade.
9. Propagar falha, interrupção e perda de transporte como estados distintos e preservar a retenção de permit em terminalidade incerta.
10. Parar por ociosidade apenas quando não houver turno, item admitido, reconciliação ou entrega pendente de qualquer origem.

### 7.3 Codex CLI

**[U]** O gateway pode invocar App Server ou Codex CLI. **[I]** O repositório atual centraliza lançamento do CLI, mas não possui um executor não interativo com contrato de resultado equivalente.

Antes de criar o adaptador CLI:

- provar comando, formato de saída, timeout, cancelamento, cwd, sandbox, aprovação e retomada de thread no executável configurado;
- registrar as capacidades numa validação local versionável, sem credenciais ou prompts reais;
- manter o App Server como caminho padrão se o CLI não oferecer equivalência;
- não analisar texto de terminal instável quando houver protocolo estruturado disponível.

### 7.4 Associação de thread

**[T]** A chave funcional proposta é `(channel_type, channel_value)`, pois a definição do usuário associa thread ao destino. O repositório deve persistir o ID da thread, revisão das instruções e atualização.

**[U]** `new` inicia uma nova thread para o evento. Quando a troca é concluída, essa thread substitui a associação anterior e passa a ser a vigente para `reuse`; a thread anterior não é retomada automaticamente.

Eventos do mesmo destino são serializados pelo conjunto global de destinos ativos; não há FIFO separada por `response_channel`. Destinos diferentes disputam o orçamento global junto com o Telegram. **[B]** Ainda é necessário decidir o tratamento técnico quando a criação ou o primeiro turno da nova thread falha antes de concluir a troca. Não inferir exclusão da thread anterior nem rollback.

### 7.5 Timeout e cancelamento

O caminho vigente já define o timeout do turno: `turn_timeout_seconds` (900 segundos por padrão). Ao vencê-lo, solicita `turn/interrupt`, usa `interrupt_grace_seconds` (15 segundos por padrão) e mantém a execução/permit como incertos quando não há terminalidade confirmada. Eventos reutilizam esses mesmos valores, a mesma interrupção e a mesma reconciliação. Os 45 segundos padrão de uma chamada protocolar do `CodexAppServer` limitam ACKs individuais, não a duração total do turno; `app_server.idle_timeout_seconds` controla ociosidade do processo e também não é timeout de execução.

Não criar `event_timeout`, timeout por canal ou default novo. Permanecem bloqueantes apenas:

- a forma de o chamador acompanhar a resposta sem bloquear o RPC serial e sem criar outro modo de execução;
- se o chamador pode solicitar cancelamento no ingresso privado/local;
- o comportamento de desligamento do daemon com execução ativa;
- a política de entrega/retry quando um efeito externo fica incerto;
- se, ao extrair configurações comuns do arquivo Telegram, os nomes atuais serão preservados ou migrados sem alterar seus valores.

<a id="entrega"></a>
## 8. Adaptadores de entrega

### 8.1 Contrato comum

Cada adaptador recebe o resultado textual, destino normalizado, UUIDv7 e metadados da recepção. Deve retornar um recibo com `confirmed`, `failed` ou `uncertain`, sem mascarar aceite como entrega final.

Regras comuns:

- uma recepção válida produz no máximo uma tentativa automática por adaptador enquanto a política de retry estiver aberta;
- falha de destino é operacional, não erro de schema;
- resultado incerto nunca é repetido cegamente;
- o UUIDv7 acompanha logs e diagnóstico, mas não deduplica;
- a falha do canal funcional não impede o registro do resultado e a tentativa opcional de incidente.

### 8.2 Telegram

**[T]** Reutilizar `TelegramMcpService.list_channels()` e `send()` ou extrair uma fachada interna equivalente, preservando o RPC autenticado do Supervisor.

Fluxo:

1. validar que o valor é ID não vazio no schema;
2. consultar o catálogo registrado para confirmar existência;
3. enviar texto pelo destino exato;
4. omitir `idempotency_key`, porque repetir o mesmo UUIDv7 deve gerar nova mensagem;
5. tratar `sent=true` como confirmação, `sent=false` como falha e `sent=null` como incerteza;
6. não criar tópico novo implicitamente;
7. registrar remoção do canal entre validação e envio como falha operacional normal.

O teste manual real deve usar um canal de homologação escolhido pelo proprietário; a suíte automatizada usa RPC e API falsos.

### 8.3 E-mail

O adaptador pode reutilizar `EmailService`, composição MIME, conexões e `SubmissionStore`, mas precisa resolver duas lacunas:

1. **[B] conta remetente:** o evento informa destinatário, não qual conta SMTP usar;
2. **[T] chave de submissão:** cada recebimento precisa de chave interna diferente, inclusive para o mesmo UUIDv7, sem expô-la como novo ID de evento.

Uma solução aceitável para a chave é derivá-la do UUIDv7 e do registro operacional da recepção, mantendo-a privada ao adaptador. Ela não pode ser somente o UUIDv7. A implementação deve provar duas submissões quando o mesmo evento é recebido duas vezes e preservar a proteção contra repetição da mesma tentativa incerta.

O aceite SMTP significa aceite pelo servidor, não entrega ao destinatário. O recibo e o log devem manter essa distinção.

**[B]** Também definir remetente, assunto, conta padrão ou seleção por configuração, conteúdo texto/HTML, política para resultado vazio e tratamento de endereços internacionalizados antes da fase de e-mail.

### 8.4 Canal de incidentes

- **[U]** É opcional, distinto do destino funcional e não recebe respostas normais.
- **[U]** Sem configuração, o log local é suficiente para o serviço funcionar.
- **[T]** Implementar como fachada separada que recebe uma descrição sanitizada e referência operacional.
- **[T]** Falha do próprio canal gera somente log local; nunca tenta notificar a si mesmo em recursão.
- **[B]** Tipo, valor, escopo global versus override, conteúdo, anexação de log e tentativas ainda não foram decididos.

<a id="logs"></a>
## 9. Logs, retenção e incidentes

### 9.1 Contrato funcional

- **[U]** Cada chamada recebida registra UUIDv7 e timestamp acrescentado pelo daemon com precisão de milissegundos.
- **[U]** Recebimentos repetidos têm registros separados.
- **[U]** O log pode conter os dados disponíveis do chamador, a entrada integral, a saída integral e a falha, sob a premissa de acesso exclusivo do proprietário.
- **[U]** Retenção é de 20 dias e a limpeza alcança somente logs.

### 9.2 Formato proposto

**[T]** Usar JSON Lines por dia em `configs/daemon/services/<serviço>/logs/`, com um evento operacional por linha e arquivos nomeados pela data da política temporal aprovada. Motivos:

- anexação simples e tolerante a encerramento entre registros;
- busca por UUIDv7 com ferramentas comuns;
- remoção por arquivo sem reescrever banco;
- conteúdo integral sem misturar com stdout do Supervisor.

Campos mínimos propostos:

- `event_id`;
- `received_at`;
- etapa e estado;
- versão do schema;
- entrada, saída ou erro quando aplicável;
- tipo e valor do destino;
- estado da execução e da entrega.

Não gravar token, segredo de autenticação, senha SMTP ou credencial do vault. A permissão para conteúdo integral não autoriza segredos operacionais que não pertencem ao evento.

### 9.3 Relógios e limpeza

**[T] recomendação:** timestamp civil em UTC, RFC 3339 com três casas decimais e `Z`; `time.monotonic()` somente para deadlines internos. **[B]** O rascunho deixou timezone e formato abertos, portanto a recomendação precisa de aprovação antes de congelar fixtures.

A limpeza deve:

1. rodar na inicialização e periodicamente;
2. calcular vencimento pela data registrada, não apenas pelo `mtime` manipulável;
3. ignorar arquivo ativo;
4. remover somente arquivos reconhecidos dentro do diretório exato de logs;
5. registrar contagem, nunca o conteúdo excluído;
6. ser idempotente e tolerar arquivo parcialmente escrito;
7. ter testes com fronteira de exatamente 20 dias e relógio injetado.

**[B]** Periodicidade, rotação por tamanho e limite total continuam abertos. A rotação diária é a recomendação inicial, não decisão do usuário.

<a id="persistencia"></a>
## 10. Persistência e recuperação

### 10.1 Dados que podem ser persistidos sem criar cache de resultados

- versão das migrações;
- associação destino-thread;
- revisão de instruções da associação;
- configurações operacionais não secretas;
- estado mínimo de uma execução ativa quando a política de recuperação for aprovada;
- estado de fila/admissão estritamente necessário para preservar FIFO e recuperar slots conforme a política aprovada;
- recibo de entrega necessário para impedir repetição de uma tentativa incerta.

Não criar tabela que devolva resultado anterior ao receber o mesmo UUIDv7. Logs não são consultados para deduplicar.

### 10.2 Repositório proposto

**[T]** Evoluir por migração explícita o SQLite do gateway vigente, hoje `gateway.sqlite3`, mantendo um único lease de escritor e uma única verdade transacional para inbox, destinos ativos, execuções, threads e entregas. Um banco separado para logs ou configuração do contrato não pode conter fila, associação de thread ou estado de execução duplicado.

Estruturas mínimas após a migração, em nomes ainda técnicos:

```text
schema_migrations
gateway_inbox(origin, priority, global_sequence, destination_key,
              event_id, payload, state, ...)
destination_threads(destination_key, thread_id,
                    instruction_revision, updated_at)
```

Ao concluir com `thread_policy = "new"`, o repositório deve trocar a associação do destino pela nova thread. A implementação precisa preservar uma única associação vigente por destino. O comportamento antes da conclusão, em falha de criação ou de primeiro turno, continua pendente e não autoriza excluir a thread anterior.

O escalonador global não pode reconstruir prioridade e ordem de recepção apenas por timestamp quando houver empate. A migração deve preservar os itens Telegram existentes e atribuir sequência global estável. A representação persistente e a recuperação continuam condicionadas à política de restart, mas qualquer mecanismo escolhido deve conservar origem, prioridade, ordem comparável, itens pulados, destinos ativos e concessões do orçamento global sem criar outro limite.

Tabelas de execução e entrega só entram após as decisões de recuperação e retry. O `event_id` pode ter índice de busca, nunca unicidade.

### 10.3 Reinício

**[B]** Persistência e retomada estão entre as decisões prioritárias abertas. Antes da fase de produção, definir:

- se um turno ativo é retomado, reconciliado ou marcado incerto;
- como descobrir que o App Server aceitou o turno;
- se uma entrega confirmada antes do crash pode ser reconhecida;
- se um resultado produzido e ainda não entregue sobrevive;
- comportamento de shutdown gracioso e forçado;
- quais estados exigem ação do proprietário.

Até essa decisão, o primeiro corte deve marcar perda de processo como falha/incerteza e nunca repetir automaticamente geração ou entrega.

<a id="seguranca"></a>
## 11. Ingresso inicial e publicação futura

### 11.1 Ingresso privado/local

- **[U]** A fase inicial oferece somente ingresso privado/local e não publica endpoint externo.
- **[U]** Não há autenticação, cadastro nem autorização de emissores nessa fase.
- **[U]** O receptor aceita qualquer evento que passe pela validação do contrato JSON v1.
- **[T]** O primeiro corte usa o RPC privado já operado pelo Supervisor como transporte local, sem introduzir identidade ou permissão de emissor no contrato do evento.
- **[T]** JSON inválido é rejeitado antes da execução; JSON válido não depende de token, credencial ou cadastro adicional do emissor.

### 11.2 Exposição externa futura

A publicação pelo Gateway HTTP compartilhado fica fora do escopo inicial. Nenhum `publication.describe`, listener HTTP de negócio ou prefixo público deve ser implementado ou habilitado nessa fase.

Uma fase futura só poderá expor o receptor depois de nova decisão explícita sobre segurança e autenticação. Essa decisão futura deverá definir o mecanismo e seus efeitos sobre operação, testes e setup; o plano atual não escolhe token, credencial, permissões, rotação, revogação ou alternativa equivalente.

Não adicionar rota de negócio diretamente em `PublicGateway`: se a exposição futura for aprovada, o proxy continuará separado do processo compartilhado do gateway de execução.

### 11.3 Princípios obrigatórios

- nenhum campo de identidade de emissor é acrescentado ao envelope v1;
- a ausência de autenticação não autoriza exposição de rede;
- a validação do schema é o único gate de admissão funcional na fase privada/local;
- o UUIDv7 continua sendo correlação do evento, não credencial, nonce nem chave de idempotência;
- uma futura publicação externa não pode ser ativada implicitamente por instalação, setup ou habilitação do serviço local.

<a id="agendador"></a>
## 12. Agendador como fase independente

### 12.1 Fronteira

**[U]** O agendador administra definições temporais e dispara ações. Notificar o agente é uma ação possível, não sua finalidade exclusiva.

**[T]** Implementar em processo supervisionado separado, com pacote `task_scheduler`, namespace RPC `task.` já reservado e banco próprio. Ele não importa repositório interno do gateway; usa a porta de aplicação privada/local para emitir o mesmo envelope validado, sem cadastro próprio como emissor na fase inicial.

### 12.2 Contrato próprio

Antes de código, publicar um contrato versionado de agendamento que separe:

- identidade e metadados da definição;
- regra temporal;
- ação;
- parâmetros da ação;
- criação e próxima execução;
- estado habilitado/desabilitado;
- dados exibidos no setup.

Para ação de notificação, armazenar o envelope ou uma referência imutável aos parâmetros necessários. Para ação local, o contrato não exige destino, política de thread nem gateway.

### 12.3 Bloqueios do agendador

**[B]** Não iniciar implementação funcional antes de decidir:

- origem autorizada das definições, já que o setup inicial não cria nem edita;
- identidade de agendamento;
- calendário, timezone, horário de verão e recorrência;
- comportamento de atraso, reinício e disparos perdidos;
- concorrência e sobreposição;
- catálogo inicial de ações locais;
- limites e isolamento de scripts;
- habilitação, desabilitação e remoção fora do setup inicial;
- retenção de histórico;
- contrato de erro e retry por ação.

### 12.4 Fases internas do agendador

1. contrato e repositório somente leitura;
2. listagem RPC determinística com ordenação por campo permitido;
3. tela de setup somente leitura;
4. cálculo temporal e dispatcher, após decisões de calendário;
5. ação local mínima e segura;
6. adaptador que emite notificação pelo contrato compartilhado;
7. recuperação e concorrência;
8. futura criação/edição, somente mediante nova aprovação funcional.

<a id="setup"></a>
## 13. Setup e experiência operacional

### 13.1 Contextos separados

No menu `D. Daemon`, acrescentar entradas independentes, com rótulos finais aprovados:

- contexto do gateway de notificações ao agente;
- contexto do agendador.

Não criar “Eventos e agendamentos”. Publicação externa continua sendo contexto da infraestrutura HTTP/Tunnel, não configuração funcional do gateway.

### 13.2 Contexto do gateway

O menu navegável poderá conter, conforme decisões concluídas:

- diagnóstico;
- habilitar/desabilitar somente o ingresso de eventos, sem desligar o gateway compartilhado nem o Telegram;
- configuração do canal operacional de incidentes;
- teste controlado sem enviar a destino real por padrão.

O contexto inicial não oferece cadastro de emissores nem controles de publicação externa.

Cada wizard, pergunta, diagnóstico e resultado usa apresentação em fluxo. Escolhas enumeradas indicam o default no prompt, confirmações usam `S/n` ou `s/N`, e `X` cancela ou volta.

### 13.3 Contexto inicial do agendador

- **[U]** Somente listar agendamentos, modelos e definições atuais.
- **[U]** Exibir inicialmente próxima execução, criação, tipo/valor do canal e política de thread quando aplicáveis.
- **[U]** Toda coluna exibida pode ser escolhida para ordenação por atalho.
- **[U]** Não incluir criar, editar ou excluir.

**[I]** Como os atalhos alteram o estado de ordenação e a tela possui saída explícita, a listagem tende a ser menu navegável com `screen()`, não simples resultado em fluxo. **[B]** O rascunho deixou essa classificação aberta; aprová-la antes de escrever a tela.

O desenho precisa decidir teclas, direção, indicador atual, nomes, largura, paginação e ordenação de valores nulos. O backend deve aceitar somente chaves conhecidas, nunca nome de coluna SQL arbitrário.

### 13.4 Configuração

**[T]** Configurações locais ficam no diretório do respectivo serviço, versionadas por `schema_version`, sem segredo em texto claro. Salvamento usa arquivo em `tmp/` e substituição atômica, preservando seções desconhecidas quando o contrato permitir.

Mudanças no setup devem ter funções de persistência testáveis fora da UI e testes de navegação em [`tests/test_setup_onmyoji.py`](../../tests/test_setup_onmyoji.py).

<a id="impacto"></a>
## 14. Impacto por componente

| Arquivo/componente | Alteração planejada |
| --- | --- |
| `pyproject.toml` | Incluir recursos JSON do schema no pacote; nenhuma dependência nova é necessária para JSON Schema. |
| `registry.py` | Fazer o mesmo `ServiceSpec` do gateway aceitar `telegram.` e o namespace local de eventos, preservando compatibilidade; registrar separadamente apenas o agendador e preservar `task.` para ele. |
| `supervisor.py` | Encaminhar os dois namespaces ao mesmo processo e preservar lifecycle único. Não manter o `outbound_lock` ocupado durante a execução longa; a forma de acompanhamento depende da decisão bloqueante do ingresso. |
| `cli.py` | Administração e diagnóstico coerentes com os serviços; não transformar CLI isolada na interface principal de configuração. |
| `codex_app_server.py` | Generalizar `clientInfo` sem criar segunda instância nem quebrar o Telegram. |
| `codex_execution.py` | Continuar fonte única de launch; acrescentar somente suporte comprovado ao adaptador CLI. |
| `execution_gateway/` | Componentes extraídos do caminho vigente: escalonamento global, ciclo de turno e persistência multiorigem, todos instanciados uma vez no mesmo processo. |
| `agent_notification/` | Novo contrato, adaptador de ingresso privado/local, logs e adaptadores de entrega; sem executor, App Server, inbox ou listener externo próprios. |
| `gateway/` | Nenhuma alteração na fase inicial; uma publicação futura exigirá decisão própria. |
| `telegram_channels.py` / `mcp/telegram/service.py` | Extrair ou reutilizar fachada segura de catálogo e envio; preservar contratos MCP. |
| `telegram.py` / `telegram_execution.py` | Separar receptor/entrega Telegram do núcleo do gateway, manter o único App Server e substituir a corrida entre workers pelo escalonador global sem alterar respostas observáveis do Telegram. |
| `telegram_persistence.py` | Migrar inbox e associações para origem/prioridade/sequência global e destinos genéricos; manter um banco e um lease. |
| `mcp/email/` | Expor serviço interno apropriado e chave por recepção sem alterar deduplicação das tools existentes. |
| `task_scheduler/` | Fase separada, contrato e armazenamento próprios. |
| `setupOnmyoji.py` | Dois contextos independentes, diagnósticos e listagem conforme interface. |
| `setupDaemon.py` | Funções de configuração/validação do serviço, se mantido como backend administrativo. |
| `docs/daemon.md` e documentação do daemon | Atualizar somente quando o comportamento correspondente for entregue. |
| `docs/index.md` | Indexar a documentação operacional final quando existir; o presente plano pode permanecer como artefato de planejamento. |

<a id="fases"></a>
## 15. Fases e dependências

Cada fase termina com testes, documentação correspondente e commit isolado. Não agrupar gateway e agendador no mesmo commit apenas por pertencerem a este plano.

| Fase | Entrega | Depende de | Critério para avançar |
| --- | --- | --- | --- |
| 0 — Decisões consolidadas | Envelope v1 fechado e ingresso inicial exclusivamente privado/local, sem autenticação de emissores | Aprovação do proprietário | O schema não possui placeholders e nenhuma publicação externa integra o escopo inicial. |
| 1 — Contrato compartilhado | Schema v1, loader, UUIDv7, erros e fixtures | Fase 0 | Emissor e receptor usam o mesmo artefato; 100% dos ramos unitários do validador cobertos. |
| 2 — Núcleo multiorigem | Extrair o ciclo vigente para componentes neutros no mesmo processo, migrar a inbox, implementar escalonador global, destinos ativos e prioridade Telegram | Contrato de threads e prioridade | O Telegram continua passando pelos mesmos testes; há uma única instância de App Server, um banco e um limite. A seleção pula destino ativo sem head-of-line blocking. |
| 3 — Corte vertical privado | Adaptador local do contrato, log de recepção e entrega Telegram conectados ao núcleo da fase 2 | Fases 1–2 e decisão sobre acompanhamento do chamador | Mesmo UUID duas vezes produz duas execuções e duas tentativas de entrega pelo mesmo gateway usado pelo Telegram. |
| 4 — Persistência e recuperação | Associação destino-thread no banco comum, migrações e política de restart | Decisões de retomada | Crash e restart têm estado determinístico, liberam permits próprios e não repetem efeito incerto. |
| 5 — Entregas completas | Telegram consolidado, e-mail, recibos e falhas | Conta SMTP e retry decididos | Destino inválido, removido, aceito, falho e incerto cobertos. |
| 6 — Logs e incidentes | JSONL, retenção 20 dias e canal opcional | Timestamp e canal decididos | Limpeza não alcança dados fora dos logs; falha do incidente não recursa. |
| 7 — Ingresso privado/local | Admissão por schema no transporte privado do Supervisor, sem autenticação de emissores | Fases 1–6 | Evento válido é aceito, inválido é rejeitado e não existe listener externo. |
| 8 — Setup do gateway | Menu, diagnóstico e configuração operacional local | Fases 5–7 | Interface segue `setup-interface.md`; sem cadastro de emissores e sem dependência do canal opcional. |
| 9 — Fundação do agendador | Contrato, repositório e RPC de listagem | Bloqueios do agendador | Listagem determinística, sem criação/edição. |
| 10 — Setup do agendador | Tela ordenável por qualquer coluna exibida | Fase 9 e desenho aprovado | Atalhos, defaults, `X`, nulos e paginação testados. |
| 11 — Execução temporal | Cálculo, ações locais e emissão opcional ao gateway | Contrato temporal e de ações | Agendador funciona sem gateway; integração reutiliza o mesmo schema. |
| 12 — Rollout local | Diagnóstico, documentação, migração e ativação gradual sem publicação externa | Todas as fases iniciais aplicáveis | Suíte completa verde, validação manual controlada registrada e ausência de endpoint externo comprovada. |

<a id="vertical"></a>
## 16. Primeiro corte vertical

O primeiro corte não inclui o agendador nem o endpoint público. Ele deve validar a arquitetura de ponta a ponta com o menor conjunto seguro:

```text
RPC privado/local do Supervisor
    -> adaptador agent_notification.ingress desabilitado por padrão
    -> schema v1 compartilhado
    -> log de recepção
    -> inbox/escalonador do gateway vigente
    -> mesma instância do App Server usada pelo Telegram
    -> `thread_policy = "new"`
    -> adaptador Telegram para canal registrado
    -> resposta ao chamador conforme decisão específica de acompanhamento
```

### 16.1 Restrições do corte

- usar `thread_policy = "new"` no cenário de demonstração e confirmar que a nova thread se torna a associação vigente após a conclusão;
- não publicar listener HTTP;
- não enviar e-mail;
- não configurar canal de incidentes;
- não criar tabela de cache ou resultado;
- não habilitar o novo ingresso automaticamente em instalação existente;
- usar fakes em CI e destino real somente em validação manual autorizada;
- reutilizar `turn_timeout_seconds` e `interrupt_grace_seconds`; testes podem injetar valores menores sem criar default novo.

### 16.2 Aceite do corte

1. JSON inválido é rejeitado antes do App Server.
2. UUID que não é v7 é rejeitado.
3. Evento válido registra exatamente o UUID recebido e timestamp.
4. O gateway compartilhado inicia thread e turno na mesma instância App Server, aguarda terminalidade e extrai resposta final.
5. Depois da conclusão, a nova thread substitui a associação anterior do destino e um evento posterior com `reuse` usa essa nova thread.
6. Dois eventos do mesmo `response_channel` nunca executam simultaneamente; enquanto um está ativo, o posterior permanece pendente e outro destino elegível pode ocupar o slot.
7. Trabalhos de Telegram e eventos somados nunca excedem `agent.max_parallel_conversations`; Telegram elegível passa à frente de eventos aguardando, sem preempção, e itens pulados são reconsiderados.
8. O adaptador entrega ao ID Telegram registrado sem chave de idempotência.
9. Repetir o mesmo payload e UUID chama executor e entrega novamente.
10. Encerrar o acompanhamento aprovado depois da aceitação não cancela execução nem entrega.
11. Falha de canal produz log operacional e resposta coerente quando o chamador aguarda.
12. Nenhum segundo ID de evento aparece em resposta, log ou entrega.
13. Parar o processo durante turno não causa retry automático ao reiniciar.

Esse corte valida as fronteiras. Ele não deve ser anunciado como produção até acompanhamento do chamador, cancelamento, recuperação e políticas de entrega estarem decididos. O timeout do turno e sua graça de interrupção já são os do gateway vigente.

<a id="testes"></a>
## 17. Testes e critérios de aceite

### 17.1 Arquivos de teste propostos

| Arquivo | Cobertura principal |
| --- | --- |
| `test_agent_notification_contract.py` | Schema, versões, UUIDv7, campos obrigatórios, nulos, extremos e propriedades extras. |
| `test_agent_notification_service.py` | Ordem do fluxo, reenvio, desconexão, falhas e correlação. |
| `test_execution_gateway_scheduler.py` | Uma inbox, prioridade, sequência global, destino ativo, reconsideração e limite conjunto. |
| `test_execution_gateway_events.py` | Thread start/resume, turno multiorigem, terminalidade, timeout vigente, permit incerto e perda. |
| `test_agent_notification_delivery.py` | Telegram/e-mail, destino removido, recibo confirmado/falho/incerto e reenvio. |
| `test_agent_notification_logging.py` | Conteúdo, sanitização de credencial, UTC/formato aprovado e retenção. |
| `test_agent_notification_ingress.py` | Limites, JSON, admissão privada/local, cancelamento do cliente e ausência de publicação externa. |
| `test_agent_notification_supervision.py` | Dois namespaces no mesmo lifecycle/processo, ingresso habilitável e ausência de declaração HTTP. |
| `test_task_scheduler_contract.py` | Regra temporal e ações após contrato aprovado. |
| `test_task_scheduler_service.py` | Cálculo, concorrência, restart, ação local e emissão opcional. |
| `tests/test_setup_onmyoji.py` | Menus separados, fluxos, defaults, `X` e listagem ordenável. |

### 17.2 Matriz mínima do gateway

| Área | Casos críticos |
| --- | --- |
| JSON | vazio, não objeto, UTF-8 inválido, versão ausente/desconhecida, extra, tipo incorreto e objeto profundamente inválido. |
| UUIDv7 | válido, versões 1/4/6/8, caixa, hífens, nulo e valor quase válido. |
| Prompt | vazio rejeitado, texto não vazio, acentos, quebras, conteúdo grande dentro do limite técnico e corpo acima do limite de transporte. |
| Canal | `response_channel` ausente ou não objeto, `type`/`value` ausentes, literais `telegram`/`email`, tipo desconhecido, valor inválido, Telegram não registrado e destino removido durante execução. |
| Reenvio | mesmo UUID/payload, mesmo UUID/payload alterado e duas recepções no mesmo milissegundo; todas processam novamente. |
| Thread e canal | `new` cria e, após conclusão, substitui a associação; `reuse` posterior usa a nova thread; reuse sem vínculo, thread removida, resume transitório, falha antes da troca concluída, sem simultaneidade no mesmo destino e paralelismo entre destinos. |
| Admissão global | Telegram + eventos nos valores 1 e 16 do limite; destinos ativos; candidato inelegível pulado sem remoção; próximo elegível admitido; reconsideração após liberação; FIFO entre comparáveis; Telegram elegível antes de eventos; nenhuma preempção; liberação após sucesso, falha, cancelamento e queda comprovada do dono. |
| App Server único | Telegram e evento simultâneos usam a mesma instância/epoch; resposta antes do ACK, evento antigo, item final sem terminalidade, terminal falho, interrupção e transporte perdido. |
| Timeouts existentes | `turn_timeout_seconds`, `interrupt_grace_seconds`, retenção/liberação do permit incerto e ausência de configuração específica de evento. |
| Desconexão | RPC/HTTP fechado antes de conclusão, shutdown gracioso e processo morto. |
| Telegram | catálogo indisponível, `sent` true/false/null, mensagem acima do limite e nenhuma idempotência pelo UUID. |
| E-mail | conta ausente, destinatário inválido, aceito, parcialmente aceito, rejeitado, incerto e duas submissões do mesmo UUID. |
| Incidente | ausente, confirmado, falho e sem recursão. |
| Logs | repetição de UUID, conteúdo integral permitido, segredo excluído, arquivo parcial, limpeza e confinamento de caminho. |
| Ingresso | evento válido aceito sem autenticação de emissor, evento inválido rejeitado e ausência de listener ou publicação externa. |

### 17.3 Matriz mínima do agendador

| Área | Casos críticos |
| --- | --- |
| Listagem | zero, um, muitos, nulos, Unicode, colunas largas e paginação. |
| Ordenação | cada coluna exibida, ascendente/descendente conforme decisão, empate determinístico e chave inválida. |
| Tempo | timezone, DST, atraso, restart, relógio retrocedendo/avançando e disparo perdido, após contrato aprovado. |
| Ações | local sem gateway, notificação com gateway, gateway indisponível e ação não suportada. |
| Concorrência | dois disparos simultâneos, sobreposição da mesma definição e shutdown durante ação. |
| Setup | nenhuma opção de criar/editar/excluir, atalhos visíveis e `X` volta ao daemon. |

### 17.4 Regressão e comandos

Executar por etapa, no mínimo:

```powershell
python -B -m pytest onmyoji-daemon/tests/test_agent_notification_contract.py onmyoji-daemon/tests/test_agent_notification_service.py
python -B -m pytest onmyoji-daemon/tests/test_execution_gateway_scheduler.py onmyoji-daemon/tests/test_execution_gateway_events.py
python -B -m pytest onmyoji-daemon/tests/test_codex_app_server.py onmyoji-daemon/tests/test_codex_execution.py onmyoji-daemon/tests/test_telegram_gateway_async.py
python -B -m pytest onmyoji-daemon/tests/test_telegram_delivery_dispatch.py onmyoji-daemon/tests/test_mcp_telegram.py onmyoji-daemon/tests/test_mcp_email.py
python -B -m pytest onmyoji-daemon/tests/test_supervisor.py onmyoji-daemon/tests/test_gateway_http.py
python -B -m pytest tests/test_setup_onmyoji.py onmyoji-daemon/tests/test_setup_daemon.py
python -B -m pytest
```

Os nomes novos são metas do plano. Enquanto um arquivo ainda não existir, a fase correspondente não está concluída. Classes utilitárias e unidades isoláveis devem atingir 100% de cobertura; integração real cobre comportamentos críticos sem substituir testes unitários.

### 17.5 Validação manual controlada

Após a suíte automatizada:

1. preparar instância sem habilitar listener público;
2. habilitar o serviço por RPC/setup em ambiente de homologação;
3. selecionar canal Telegram registrado de teste;
4. emitir evento com UUIDv7 conhecido;
5. confirmar log, thread, resposta e entrega;
6. repetir o mesmo evento e confirmar nova resposta;
7. remover/invalidar destino e confirmar falha + log;
8. desconectar o chamador e confirmar conclusão;
9. reiniciar conforme política aprovada;
10. confirmar que nenhum listener ou endpoint externo foi publicado.

Nenhum teste manual usa conversa, e-mail, credencial ou conteúdo real como fixture versionada.

<a id="rollout"></a>
## 18. Migração, rollout e rollback

### 18.1 Migração

- o novo ingresso ausente na configuração equivale a desabilitado; ele não cria um serviço executor em `services.json`;
- migrar o banco do gateway sob o lease vigente, com backup e teste de compatibilidade; não copiar dados para outro banco;
- preservar o estado habilitado e o namespace `telegram.` durante eventual generalização do identificador do processo;
- consultar canais Telegram pelo contrato existente;
- reutilizar configuração de e-mail sem copiar credenciais;
- schema e banco novos começam em versão 1 com migrações testadas;
- nenhum agendamento legado existe para importar;
- não alterar automaticamente a habilitação de Gateway HTTP ou Tunnel.

### 18.2 Rollout

1. publicar contrato e testes sem ingresso ativo;
2. extrair o núcleo e o escalonador dentro do processo vigente, ainda sem admitir eventos;
3. validar regressão do Telegram e provar uma única instância App Server em homologação;
4. habilitar persistência/recuperação;
5. habilitar e-mail e incidentes separadamente;
6. validar o ingresso privado/local sem autenticação de emissor;
7. ampliar destinos locais gradualmente;
8. iniciar fundação do agendador somente após estabilidade do contrato compartilhado;
9. ativar disparos reais do agendador após contrato temporal homologado;
10. manter publicação externa fora do rollout inicial; qualquer evolução nessa direção exige nova decisão de segurança e plano próprio.

### 18.3 Rollback

- desabilitar o ingresso de eventos sem interromper a recepção Telegram nem desligar o núcleo compartilhado quando outra origem ainda o utiliza;
- preservar configuração, logs ainda dentro da retenção e banco para diagnóstico;
- não apagar canais Telegram, contas de e-mail ou conteúdo na origem;
- não reexecutar eventos ou disparos interrompidos automaticamente;
- restaurar versão anterior do código somente com schema de banco compatível ou migração reversa testada;
- documentar estados incertos que exijam decisão do proprietário.

<a id="riscos"></a>
## 19. Riscos e mitigação

| Risco | Impacto | Mitigação planejada |
| --- | --- | --- |
| Confundir o gateway de execução com o proxy HTTP existente | Processo privilegiado e responsabilidades misturadas | Ingresso privado/local no processo de execução; nenhuma publicação no escopo inicial. |
| Herança direta de classes Telegram | Núcleo continua acoplado a conversa e tools | Extrair componentes neutros no mesmo processo e manter Telegram como adaptador. |
| Usar UUIDv7 como idempotência | Reenvio não processa novamente | Teste obrigatório com mesmo UUID; nenhuma unicidade de evento. |
| `request_id` SMTP igual ao UUIDv7 | Segunda recepção consulta resultado anterior | Chave privada por recepção e teste de duas submissões. |
| Desconexão cancelar execução | Evento aceito fica sem processamento/entrega | Transferir posse ao serviço depois da aceitação e testar cancelamento do cliente. |
| Thread inexistente ou resume transitório | Perda de contexto ou resposta na thread errada | Diferenciar ausência conclusiva de falha transitória; vínculo persistente. |
| Dois eventos no mesmo destino | Corrida na associação/turno | Conjunto global de destinos ativos; candidato do mesmo destino permanece pendente e só o item admitido atualiza a associação. |
| Primeiro item aguarda destino ocupado | Bloqueio de cabeça impede uso de slots livres | Escalonador pula o candidato temporariamente e procura o próximo elegível, sem removê-lo. |
| Inbox, App Server ou semáforo separados por origem | Soma real ultrapassa o limite e estados divergem | Uma instância de cada recurso no mesmo processo; teste conjunto das duas origens. |
| RPC de ingresso aguardar o turno | Bloqueia todas as chamadas do processo e o `outbound_lock` do Supervisor | Decidir mecanismo de acompanhamento antes do corte; não aumentar timeout nem criar executor paralelo como atalho. |
| Tráfego contínuo do Telegram | Starvation dos eventos do gateway ainda aguardando | Risco aceito e explicitado; não criar quota, aging ou alternância sem nova decisão. |
| Timeout após efeito externo | Duplicação por retry cego | Estados incertos e nenhuma repetição automática. |
| Logs integrais exporem credencial | Comprometimento operacional | Separar headers/segredos do payload funcional e sanitizar sempre. |
| Limpeza apagar dados fora do log | Perda de estado/conteúdo na origem | Diretório confinado, nomes reconhecidos e testes destrutivos em `tmp_path`. |
| Incidente falhar e gerar loop | Tempestade de mensagens/logs | Uma tentativa controlada; falha termina em log local. |
| Publicar endpoint externo na fase inicial | Execução do agente fora da fronteira aprovada | Não implementar listener/publicação; exigir nova decisão futura sobre segurança e autenticação. |
| Agendador nascer acoplado ao gateway | Ações locais passam a depender do agente | Contrato de ação e processo próprios; cliente interno opcional. |
| Setup exceder escopo inicial | Criação/edição não aprovada | Testes negativos da interface e backend somente leitura. |
| CLI não interativo não ser equivalente | Resultado frágil ou contexto divergente | Gate de capacidade; App Server permanece padrão. |

<a id="bloqueios"></a>
## 20. Perguntas bloqueantes

### P1 — antes de definir limites do ingresso privado/local

1. Qual limite técnico de corpo protege o transporte sem virar máximo funcional do prompt?

### P2 — antes da execução de produção

1. Qual formato/timezone do timestamp de recepção?
2. Como o chamador local poderá acompanhar a resposta sem manter o `RpcServer` serial e o `outbound_lock` do Supervisor bloqueados durante todo o turno, e sem criar um segundo modo de execução? O handler atual tem 5 segundos por padrão, enquanto o turno usa `turn_timeout_seconds`.
3. O chamador poderá solicitar cancelamento, ou somente abandonar o acompanhamento sem cancelar o trabalho aceito?
4. Como tratar falha de criação ou do primeiro turno `new` antes da conclusão da troca, sem inventar exclusão ou rollback da associação anterior?
5. Como reconciliar e retomar após restart e shutdown ativo?

Os timeouts do turno não estão em aberto: eventos reutilizam `turn_timeout_seconds` e `interrupt_grace_seconds`, inclusive a retenção do permit quando falta terminalidade. A decisão pendente é de transporte/acompanhamento e cancelamento, não a criação de um prazo novo.

### P3 — antes das entregas completas

1. Há retry automático de entrega? Em quais estados e quantas vezes?
2. Qual conta SMTP envia, qual remetente e qual assunto?
3. Como formar a chave privada de submissão por recepção sem expor outra identidade?
4. Qual tipo/valor, escopo e conteúdo do canal de incidentes?

### P4 — antes do agendador

1. Quem cria as definições se o setup inicial só lista?
2. Qual contrato de recorrência, timezone, atraso e disparo perdido?
3. Qual catálogo inicial de ações locais e como scripts são isolados?
4. Qual política de concorrência, histórico e retry?
5. A listagem é formalmente menu navegável? Quais atalhos, direção, paginação e nomes de coluna?

Essas perguntas não impedem contrato, fakes e testes anteriores à fase indicada. Elas impedem ativação do comportamento que depende da resposta.

### Decisão futura — somente antes de eventual exposição externa

O escopo inicial não possui endpoint externo nem autenticação de emissores. Se uma publicação externa for proposta futuramente, será necessária nova decisão explícita sobre segurança e autenticação antes de planejar mecanismo, credencial, permissões, rotação ou revogação.

<a id="checklist"></a>
## 21. Checklist de conclusão

### Gateway de notificações

- [ ] Decisões P1–P3 registradas e aprovadas.
- [ ] Schema v1 imutável, distribuível e validado nos dois lados.
- [ ] `schema_version`, `event_id`, `prompt`, `response_channel` aninhado e `thread_policy` implementados exatamente com os literais aprovados.
- [ ] UUIDv7 validado e reutilizado sem segundo ID de evento.
- [ ] Mesmo UUIDv7 comprovadamente reprocessado.
- [ ] Mesmo gateway, inbox, banco, App Server e limite global usados por Telegram e eventos; nenhum executor paralelo.
- [ ] Adaptador CLI incluído somente se a equivalência for comprovada.
- [ ] Duas políticas de thread implementadas conforme decisão explícita.
- [ ] `new` substitui a associação após a conclusão e o próximo `reuse` usa a nova thread.
- [ ] Escalonador global impede simultaneidade no mesmo destino, pula candidatos inelegíveis e reconsidera-os sem head-of-line blocking.
- [ ] Telegram e eventos compartilham `agent.max_parallel_conversations`, com prioridade Telegram sem preempção.
- [ ] `turn_timeout_seconds`, `interrupt_grace_seconds` e permits incertos são compartilhados sem configuração específica de eventos.
- [ ] Telegram e e-mail com recibos e estados incertos corretos.
- [ ] Desconexão do chamador não cancela evento aceito.
- [ ] Logs integrais permitidos, segredos excluídos e retenção de 20 dias testada.
- [ ] Canal de incidentes opcional e sem recursão.
- [ ] Ingresso inicial aceita eventos válidos sem autenticação de emissor e rejeita eventos inválidos.
- [ ] Nenhum listener ou endpoint externo é publicado na fase inicial.
- [ ] Setup próprio, diagnóstico e documentação operacional atualizados.

### Agendador

- [ ] Decisões P4 registradas e aprovadas.
- [ ] Processo, pacote, banco e contrato independentes do gateway.
- [ ] Ação local funciona sem gateway.
- [ ] Ação de notificação reutiliza o mesmo schema e validador.
- [ ] Listagem mostra atributos aprovados e ordena por toda coluna exibida.
- [ ] Setup não cria, edita nem exclui agendamentos no primeiro escopo.
- [ ] Restart, atraso, concorrência e timezone cobertos pelo contrato e pelos testes.

### Qualidade e entrega

- [ ] Arquivos preservam UTF-8 sem BOM.
- [ ] Temporários e caches ficam em `tmp/`.
- [ ] Testes unitários isoláveis atingem a cobertura exigida.
- [ ] `python -B -m pytest` passa integralmente.
- [ ] Documentação acompanha somente capacidades realmente entregues.
- [ ] Nenhum segredo, log operacional real ou banco de conversas foi versionado.
- [ ] Cada fase validada foi commitada isoladamente e enviada ao `origin`.
