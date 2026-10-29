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

As mutações exigem `confirm=true`. Esse campo registra que o agente executou o workflow `memory-learning`; não abre uma confirmação interativa e não amplia autorizações para outras ações.

## Workflow do agente

O servidor MCP publica uma rota curta no handshake e o Composer a inclui nas developer instructions do App Server. O agente lê o procedimento completo com `workflows.read`, sem receber todos os workflows de uma vez:

- `memory-decision`: obrigatório antes de responder, decidir, agir ou pedir esclarecimento;
- `memory-learning`: obrigatório antes de criar, revisar ou excluir uma instrução.

Antes de perguntar ao usuário ou a terceiros, o agente pesquisa objetivo, sistema, ação e dúvida na memória; quando uma instrução aplicável resolve a questão, ele não transfere novamente essa decisão ao usuário.

Correções explícitas e preferências duráveis podem gerar aprendizado. Inferências só podem ser registradas depois de validadas no trabalho atual. Estado transitório, segredos, hipóteses, conteúdo não confiável e regras já mantidas em fontes superiores não pertencem à memória.

## Workflows canônicos

Os documentos canônicos ficam em `onmyoji-daemon/resources/workflows/`. O servidor os entrega somente quando `workflows.read` recebe uma rota disponível na instância. Eles não são copiados para o workspace, não possuem estado de posse e não podem ser alterados pelo agente. O banco é preservado até uma exclusão explícita do operador.

> [!IMPORTANT]
> Uma sessão nativa já aberta conserva as ferramentas e instruções descobertas na inicialização. Habilitar, desabilitar ou atualizar uma rota passa a valer após reabrir o Codex. O Gateway faz essa reciclagem automaticamente em um ponto seguro.
