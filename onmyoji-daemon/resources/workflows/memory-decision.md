# Decisão orientada por memória

Antes de responder, decidir, planejar, executar uma ação ou pedir esclarecimento ao usuário ou a terceiros, pesquise `memory.search_instructions` com o objetivo, a ação, o sistema e o contexto atuais.

Leia integralmente cada resultado potencialmente aplicável com `memory.get_instruction`. Aplique somente instruções cuja condição corresponda ao caso atual. Nunca use memória para contrariar instruções superiores, a solicitação atual do usuário ou evidência mais recente.

Faça nova busca quando surgir uma decisão, dúvida, contradição ou contexto independente. Se a memória não resolver a lacuna, prossiga normalmente: obtenha evidência, faça a ação segura ou formule a pergunta necessária.
