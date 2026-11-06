# Telegram: localização fixa

**Registro:** TG-LOC · **Data:** 2026-11-06 · **Estado:** implementado e validado localmente.

> [!IMPORTANT]
> O escopo definido pelo usuário é receber e enviar pontos fixos nativos.
> Localização ao vivo, tracking, edições, geocodificação e envio dedicado de
> venue ficam fora de escopo, sem fase futura prometida.

## Diagnóstico e decisão

Na base `becbb69`, o gateway reconhecia somente texto, foto, documento e voz
na recepção. Uma localização isolada em DM caía em “Mensagem sem texto ou anexo
reconhecido”; em grupos, não entrava no contexto. Os contratos MCP, RPC e
ferramentas da conversa também não tinham coordenadas ou rota `sendLocation`.

A entrega acrescenta localização como conteúdo estruturado, sem transformá-la
em arquivo e sem alterar os contratos de texto/anexos. A documentação vigente
é [Gateway Telegram](Telegram.md) e [Telegram MCP](mcp-telegram.md). O antigo
[SDD outbound](../../docs/sdd/telegram-outbound/requirements.md) permanece histórico.

## Ativação

O serviço **Gateway Telegram** precisa estar habilitado e disponível. Receber
pontos fixos segue o atendimento já existente: owner em DM; em grupos, conteúdo
entregue ao bot pode compor o contexto, mas só a invocação autorizada por owner
com menção ou reply dispara turno. Não é preciso cadastrar a conversa ativa
como destino externo para respondê-la.

Envio à conversa ativa exige `agent_outbound_media=true` e origem `OWNER` ou
`AUTHORIZED_USER`. Envios externos por `channel` seguem a habilitação MCP,
catálogo e confirmação atuais; não recebem um novo gate de mídia da conversa.

> [!NOTE]
> Reinicie o daemon/supervisor após instalar o código atualizado: ele recria o
> Gateway Telegram habilitado, carrega os módulos e aplica as migrações. Reiniciar
> somente o Gateway não recarrega o ajuste de timeout no supervisor. Clientes MCP
> externos já abertos precisam reconectar ou reiniciar seu servidor MCP. Nenhuma
> configuração operacional foi alterada nesta entrega. Threads Telegram
> existentes mantêm seu histórico e não exigem `/new`.

## Entrada do agente

O objeto `location` contém `kind:"static"`, latitude e longitude obrigatórias,
`horizontal_accuracy` opcional e metadados textuais opcionais `venue.title` e
`venue.address`. Coordenadas são um ponto definido pelo remetente; não provam
GPS, presença atual ou domicílio. Precisão é raio de incerteza em metros,
não uma medição garantida. Ausência é desconhecida; zero é válido.

Na DM, `location`, `message_id` e `timestamp` disponíveis acompanham o envelope
atual, preservando `content` como string. Exemplo ilustrativo:

```json
{
  "telegram_turn": {
    "provenance": {
      "conversation_kind": "private",
      "source_trust": "OWNER",
      "contains_untrusted_context": false
    },
    "content": "",
    "message_id": 321,
    "timestamp": "2026-11-06T12:00:00Z",
    "location": {
      "kind": "static",
      "latitude": -23.55052,
      "longitude": -46.633308,
      "horizontal_accuracy": 15
    }
  }
}
```

Em grupos, o mesmo objeto fica na mensagem correspondente de
`telegram_turn.content.conversation_context.included_messages`, ao lado de
`message_id`, `sender`, `trust`, `text`, `attachment_ids` e `timestamp`.
Um venue é extraído antes de `message.location`, evitando duplicidade;
título/endereço disponíveis permanecem dados do remetente:

```json
{"kind":"static","latitude":-23.55052,"longitude":-46.633308,"venue":{"title":"Local de exemplo","address":"Endereço informado pelo remetente"}}
```

