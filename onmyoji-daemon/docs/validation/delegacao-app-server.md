# Validação de capacidades de delegação no App Server

Revisão: 2026-11-05. Relacionado ao [plano de delegação e Gateway](../plans/delegacao-gateway-subagentes.md), etapa E0 e gates G0–G7.

## Resultado e alcance da prova

Foi executado o binário **Codex CLI 0.166.0**, no Windows, em transporte stdio, contra um provider Responses determinístico restrito a `127.0.0.1`. São execuções reais do App Server, da persistência de threads e das ferramentas de colaboração do runtime. Nenhuma conta, token, API Telegram, MCP de negócio ou serviço externo de negócio foi utilizado. O provider só produz texto fictício e chamadas de colaboração para tarefas fictícias; shell, apps, plugins e memórias estão desabilitados no laboratório.

O executável foi resolvido pelo PATH, porque o checkout restaurado não contém configuração operacional da instância. O runtime usa posteriormente a configuração real de `CodexLaunch` e só aceita este certificado quando o hash do executável selecionado coincidir. A prova não certifica disponibilidade de conta/modelo, permissões de serviços, qualidade da inferência ou latência de serviços externos.

| Identidade | Valor |
| --- | --- |
| Versão observada | `codex-cli 0.166.0` |
| SO / transporte | Windows / stdio |
| Revisão do adaptador | `1` |
| SHA-256 do executável | `9d563ee4e0441ff9d59d612013e0460ddf8e5709735c2d9f6b98b7ef8ca4eb42` |
| SHA-256 agregado do esquema gerado | `a63d6a0e71eee85c1bbe229a2d997a6270ae458c24c2ad4e2b69dc1b7d5d7664` |

O [certificado sanitizado](../../resources/codex-capabilities/9d563ee4e0441ff9d59d612013e0460ddf8e5709735c2d9f6b98b7ef8ca4eb42.json) é metadado versionável de compatibilidade; não contém estado de instância, prompts, caminhos locais, IDs de sessão ou credenciais. Alteração de binário, transporte, SO ou revisão invalida a certificação. O hash do esquema é registrado junto ao binário que o gerou; a presença de um campo no esquema não é tratada como teste de execução.

A rodada final do runner terminou com exit 0 em 5,77 segundos, sem
`probe_failure`, incluindo o check de phase ausente. O artefato local está em
`tmp/codex-probe-03d666848120473695a6cca1a9226f5b/report.json`.

## Gates exercitados

| Gate | Estado | Evidência observada e limite | Alternativa aplicada |
| --- | --- | --- | --- |
| G0 — ciclo básico | `supported` | Handshake, criar thread, iniciar/concluir turno, manter processo vivo, retomar thread e iniciar outro turno. Após encerrar/reabrir o processo, a thread foi retomada e recebeu um novo turno. Namespace dinâmico `telegram_gateway` e chamada `ask_menu` também foram exercitados com retorno fictício de seleção e erro | Binário desconhecido segue sem certificação; não perder referência de thread por erro transitório |
| G1 — steering | `supported` | Acrescentar múltiplos inputs ao mesmo turno real; ID esperado errado é recusado; não há segundo `turn/started`; steering depois de `turn/completed` é recusado. Foram aceitos imagem local e envelopes textuais de documento/voz/áudio com procedência | Política continua `queue` por padrão; só usar steering mediante opção explícita e matriz de inputs certificada |
| G2 — reconciliação de entrada | `supported` | `clientUserMessageId` de `turn/start` e de `turn/steer` reaparece como `userMessage.clientId` na história retornada por `thread/items/list`. A consulta reconcilia aceitação sem reutilizar o ACK como única prova | Ausência de marcador não prova recusa. Timeout sem evidência continua `uncertain`; não retransmitir automaticamente |
| G3 — filhos no turno | `supported` | Principal criou dois filhos, aguardou todos e consolidou. Falha HTTP controlada de um filho chegou ao principal como `errored`; interrupção por `send_input(interrupt=true)` preservou turno interrompido na história do filho. Filhos concluídos foram fechados pelo principal | Sem G3, principal executa sozinho; nenhuma herança implícita de autorização de canal |
| G4 — publicação segura de perfis | `unsupported` para snapshot de abertura | TOML em `.codex/agents` foi efetivamente selecionado: o marcador das developer instructions apareceu no request do filho. Porém, após trocar V1 por V2, outro filho na **mesma thread e processo** já recebeu V2. Isso demonstra leitura dinâmica e invalida a premissa `snapshot=startup` | Recusar materialização habilitada sem contrato de leases ou revisão imutável comprovado. Instância sem perfis continua válida |
| G5 — término do principal | `supported`, no processo vivo | Principal concluiu antes de um filho lento. Depois da liberação, o filho concluiu e seu resultado permaneceu consultável. A conclusão tardia não iniciou automaticamente outro turno do principal | Continuidade posterior exige monitor/registro e `turn/start` explícito; não prometer despertar nativo automático |
| G6 — perda do processo | `unsupported` | O processo foi encerrado enquanto um filho estava ativo. Ao reabrir e retomar a thread filha, a execução anterior estava `interrupted`; `resume` não reiniciou geração | Marcar trabalho interrompido/indeterminado e reconciliar. Nenhum replay automático de tarefas ou efeitos externos |
| G7 — resultado como dado | `supported` | `turn/start` com `input: []` e `toolOutput` iniciou execução e persistiu o resultado como `functionCallOutput`, preservando sua natureza de saída de ferramenta | Não converter resultado de tarefa em solicitação do owner ou developer instruction |

