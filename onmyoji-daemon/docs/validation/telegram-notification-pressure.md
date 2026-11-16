# Pressão de notificações no Gateway Telegram

Correção publicada na versão `2026.11.16.1`.

## Falha e causa

O diagnóstico de uma continuação interrompida registrou `transport.lost` com
`event_overflow`. O transporte recebia notificações de progresso que o Gateway
não utilizava e só as descartava depois da fila e da correlação com o banco.
Uma rajada podia preencher a fila de uma thread e acionar a perda de processo.
O aviso de confirmação ausente era a consequência dessa interrupção.

## Contrato corrigido

O Gateway declara quais notificações remotas consome. O transporte filtra as
demais antes de ocupar as filas, preservando validação do envelope e a sequência
observada. Outros consumidores que não declaram filtro continuam recebendo todas
as notificações. Uma seleção vazia suprime somente notificações remotas.

Respostas RPC, chamadas de ferramentas, respostas tardias e perda de transporte
local não são filtradas. Os eventos consumidos continuam ordenados por thread;
a conclusão de itens conserva os textos completos e a conclusão do turno mantém
a autoridade sobre seu estado final. A correlação deixa de abrir transações de
geração que não alteravam a decisão de encaminhamento; a geração continua sendo
verificada antes dos efeitos que a exigem.

> [!IMPORTANT]
> Não houve aumento de limite nem descarte de confirmações de início/término.
> Saturação de eventos efetivamente consumidos continua produzindo perda explícita
> de transporte. Operações possivelmente aceitas não são repetidas automaticamente.

A atualização precisa ser seguida pelo reinício do serviço Telegram. Ela evita
a causa de saturação identificada, mas não conclui solicitações já interrompidas
nem comprova o resultado de operações antigas sem confirmação. `/resolver`
continua sendo o controle de recuperação para incertezas reais.

## Validação

Os testes usam processos simulados e bancos temporários; não enviam mensagens
reais ao Telegram nem executam inferência. Cobrem rajadas de notificações não
consumidas, preservação da ordem e da resposta completa, login, requisições de
ferramentas, correlação RPC, eventos locais de recuperação, compatibilidade sem
filtro e falha explícita quando os eventos admitidos saturam a fila.

As suítes de execução, recuperação, geração e login verificam os contratos
adjacentes. Não se atribui à correção a resolução de falhas antigas cuja causa
específica não foi registrada.
