# Telegram no MCP interno

O módulo `telegram` permite ao agente consultar destinos registrados e enviar texto, arquivos e pontos fixos nativos pelo Gateway Telegram. Ele reutiliza o RPC local autenticado do daemon e as regras de segurança do gateway.

## Configuração

Configure o módulo em **Daemon > Gateway Telegram**, incluindo credencial do bot no vault MCP, owners e destinos autorizados. O estado privado e a configuração ficam em `configs/daemon/services/telegram/`.

Ao habilitar o serviço **Gateway Telegram** no setup, o MCP interno publica automaticamente `telegram.list_channels`, `telegram.send`, `telegram.send_location` e `telegram.remove_channel`. Desabilitar o serviço oculta essas ferramentas e bloqueia novas chamadas pelo módulo. Não há perfil ou configuração adicional de publicação em `telegram.*`.

A publicação acompanha o estado do ciclo de vida do serviço em `configs/daemon/services.json`. O campo legado `enabled` de `services/telegram/config.toml` não controla essa disponibilidade e não exige ajuste nas instalações existentes. Se o estado do serviço estiver ausente, ilegível ou em formato inválido, as ferramentas ficam ocultas. Para executar as chamadas, o daemon e o Gateway Telegram precisam estar disponíveis.

## Ferramentas e dados

| Ferramenta | Comportamento |
| --- | --- |
| `telegram.list_channels` | Lista destinos já registrados pelo owner. Aceita `query` para filtrar pelo nome. |
| `telegram.send` | Envia texto e/ou um arquivo a um único destino registrado. Recebe `channel`, `text`, `file_path`, `kind` e, quando aplicável, `new_topic`. Exige `confirm=true`. |
| `telegram.send_location` | Envia um ponto fixo nativo. Recebe `latitude`, `longitude`, precisão opcional `horizontal_accuracy`, `channel` cadastrado e `confirm=true`; aceita `new_topic` nas regras do envio externo. Na conversa ativa, aceita alternativamente `reply_context` temporário, sem destino arbitrário ou novo tópico. |
| `telegram.remove_channel` | Remove um destino do registro por solicitação explícita do owner. Exige `channel` e `confirm=true`. |

Consulte os destinos antes de enviar quando o destinatário não for inequívoco. O envio exige autorização explícita do usuário; a confirmação técnica não substitui a escolha correta do destino. `kind` aceita `document`, `photo`, `audio` e `voice`.

Latitude e longitude são números finitos nas faixas inclusivas -90 a 90 e
-180 a 180. Precisão é opcional, de 0 a 1500 metros; omitir quando desconhecida.
Zero é válido e difere de ausência. Exemplo, supondo o destino retornado pela
listagem e envio autorizado:

```json
{"channel":"group:-1000:12","latitude":-23.55052,"longitude":-46.633308,"horizontal_accuracy":15,"confirm":true}
```

O ponto não aceita legenda; texto complementar é mensagem separada.
Localização ao vivo, tracking, edição/parada e `sendVenue` não são expostos.
Sucesso exige recibo Telegram válido. Timeout ou recibo inválido não autorizam
reenvio automático; o envio pode ter ocorrido. Falha local depois da entrega
confirmada mantém sucesso com aviso. O histórico fica no destino e não inicia
turno nem troca a thread de origem.

Em uma thread antiga sem a dinâmica `telegram_gateway.send_location`, chame
`telegram_gateway.get_channel_state` e use o `location_reply_context` retornado
como `reply_context` de `telegram.send_location`, com coordenadas e `confirm=true`.
Esse modo não aceita `channel` ou `new_topic`: a capacidade é privada,
temporária e vinculada ao turno/conversa/geração autorizados, com mídia de saída
habilitada. Não deve ser exposta, persistida ou reutilizada. Detalhes e registro
da implementação: [Localização Telegram](telegram-location.md).

## Descoberta pelo agente

Quando o serviço está habilitado, o contrato `telegram-mcp.md` acompanha o
handshake do servidor MCP e a composição das instruções dos clientes Codex.
Ele orienta a descoberta de `telegram.list_channels` e a consulta sem filtro
quando o usuário pedir o catálogo completo. Memória e ferramentas da conversa
ativa não substituem a listagem. Uma falha de consulta deve ser informada como
falha, sem presumir ausência de destinos.

O contrato do canal Telegram também explicita essa distinção. Ele é relido a
cada turno e reaplicado ao retomar a mesma thread; atualizar essas instruções
não exige apagar a conversa. O bloco MCP passa a integrar o handshake dos
novos processos do servidor.

Iniciar uma interação externa significa enviar uma nova mensagem a um destino
cadastrado por `telegram.send`. `new_topic` permite criar um tópico quando
explicitamente solicitado e aceito pelo destino. Retransmissão por esse método
é um novo envio de conteúdo; encaminhamento nativo de mensagens não é exposto
pelo contrato atual.

## Arquivos e contexto da conversa

Arquivos enviados devem existir, ser regulares e usar caminho absoluto dentro do Work ou de uma raiz de arquivos declarada por um MCP ativo. O gateway rejeita links, junctions, caminhos fora dessas raízes e arquivos acima do limite. Assim, um anexo já baixado pelo E-mail ou uma saída documental pode seguir diretamente para o Telegram sem cópia intermediária.

No gateway da conversa ativa, `telegram_gateway.send_file` aceita `file_name` para o outbox do turno ou `file_path` para um arquivo existente em raiz autorizada. O segundo modo exige pedido de owner ou usuário autorizado; o diagnóstico registra a origem, a validação e o resultado do envio. Anexos recebidos permanecem em `<workspace>/.onmyoji/telegram/attachments/`, com staging em `<workspace>/.onmyoji/telegram/staging/`, para que o agente possa ler e editar o material.

As ferramentas `telegram.*` são a superfície MCP de destinos e envios. O Gateway também fornece recursos específicos da conversa ativa, como menus e respostas, conforme o contexto e a autorização do turno. Consulte a [documentação do Gateway Telegram](Telegram.md) para procedência das mensagens, formatação, anexos e voz.
