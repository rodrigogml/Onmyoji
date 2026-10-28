# SDD · Telegram outbound — tarefas e validação

**Fonte funcional:** [briefing P1–P8](../../20261017-briefing-skill-telegram.md). **Repositório:** Onmyoji upstream. **Branch:** `main`.

## Desenvolvimento — concluído

- [x] **T1** — Catálogo SQLite explícito e granular por DM, grupo e tópico; somente `/channel` registra.
- [x] **T2** — Menus `/channel`; owner registra/remove; terceiros só solicitam registro de DM simples, com aprovação/rejeição explícita por owner. Não-owner é bloqueado em grupo/tópico. Tokens com expiração, identidade, vínculo ao menu do owner e deduplicação.
- [x] **T3** — Novos métodos RPC `telegram.channels.list`, `telegram.channels.remove`, `telegram.outbound.send` com checagem de catálogo, envio de texto/foto/documento/áudio/voz, tópico novo sem cadastro automático e timeout de mídia. `telegram.send` legado preservado.
- [x] **T4** — Histórico `external_outbound` persistido por conversa/geração, anexos retidos no contexto de destino, apresentados no próximo turno com tag de conteúdo externo; turnos e threads existentes preservados. Identificação do bot para replies de grupo. Falha de pós-envio reportada como aviso sem esconder entrega confirmada.
- [x] **T5** — A antiga skill Telegram foi migrada para o módulo MCP `telegram`, sem credenciais próprias e usando RPC local. `GATEWAY_INSTRUCTIONS` diferencia resposta atual de envio externo autorizado.
- [x] **T6** — Testes offline de gateway (`test_telegram.py`, `test_telegram_channels.py`) e wrapper (`test_telegram.py` da skill), com API simulada e casos de cadastro, aprovações, callbacks, anexos, tópicos, geração e recibo.
- [x] **T7** — Compilação Python e testes segmentados em CI GitHub Actions, além de relatório não bloqueante da suíte integrada. Revisão de segurança no âmbito da skill; dependência de autenticação do daemon documentada no desenho.

**Evidência CI específica:** [GitHub Actions — Telegram outbound](https://github.com/rodrigogml/Onmyoji/actions/workflows/telegram-dev-tests.yml) — compilação + **38 testes segmentados aprovados** na execução que adicionou testes e 38 aprovados novamente na sequência de correções. Verificar a execução mais recente da branch antes de revisão/merge.

**Suíte completa (diagnóstico consultivo, não bloqueante):** na execução [39044117672](https://github.com/rodrigogml/Onmyoji/actions/runs/39044117672), **407 passaram e 11 falharam**, em componentes não alterados (dependência `boto3` do AWS IAM ausente no runner; teste de layout de terminal; teste de BIS2CMD; plataforma do cofre KeePass). As falhas não foram corrigidas nesta feature para evitar ampliar escopo e devem ser tratadas separadamente pelo upstream. Não alegar que a suíte total passou.

## Homologação — concluída

- [x] **H1** — Habilitar skill no setup e gateway Telegram real em ambiente de homologação; confirmar menus `/channel`, seleção owner, solicitação de terceiro, aprovação/rejeição/expiração e reinício. Homologação manual concluída na instância `Shikigami-Akuma`.
- [x] **H2** — Testar DMs simples e com tópicos, grupos com/sem tópicos, tópico Geral e criação proativa de tópico, incluindo permissões reais da Bot API. Validar ID de contexto e destinatário. Homologação manual concluída na instância `Shikigami-Akuma`.
- [x] **H3** — Testar upload real de documento, foto, áudio e voz; arquivo externo ao workspace rejeitado; limites; retries e recibos; comportamento ao bot bloqueado/sem privilégios. Homologação manual concluída na instância `Shikigami-Akuma`.
- [x] **H4** — Confirmar que envios da skill não disparam turno, o contexto é apresentado uma vez no próximo turno de destino e não vaza o histórico da origem; preservar thread e comportamento após remoção. Homologação manual concluída na instância `Shikigami-Akuma`.
- [x] **H5** — Verificar permissões de não-owner, callbacks forjados/expirados, exposição local do RPC e ausência de resposta duplex pela skill; regressão dos recursos de Telegram existentes. Homologação manual concluída na instância `Shikigami-Akuma`.

**Gate:** desenvolvimento/CI direcionado e homologação manual concluídos. A tarefa pode ser encerrada. Nenhuma credencial foi registrada nesta documentação.
