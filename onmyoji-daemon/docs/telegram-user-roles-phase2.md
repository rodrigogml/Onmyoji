# Telegram — Fase 2: autocadastro e controles públicos em DM

Data: 2026-11-12. Versão de setup: `2026.11.12.37`. Schema do Gateway: **v5** (inalterado).

## Resultado desta fase

O Gateway Telegram agora trata três controles públicos **diretamente no Gateway**: `/register`, `/status` e `/unregister`. Esses comandos estão disponíveis apenas no chat privado do próprio remetente, inclusive com tópico privado reconhecido, e não são enviados ao Codex, não enfileiram turnos, não criam threads e não transformam DMs em canais registrados.

O Owner continua sendo obtido exclusivamente de `contacts.json`, com o pareamento `/pair <PIN>` original. Uma conta Owner recebe informação de que não participa do cadastro público e não pode retirar seu status por `/unregister`.

Mesmo que a configuração `[telegram].user_roles_enabled = true` esteja ligada, **a Fase 2 não habilita conversação de não-Owners nem autorização derivada de notificação**. Essa flag continua dependente das integrações das Fases 4 e 5.

## Contrato dos comandos

| Comando | Efeito |
| --- | --- |
| `/register` | Ausente ou `WITHDRAWN` passa a `PENDING`; repetição é idempotente. `REJECTED` e `SUSPENDED` não reabrem. `ACTIVE` consulta o estado sem alterar roles. Novo pedido gera aviso aos Owners. |
| `/status` | Responde somente sobre o remetente: inexistente, pendente, ativo (com roles atuais), suspenso, rejeitado ou retirado. |
| `/unregister` | Envia menu com botões **Confirmar retirada** e **Cancelar**. Somente o primeiro clique validado, dentro de cinco minutos, pode concluir. Confirmar chama `TelegramUserRegistry.withdraw` com revisão otimista, revogando roles e persistindo auditoria. Cancelar não altera estado. |
| `/users` | Reservado ao Owner, na DM. **Ainda não é um menu de administração**: a Fase 2 mostra aviso de que o gerenciamento será implementado na Fase 3. |

O cadastro usa o `from.id` numérico fornecido pelo Bot API como identidade e o `_conversation_id` calculado pelo Gateway como destino DM/tópico. `username`, nome e idioma são metadados, nunca credenciais. Os controles rejeitam chat não privado, `chat.id != from.id`, IDs booleanos ou inválidos, bots, legendas/mídia, mensagens editadas e comandos endereçados a outro bot. Comandos com argumentos retornam orientação de sintaxe, sem enviar argumentos ao agente.

A confirmação `/unregister` usa prefixo exclusivo `ur:` de até 64 bytes por `callback_data`, token criptograficamente aleatório, `from.id`, `chat.id`, `message_id`, tópico (se enviado no callback), geração da conversa, prazo monotônico e revisão do usuário. Sessões permanecem em memória com limite de 512 registros; após reinício os menus anteriores deixam de ser válidos. O token é consumido antes da mutação. Caso a revisão mude, a operação é negada e a pessoa recebe orientação para consultar `/status`. A auditoria e a revogação real ocorrem na transação SQLite existente.

## Avisos e falhas

Apenas uma **transição nova para `PENDING`** envia aviso aos Owners pareados, com nome público, `@username` se disponível e ID numérico. Nenhuma mensagem recebida, conteúdo de conversa, credencial ou PIN integra esse aviso. Se um ou vários avisos falharem, o pedido continua gravado e consultável; uma repetição de `/register` não provoca nova enxurrada de avisos.

O menu de aprovação ainda não existe na Fase 2. A mensagem informa ao Owner que o processamento do pedido ficará disponível na Fase 3. A ativação de roles nunca é realizada pelo próprio usuário.

A ausência de confirmação de envio do menu `/unregister` impede a criação da sessão. Não se presume que o botão foi entregue; uma retirada não é executada sem callback válido. Falhas de transporte nos avisos e respostas são registradas de forma sanitizada, sem dados pessoais de conversas.

## Arquivos e pontos de integração

- `src/onmyoji_daemon/telegram_user_controls.py`: mixin isolando autocadastro, mensagens operacionais, aviso a Owners e confirmação de retirada.
- `src/onmyoji_daemon/telegram.py`: chamada aos comandos antes do bloqueio de não-Owners, validação de DM real sem usar chat sintetizado, inicialização de sessões e roteamento de `ur:` antes do early-return Owner-only de callbacks.
- `src/onmyoji_daemon/telegram_commands.py`: Owner vê comandos novos (incluindo o stub `/users`); demais DMs veem comandos de autocadastro e controles legados. `/pair` continua fora do catálogo.
- `docs/Telegram.md`: matriz de permissões efetivas e catálogo atualizado.
- `tests/test_telegram_user_controls.py`: testes de API do mixin e SQLite com Telegram simulado.
- `tests/test_telegram_user_registration.py`: testes de ingresso e callback do Gateway real, com Bot API simulado.
- `tests/test_telegram_commands.py`: contrato de catálogo e documentação atualizados; proposta histórica `Telegram-2026-11-12.proposed.md` permanece intocada.

## Validação e passagem de fase

Testes locais de serviço SQLite e mixin, sem rede, foram executados com `python -B -m pytest` usando área temporária `tmp/`. A suíte completa do daemon e os testes do Gateway real exigem checkout/ambiente completo e devem ser executados antes da ativação dos roles em produção.

**Não houve migração de banco na Fase 2**; a versão de schema permanece 5, e o backup da Fase 1 continua sendo o ponto de referência. A feature flag segue desligada por padrão no modelo de configuração. Reiniciar o Gateway após atualizar a instalação para carregar comandos e handlers; a sincronização normal atualizará `all_private_chats` e os escopos dos Owners.

**Fase 3:** substituir o aviso provisório de `/users` por menu Owner-only, filtros/paginação, edição de roles, auditoria e callbacks seguros. Os registros `PENDING` gerados nesta fase estarão disponíveis para decisão. Continuam fora da fase atual as respostas de `CHAT_USER`, os destinos de `NOTIFY_TARGET` e o isolamento avançado de Codex/MCP.