“Supported” indica o contrato específico exercitado no engine do binário identificado. Não implica exatamente uma entrega em Telegram, execução durável de ação externa nem sobrevivência de subagente após perda do processo.

### Limite de G4

A descoberta foi exercitada em workspace com Git e em workspace sem `.git` próprio, com marcador explícito de raiz de projeto e confiança apenas na configuração descartável do laboratório. A prova de alteração V1→V2 foi feita no mesmo thread do App Server, verificando o conteúdo recebido **pelo filho**, sem confundir a lista de perfis apresentada ao principal com aplicação de instruções especializadas.

A seleção e o ciclo de reload dos três clientes não foram certificados como um snapshot seguro. A falha já observada no App Server é suficiente para reprovar o contrato cumulativo de publicação; não há certificado `startup` para CLI nativo ou Console apenas por compartilharem configuração. Registro explícito por `config_file` também não foi considerado um snapshot imutável automaticamente. O mecanismo de cópias pode ser testado com certificados fictícios, mas habilitação neste runtime exige uma modalidade segura adicional ou outra versão validada.

### Limite de G2 e das entregas

O probe descartou o uso do ACK como prova exclusiva e procurou os marcadores na história persistida. Isso confirma a rota de reconciliação do engine; testes do Gateway cobrem as janelas de perda de transporte e commit. Não há afirmação de que repetir `clientUserMessageId` deduplica ações automaticamente. Tampouco uma outbox SQLite prova entrega única em Telegram ou em MCPs: resultado de envio perdido após escrita possível permanece incerto e exige reconciliação/decisão.

## Reproduzir sem contas reais

A partir da raiz do repositório:

```powershell
python -B onmyoji-daemon/tools/validate_codex_delegation.py
```

O destino padrão é uma pasta nova em `tmp/`. Para escolher executável e destino explicitamente:

```powershell
python -B onmyoji-daemon/tools/validate_codex_delegation.py --executable codex --output tmp/codex-probe-revisao
```

A pasta deve estar vazia. O runner cria `CODEX_HOME`, workspace, provider e esquema dentro dela e recusa destino fora de `tmp/`. Não copia autenticação, não importa configuração de produção e não instala o certificado numa instância. O relatório `report.json` contém apenas diagnóstico sanitizado. Um relatório com `probe_failure` não pode ser promovido a certificado completo; gates não exercitados permanecem desabilitados.

O runner usa o modelo fictício `probe` apenas para ativar o engine com provider local. A representação de ferramentas pode variar por modelo/client. Nesta execução, colaboração foi exposta como namespace `multi_agent_v1`; o protocolo do App Server emitiu itens `collabAgentToolCall`, com `senderThreadId`, `receiverThreadIds` e `agentsStates`. Não inferir ausência de subagentes a partir de outro nome de namespace, nem usar esse namespace do provider como API pública do Gateway.

### Compatibilidade de final sem phase

Um probe real complementar omitiu `phase` na mensagem Responses do provider
local. O App Server concluiu o turno e persistiu `agentMessage` com `phase:null`
e o texto esperado em `thread/read`. A evidência está em
`tmp/phase-none-e2ea78fb39cb4456a35544deb0081533/report.json`; o mesmo caso foi
incorporado ao runner E0 como `missing_response_phase_persisted_as_null` em G0.
O certificado ganhou somente essa evidência, sem habilitar gates adicionais.
Os testes do Gateway verificam separadamente que itens finais ficam guardados
por ID/ordem, que a entrega seleciona o último e que failed/interrupted produzem
resposta parcial. Essa é compatibilidade do fingerprint validado, não inferência
a partir da presença de um campo opcional no esquema.

### Ferramentas dinâmicas do Gateway

