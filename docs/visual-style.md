# Padrão visual dos scripts de setup

Os scripts `setupOnmyoji.py` e `setupSkill.py` formam uma única aplicação de console. Preserve esta linguagem visual em qualquer novo menu ou configuração de skill. Os configuradores de skills devem importar os componentes comuns de `skills/setup_ui.py`; não recrie uma variante local, exceto quando a skill já tiver uma camada compatível e equivalente.

## Terminal e cores

Não introduza dependências de interface para o bootstrap. Use a camada `Ui` local baseada em ANSI, habilitada somente em TTY e desabilitada quando `NO_COLOR` estiver definido. A aplicação precisa continuar inteligível sem cores e em saídas redirecionadas. Quando a codificação de saída não suportar os caracteres de moldura Unicode, use a alternativa ASCII.

Use violeta para a moldura e o prompt, ciano para o atalho da opção, cinza para textos auxiliares e valores, verde claro para sucesso e vermelho claro para falhas. Não dependa apenas de cor: toda mensagem de resultado precisa de um marcador textual.

## Cabeçalhos e menus

Cada tela deve iniciar com `screen(título, subtítulo)`, mantendo a moldura, o nome do produto e o contexto atual. Use `item()` ou `menu_item()` para as opções: atalho com quatro caracteres, alinhado à direita, seguido por dois espaços e um rótulo com largura mínima de 30 caracteres. Use `X.` como a última opção para voltar ou sair.

No menu de skills, mantenha a coluna de estado e o preenchimento alternado com pontos na linha par; use ponto final de base (`.`), nunca ponto central.

## Entrada e resultado

Toda entrada interativa deve usar `prompt()`, inclusive escolhas, confirmações e campos de texto. O formato é `› <rótulo>` em violeta e negrito; o rótulo deve terminar em `: `.

Após toda operação que altere estado, valide ou teste uma integração, apresente seu resultado antes de redesenhar o menu. Use `result(ok, mensagem)` no configurador da skill ou um equivalente no setup geral. O formato é uma linha própria, recuada em quatro espaços: `+ OK` em verde claro para sucesso e `! ERRO` em vermelho claro para falha, seguida da mensagem explicativa. Inclua o mesmo destaque para operações canceladas ou recusadas por validação, preservando a causa e, quando aplicável, uma orientação de correção.

Não limpe a tela automaticamente após uma mensagem de resultado; o próximo redesenho do menu deve deixá-la imediatamente acima dele, para que a confirmação não passe despercebida.
