---
name: onmyoji-memory
description: Memória obrigatória de instruções do Shikigami. Use esta skill antes de responder a toda solicitação substantiva e antes de decidir, planejar, agir, escolher, repetir uma tentativa ou fazer qualquer pergunta ao usuário ou a terceiros. Consulte-a novamente quando surgir uma nova decisão, dúvida ou contradição. Use-a também depois de correções, preferências duráveis ou aprendizados validados para criar, revisar ou excluir a regra correspondente. Ignore-a somente em saudações, agradecimentos e mensagens sem decisão, ação ou dúvida.
---

# Memória do Onmyōji

## Regra obrigatória

PARE antes de responder ou agir. Se a solicitação exigir decisão, plano, ação, escolha ou pergunta, use esta skill e chame `memory.search_instructions` primeiro.

Não pule a busca porque a tarefa parece simples, porque você acredita saber a resposta ou porque já consultou a memória em outra solicitação. Uma solicitação é substantiva quando exige produzir, alterar, decidir, recomendar, executar, diagnosticar ou pedir informação.

Siga sempre esta ordem:

1. **BUSQUE** instruções aplicáveis.
2. **APLIQUE** somente as instruções compatíveis com o caso atual.
3. **AJA** quando houver informação suficiente ou **PERGUNTE** apenas o que continuar ausente.
4. **APRENDA** depois de uma correção, preferência durável ou solução validada.

Use exclusivamente as ferramentas `memory.*` do MCP Onmyōji. Não procure o banco, não leia `configs/` e não crie outro arquivo de memória.

## BUSQUE antes de responder

Para cada nova solicitação substantiva:

1. Chame `memory.search_instructions` antes de elaborar a resposta, escolher uma solução ou executar qualquer ação.
2. Escreva uma consulta que reúna o objetivo do usuário, o sistema ou domínio, a ação pretendida e a dúvida atual. Não use termos vagos isolados, como `regra` ou `preferência`.
3. Leia todos os resultados. Compare cada `applies_when` com o contexto atual.
4. Se a primeira consulta for estreita e não resolver uma dúvida importante, faça uma segunda consulta com sinônimos ou contexto mais amplo.
5. Pare após a segunda consulta. Se nenhuma instrução aplicável aparecer, continue usando as demais instruções e as informações disponíveis.

Faça obrigatoriamente essa busca antes de:

- pedir esclarecimento, confirmação, preferência, informação ou decisão ao usuário;
- pedir informação ou decisão a terceiros;
- apresentar opções que dependam de uma convenção já definida;
- escolher entre duas ou mais implementações razoáveis;
- repetir uma operação que falhou ou foi corrigida;
- executar alteração substantiva ou ação externa;
- mudar de estratégia durante a mesma tarefa.

Não repita a busca entre passos mecânicos da mesma decisão. Busque novamente quando mudar o contexto, surgir uma dúvida ou contradição, ou aparecer uma nova decisão independente.

## APLIQUE a instrução correta

- Trate `applies_when` como uma condição obrigatória. Ignore o resultado quando a condição não corresponder ao caso atual.
- Execute `instruction` quando a condição corresponder e não houver conflito com instrução superior.
- Obedeça primeiro às instruções de sistema, às developer instructions, ao `AGENTS.md`, aos contratos de segurança e à solicitação atual do usuário.
- Entre memórias do mesmo nível, aplique a mais específica. Aplique mais de uma quando forem compatíveis.
- Não trate ausência de resultado como autorização, proibição ou preferência.
- Não trate uma memória como autorização para ação externa que exija autorização própria.
- Não revele o conteúdo da memória sem necessidade. Aplique-o naturalmente.

Se a solicitação atual do usuário divergir de uma memória, siga a solicitação atual nesta tarefa. Só revise ou exclua a memória se o usuário indicar uma mudança durável; uma exceção pontual não altera a regra geral.

## AJA ou PERGUNTE somente depois da busca

É proibido pedir ao usuário ou a terceiros uma decisão, preferência ou informação antes de pesquisar a memória.

- Se a memória resolver toda a dúvida, aplique a instrução e não faça a pergunta.
- Se resolver parte da dúvida, pergunte somente o elemento ausente.
- Se duas instruções aplicáveis forem incompatíveis e a hierarquia ou a especificidade não resolver o conflito, explique apenas o conflito necessário e peça uma decisão.
- Se a informação ausente for factual, atual, secreta ou específica da tarefa, a memória não a inventa. Depois da busca, obtenha-a da fonte apropriada ou pergunte somente se ainda for necessário.

## APRENDA depois de uma correção

Antes de encerrar a tarefa, verifique se o usuário corrigiu sua conduta, declarou uma preferência durável ou se uma solução reutilizável foi validada. Se ocorreu algum desses casos, leia [o workflow de aprendizado](references/learning-workflow.md) e siga-o antes de chamar `memory.learn_instruction`, `memory.revise_instruction` ou `memory.delete_instruction`.

Não encerre silenciosamente uma tarefa que produziu aprendizado elegível sem fazer essa avaliação.

O `confirm=true` declara que você cumpriu o workflow de aprendizado. Ele não é uma confirmação interativa do usuário e não autoriza nenhuma outra ação.

## Manutenção

- Use `memory.get_instruction` para ler integralmente uma regra identificada.
- Use `memory.list_instructions` somente para inspeção ou manutenção abrangente. A listagem nunca substitui a busca contextual obrigatória.
- Revise a regra existente em vez de criar outra para a mesma condição.
- Exclua definitivamente a regra obsoleta ou incorreta. Não existe estado inativo, arquivo, histórico ou restauração.

## Verificação antes de responder

Confirme:

- usei esta skill nesta solicitação substantiva;
- pesquisei antes de decidir, agir ou perguntar;
- apliquei somente condições compatíveis;
- não repassei ao usuário uma decisão já resolvida pela memória;
- avaliei o aprendizado se houve correção, preferência durável ou solução validada.