O probe declarou `dynamicTools` na mesma forma usada pelo Gateway: um objeto
`type: "namespace"`, `name: "telegram_gateway"` e uma lista `tools` com funções
que usam `inputSchema`. O provider chamou `ask_menu` sem Telegram real. O App
Server enviou server request `item/tool/call` com `namespace`, `tool`, `threadId`
e `turnId` correlacionados; o cliente respondeu pelo ID original com
`contentItems` e `success`. Tanto seleção fictícia (`success: true`) quanto erro
controlado (`success: false`) retornaram ao modelo antes de concluir o turno.
Essa é prova de compatibilidade wire; a validação de owner, clique, expiração e
geração continua sendo responsabilidade dos testes do Gateway.

## Pipeline real do Gateway com Telegram falso

Também foi executado [validate_telegram_delegation.py](../../tools/validate_telegram_delegation.py),
que usa Gateway e App Server reais, o mesmo provider loopback e uma API Telegram
falsa. As variáveis de autenticação são retiradas do ambiente desse laboratório,
`CODEX_HOME` é descartável e todos os caminhos ficam em `tmp/`. O registro MCP é
declarado sincronizado apenas na fixture sem servidores, para não instalar nem
conectar integração real durante a prova.

A rodada aprovada verificou:

1. Nova entrada foi aceita por steering durante o turno ativo, mantendo o
   comentário `PROBE_PROGRESS` visível enquanto o trabalho continuava.
2. A limpeza do comentário só ocorreu depois da entrega terminal do respectivo
   turno. O provider mantém também a requisição sucessora bloqueada: steering
   pode cancelar a requisição de modelo em andamento, e isso não é falha de fila.
3. O principal concluiu enquanto um filho conhecido ainda estava ativo.
4. O resultado tardio gerou exatamente uma inbox `route=result`, com procedência
   `SYSTEM_EVENT` e `authority=data_only`, e iniciou continuação do principal.
5. A consulta ao histórico real confirmou `functionCallOutput` e ausência de
   `userMessage` no turno de continuação, sem personificar uma nova autorização.
6. As três execuções de steering e continuação usaram a mesma thread persistida.
7. Um menu real `ask_menu` ficou pendente enquanto outra conversa concluiu pelo
   mesmo transporte. O callback do owner sintético selecionou a opção B, a
   seleção voltou ao modelo e o menu foi removido após a conclusão.
8. A rodada ampliada teve 24 execuções em três threads: as três iniciais, vinte
   menus e uma execução paralela. Não houve envio real em Telegram nem uso de
   conta/modelo remoto.
9. O checkpoint privado de processo já continha PID/epoch ativos ao chegar o
   primeiro input ao modelo. No shutdown do Gateway, passou a `stopped` depois
   da saída nativa confirmada.

Para reproduzir, a partir da raiz:

```powershell
python -B onmyoji-daemon/tools/validate_telegram_delegation.py
```

Na rodada ampliada com vinte amostras por fronteira, o p95 de input pronto até
aceitação por steering foi **69,584 ms**; recepção de updates sintéticos até
persistência/checkpoint durável, **7,552 ms**; callback até a chamada de ACK na
API falsa, **0,707 ms**. São medidas locais, sem anexo/transcrição, rede externa,
preparação de voz nem startup inicial. O callback foi exercitado diretamente no
handler após um menu real; seu tempo não inclui polling nem HTTP Telegram. Os
updates de medição de recepção eram de remetente não pareado e não iniciaram
geração. A evidência local desta rodada está em
`tmp/gateway-live-a5d4e68b25c542ce9283b02d4022f64d/report.json`.

A rodada integrada anterior permanece em
`tmp/gateway-live-50aeb4cc023345ea9e4cb6a8d46f0ed3/report.json`, também aprovada
com 24 execuções, três threads e vinte amostras por fronteira; seus p95 foram
79,686 ms, 9,911 ms e 0,916 ms, respectivamente. São rodadas locais distintas,
sem combinar amostras nem apresentar os números anteriores como a rodada final.

O destino padrão é uma pasta nova em `tmp/gateway-live-<id>/`; `--output` aceita
somente destino vazio dentro de `tmp/`. `--samples` aceita de 1 a 32 e usa 20 por
padrão; o relatório informa contagem e p95 de cada fronteira. `--executable` permite escolher o binário,
que precisa ter certificado compatível para as capacidades usadas. O relatório
`report.json` contém checks, contagens e resultado sanitizados. A prova começa na
entrada pronta da inbox e não substitui pairing, autenticação, polling e rede
Telegram: essas fronteiras são verificadas separadamente pelos testes locais.
Não houve promessa de disponibilidade de conta ou de latência externa.

## Identidade nativa e processo órfão