Localização ao vivo não vira ponto fixo silenciosamente. Em contexto de
atendimento autorizado, o gateway informa “Localização ao vivo não é suportada.
Envie um ponto fixo.”, sem enfileirar esse bloco. Texto/anexos válidos continuam
seu fluxo. Terceiros em grupo não recebem resposta automática. `edited_message`
continua fora dos updates processados.

## Saída nativa

| Ferramenta | Contrato |
| --- | --- |
| `telegram_gateway.send_location` | Coordenadas obrigatórias e precisão opcional. Destino é exclusivamente a conversa/tópico do turno ativo; não aceita IDs ou `channel`. |
| `telegram.send_location` com `channel` | Destino obtido de `telegram.list_channels`, coordenadas e `confirm:true`. `new_topic` opcional segue as regras atuais de criação explicitamente solicitada em grupo cadastrado; o tópico criado não é cadastrado automaticamente. |
| `telegram.send_location` com `reply_context` | Alternativa para threads antigas. Capacidade temporária obtida em `telegram_gateway.get_channel_state`, coordenadas e `confirm:true`; proíbe `channel` e `new_topic`. Vinculada ao turno, conversa, tópico, geração, origem autorizada e capacidade de mídia. |

Exemplo ativo e recibo no conteúdo JSON do resultado App Server:

```json
{"latitude":0,"longitude":0,"horizontal_accuracy":0}
```

```json
{"sent":true,"message_id":700}
```

Exemplo externo, supondo destino cadastrado retornado pela listagem e pedido
autorizado:

```json
{"channel":"group:-1000:12","latitude":-23.55052,"longitude":-46.633308,"confirm":true}
```

O MCP chama `telegram.outbound.send_location` com o mesmo conteúdo, sem
`confirm`, exigido na ponte MCP. O gateway resolve internamente o endereço:

```json
{"chat_id":-1000,"message_thread_id":12,"latitude":-23.55052,"longitude":-46.633308}
```

```json
{"sent":true,"channel":"group:-1000:12","message_id":701,"topic_created":false}
```

No modo `reply_context`, a ponte chama `telegram.reply.send_location`, sem
aceitar destino arbitrário. A capacidade deixa de funcionar após fim,
interrupção/cancelamento do turno ou `/new`; nunca deve ser mostrada ao usuário,
registrada em diagnósticos ou reutilizada. Um token de outro tópico não muda
o endereço ao qual ele está vinculado.

Não há legenda em `sendLocation`; explicações usam os mecanismos de texto
existentes como mensagem separada, sem atomicidade com o cartão. A localização
complementa a resposta normal. Não usa outbox, arquivo, `kind=location` ou um
novo `delivery=only`. Um pedido autorizado e inequívoco permite a confirmação
técnica sem nova pergunta; conteúdo não confiável não autoriza retransmissão.

## Validação, erros e privacidade

- Latitude/longitude devem ser números finitos nas faixas inclusivas `[-90,90]`
  e `[-180,180]`; precisão opcional entre 0 e 1500 metros. Rejeitar booleanos,
  texto numérico, nulos, par incompleto, NaN/infinito e campos de saída desconhecidos.
  Não arredondar, completar precisão ou converter coordenadas em endereço.
- Inbound inválido recebe aviso específico no atendimento autorizado. No cache
  passivo, descartar só o bloco inválido e preservar texto/anexos válidos.
- Entrega só é confirmada com `message_id` inteiro positivo, não booleano.
  Timeout/recibo inválido é entrega não confirmada, possivelmente ocorrida;
  não repetir automaticamente. Criação parcial de tópico é informada ao agente.
- O supervisor encaminha `telegram.outbound.send_location` e
  `telegram.reply.send_location` com timeout de 120 segundos, como o envio
  externo existente. O limite genérico de 5 segundos encerrava a espera antes
  do prazo da ponte MCP e da API Telegram, podendo reportar entrega incerta
  enquanto o gateway ainda enviava o ponto. O ajuste não acrescenta retries.
