# Interface dos scripts de setup

Esta convenção define a apresentação e a interação dos scripts de setup do Onmyōji. Ela existe para que a navegação continue previsível mesmo quando uma configuração reúne daemon, MCP, gateway e integrações externas.

## Tipos de interação

| Tipo | Cabeçalho | Identificador | Prompt padrão |
| --- | --- | --- | --- |
| Menu navegável | Sim | Números ou atalhos estáveis | Sem padrão, salvo quando houver seleção atual |
| Escolha dentro de roteiro | Não | Números | O número da opção efetiva, como `Opção [2]:` |
| Entrada livre | Não | Não se aplica | O valor efetivamente adotado ao pressionar Enter |
| Confirmação | Não | `S/n` ou `s/N` | A alternativa em maiúscula |

## Menus navegáveis

Use `screen()` somente ao abrir uma tela que possui navegação própria e permanece ativa até o operador escolher `X. Voltar`. Menus podem usar atalhos alfabéticos quando eles representam destinos estáveis, como `T. Gateway Telegram`. Use `flow_heading()` para identificar o início de um assistente ou uma visualização de resultado; ele não desenha uma moldura de menu.

Não abra um cabeçalho para escolher uma opção no meio de uma criação, teste, assistente ou alteração de perfil. Diagnósticos, listagens e resultados sem opções próprias também são visualizações de ação, não menus. Ao concluir ou cancelar uma ação, retorne ao menu que ainda é válido.

## Roteiros de ação

Use `flow_choice()` para escolhas enumeradas internas. Ele não cria uma tela nova e sempre converte o padrão lógico no respectivo número.

```text
  Credencial · Omie
    1. Informar app_key e salvar nesta entrada
    2. Reinformar entrada KeePass
    X. Cancelar
› Opção [1]:
```

`X` sempre interrompe a etapa atual e retorna ao contexto imediatamente anterior; nunca significa confirmar, apagar ou outra ação.

## Entradas livres e confirmações

Uma entrada livre exibe entre colchetes somente o valor que será usado se o operador responder vazio.

```text
› Porta do callback local [8888]:
› Raízes permitidas [vazio = todas]:
› Confirmar criação? [s/N]:
```

Não use o texto descritivo de uma alternativa enumerada como valor padrão. Para decisões binárias, use confirmações; para três ou mais alternativas, use uma enumeração numerada.

## Resultados e falhas

Use `note()` para contexto, `result(True, ...)` para conclusão e `result(False, ...)` para falha recuperável. Scripts chamados pelo setup devem capturar exceções e retornar uma mensagem operacional sanitizada; tracebacks não fazem parte da interface do operador.

## Implementação

- `choose()` abre um submenu navegável e apresenta o padrão pelo número.
- `flow_choice()` apresenta uma enumeração em linha, sem cabeçalho.
- Funções de seleção usadas por assistentes, criação de perfis ou credenciais devem usar `flow_choice()`.
- Antes de criar um novo helper de UI, verifique se um desses contratos já representa a interação desejada.
