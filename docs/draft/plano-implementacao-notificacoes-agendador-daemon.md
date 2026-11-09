# Plano de implementação do gateway de notificações e do agendador do daemon

**Estado:** plano fechado para entrega ao desenvolvimento; a publicação deste documento não implementa as capacidades descritas.

**Revisão:** 2026-11-09.

**Baseline inspecionado:** `5eed184`.

**Componente responsável:** `onmyoji-daemon`.

**Fonte funcional:** [rascunho de concepção](infraestrutura-eventos-daemon.md).

> [!IMPORTANT]
> Este documento é autocontido e separa definições do usuário, decisões técnicas deste plano, inferências sobre o código atual e perguntas bloqueantes. O rascunho de concepção permanece a fonte histórica das decisões funcionais; divergências futuras devem ser resolvidas nele antes de alterar o contrato implementado.

> [!CAUTION]
> O plano não autoriza expor um endpoint público antes de decidir autenticação e cadastro dos emissores. Também não autoriza criar ou editar agendamentos no setup: o primeiro escopo aprovado do agendador é somente consulta e ordenação.

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
11. [Publicação, autenticação e emissores](#seguranca)
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

Os nomes `agent_notification`, `agent-notifications` e `task_scheduler` usados neste plano são **[T] nomes de trabalho**, não nomes funcionais aprovados pelo usuário. Renomeá-los antes da implementação não reabre a arquitetura, desde que os contextos continuem separados.

<a id="objetivo"></a>
## 2. Objetivo, escopo e não objetivos

### 2.1 Objetivo

Implementar duas capacidades persistentes e independentes do daemon:

1. um gateway que recebe uma solicitação deliberada de participação do agente, valida seu contrato, executa o agente e entrega o resultado no destino obrigatório; e
2. um agendador que administra disparos temporais e pode executar ações locais ou, opcionalmente, emitir uma solicitação ao gateway.

### 2.2 Escopo obrigatório

- **[U]** Gateway e agendador são serviços distintos, ligados por composição e sem dependência obrigatória.
- **[U]** A origem pode tratar um acontecimento integralmente sem gateway.
- **[U]** O gateway recebe JSON conforme esquema versionado e compartilhável com emissores.
- **[U]** O envelope mínimo contém versão, UUIDv7 do emissor, prompt, tipo e valor do destino e política de thread.
- **[U]** Cada recebimento é processado novamente, mesmo que repita o UUIDv7.
- **[U]** A execução aguarda App Server ou Codex CLI; abandonar a resposta não cancela o trabalho aceito.
- **[U]** O resultado funcional é entregue por Telegram ou e-mail; falhas operacionais vão para log e, se configurado, canal de incidentes.
- **[U]** Logs do gateway têm retenção de 20 dias.
- **[U]** Gateway e agendador têm contextos próprios no setup.
- **[U]** A primeira interface do agendador lista definições existentes e permite ordenar por qualquer coluna exibida; não cria nem edita agendamentos.

### 2.3 Fora do escopo inicial

- barramento genérico de webhooks, e-mails ou mensagens;
- captura automática de todo acontecimento externo;
- exclusão automática de conteúdo na origem;
- deduplicação, cache ou consulta de resultado anterior por UUIDv7;
- múltiplos destinatários no mesmo evento;
- editor de agendamentos no setup;
- catálogo definitivo de ações do agendador;
- garantia de entrega exatamente uma vez;
- novo broker externo, fila distribuída ou dependência operacional em `.github/`;
- migração do Gateway Telegram inteiro para a nova camada de execução no primeiro corte.

<a id="baseline"></a>
## 3. Baseline confirmado

### 3.1 Componentes existentes e implicações

| Fonte vigente | Comportamento confirmado | Implicação para a implementação |
| --- | --- | --- |
| [`registry.py`](../../onmyoji-daemon/src/onmyoji_daemon/registry.py) | Registra processos supervisionados, namespace RPC, publicação HTTP e contenção de árvore. O namespace `task.` já está reservado. | **[T]** Criar um serviço supervisionado para notificações e, em fase separada, um serviço de agendamento que use o namespace reservado. |
| [`supervisor.py`](../../onmyoji-daemon/src/onmyoji_daemon/supervisor.py) | Inicia processos com diretório próprio em `configs/daemon/services/`, RPC com token efêmero, logs de stdout e recuperação de serviços habilitados. | Reutilizar ciclo de vida, estado `services.json`, temporários em `tmp/daemon/` e roteamento por namespace; não criar outro supervisor. |
| [`rpc.py`](../../onmyoji-daemon/src/onmyoji_daemon/rpc.py) | RPC local em loopback, autenticado por token, com chamada síncrona e leitura limitada a 1 MiB. O servidor conclui o handler mesmo se o cliente fechar antes da resposta. | Bom ponto de entrada privado para o primeiro corte; o limite atual não pode virar limite funcional implícito do prompt. |
| [`gateway/service.py`](../../onmyoji-daemon/src/onmyoji_daemon/gateway/service.py) e [`supervision/publication.py`](../../onmyoji-daemon/src/onmyoji_daemon/supervision/publication.py) | O Gateway HTTP compartilhado é proxy HTTPS; o Supervisor valida owner, prefixo, porta, TLS e geração dos listeners. | O gateway de notificações não deve ser incorporado ao proxy. Deve publicar um listener local próprio por meio do mecanismo existente. |
| [`codex_execution.py`](../../onmyoji-daemon/src/onmyoji_daemon/codex_execution.py) | `CodexLaunch` centraliza executável, workspace, sandbox, aprovação, instruções, skills, agentes e MCP. | Toda execução nova deve partir de `CodexLaunch`; não remontar ambiente ou comando em outro módulo. |
| [`codex_app_server.py`](../../onmyoji-daemon/src/onmyoji_daemon/codex_app_server.py) | Transporte persistente com handshake, RPCs correlacionados, filas limitadas, eventos ordenados por thread e estados de perda. O `clientInfo` ainda identifica o Gateway Telegram. | **[T]** Parametrizar a identidade do cliente e criar um executor síncrono reutilizável sobre esse transporte. |
| [`telegram_execution.py`](../../onmyoji-daemon/src/onmyoji_daemon/telegram_execution.py) e [`telegram.py`](../../onmyoji-daemon/src/onmyoji_daemon/telegram.py) | Implementam `thread/start`, `thread/resume`, `turn/start`, espera por `turn/completed`, interrupção, coleta da resposta final e associação persistente por conversa. | Reutilizar protocolos e testes como referência, mas não herdar mixins, banco, ferramentas dinâmicas ou semântica conversacional do Telegram. |
| [`telegram_channels.py`](../../onmyoji-daemon/src/onmyoji_daemon/telegram_channels.py) e [`mcp/telegram/service.py`](../../onmyoji-daemon/src/onmyoji_daemon/mcp/telegram/service.py) | Mantêm catálogo de destinos e envio externo por RPC. Sem chave de idempotência, cada chamada envia novamente; resultado incerto não é repetido automaticamente. | O adaptador Telegram deve consultar o catálogo vigente e enviar sem usar o UUIDv7 como chave de idempotência. |
| [`mcp/email/service.py`](../../onmyoji-daemon/src/onmyoji_daemon/mcp/email/service.py) e [`mcp/email/store.py`](../../onmyoji-daemon/src/onmyoji_daemon/mcp/email/store.py) | Envio SMTP exige conta, confirmação e `request_id`; a mesma chave consulta a submissão anterior e não reenvia. | O UUIDv7 sozinho não pode ser o `request_id`, pois um reenvio do evento precisa gerar nova submissão. Seleção da conta remetente permanece bloqueante. |
| [`telegram_persistence.py`](../../onmyoji-daemon/src/onmyoji_daemon/telegram_persistence.py) | SQLite versionado, lease, recuperação, estados incertos, entregas e expurgo periódico. | Referência para concorrência e migração, não banco compartilhado com o novo serviço. |
| [`setupOnmyoji.py`](../../setupOnmyoji.py) | `D. Daemon` já separa Telegram, MiniApps e Publicação externa; menus usam `screen()`, assistentes e diagnósticos usam fluxo. | Acrescentar dois contextos independentes sem combinar “eventos e agendamentos”. |
| [`docs/setup-interface.md`](../setup-interface.md) | Define moldura, fluxo, escolhas, padrões, confirmações e uso de `X`. | Toda interação nova deve ter sua classificação registrada e testada antes de implementação. |
| [`conftest.py`](../../conftest.py) | Redireciona temporários e bytecode de testes para `tmp/`. | Executar a suíte com `python -B -m pytest`; não criar temporários fora de `tmp/`. |

### 3.2 Restrições observadas

1. O serviço Telegram é simultaneamente receptor conversacional, executor e entregador. Reutilizá-lo como classe base criaria dependência indevida.
2. O `CodexAppServer` é reutilizável como transporte, mas a agregação de resposta e o ciclo de thread ainda estão embutidos no Telegram.
3. O Gateway HTTP preserva `Authorization` do negócio e remove o token RPC interno; isso permite autenticação no listener consumidor, mas não define qual autenticação usar.
4. A configuração de e-mail possui contas SMTP habilitadas, porém o evento conceitual informa somente o endereço destinatário.
5. Os logs de stdout do Supervisor não possuem rotação nem retenção de 20 dias; a política nova precisa de armazenamento próprio.
6. Não há contrato de agendamentos nem origem aprovada para criar suas definições.

<a id="arquitetura"></a>
## 4. Arquitetura alvo

### 4.1 Visão de componentes

```text
emissor cadastrado
    -> listener autenticado do serviço de notificações
    -> contrato JSON compartilhado + validação
    -> registro de recepção (UUIDv7 + timestamp)
    -> orquestrador síncrono
        -> executor do agente
            -> App Server (primeiro adaptador)
            -> Codex CLI (adaptador posterior, após prova)
        -> adaptador de entrega obrigatório
            -> Telegram registrado
            -> e-mail por conta SMTP configurada
        -> log operacional
        -> canal opcional de incidentes

agendador
    -> ação local independente
    -> ou cliente interno do mesmo contrato de notificações

Gateway HTTP compartilhado
    -> apenas publica/proxy o listener local autenticado
```

### 4.2 Limites de responsabilidade

| Componente | Responsabilidade | Não deve fazer |
| --- | --- | --- |
| Contrato | Carregar versão, validar schema e normalizar erro de validação. | Autenticar, executar agente ou consultar destino. |
| Ingresso | Autenticar emissor, limitar transporte, decodificar JSON e chamar o serviço. | Decidir tratamento de acontecimentos na origem. |
| Orquestrador | Registrar recepção, executar, entregar, registrar falha e acionar incidente. | Implementar detalhes de Telegram, SMTP ou protocolo App Server. |
| Executor | Resolver thread, iniciar turno, aguardar terminalidade e devolver resultado. | Entregar resultado ao canal funcional. |
| Adaptador de saída | Validar existência operacional do destino e efetuar uma tentativa controlada. | Deduplicar eventos pelo UUIDv7. |
| Registro operacional | Preservar ocorrência e limpar somente logs vencidos. | Excluir conteúdo na origem ou servir como cache de respostas. |
| Agendador | Calcular disparos e executar a ação definida. | Ser requisito para o gateway ou transformar toda ação em evento. |

### 4.3 Organização proposta de pacotes

**[T]** Organizar por funcionalidade, sem `util` ou `common` genéricos:

```text
onmyoji_daemon/
    agent_execution/
        service.py              # ciclo síncrono independente do canal
        app_server.py           # adaptador do Codex App Server
        cli.py                  # adaptador futuro do Codex CLI
        exception/
    agent_notification/
        contract.py             # seleção de versão e validação
        service.py              # orquestração
        repository.py           # vínculos de thread/configuração operacional
        logging.py              # JSONL, retenção e limpeza
        http.py                 # listener local após gate de autenticação
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

`agent_execution` só deve nascer quando houver contrato reaproveitável real. O primeiro adaptador pode ser implementado dentro de `agent_notification` e extraído no mesmo marco antes de duplicação; não criar abstração vazia.

<a id="contrato"></a>
## 5. Contrato JSON e validação compartilhada

### 5.1 Envelope funcional consolidado

| Campo conceitual | Contrato **[U]** | Situação técnica |
| --- | --- | --- |
| Versão do esquema | Obrigatória e identificável no evento. | **[B]** Nome, representação e política de compatibilidade ainda não aprovados. |
| Identificador | UUID versão 7, gerado pelo emissor, preservado em reenvios. | Validar sintaxe e `UUID.version == 7`; não gerar substituto. |
| Prompt | Texto livre, sem template obrigatório e sem máximo funcional definido. | Limite de bytes do transporte deve ser separado e documentado como proteção operacional. |
| Tipo do canal | Obrigatório; inicialmente Telegram ou e-mail. | **[B]** Nomes JSON e valores literais finais ainda não aprovados. |
| Valor do canal | Obrigatório; ID Telegram registrado ou endereço de e-mail. | Validação estrutural ocorre no schema; existência é verificação operacional posterior. |
| Política de thread | Obrigatória; reutilizar thread associada ao destino ou forçar nova thread. | **[B]** Literais finais e efeito da nova thread sobre a associação existente ainda não aprovados. |

> [!IMPORTANT]
> O plano não atribui nomes finais aos campos. A primeira tarefa da fase de contrato é aprovar um exemplo canônico e congelar esses nomes. Até isso ocorrer, fixtures devem usar marcadores ou uma versão explicitamente experimental não publicada.

### 5.2 Tecnologia recomendada

- **[T]** Usar JSON Schema Draft 2020-12, já disponível por `jsonschema>=4,<5` e já usado nos contratos MCP.
- **[T]** Manter o schema como recurso versionado do pacote, com `additionalProperties: false` em cada objeto fechado.
- **[T]** Criar `EventContract` que selecione a versão, valide o próprio schema com `Draft202012Validator.check_schema()` e devolva erros estáveis por caminho.
- **[T]** Registrar um `FormatChecker` próprio para UUIDv7; o formato genérico `uuid` não garante a versão.
- **[T]** Expor a mesma função de validação para receptor, agendador, scripts locais e futuros emissores Python. O arquivo JSON é a fonte única; não manter validação manual divergente.
- **[T]** Não aplicar coerção silenciosa. Número não vira texto, espaços não são removidos de identificadores e campos desconhecidos falham.

### 5.3 Evolução do contrato

1. Uma versão publicada é imutável.
2. O receptor escolhe o validador pela versão declarada antes de ler campos dependentes dela.
3. Versão desconhecida retorna erro específico e não invoca o agente.
4. Mudança incompatível cria novo arquivo e nova fixture de conformidade.
5. O serviço pode aceitar mais de uma versão durante migração, mas normaliza para um Value Object interno.
6. Emissores recebem schema e casos válidos/inválidos sem importar módulos internos do daemon.
7. O schema não promete existência de canal, disponibilidade do agente, autenticação ou entrega.

### 5.4 Identidade e reenvio

- **[U]** O UUIDv7 é a única identidade de domínio do evento.
- **[U]** O daemon não cria segundo ID de execução nem usa o UUIDv7 como chave de idempotência.
- **[U]** Duas recepções com o mesmo UUIDv7 executam duas vezes e produzem duas respostas.
- **[T]** Chaves internas de banco, índices de linha ou tokens de submissão podem existir como detalhes de armazenamento, mas nunca são expostos como identidade do evento nem alteram a correlação apresentada ao operador.
- **[T]** Todos os registros, erros, chamadas de execução e entregas carregam o UUIDv7 recebido.

### 5.5 Erros de contrato

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

1. autenticar e identificar o emissor;
2. aplicar limite técnico ao corpo sem registrar segredo de autenticação;
3. decodificar JSON;
4. selecionar e validar o schema;
5. capturar o timestamp de recepção definido;
6. registrar a ocorrência aceita com UUIDv7 e emissor;
7. invocar o agente;
8. entregar o resultado ao canal obrigatório;
9. finalizar a resposta ao chamador, se a conexão ainda existir;
10. em falha, registrar log e tentar incidente sem recursão.

Autenticação inválida não é evento recebido e não deve registrar o corpo integral como evento. JSON autenticado, mas inválido, deve gerar registro de rejeição sanitizado conforme a política definida para logs.

### 6.2 Síncrono não significa dependente da conexão

**[U]** A operação lógica aguarda a resposta do agente. O chamador pode acompanhar a resposta ou abandoná-la; abandonar não cria outro modo de execução e não cancela o processamento aceito.

**[T]** Depois do passo 6, a execução pertence ao serviço, não ao socket. No HTTP, cancelamento do handler deve ser isolado do trabalho aceito; no RPC local, o handler já conclui mesmo se o envio da resposta falhar. Isso exige:

- estado em memória ou persistente controlado pelo serviço;
- desligamento gracioso que pare de admitir eventos e trate os ativos conforme a política de recuperação;
- teste de desconexão antes da resposta;
- nenhuma fila pública ou endpoint de consulta de resultado apenas para simular assincronicidade.

### 6.3 Resposta do ingresso

**[B]** O formato HTTP de sucesso/erro, o status para desconexão impossível de observar e a presença do resultado no corpo ainda precisam ser aprovados com a autenticação. O contrato deve distinguir:

- rejeição antes da aceitação;
- execução recusada antes de iniciar o turno;
- execução com terminalidade incerta;
- resultado produzido e entrega confirmada;
- resultado produzido e entrega falha ou incerta.

Não retornar “sucesso” apenas porque o JSON foi aceito quando o chamador permaneceu aguardando o processamento completo.

<a id="execucao"></a>
## 7. Execução pelo agente e threads

### 7.1 Interface do executor

**[T]** Definir uma porta interna semelhante a:

```text
execute(prompt, destination_key, thread_policy, event_id) -> AgentResult
```

`AgentResult` contém estado terminal, texto final e metadados mínimos de diagnóstico. Ele não contém credenciais nem cria outra identidade de evento.

### 7.2 App Server como primeiro adaptador

1. Parametrizar `CodexAppServer` para receber `clientInfo`, mantendo o valor atual do Telegram por compatibilidade.
2. Iniciar o processo por `CodexLaunch.from_system(...).app_server_launch()`.
3. Usar `CodexLaunch.developer_instructions()` para o contexto não Telegram.
4. Para nova thread, chamar `thread/start` com cwd, aprovação, sandbox e instruções; não registrar ferramentas Telegram.
5. Para reutilização, buscar vínculo do destino e chamar `thread/resume`.
6. Se a ausência da thread for conclusiva, aplicar a política aprovada de recriação; falha transitória não apaga vínculo.
7. Chamar `turn/start` com o prompt como input do usuário e aguardar `turn/completed` correlacionado.
8. Coletar somente resposta final correlacionada; comentário intermediário não é terminalidade.
9. Propagar falha, interrupção e perda de transporte como estados distintos.
10. Parar por ociosidade apenas quando não houver turno, evento admitido ou entrega pendente.

### 7.3 Codex CLI

**[U]** O gateway pode invocar App Server ou Codex CLI. **[I]** O repositório atual centraliza lançamento do CLI, mas não possui um executor não interativo com contrato de resultado equivalente.

Antes de criar o adaptador CLI:

- provar comando, formato de saída, timeout, cancelamento, cwd, sandbox, aprovação e retomada de thread no executável configurado;
- registrar as capacidades numa validação local versionável, sem credenciais ou prompts reais;
- manter o App Server como caminho padrão se o CLI não oferecer equivalência;
- não analisar texto de terminal instável quando houver protocolo estruturado disponível.

### 7.4 Associação de thread

**[T]** A chave funcional proposta é `(channel_type, channel_value)`, pois a definição do usuário associa thread ao destino. O repositório deve persistir o ID da thread, revisão das instruções e atualização.

**[B]** Antes de implementar `force_new`, decidir se a nova thread substitui a associação usada por futuras solicitações `reuse` ou se é isolada e descartável. Também decidir concorrência de dois eventos `reuse` para o mesmo destino. Até lá, não inferir serialização, steering ou paralelismo.

### 7.5 Timeout e cancelamento

**[B]** São bloqueantes para produção. O primeiro corte usa prazos curtos de teste injetados, sem transformar esses valores em defaults. A decisão deve cobrir:

- prazo de inicialização, turno e entrega;
- interrupção pelo App Server e período de graça;
- resultado incerto após timeout;
- desligamento do daemon com execução ativa;
- se o emissor pode solicitar cancelamento e como se autentica;
- ausência de retry automático quando a aceitação do turno ou um efeito externo for incerta.

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
- **[U]** O log pode conter emissor, entrada integral, saída integral e falha, sob a premissa de acesso exclusivo do proprietário.
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
- identidade autenticada do emissor;
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
- recibo de entrega necessário para impedir repetição de uma tentativa incerta.

Não criar tabela que devolva resultado anterior ao receber o mesmo UUIDv7. Logs não são consultados para deduplicar.

### 10.2 Repositório proposto

**[T]** SQLite próprio em `configs/daemon/services/<serviço>/state/notifications.sqlite3`, com lease de escritor e migrações explícitas. Não compartilhar `gateway.sqlite3` do Telegram.

Tabela inicial necessária após decisão de threads:

```text
schema_migrations
destination_threads(channel_type, channel_value, thread_id,
                    instruction_revision, updated_at)
```

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
## 11. Publicação, autenticação e emissores

### 11.1 Publicação

**[T]** O serviço de notificações declara um listener HTTP local com `publication.describe`; o Supervisor valida porta e prefixo e o Gateway HTTP compartilhado o publica. O serviço continua utilizável pelo RPC privado sem Gateway ou Tunnel.

Não adicionar rota de negócio diretamente em `PublicGateway`: isso misturaria proxy compartilhado, autenticação de emissores e execução do agente no mesmo processo.

### 11.2 Gate de segurança

**[B] BLOQUEIO P0:** definir o que é integração cadastrada, credencial, autenticação, revogação e permissão antes de implementar ou habilitar o listener externo.

A decisão precisa responder:

- como a integração recebe e apresenta credencial;
- onde a referência da credencial é armazenada e como o segredo usa o vault;
- se há escopo por destino, versão ou operação;
- como ocorre cadastro, rotação e revogação;
- quais dados identificam o emissor nos logs;
- proteção contra replay de transporte, sem confundir replay com deduplicação de evento;
- limites por emissor e resposta a abuso;
- se o prefixo público e o listener ficam disponíveis sem Tunnel.

O Gateway HTTP já preserva `Authorization`, mas isso é apenas capacidade de transporte. Não escolher Bearer, HMAC, mTLS ou outro mecanismo por inferência.

### 11.3 Princípios obrigatórios

- segredo nunca entra no JSON do evento nem no log;
- identidade vem da autenticação, não de campo autodeclarado;
- cadastro é configuração administrativa, não chamada anônima;
- uma integração cadastrada pode emitir conforme a política aprovada;
- autenticação não transforma UUIDv7 em nonce nem impede reprocessamento funcional;
- endpoint externo fica desabilitado por padrão durante rollout.

<a id="agendador"></a>
## 12. Agendador como fase independente

### 12.1 Fronteira

**[U]** O agendador administra definições temporais e dispara ações. Notificar o agente é uma ação possível, não sua finalidade exclusiva.

**[T]** Implementar em processo supervisionado separado, com pacote `task_scheduler`, namespace RPC `task.` já reservado e banco próprio. Ele não importa repositório interno do gateway; usa uma porta de aplicação ou RPC autenticado para emitir o mesmo envelope validado.

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
- habilitar/desabilitar serviço;
- cadastro e revogação de emissores;
- configuração do canal operacional de incidentes;
- consulta do estado de publicação;
- teste controlado sem enviar a destino real por padrão.

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
| `registry.py` | Registrar serviço de notificações e posteriormente o agendador; preservar `task.` para um único `ServiceSpec`. |
| `supervisor.py` | Adicionar timeouts/namespaces específicos somente se o contrato exigir; reaproveitar lifecycle e publicação. |
| `cli.py` | Administração e diagnóstico coerentes com os serviços; não transformar CLI isolada na interface principal de configuração. |
| `codex_app_server.py` | Parametrizar `clientInfo` sem quebrar o Telegram. |
| `codex_execution.py` | Continuar fonte única de launch; acrescentar somente suporte comprovado ao adaptador CLI. |
| `agent_execution/` | Novo contexto de execução síncrona, terminalidade e threads. |
| `agent_notification/` | Novo contrato, serviço, listener, repositório, logs e adaptadores. |
| `gateway/` | Nenhuma regra de negócio nova; apenas testes de publicação do novo consumidor pelo mecanismo existente. |
| `telegram_channels.py` / `mcp/telegram/service.py` | Extrair ou reutilizar fachada segura de catálogo e envio; preservar contratos MCP. |
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
| 0 — Decisões P0 | Auth/cadastro, nomes do envelope, timestamp e política mínima de timeout documentados | Aprovação do proprietário | Não há placeholder funcional nem endpoint público aberto. |
| 1 — Contrato compartilhado | Schema v1, loader, UUIDv7, erros e fixtures | Fase 0 para nomes | Emissor e receptor usam o mesmo artefato; 100% dos ramos unitários do validador cobertos. |
| 2 — Executor síncrono | Cliente App Server parametrizado, thread/turno/terminalidade e fakes | Contrato de threads e timeout | Turno válido conclui; falha, perda e timeout não viram sucesso. |
| 3 — Corte vertical privado | Serviço supervisionado, RPC privado, log de recepção, App Server e Telegram por adaptador | Fases 1–2 | Mesmo UUID duas vezes produz duas execuções e duas tentativas de entrega. |
| 4 — Persistência e recuperação | Associação destino-thread, migrações, lease e política de restart | Decisões de retomada/concurrency | Crash e restart têm estado determinístico e não repetem efeito incerto. |
| 5 — Entregas completas | Telegram consolidado, e-mail, recibos e falhas | Conta SMTP e retry decididos | Destino inválido, removido, aceito, falho e incerto cobertos. |
| 6 — Logs e incidentes | JSONL, retenção 20 dias e canal opcional | Timestamp e canal decididos | Limpeza não alcança dados fora dos logs; falha do incidente não recursa. |
| 7 — Ingresso autenticado | Listener HTTP, cadastro, revogação, limites e publicação | Gate P0 de segurança | Testes de auth/replay/abuso e publicação; desabilitado por default. |
| 8 — Setup do gateway | Menu, diagnóstico e configuração operacional | Fases 5–7 | Interface segue `setup-interface.md`; sem dependência do canal opcional. |
| 9 — Fundação do agendador | Contrato, repositório e RPC de listagem | Bloqueios do agendador | Listagem determinística, sem criação/edição. |
| 10 — Setup do agendador | Tela ordenável por qualquer coluna exibida | Fase 9 e desenho aprovado | Atalhos, defaults, `X`, nulos e paginação testados. |
| 11 — Execução temporal | Cálculo, ações locais e emissão opcional ao gateway | Contrato temporal e de ações | Agendador funciona sem gateway; integração reutiliza o mesmo schema. |
| 12 — Rollout | Diagnóstico, documentação, migração e ativação gradual | Todas as fases aplicáveis | Suíte completa verde e validação manual controlada registrada. |

<a id="vertical"></a>
## 16. Primeiro corte vertical

O primeiro corte não inclui o agendador nem o endpoint público. Ele deve validar a arquitetura de ponta a ponta com o menor conjunto seguro:

```text
RPC privado autenticado do Supervisor
    -> serviço agent-notifications desabilitado por padrão
    -> schema v1 compartilhado
    -> log de recepção
    -> App Server por executor novo
    -> thread nova
    -> adaptador Telegram para canal registrado
    -> recibo síncrono ao chamador
```

### 16.1 Restrições do corte

- usar `force_new` no cenário de demonstração até a semântica da associação ser aprovada;
- não publicar listener HTTP;
- não enviar e-mail;
- não configurar canal de incidentes;
- não criar tabela de cache ou resultado;
- não habilitar serviço automaticamente em instalação existente;
- usar fakes em CI e destino real somente em validação manual autorizada;
- prazo de teste injetado, não default de produção.

### 16.2 Aceite do corte

1. JSON inválido é rejeitado antes do App Server.
2. UUID que não é v7 é rejeitado.
3. Evento válido registra exatamente o UUID recebido e timestamp.
4. O executor inicia thread e turno, aguarda terminalidade e extrai resposta final.
5. O adaptador entrega ao ID Telegram registrado sem chave de idempotência.
6. Repetir o mesmo payload e UUID chama executor e entrega novamente.
7. Fechar o cliente RPC depois da aceitação não cancela execução nem entrega.
8. Falha de canal produz log operacional e resposta coerente quando o chamador aguarda.
9. Nenhum segundo ID de evento aparece em resposta, log ou entrega.
10. Parar o processo durante turno não causa retry automático ao reiniciar.

Esse corte valida as fronteiras. Ele não deve ser anunciado como produção até timeout, recuperação, concorrência, segurança e políticas de entrega estarem decididos.

<a id="testes"></a>
## 17. Testes e critérios de aceite

### 17.1 Arquivos de teste propostos

| Arquivo | Cobertura principal |
| --- | --- |
| `test_agent_notification_contract.py` | Schema, versões, UUIDv7, campos obrigatórios, nulos, extremos e propriedades extras. |
| `test_agent_notification_service.py` | Ordem do fluxo, reenvio, desconexão, falhas e correlação. |
| `test_agent_notification_execution.py` | Thread start/resume, turno, eventos fora de ordem, terminalidade, timeout e perda. |
| `test_agent_notification_delivery.py` | Telegram/e-mail, destino removido, recibo confirmado/falho/incerto e reenvio. |
| `test_agent_notification_logging.py` | Conteúdo, sanitização de credencial, UTC/formato aprovado e retenção. |
| `test_agent_notification_http.py` | Auth, limites, JSON, cancelamento do cliente e publicação pelo proxy. |
| `test_agent_notification_supervision.py` | Registry, lifecycle, namespace, recuperação e declaração HTTP. |
| `test_task_scheduler_contract.py` | Regra temporal e ações após contrato aprovado. |
| `test_task_scheduler_service.py` | Cálculo, concorrência, restart, ação local e emissão opcional. |
| `tests/test_setup_onmyoji.py` | Menus separados, fluxos, defaults, `X` e listagem ordenável. |

### 17.2 Matriz mínima do gateway

| Área | Casos críticos |
| --- | --- |
| JSON | vazio, não objeto, UTF-8 inválido, versão ausente/desconhecida, extra, tipo incorreto e objeto profundamente inválido. |
| UUIDv7 | válido, versões 1/4/6/8, caixa, hífens, nulo e valor quase válido. |
| Prompt | vazio conforme decisão final, acentos, quebras, conteúdo grande dentro do limite técnico e corpo acima do limite de transporte. |
| Canal | tipos aceitos, tipo desconhecido, valor vazio, e-mail malformado, Telegram não registrado e destino removido durante execução. |
| Reenvio | mesmo UUID/payload, mesmo UUID/payload alterado e duas recepções no mesmo milissegundo; todas processam novamente. |
| Thread | nova, reuse sem vínculo, reuse válido, thread removida, resume transitório, concorrência no mesmo destino e destinos diferentes. |
| App Server | resposta antes do ACK, evento antigo, item final sem terminalidade, terminal falho, interrupção e transporte perdido. |
| Desconexão | RPC/HTTP fechado antes de conclusão, shutdown gracioso e processo morto. |
| Telegram | catálogo indisponível, `sent` true/false/null, mensagem acima do limite e nenhuma idempotência pelo UUID. |
| E-mail | conta ausente, destinatário inválido, aceito, parcialmente aceito, rejeitado, incerto e duas submissões do mesmo UUID. |
| Incidente | ausente, confirmado, falho e sem recursão. |
| Logs | repetição de UUID, conteúdo integral permitido, segredo excluído, arquivo parcial, limpeza e confinamento de caminho. |
| Segurança | credencial ausente/inválida/revogada, identidade falsa no corpo, replay de transporte e rate limit aprovado. |

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
python -B -m pytest onmyoji-daemon/tests/test_codex_app_server.py onmyoji-daemon/tests/test_codex_execution.py
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
10. somente depois testar publicação externa com emissor cadastrado e credencial de homologação.

Nenhum teste manual usa conversa, e-mail, credencial ou conteúdo real como fixture versionada.

<a id="rollout"></a>
## 18. Migração, rollout e rollback

### 18.1 Migração

- serviços novos ausentes em `services.json` equivalem a desabilitados;
- criar configuração e banco somente quando setup/bootstrap autorizado ou serviço iniciar;
- não migrar nem copiar o banco Telegram;
- consultar canais Telegram pelo contrato existente;
- reutilizar configuração de e-mail sem copiar credenciais;
- schema e banco novos começam em versão 1 com migrações testadas;
- nenhum agendamento legado existe para importar;
- não alterar automaticamente a habilitação de Gateway HTTP ou Tunnel.

### 18.2 Rollout

1. publicar contrato e testes sem serviço ativo;
2. publicar executor e corte privado desabilitado;
3. validar App Server e Telegram em homologação;
4. habilitar persistência/recuperação;
5. habilitar e-mail e incidentes separadamente;
6. publicar listener somente local;
7. publicar externamente para um emissor de homologação;
8. ampliar emissores e destinos gradualmente;
9. iniciar fundação do agendador somente após estabilidade do contrato compartilhado;
10. ativar disparos reais do agendador após contrato temporal homologado.

### 18.3 Rollback

- desabilitar o serviço no Supervisor e invalidar sua publicação;
- preservar configuração, logs ainda dentro da retenção e banco para diagnóstico;
- não apagar canais Telegram, contas de e-mail ou conteúdo na origem;
- não reexecutar eventos ou disparos interrompidos automaticamente;
- restaurar versão anterior do código somente com schema de banco compatível ou migração reversa testada;
- documentar estados incertos que exijam decisão do proprietário.

<a id="riscos"></a>
## 19. Riscos e mitigação

| Risco | Impacto | Mitigação planejada |
| --- | --- | --- |
| Confundir o novo gateway com o proxy HTTP existente | Processo privilegiado e responsabilidades misturadas | Serviço consumidor separado, publicado pelo mecanismo atual. |
| Reusar classes Telegram diretamente | Acoplamento a conversa, tools, banco e geração | Executor e adaptadores por portas explícitas. |
| Usar UUIDv7 como idempotência | Reenvio não processa novamente | Teste obrigatório com mesmo UUID; nenhuma unicidade de evento. |
| `request_id` SMTP igual ao UUIDv7 | Segunda recepção consulta resultado anterior | Chave privada por recepção e teste de duas submissões. |
| Desconexão cancelar execução | Evento aceito fica sem processamento/entrega | Transferir posse ao serviço depois da aceitação e testar cancelamento do cliente. |
| Thread inexistente ou resume transitório | Perda de contexto ou resposta na thread errada | Diferenciar ausência conclusiva de falha transitória; vínculo persistente. |
| Dois eventos no mesmo destino | Corrida na associação/turno | Política de concorrência bloqueante e locks por chave após aprovação. |
| Timeout após efeito externo | Duplicação por retry cego | Estados incertos e nenhuma repetição automática. |
| Logs integrais exporem credencial | Comprometimento operacional | Separar headers/segredos do payload funcional e sanitizar sempre. |
| Limpeza apagar dados fora do log | Perda de estado/conteúdo na origem | Diretório confinado, nomes reconhecidos e testes destrutivos em `tmp_path`. |
| Incidente falhar e gerar loop | Tempestade de mensagens/logs | Uma tentativa controlada; falha termina em log local. |
| Endpoint público antes de auth | Execução arbitrária do agente | Gate P0 e publicação desabilitada por padrão. |
| Agendador nascer acoplado ao gateway | Ações locais passam a depender do agente | Contrato de ação e processo próprios; cliente interno opcional. |
| Setup exceder escopo inicial | Criação/edição não aprovada | Testes negativos da interface e backend somente leitura. |
| CLI não interativo não ser equivalente | Resultado frágil ou contexto divergente | Gate de capacidade; App Server permanece padrão. |

<a id="bloqueios"></a>
## 20. Perguntas bloqueantes

### P0 — antes do endpoint público

1. Qual mecanismo autentica e cadastra integrações emissoras?
2. Quais permissões e limites existem por integração?
3. Como ocorre rotação/revogação e qual identidade aparece no log?
4. Qual prefixo público e nome funcional do serviço serão usados?

### P1 — antes de congelar o schema v1

1. Quais são os nomes JSON finais e a representação da versão?
2. O destino é objeto aninhado ou campos no nível raiz?
3. Quais literais representam Telegram, e-mail, reuse e nova thread?
4. Prompt vazio é válido?
5. Qual limite técnico de corpo protege RPC/HTTP sem virar máximo funcional do prompt?

### P2 — antes da execução de produção

1. Qual formato/timezone do timestamp de recepção?
2. Quais timeouts e regras de cancelamento?
3. `force_new` substitui a associação do destino?
4. Como serializar ou paralelizar eventos do mesmo destino?
5. Como reconciliar e retomar após restart?

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

<a id="checklist"></a>
## 21. Checklist de conclusão

### Gateway de notificações

- [ ] Decisões P0–P3 registradas e aprovadas.
- [ ] Schema v1 imutável, distribuível e validado nos dois lados.
- [ ] UUIDv7 validado e reutilizado sem segundo ID de evento.
- [ ] Mesmo UUIDv7 comprovadamente reprocessado.
- [ ] Executor App Server independente do Telegram.
- [ ] Adaptador CLI incluído somente se a equivalência for comprovada.
- [ ] Duas políticas de thread implementadas conforme decisão explícita.
- [ ] Telegram e e-mail com recibos e estados incertos corretos.
- [ ] Desconexão do chamador não cancela evento aceito.
- [ ] Logs integrais permitidos, segredos excluídos e retenção de 20 dias testada.
- [ ] Canal de incidentes opcional e sem recursão.
- [ ] Autenticação, cadastro, revogação e limites testados.
- [ ] Listener externo desabilitado por padrão e publicado pelo Gateway compartilhado.
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