- Dinâmica retorna `sent:false`, `stage` e erro sanitizado. A ponte externa usa
  `TelegramMcpError` sanitizado; token inválido/expirado é falha de autorização,
  sem chamada à API. Falha local após recibo confirmado mantém `sent:true` com
  aviso, mesmo se o diagnóstico também estiver indisponível.
- Coordenadas, precisão, títulos/endereço e capacidade temporária não entram
  nos diagnósticos. Conteúdo recebido mantém procedência e prioridade de dados,
  separado de developer instructions e de autorização para outros destinos.

O dado persiste no banco privado atual do gateway. Não há nova política de
retenção, memória durável automática ou expurgo. A janela de contexto filtra
leitura por idade/quantidade; `/new` isola a geração e remove fila/histórico
externo/anexos nos termos atuais, sem prometer apagar `conversation_messages`.

## Implementação e evidências

| Responsabilidade | Fonte |
| --- | --- |
| Contrato e normalização | [telegram_location.py:35](../src/onmyoji_daemon/telegram_location.py#L35), [extração:66](../src/onmyoji_daemon/telegram_location.py#L66), [revalidação:91](../src/onmyoji_daemon/telegram_location.py#L91) |
| Fila/cache e serialização | [telegram.py:623](../src/onmyoji_daemon/telegram.py#L623), [654](../src/onmyoji_daemon/telegram.py#L654), [671](../src/onmyoji_daemon/telegram.py#L671) |
| Turno e confiança | [TelegramTurnContext](../src/onmyoji_daemon/instructions.py#L25), [entrada App Server](../src/onmyoji_daemon/telegram.py#L1010), [contrato de canal](../resources/instructions/telegram.md) |
| Resposta ativa e compatibilidade | [envio ativo](../src/onmyoji_daemon/telegram.py#L704), [capacidade temporária](../src/onmyoji_daemon/telegram.py#L751), [MCP](../src/onmyoji_daemon/mcp/telegram/service.py#L70) |
| Envio externo e histórico | [telegram_channels.py:375](../src/onmyoji_daemon/telegram_channels.py#L375), [registro:479](../src/onmyoji_daemon/telegram_channels.py#L479), [migração:494](../src/onmyoji_daemon/telegram_channels.py#L494), [replay:509](../src/onmyoji_daemon/telegram_channels.py#L509) |
| Prazo de encaminhamento RPC | [supervisor.py:269](../src/onmyoji_daemon/supervisor.py#L269), [teste do timeout no transporte](../tests/test_supervisor_telegram_timeout.py) |

As três tabelas `inbox_items`, `conversation_messages` e
`telegram_external_messages` ganham `structured_content TEXT NOT NULL DEFAULT '{}'`
por migração aditiva/idempotente. Registros antigos continuam legíveis. O JSON
normalizado é validado novamente ao ler; conteúdo corrompido não vira instrução
nem impede usar outro conteúdo válido. Apenas `has_location` entra nos metadados
confiáveis; geodados não viram anexo ou arquivo temporário.

O schema gerado pelo Codex CLI 0.166.0 publica `dynamicTools` em `thread/start`,
sem rota declarada de atualização em `thread/resume`. Por isso, a entrega usa a
ferramenta dinâmica em threads novas e a capacidade MCP temporária nas antigas,
sem alterar rollout ou descartar histórico. A [documentação oficial do App
Server](https://learn.chatgpt.com/docs/app-server) descreve restauração das
ferramentas persistidas; não se presume atualização por trocar somente um hash.

A Bot API confirma precisão opcional de 0 a 1500 m, venue também em `location`
por compatibilidade e envio nativo sem legenda, retornando `Message`.
As faixas geográficas/finitude são contrato local. Referências oficiais,
consultadas em 2026-11-06: [Location](https://core.telegram.org/bots/api#location),
[Message](https://core.telegram.org/bots/api#message),
[sendLocation](https://core.telegram.org/bots/api#sendlocation).

## Entrega e validação

- [x] TG-LOC-01: contratos e continuidade de threads, incluindo alternativa MCP.
- [x] TG-LOC-02: normalização e migrações aditivas.
- [x] TG-LOC-03: recepção, fila/cache/envelope e procedência.
- [x] TG-LOC-04: envio nativo à conversa ativa e recibo.
- [x] TG-LOC-05: envio ao catálogo, tópicos e histórico externo.
- [x] TG-LOC-06: instruções canônicas e documentação de uso/ativação.
- [x] TG-LOC-07: testes automatizados de contratos e regressão locais.

Suítes específicas: [contrato](../tests/test_telegram_location_contract.py),
[inbound](../tests/test_telegram_location_inbound.py),
[ativo](../tests/test_telegram_location_active.py),
[outbound](../tests/test_telegram_location_outbound.py) e
[alternativa MCP](../tests/test_telegram_location_reply_mcp.py). Cobrem números,
zeros/limites/ausência, tipos inválidos, venue/live não suportado, confiança,
tópicos, fila/reinício/migração/corrupção, catálogo, recibos e falhas parciais,
revogação por turno/geração e ausência de geodados nos diagnósticos.

Exemplo de execução na raiz, mantendo cache e temporários em `tmp/`:

```powershell
$telegramTestTmp = Join-Path (Get-Location).Path 'tmp'
python -B -m pytest -o "cache_dir=$telegramTestTmp/pytest-cache" --basetemp="$telegramTestTmp/pytest-telegram-location" onmyoji-daemon/tests/test_telegram_location_contract.py onmyoji-daemon/tests/test_telegram_location_inbound.py onmyoji-daemon/tests/test_telegram_location_active.py onmyoji-daemon/tests/test_telegram_location_outbound.py onmyoji-daemon/tests/test_telegram_location_reply_mcp.py onmyoji-daemon/tests/test_telegram.py onmyoji-daemon/tests/test_telegram_channels.py onmyoji-daemon/tests/test_mcp_telegram.py onmyoji-daemon/tests/test_instructions.py
```

Na validação inicial, **358 testes passaram** em 26,40 segundos, incluindo essas
suítes e regressões de servidor/runtime MCP e composição de execução Codex.
Cache e `basetemp` foram passados como caminhos absolutos dentro de `tmp/` da
raiz. A suíte completa do monorepo não foi executada.

Na revisão do timeout, os **9 novos testes de encaminhamento passaram**. Com o
supervisor anterior do `HEAD`, a mesma suíte reproduziu duas falhas nas rotas
novas (5 em vez de 120 segundos) e sete sucessos nos demais contratos. A
correção preserva os prazos de consultas (5 segundos), envio existente
(120 segundos) e OAuth de Mini Apps (200 segundos), sem espera real ou rede.

> [!NOTE]
> A execução conjunta de supervisor, localização outbound, resposta MCP e MCP
> Telegram teve **129 testes aprovados e 4 falhas** nos testes antigos de
> instalação/runtime. As mesmas quatro falhas foram reproduzidas com o
> supervisor anterior do `HEAD`; `management.py`, `runtime.py`, `registry.py`
> e `test_supervisor.py` não foram alterados. Elas tratam mensagens de instalação
> e expectativas de caminhos legados de Python, não o encaminhamento RPC.
> A suíte inteira de supervisor não está declarada como aprovada.

O normalizador teve 100% das 61 linhas executáveis cobertas com `trace` da
biblioteca padrão. Isso não é uma alegação de cobertura de ramos ou de todo o
gateway. Testes automatizados usam APIs simuladas, sem credenciais ou Telegram
real. Homologação visual do cartão nativo em conta real permanece não executada;
nenhuma mensagem real ou configuração operacional foi usada nesta entrega.
