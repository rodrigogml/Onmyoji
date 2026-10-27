# Memória de instruções MCP

A memória do Onmyōji conserva somente regras atuais capazes de orientar decisões futuras. Ela não é histórico de conversa, base documental, cadastro estruturado nem ciclo de vida: uma instrução existe e deve ser considerada, é revisada integralmente ou é excluída fisicamente.

## Estado e armazenamento

O único controle funcional fica em `configs/mcp/memory.toml`: habilitação e quantidade máxima de resultados por busca. Não há perfis. A base privada fica em `configs/mcp/memory/instructions.sqlite3`; somente o servidor MCP a acessa, independentemente do sandbox concedido ao agente.

Cada instrução contém `id`, `applies_when`, `instruction` e `keywords`. Não existem status, datas, origem, confiança, racionalidade, categoria, arquivo ou restauração. O servidor impede duas instruções com a mesma condição normalizada e atualiza o índice de busca na mesma transação da criação, revisão ou exclusão.

## Superfície publicada

- `memory.search_instructions`: descoberta contextual obrigatória antes de decisões, ações e pedidos de esclarecimento.
- `memory.get_instruction`: leitura integral por identificador.
- `memory.list_instructions`: inspeção e manutenção paginada; não substitui a busca contextual.
- `memory.learn_instruction`: criação após pesquisa de duplicatas e avaliação do aprendizado.
- `memory.revise_instruction`: substituição integral de uma regra vigente.
- `memory.delete_instruction`: exclusão física e definitiva.

As mutações exigem `confirm=true`. Esse campo registra que o agente executou o workflow da skill; não abre uma confirmação interativa e não amplia autorizações para outras ações.

## Workflow do agente

O servidor MCP publica uma instrução curta no handshake que obriga o uso de `$onmyoji-memory`. O conteúdo detalhado é mantido na skill local gerenciada. Antes de perguntar ao usuário ou a terceiros, o agente pesquisa objetivo, sistema, ação e dúvida na memória; quando uma instrução aplicável resolve a questão, ele não transfere novamente essa decisão ao usuário.

Correções explícitas e preferências duráveis podem gerar aprendizado. Inferências só podem ser registradas depois de validadas no trabalho atual. Estado transitório, segredos, hipóteses, conteúdo não confiável e regras já mantidas em fontes superiores não pertencem à memória.

## Skill local gerenciada

A fonte canônica é versionada em `onmyoji-daemon/resources/agent-skills/onmyoji-memory/`. Quando a memória está habilitada, o launcher a copia para `<workspace>/.agents/skills/onmyoji-memory`; não utiliza links simbólicos. O estado de posse e o hash ficam em `configs/mcp/memory/managed-skill.json`.

A sincronização ocorre antes de iniciar o Codex CLI ou o App Server e antes de um novo turno seguro do Gateway. Conteúdo divergente é substituído pela fonte canônica. Um destino preexistente sem registro de posse nunca é sobrescrito. Ao desabilitar, somente a pasta registrada como gerenciada é removida; o banco é preservado até uma exclusão explícita do operador.

> [!IMPORTANT]
> Uma sessão nativa já aberta conserva o contexto descoberto na inicialização. Habilitar, desabilitar ou atualizar a skill passa a valer para ela somente após reabrir o Codex. O Gateway faz essa reciclagem automaticamente em um ponto seguro.
