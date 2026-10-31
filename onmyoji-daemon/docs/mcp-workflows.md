# Workflows no MCP interno

O módulo `workflows` entrega procedimentos operacionais completos dos serviços MCP ativos. As instruções curtas de roteamento indicam quando ler cada documento; o agente obtém seu conteúdo com `workflows.read`, sem materializar skills ou arquivos no workspace.

## Configuração e publicação

Não há perfil, credencial ou habilitação global independente para Workflows. O catálogo é calculado conforme a configuração de cada serviço. Quando não há workflow disponível, a ferramenta não é publicada.

Os documentos canônicos ficam em `onmyoji-daemon/resources/workflows/`. O catálogo interno associa cada identificador ao documento e ao gatilho de uso; a chamada aceita somente os identificadores publicados, sem permitir caminhos arbitrários.

| Identificador | Quando fica disponível | Quando ler |
| --- | --- | --- |
| `memory-decision` | Memória habilitada. | Antes de responder a uma solicitação substantiva, decidir, planejar, agir ou perguntar ao usuário ou a terceiros. |
| `memory-learning` | Memória habilitada. | Após correção, preferência durável ou aprendizado validado, e antes de criar, revisar ou excluir uma instrução de memória. |
| `data-tables-schema-authoring` | Autoria de schemas Data Tables habilitada no setup. | Antes de criar ou alterar schemas destinados especificamente ao serviço MCP `data_tables`. |
| `email-operations` | Há conta de E-mail habilitada e elegível para publicação. | Antes de consultar, organizar ou enviar mensagens com `email.*`. |

## Ferramenta e uso

`workflows.read` recebe `id` e retorna `id`, `module` e `content`. Por exemplo:

```json
{"id":"memory-decision"}
```

O schema da ferramenta enumera os identificadores disponíveis na instância. Ao ocorrer um gatilho, o agente deve ler o documento completo, caso ainda não esteja no contexto atual, e seguir suas etapas antes de continuar.

As rotas são publicadas nas instruções do servidor MCP e incorporadas à composição centralizada de developer instructions. O procedimento completo permanece no servidor e é lido somente quando necessário. Veja [Developer instructions](../../docs/developer-instructions.md) para a composição compartilhada entre CLI, console e Gateway.