O wrapper [telegram_process.py](../../src/onmyoji_daemon/telegram_process.py)
registra no checkpoint privado PID, epoch, criação nativa e hashes do executável
e da instância após initialize, antes de permitir geração. Stdio perdido não é
reconectado: uma abertura seguinte precisa confirmar morte ou encerrar somente
o processo cuja identidade completa ainda coincide. PID reutilizado ou outro
executável não é morto; identidade inacessível mantém a abertura bloqueada.
`mark_stopped` exige morte/substituição confirmada, e atualizações usam CAS por
epoch para não encerrar o registro de um processo substituto.
Mudança apenas do hash do arquivo, com PID/criação/path ainda iguais, é ambígua:
os bytes no disco podem ter mudado enquanto o processo conserva código já
carregado. Nesse caso não há término nem liberação para abrir outro processo.

Os 28 testes de [test_telegram_process.py](../../tests/test_telegram_process.py)
passaram, incluindo um Popen Windows descartável real. A consulta usa
`GetProcessTimes` e `QueryFullProcessImageNameW`; o término e a espera de até
cinco segundos conservam o mesmo handle. O runner Gateway real confirmou o
checkpoint antes do input ao modelo e sua conclusão após shutdown. A alternativa
Linux usa `/proc`, ID do boot e pidfd, mas não foi executada neste host Windows.
A matriz de crash, perda e ociosidade foi consolidada na suíte final registrada
no inventário. Isso recupera a posse do processo, sem mudar o resultado G6: uma
execução filha interrompida não sobrevive por causa do checkpoint.

Posteriormente, a identidade/lease Linux foi executada nativamente num kernel
Linux em VM isolada, e o materializador foi ensaiado entre volumes Windows
distintos. A [validação de plataformas](plataformas-delegacao-gateway.md) registra
os novos hashes e limites. Ela não certifica o engine Codex em Linux nem altera
os resultados G4/G6 deste relatório Windows.

As APIs nativas foram conferidas na documentação primária:
[criação do processo](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes),
[imagem executável](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-queryfullprocessimagenamew),
[término assíncrono](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)
e [confirmação por espera no handle](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject).
A portabilidade Linux usa [pidfd_open](https://docs.python.org/3.12/library/os.html#os.pidfd_open)
e [pidfd_send_signal](https://docs.python.org/3.12/library/signal.html#signal.pidfd_send_signal),
mantendo bloqueio seguro quando esses recursos não estão disponíveis.

## Uso do certificado no produto

`CapabilitySet.from_instance()` lê primeiro `configs/shikigami/codex-capabilities.json`, caso exista. Esse relatório privado tem precedência inclusive para revogar ou limitar uma capacidade. Se não existir, procura certificado versionado pelo hash do executável. Relatório inválido, incompatível ou sem evidência `runtime` mantém as capacidades afetadas desligadas. Nunca gerar ou alterar certificação durante recebimento de uma mensagem Telegram.

O Gateway ainda decide a política efetiva pelo config local, identidade da origem e estado da execução. Um certificado não liga steering automaticamente. `post_turn` exige G3+G5+G7, enquanto retomada de trabalho após perda do processo exige adicionalmente G6; nesta versão, esta segunda modalidade está desligada. `load_profile_capability()` entrega ao materializador somente a decisão e os campos opcionais expressamente comprovados em G4; no certificado atual, a publicação de perfis fica bloqueada. Resultado já salvo no banco do Gateway é dado durável e pode ser recuperado sem presumir sobrevivência de worker; G6 limita a execução ainda em andamento, não transforma dado confirmado em trabalho perdido.

## Referências oficiais revalidadas

- [App Server](https://learn.chatgpt.com/docs/app-server): início/retomada de threads, steering, eventos, geração de esquema e resultado de ferramenta. Os nomes e eventos efetivamente observados no binário são registrados acima para não depender só de exemplos da página.
- [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents): formato TOML, perfis por projeto e herança de permissões/MCPs. Não estabelece sobrevivência automática após encerrar processo.
- [Advanced Configuration](https://learn.chatgpt.com/docs/config-file/config-advanced): descoberta por raiz de projeto e camadas de configuração, usadas apenas no laboratório descartável.
- [Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events): formato SSE utilizado pelo provider falso para exercitar o App Server sem inferência externa.

## Verificação automatizada associada

```powershell
python -B -m pytest -c pytest.ini onmyoji-daemon/tests/test_codex_capabilities.py onmyoji-daemon/tests/test_delegation_policy.py
```

Esses testes verificam identidade, precedência/revogação, relatório inválido, recusa de prova somente de esquema, separação de pós-turno e sobrevivência de processo, matriz de inputs, diagnóstico sanitizado e presença da política comum nos clientes. O runner é a evidência de integração com o binário; testes unitários com fixtures não substituem essa evidência.
