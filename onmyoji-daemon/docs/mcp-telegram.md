# Telegram no MCP interno

O módulo `telegram` permite ao agente consultar destinos registrados e enviar texto e arquivos pelo Gateway Telegram. Ele reutiliza o RPC local autenticado do daemon e as regras de segurança do gateway.

## Configuração

Configure o módulo em **Daemon > Gateway Telegram**, incluindo credencial do bot no vault MCP, owners e destinos autorizados. O estado privado e a configuração ficam em `configs/daemon/services/telegram/`.

Ao habilitar o serviço **Gateway Telegram** no setup, o MCP interno publica automaticamente `telegram.list_channels`, `telegram.send` e `telegram.remove_channel`. Desabilitar o serviço oculta essas ferramentas e bloqueia novas chamadas pelo módulo. Não há perfil ou configuração adicional de publicação em `telegram.*`.

A publicação acompanha o estado do ciclo de vida do serviço em `configs/daemon/services.json`. O campo legado `enabled` de `services/telegram/config.toml` não controla essa disponibilidade e não exige ajuste nas instalações existentes. Se o estado do serviço estiver ausente, ilegível ou em formato inválido, as ferramentas ficam ocultas. Para executar as chamadas, o daemon e o Gateway Telegram precisam estar disponíveis.

## Ferramentas e dados

| Ferramenta | Comportamento |
| --- | --- |
| `telegram.list_channels` | Lista destinos já registrados pelo owner. Aceita `query` para filtrar pelo nome. |
| `telegram.send` | Envia texto e/ou um arquivo a um único destino registrado. Recebe `channel`, `text`, `file_path`, `kind` e, quando aplicável, `new_topic`. Exige `confirm=true`. |
| `telegram.remove_channel` | Remove um destino do registro por solicitação explícita do owner. Exige `channel` e `confirm=true`. |

Consulte os destinos antes de enviar quando o destinatário não for inequívoco. O envio exige autorização explícita do usuário; a confirmação técnica não substitui a escolha correta do destino. `kind` aceita `document`, `photo`, `audio` e `voice`.

## Arquivos e contexto da conversa

Arquivos enviados devem existir, ser regulares e usar caminho absoluto dentro do Work ou de uma raiz de arquivos declarada por um MCP ativo. O gateway rejeita links, junctions, caminhos fora dessas raízes e arquivos acima do limite. Assim, um anexo já baixado pelo E-mail ou uma saída documental pode seguir diretamente para o Telegram sem cópia intermediária.

No gateway da conversa ativa, `telegram_gateway.send_file` aceita `file_name` para o outbox do turno ou `file_path` para um arquivo existente em raiz autorizada. O segundo modo exige pedido de owner ou usuário autorizado; o diagnóstico registra a origem, a validação e o resultado do envio. Anexos recebidos permanecem em `<workspace>/.onmyoji/telegram/attachments/`, com staging em `<workspace>/.onmyoji/telegram/staging/`, para que o agente possa ler e editar o material.

As ferramentas `telegram.*` são a superfície MCP de destinos e envios. O Gateway também fornece recursos específicos da conversa ativa, como menus e respostas, conforme o contexto e a autorização do turno. Consulte a [documentação do Gateway Telegram](Telegram.md) para procedência das mensagens, formatação, anexos e voz.
