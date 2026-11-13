# Telegram — Fase 3: menu de gestão Owner-only

Data: 2026-11-13. Versão de setup: `2026.11.13.0`. Gateway SQLite: schema **v5** (sem nova migração).

## O que está disponível

O comando `/users`, **somente na DM do Owner pareado**, abre um menu com contagens
e filtros de cadastros: pendentes, ativos, suspensos, rejeitados e todos.
Cada listagem permite navegar em páginas de oito usuários. Para pesquisar por
nome, `@username` ou ID do Telegram, usar `/users <texto>` na DM.

O detalhe apresenta nome público, ID, username, status, data da solicitação,
DM registrada, revisão persistida e os dois roles, selecionáveis separadamente:

- `CHAT_USER`: autorização cadastrada para conversar (ingresso somente na Fase 4).
- `NOTIFY_TARGET`: autorização cadastrada para ser destino de envio (catálogo de saída somente na Fase 5).

`Salvar roles e ativar/reativar` exige pelo menos um role, podendo aprovar um
pedido pendente, alterar roles ativos ou reativar um cadastro suspenso/rejeitado.
A retirada de todos os roles usa **Suspender**, com confirmação explícita.
**Rejeitar** também exige confirmação. `WITHDRAWN` é somente leitura: a
pessoa deve solicitar novo cadastro com `/register`.

O Gateway executa todas as ações por `TelegramUserRegistry.set_roles` e
`set_status`. A transação existente persiste alterações de roles, status,
revisão e auditoria em conjunto. O menu não pode administrar Owners, cujo
papel continua definido exclusivamente em `contacts.json` e `/pair <PIN>`.

## Segurança e concorrência

As callbacks usam o prefixo `usr:`, token aleatório criptograficamente seguro
e payload de até 64 bytes. A sessão é retida apenas em memória (uma por Owner);
o prazo de validade é de **15 minutos**, renovado após navegação válida.
Reiniciar o Gateway invalida todas as sessões anteriores.

Cada clique é verificado contra os dados confiáveis do Bot API:

1. `from.id` numérico não booleano; ainda é Owner pareado naquele instante;
2. `chat.type=private` e `chat.id=from.id` reais;
3. token, mensagem e conversa originalmente vinculados à sessão;
4. tópico privado, se fornecido no callback, e geração da conversa;
5. revisão SQLite original do cadastro **antes de qualquer alteração**.

Tokens são consumidos em uso único, inclusive quando a ação é apenas navegação.
Um menu novo do mesmo Owner invalida o anterior; cliques duplicados e callbacks
de terceiro não fazem mutações. Dois Owners podem abrir fichas simultaneamente,
mas uma decisão subsequente da primeira versão bloqueia a decisão antiga do
segundo, que receberá um convite para atualizar a ficha.

`answerCallbackQuery` é tentado também para callbacks inválidas, sem revelar
dados. A edição da mensagem do menu usa `editMessageText`; quando indisponível,
é enviado novo menu com novo recibo e token. Não há HTTP durante mutex de
sessões nem durante transações de registro.

## Avisos e falhas

Após aprovação, alteração de role, suspensão ou rejeição, o usuário recebe
aviso operacional na DM originalmente cadastrada. Falhas do Bot API são
registradas sem payload da mensagem e **não revertem** a decisão consolidada.
Se a edição do menu falhar, um novo menu é tentado. Se também falhar, nenhum
token novo é considerado ativo.

Não são emitidos turnos ao Codex, não são criados canais de notificação,
não são alterados catálogos por pessoa e não são concedidos privilégios
de daemon, login, configuração ou MCP. A flag `user_roles_enabled` continua
`false` por padrão e, mesmo se `true`, não autoriza conversas adicionais
até o ingresso ser integrado na Fase 4.

## Implementação e testes

- `telegram_user_menus.py`: controle de telas, filtros, paginação, detalhes,
  rascunho de roles, confirmações e callbacks de uso único.
- `telegram_users.py::status_counts`: contagens agregadas com validação
  do Owner e exclusão de IDs que tenham sido pareados posteriormente.
- `telegram.py`: integra o mixin, substitui o aviso provisório de `/users`
  e roteia callbacks `usr:` antes das validações de menu Owner legado.
- `tests/test_telegram_user_menus.py`: testes para aprovação independente
  e conjunta, roles alterados, suspensão e rejeição, corrida de dois Owners,
  token velho, remetente forjado, tópico, prazo, busca, paginação, exclusão
  de Owner e preservação do bloqueio de ingressos de terceiros.
- `tests/test_telegram_users.py`: teste adicional de contagens e permissões.
- `tests/test_telegram_user_registration.py`: expectativa do comando `/users`
  atualizada para o menu efetivo.
- `docs/Telegram.md`: matriz de acesso e instruções atualizadas.

O módulo de menu foi executado localmente com um duplo de registro SQLite/API
representando a interface existente: **11 cenários aprovados**. Os testes de
integração com o Gateway real foram adicionados, mas a execução completa
exige um checkout do repositório, que não estava disponível neste ambiente.
Antes de ativar conversação ou destinos, executar:

```bash
python -B -m pytest onmyoji-daemon/tests/test_telegram_user_menus.py
python -B -m pytest onmyoji-daemon/tests/test_telegram_users.py
python -B -m pytest onmyoji-daemon/tests/test_telegram_commands.py
python -B -m pytest onmyoji-daemon/tests/test_telegram_user_registration.py
python -B -m pytest onmyoji-daemon/tests
```

Não foram criadas branches nem workflows. O arquivo histórico
`Telegram-2026-11-12.proposed.md` permanece pendente e não foi alterado.

**Próxima fase:** Fase 4 — ingresso autorizado para `CHAT_USER`, proveniência
`REGISTERED_USER`, segregação e revalidação de filas/entregas. Até então,
um usuário aprovado não recebe atendimento conversacional do Shikigami.
