# SDD · Telegram outbound — requisitos

**Fonte aprovada:** [briefing funcional](../../20261017-briefing-skill-telegram.md), decisões P1–P8. **Upstream:** Onmyoji. **Escopo:** desenvolvimento, sem acesso a credenciais ou Telegram real.

## Contrato observável
- R1: só `/channel` no Telegram cria registro. Owner cadastra chat/tópico exato; terceiros só solicitam cadastro de DM simples, com aprovação explícita do owner. Em grupos/tópicos, terceiros recebem “Esta operação é permitida apenas ao proprietário.”
- R2: registros persistidos por identificador lógico de conversa; não derivar catálogo de mensagens recebidas. A skill lista, consulta e remove; não registra.
- R3: owner envia texto, imagem, documento e áudio/voz a destinos registrados. Envio para tópico criado sob demanda é permitido quando parte de chat/tópico já registrado, sem cadastrá-lo automaticamente.
- R4: cada envio externo é persistido no contexto de destino. Thread existente é preservada; thread ausente nasce na primeira interação autorizada; nunca transferir thread de origem.
- R5: a skill é estritamente outbound, sem menus/espera/respostas, retorna confirmação do envio ou erro. Menus administrativos `/channel` continuam no gateway.
- R6: cadastro não concede atendimento inbound. Remoção não apaga threads, chats nem histórico; não altera atendimento normal.
- R7: DM/grupo sem tópicos mantém uma thread vigente por conversa. Não direcionar respostas a threads de outras aplicações.
- R8: ferramentas precisam operar fora de turno Telegram ativo, sem IDs Telegram arbitrários fornecidos pelo usuário. Rejeitar caminhos fora do workspace e tamanho acima dos limites.

## Critérios de aceite em desenvolvimento
1. Testes sem rede: cadastrar, aprovar, negar, remover, persistir; proteção por identidade e contexto.
2. Testes com API Telegram simulada: endereçar tópico, enviar texto/arquivo, erro da API, metadados de envio e ausência de chamadas ao Codex.
3. Testes de histórico: não misturar contextos, replay no próximo turno, sem duplicação; origem não vira autorização.
4. Testes CLI: argumentos inválidos rejeitados, nenhuma leitura de arquivo fora de raiz permitida.
5. Homologação posterior: permissões e particularidades Bot API, DM topics, upload, /channel, callback Telegram real, instalação e retomada da thread.
