# Telegram no MCP interno

O módulo `telegram` permite ao agente consultar destinos registrados e enviar texto e arquivos pelo Gateway Telegram. Ele reutiliza o RPC local autenticado do daemon e as regras de segurança do gateway.

## Configuração

Configure o módulo em **Daemon > Gateway Telegram**, incluindo credencial do bot no vault MCP, owners e destinos autorizados. O estado privado e a configuração ficam em `configs/daemon/services/telegram/`.

As ferramentas são publicadas quando a configuração do gateway está habilitada. Não há perfis independentes em `telegram.*`: o módulo usa o gateway da instância. Para executar as chamadas, o daemon e o Gateway Telegram precisam estar disponíveis.

## Ferramentas e dados

| Ferramenta | Comportamento |
| --- | --- |
| `telegram.list_channels` | Lista destinos já registrados pelo owner. Aceita `query` para filtrar pelo nome. |
| `telegram.send` | Envia texto e/ou um arquivo a um único destino registrado. Recebe `channel`, `text`, `file_path`, `kind` e, quando aplicável, `new_topic`. Exige `confirm=true`. |
| `telegram.remove_channel` | Remove um destino do registro por solicitação explícita do owner. Exige `channel` e `confirm=true`. |

Consulte os destinos antes de enviar quando o destinatário não for inequívoco. O envio exige autorização explícita do usuário; a confirmação técnica não substitui a escolha correta do destino. `kind` aceita `document`, `photo`, `audio` e `voice`.

## Arquivos e contexto da conversa

Arquivos enviados devem existir, ser regulares e usar caminho absoluto dentro do workspace permitido pelo gateway. Anexos recebidos permanecem em `<workspace>/.onmyoji/telegram/attachments/`, com staging em `<workspace>/.onmyoji/telegram/staging/`, para que o agente possa ler e editar o material.

As ferramentas `telegram.*` são a superfície MCP de destinos e envios. O Gateway também fornece recursos específicos da conversa ativa, como menus e respostas, conforme o contexto e a autorização do turno. Consulte a [documentação do Gateway Telegram](Telegram.md) para procedência das mensagens, formatação, anexos e voz.
