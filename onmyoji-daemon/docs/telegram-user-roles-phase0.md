# Telegram — Fase 0: contratos e mapa de integração

Data: 2026-11-12. Escopo: baseline para `CHAT_USER` e `NOTIFY_TARGET`, sem alteração de ingresso ou saída do Gateway. Este documento orienta a **Fase 1** do plano `2026-11-12-Telegram-user-roles.md`.

## Estado entregue

- Flag proposta: `[telegram].user_roles_enabled` (booleano; `false` por padrão) na configuração privada `configs/daemon/services/telegram/config.toml`. Nesta fase `Settings.load` apenas lê e valida a flag: **mesmo com `true`, nenhum terceiro é liberado**.
- Roles combináveis `CHAT_USER` e `NOTIFY_TARGET`; status `PENDING`, `ACTIVE`, `SUSPENDED`, `REJECTED` e `WITHDRAWN`. Futuramente somente `ACTIVE`, role concedido e flag ligada podem autorizar a respectiva capacidade.
- O Owner continua definido por `contacts.json`, `Contacts.owners()` e `/pair <PIN>` sem modificações. Não está sujeito à administração de usuários comuns.
- `REGISTERED_USER` é uma futura classificação de origem de turno, **distinta** de `AUTHORIZED_USER`.
- Contrato puro de decisões em `src/onmyoji_daemon/telegram_user_policy.py`. Esse módulo não autentica o remetente: seus parâmetros devem ser obtidos de metadados confiáveis do Telegram; texto, username e callback não comprovam autoridade.
- `/register`, `/status`, `/unregister` e `/users` **não são instalados** como comandos nesta fase. A matriz modela a política-alvo, não o roteamento atual.

## Mapa de integração real

| Subsistema | Ponto de código | Próxima alteração planejada |
| --- | --- | --- |
| Configuração | `telegram.py::Settings.load`, `configs/telegram.toml.model` | Apenas flag OFF com validação estrita agora |
| Owners e PIN | `telegram.py::Contacts`, `Gateway._update` | Preservar precedência de Owner e pareamento fora da política de roles |
| DM | `telegram.py::Gateway._update` | Fase 2 insere autocadastro; fase 4 permite `CHAT_USER` antes do bloqueio de terceiros |
| Grupos e tópicos | `telegram.py::Gateway._group_message` | Fase 4 permite chamada por menção/reply de `CHAT_USER`; controles de grupo seguem Owner-only |
| Menus/callbacks | `telegram.py::Gateway._callback`; `telegram_channels.py::_channel_callback` | Fase 3 usa prefixo independente, token, TTL, `message_id`, chat, Owner e revisão verificados no servidor |
| Catálogos de comandos | `telegram_commands.py`; `TelegramApi.set_owner_commands`, `.set_other_private_commands` | Owner: escopo `chat`; outros: `all_private_chats`; não confiar no menu como controle de acesso |
| Histórico/threads | `telegram.py::_conversation_id`, `._cache_message`, `._context_snapshot`, `._turn_context` | Manter isolamento DM/tópico e histórico de terceiros como não confiável |
| Persistência | `telegram_persistence.py::GatewayStore`; `telegram.py::_initialize_persistence` | Fase 1 migra schema v4 para v5 com backup e sem alterar `contacts.json` |
| Execução e entrega | `execution_gateway/execution.py`, `telegram_delivery.py`, `telegram.py::_enqueue_turn` | Fase 4 revalida autorização por remetente antes de dispatch/entrega; não cancelar Owner em grupo |
| Proveniência | `instructions.py::TelegramTurnContext`; `resources/instructions/telegram.md` | Fase 4 define `REGISTERED_USER`, nunca herdando `AUTHORIZED_USER` |
| Catálogo de saída | `telegram_channels.py::_channels_list`, `._channel_registered`, `._outbound_send`, envio de localização | Fase 5 calcula união de canais manuais e DMs derivadas de `NOTIFY_TARGET`; revalida antes do efeito |
| Ferramentas MCP | `mcp/telegram/tools.py` e `service.py` | `confirm=true` não prova permissão de Owner; isolamento profundo pertence a projeto futuro |

## Matriz de comportamento-alvo (não ativada nesta fase)

| Ação | Owner | Pendente/sem role | `CHAT_USER` ativo | `NOTIFY_TARGET` ativo |
| --- | --- | --- | --- | --- |
| DM com agente | Sim | Não | Sim, futuramente | Não |
| Grupo com menção/reply ao bot | Sim | Não | Sim, futuramente | Não |
| Grupo sem menção/reply | Apenas contexto | Apenas contexto | Apenas contexto | Apenas contexto |
| Destino privado derivado de role | Não; usa canal manual | Não | Não | Sim, futuramente |
| `/new` e `/resolver` na própria DM | Sim | Sim (legado) | Sim (legado) | Sim (legado) |
| `/register`, `/status`, `/unregister` | Contrato próprio | DM própria, futuramente | DM própria, futuramente | DM própria, futuramente |
| `/config`, `/channel`, `/usage`, `/resetusage`, `/login`, `/totp`, `/fila`, `/direcionar`, `/cancel`, `/users` em DM | Owner, segundo handler | Não | Não | Não |
| Controles de grupo `/new`, `/resolver`, `/config`, `/channel` | Owner | Não | Não | Não |
| `/pair` | Protocolo por PIN | Somente PIN válido | Somente PIN válido | Somente PIN válido |

**Regra de escopo:** `own_dm` requer `chat.type=private` e `chat.id=from.id`. `group` exige `group`/`supergroup`, e um turno requer menção/reply ao bot validada, não uma string arbitrária no prompt. `NOTIFY_TARGET` nunca habilita inbound.

## Referência de testes e aceite

- Novo `tests/test_telegram_user_policy.py`: matriz pura, roles combináveis, comandos/escopos, default fechado e estados inválidos.
- Novo `tests/test_telegram_user_roles_baseline.py`: ingressos reais com Gateway fake (Owner, terceiro, grupo, tópico, `/pair`, `/new`, `/resolver`, canal manual) e flag sem efeito operacional, mesmo `true`.
- Regressões existentes a executar: `test_telegram.py`, `test_telegram_commands.py`, `test_telegram_channels.py`, `test_mcp_telegram.py`, `test_instructions.py`, `test_telegram_location_inbound.py` e demais testes do daemon.
- `python -B -m pytest`, com temporários em `tmp/`, de acordo com `AGENTS.md`.

**Aceite de Fase 0:** nenhum ingresso ou saída novo habilitado, Owners e PIN preservados, sem migração de banco ou dados privados, flag validada e política-alvo testável sem tocar em handlers atuais.

**Fase 1:** criar no SQLite tabelas de usuários/roles/auditoria, migração versionada, revisão otimista e operações transacionais; sem handlers públicos ou ampliação de acesso. A flag de Fase 0 é apenas uma barreira de rollout futuro e **não** resolve isolamento de Codex/MCP.
