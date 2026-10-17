# SDD · Telegram outbound — tarefas e validação

Estado inicial 2026-10-17: requisitos P1–P8 aprovados, sem homologação.

- [ ] T1 — Catálogo SQLite e identidade por chat/tópico, registro só por Telegram.
- [ ] T2 — Comando `/channel` e callbacks: owner, terceiros DM, aprovação/rejeição e negação em grupo/tópico.
- [ ] T3 — RPC seguro para listar, remover, enviar e criar tópico sem registrar automaticamente.
- [ ] T4 — Persistência de mensagem enviada como evento no contexto de destino; consumo por turno sem alterar thread.
- [ ] T5 — Wrapper e skill `telegram` outbound, integração com setup da instância.
- [ ] T6 — Testes unitários/integrados com API mock e casos de permissão, replay, arquivo e tópico.
- [ ] T7 — Revisão de segurança, suíte `pytest`, lint/compile e registro de evidências.
- [ ] H1 — Homologação em ambiente Telegram real (token/owner/tópicos/grupos/upload/supervisor).
- [ ] H2 — Testes de reinício, permissão de membros, callback expirado, registro/remoção e entrega no chat real.

**Gate de desenvolvimento:** T1–T7 fechados com testes locais antes de solicitar agente de homologação. Nenhum teste real Telegram/daemon em execução neste ambiente.
