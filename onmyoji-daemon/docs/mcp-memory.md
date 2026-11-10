# Memória de instruções MCP

A memória do Onmyōji conserva somente regras atuais capazes de orientar decisões futuras. Ela não é histórico de conversa, base documental, cadastro estruturado nem ciclo de vida: uma instrução existe e deve ser considerada, é revisada integralmente ou é excluída fisicamente.

## Estado e armazenamento

O único controle funcional fica em `configs/mcp/memory.toml`: habilitação e limite padrão de resultados por busca. Cada chamada pode solicitar de 1 a 10 resultados. Não há perfis. A base privada fica em `configs/mcp/memory/instructions.sqlite3`; somente o servidor MCP a acessa, independentemente do sandbox concedido ao agente.

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

- `memory-decision`: obrigatório para solicitações substantivas e antes de decidir, agir ou pedir esclarecimento; separa decisões, define consultas curtas, reformulações, avaliação de condições e o que fazer quando a memória não resolve a dúvida;
- `memory-learning`: avalia correções e aprendizados antes do encerramento e é obrigatório antes de qualquer mutação; define comparação de equivalentes, escolha entre criar/revisar/excluir/não gravar, preenchimento dos campos e verificação do retorno.

Antes de perguntar ao usuário ou a terceiros, o agente pesquisa objetivo, sistema, ação e dúvida na memória; quando uma instrução aplicável resolve a questão, ele não transfere novamente essa decisão ao usuário.

Correções explícitas e preferências duráveis podem gerar aprendizado. Inferências só podem ser registradas depois de validadas no trabalho atual. Estado transitório, segredos, hipóteses, conteúdo não confiável e regras já mantidas em fontes superiores não pertencem à memória.

O agente reutiliza o workflow integral que ainda estiver no contexto; relê quando ele estiver ausente ou houver atualização. Isso não dispensa novas buscas para decisões independentes. O procedimento detalhado é mantido somente nos [workflows canônicos](../resources/workflows/memory-decision.md), incluindo o [aprendizado e manutenção](../resources/workflows/memory-learning.md).

## Responsabilidade da busca e da deduplicação

A busca é lexical, considera condição, conduta e palavras-chave e combina os termos por OU. O índice ignora caixa e acentos, mas não expande sinônimos nem reconhece condições equivalentes por significado. O MCP normaliza caixa e espaços para comparar condições e keywords; acentos e paráfrases não são eliminados nessa comparação. Cabe ao agente reformular consultas, avaliar escopo e conduta e revisar o ID existente quando a orientação já estiver representada. A trava de condição duplicada não substitui esse procedimento.

## Workflows canônicos

Os documentos canônicos ficam em `onmyoji-daemon/resources/workflows/`. O servidor os entrega somente quando `workflows.read` recebe uma rota disponível na instância. Eles não são copiados para o workspace, não possuem estado de posse e não podem ser alterados pelo agente. O banco é preservado até uma exclusão explícita do operador.

> [!IMPORTANT]
> Uma sessão nativa já aberta conserva as ferramentas e instruções descobertas na inicialização. Habilitar, desabilitar ou atualizar uma rota passa a valer após reabrir o Codex. O Gateway faz essa reciclagem automaticamente em um ponto seguro.
