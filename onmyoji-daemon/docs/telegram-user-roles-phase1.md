# Telegram — Fase 1: persistencia e servico de usuarios

Data: 2026-11-12. Versao de setup: `2026.11.12.36`. Schema do Gateway: **5**.

## Resultado e limites desta fase

A Fase 1 introduz um registro SQLite transacional de pessoas nao-Owner, sem disponibilizar comandos publicos, menus, ingressos ou destinos adicionais. O modulo `telegram_users.py` e inicializado como `gateway.users` depois de `GatewayStore`, mas nenhum handler do Telegram nem RPC utiliza o registro para atender terceiros nesta fase. A flag `[telegram].user_roles_enabled`, criada na Fase 0 e desligada por padrao, continua sem efeito sobre o ingresso.

O Owner continua tendo autoridade exclusivamente por `contacts.json`/`Contacts.owners()` e `/pair <PIN>`, sem migrar nem duplicar cadastros no SQLite. Se um usuario ja registrado se tornar Owner posteriormente, o registro permanece inativo para gestao, fica oculto na listagem e o Owner ganha precedencia imediatamente.

## Esquema e upgrade

- `telegram_persistence.py::GatewayStore`: `SCHEMA_VERSION=5`. Instalacoes novas e upgrades de v1, v2, v3 e v4 passam pelo DDL da Fase 1 depois das migracoes anteriores. Versoes posteriores a 5 continuam recusadas antes do DDL.
- `telegram_user_schema.py::migrate_telegram_users`: cria `telegram_users` (identidade Telegram, DM validada, status, revisao, timestamps), `telegram_user_roles` (roles explicitamente concedidos, metadados de concessao) e `telegram_user_audit` (ator, evento, status e roles antes/depois; sem conteudo de mensagens).
- Indices para listagem por status/data, concessoes por role, auditoria por usuario e expurgo por idade. FKs SQLite habilitadas em `Gateway._initialize_persistence` antes de qualquer criacao; o registro exige `PRAGMA foreign_keys=ON` para funcionar.
- O DDL e a insercao da versao 5 ocorrem na transacao de migracao do `GatewayStore`. Uma falha preserva a versao anterior e nao publica schema parcialmente criado. A rotina de backup ja existente cria, quando necessario, `state/daemon/services/telegram/gateway-before-delegation-v4.sqlite3` antes do upgrade de v4. Nunca sobrescrever esse backup.
- O schema `v5` mantem inalterados `conversations`, `inbox_items`, `telegram_channels`, `contacts.json`, threads, tokens e configuracoes.

## API interna estavel para as proximas fases

`gateway.users` e um `TelegramUserRegistry` e disponibiliza:

| Metodo | Efeito |
| --- | --- |
| `register_from_dm(sender, conversation_id)` | Novo `PENDING`; retry idempotente; `WITHDRAWN` volta a `PENDING`; suspenso/rejeitado nao reabre. Perfil mutavel atualizado sem trocar DM aprovada |
| `self_status(telegram_user_id)` | Consulta do registro, sem expor dados de outra pessoa no protocolo; a verificacao do remetente pertence ao handler futuro |
| `withdraw(user_id, expected_revision)` | Marca `WITHDRAWN`, remove roles atomicamente; repeticao do mesmo retiro e inofensiva |
| `set_roles(owner_id, user_id, roles, expected_revision)` | Unico caminho para aprovacao/reconcessao; status `ACTIVE` exige >=1 role; lista vazia suspende e elimina roles |
| `set_status(owner_id, user_id, status, expected_revision)` | Suspender/rejeitar e limpar roles; nao permite Owner definir `PENDING` ou `WITHDRAWN`; reativacao com roles por `set_roles` |
| `list_users(owner_id, status=?, cursor=?, query=?, limit=?)` | Listagem Owner-only, paginacao por cursor com filtros, exclusao de Owners |
| `audit_for(owner_id, user_id, limit=?)` | Eventos sanitizados somente ao Owner |
| `can_chat(user_id)` | Owner sempre autorizado; terceiro exige flag, `ACTIVE`, `CHAT_USER` |
| `can_notify(user_id, dm_conversation_id)` | Somente terceiro com flag, `ACTIVE`, `NOTIFY_TARGET` e endereco privado identico ao registrado |
| `resolve_authority(sender_id, conversation_id, action, explicit_group_invocation=False)` | Consulta atual de Owner/status/roles, escopo canonico e decisao `telegram_user_policy.authorize`; nao usa cache de autorizacao |

`expected_revision` e obrigatorio nas mutacoes e alteracoes concorrentes falham com `UserRevisionConflict`. Alteracoes de perfil (nome e username) nao incrementam revisao nem mudam autorizacao, pois nao sao credenciais. O ID numerico do Telegram e a unica chave.

A mudanca de status/roles, incremento de `revision` e auditoria ocorrem na **mesma transacao SQLite**, protegida pelo `RLock` compartilhado do Gateway. Cada leitura de permissao relê o banco, sem cache. O historico de auditoria guarda somente IDs, eventos, status, roles e data; registros antigos sao removidos apos **365 dias**, durante mutacoes subsequentes. Nenhum comando de limpeza ou job automatico foi criado nesta fase.

## Premissas de integracao e riscos para a Fase 2

1. Chamadores validam `chat.type=private` e `chat.id=from.id` usando metadados do Bot API **antes** de chamar `register_from_dm`; o servico valida, adicionalmente, o formato de `conversation_id` e sua correspondencia com o ID numerico. `username` e nome nao sao prova de identidade.
2. `/register`, `/status` e `/unregister` ainda precisam ser implementados no Gateway (Fase 2). Nao ha novo catalogo de comandos ou callback nesta etapa. `register_from_dm` nao envia alertas aos Owners; as notificacoes de registro pertencem ao handler futuro.
3. O registro de DM nao cria nem registra `telegram_channels`. A uniao de destinos manuais e destinos derivados de `NOTIFY_TARGET` e a revogacao em tempo de envio pertencem a Fase 5.
4. `resolve_authority` consulta a politica-alvo; **nao** substitui validacao de origem do Bot API, controle de callback ou revalidacao antes de entrega. Estas integracoes pertencem as Fases 2-5.
5. Nunca mapear `CHAT_USER` para `AUTHORIZED_USER`. A classificacao futura de turno e `REGISTERED_USER`, sem privilegios implicitos de ferramenta.
6. A flag e configurada no startup; nao ha ativacao dinamica sem reiniciar o Gateway.

## Testes de fase e publicacao

Os arquivos `tests/test_telegram_users.py` e `tests/test_telegram_user_schema.py` cobrem: transicoes, idempotencia, roles combinaveis, IDs e DMs invalidos, status suspenso/rejeitado, `WITHDRAWN` e novo `PENDING`, Owners fora da gestao, concorrencia entre dois Owners, revisao otimista, rollback de auditoria, paginacao, expurgo, persistencia apos reabrir SQLite, DDL, indices, FK e migracao de estrutura legada.

Foi executada a suite local destes testes e o teste puro de politica da Fase 0: **84 aprovados**. Sem checkout integral do repositório no ambiente de execucao, nao foi possivel executar a suite completa existente nem um startup end-to-end do Gateway; repetir essa verificacao antes do rollout. A publicacao no GitHub foi feita na branch `main`, sem criar branch.

**Proxima fase:** Fase 2, autocadastro por DM e comandos `/register`, `/status`, `/unregister`. Nenhum usuario extra recebe respostas do Codex como consequencia da Fase 1.
