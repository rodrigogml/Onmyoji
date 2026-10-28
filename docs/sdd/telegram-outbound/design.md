# SDD · Telegram outbound — desenho técnico

**Fonte:** [requirements.md](requirements.md) e briefing aprovado. **Decisão arquitetural atual:** o módulo MCP `telegram` é cliente RPC **somente de saída**; gateway é dono do catálogo, contexto e Telegram API.

## Componentes
- `onmyoji-daemon/.../mcp/telegram`: módulo MCP que não lê token Telegram nem acessa a base de estado do gateway diretamente.
- `onmyoji-daemon/.../telegram.py`: comando e callbacks nativos /channel; persistência em SQLite dentro da instância; novos métodos RPC `telegram.channels.list`, `telegram.channels.remove`, `telegram.outbound.send`. `telegram.send` legado fica intacto para compatibilidade e não é usado pela skill.
- Módulo de canais isolado: catalogar por `conversation_id` (`chat_id`, `private:chat:topic`, `direct:chat:topic`, `group:chat:topic`), com controle de identidade de remetente e tokens de callback com expiração.
- `external_outbound`: persistência de envios entregues, indexada por `conversation_id` e geração de conversa, com controle de reapresentação no próximo turno do gateway. Mensagem do bot não é prompt de comando.

## Fronteiras
1. A autenticação do RPC é a do daemon (token local). O wrapper não aceita chat_id bruto como destino; escolhe somente canais catalogados.
2. `/channel`: menus de registro/remoção por owner; em DM simples de terceiro somente solicitação, decisão no DM do owner. Inspeção de `callback_query.from.id` e escopo de mensagem antes de qualquer alteração. No grupo/tópico negar antes de enfileirar turno.
3. Envio RPC só após consultar catálogo; a criação de tópico gera um destino operacional temporário sem cadastro independente. Sem reconstruir contexto da origem.
4. Caminho absoluto de arquivo deve resolver sob workspace configurado e corresponder a arquivo regular, não symlink que escapa. Limites do gateway/Telegram validados antes de upload. Falha de upload não gera registro de entrega.
5. Estado de thread continua sob `_app_thread`. Eventos externos são apresentados como dados a partir do próximo turno, marcados como consumidos apenas após sucesso.

## Não objetivos
- Duplex na skill, consulta de respostas, callbacks conversacionais externos, sincronização UI/CLI.
- Executor de eventos, gestão de não-owner, descoberta passiva, registro pela skill.
- Acesso remoto a Telegram diretamente pela skill.

## Riscos de integração a validar
- Per-client App Server e disponibilidade/identidade do requester: autorização rigorosa deve preservar a separação entre owner e não-owner sem depender apenas do prompt.
- Particularidades do `direct_messages_topic_id` vs `message_thread_id`; criação de tópicos Bot API.
- O catálogo mantém `conversation_id` como referência técnica estável, mas persiste rótulos visuais de chat e tópico recebidos dos eventos Telegram. Listagens e menus exibem esses rótulos; o identificador técnico continua reservado para resolução interna e envio.
- Após o cadastro, o menu do owner oferece `Identificar canal`: informa o nome atual e permite gravar um alias visual que prevalece sobre os nomes recuperados do Telegram. O último botão dos menus usa `Fechar`, sem sugerir desfazer a operação.
- Persistência e apresentação de anexos como dados (e não instruções) e retenção/limites.
