# Recuperação do Gateway Telegram

Correção publicada na versão `2026.11.13.2`.

## Problema

Uma interrupção entre a reserva do atendimento, a confirmação de `turn/start` e
o recebimento do término podia deixar a conversa bloqueada mesmo quando havia
prova suficiente para recuperar seu estado. A mensagem genérica que indicava
`/resolver` também encobria pendências diferentes, como a entrega de uma resposta
já concluída ou a preparação interrompida de uma mensagem.

O screenshot que motivou esta análise demonstra o bloqueio, mas não identifica
qual exceção ocorreu na instância. As correções abaixo foram reproduzidas por
testes de persistência e de ordenação dos eventos do Gateway.

## Mudanças

- Uma reserva interrompida sem nenhuma operação de entrada registrada pode voltar
  à fila. Essa fronteira é anterior ao envio ao Codex. Cancelamentos e gerações
  encerradas não voltam à fila; uma operação registrada continua exigindo prova
  antes de qualquer decisão sobre seu resultado.
- A preparação de uma mensagem é encerrada na recuperação quando a inbox já
  contém o mesmo recebimento, conversa e geração. Isso recupera a transferência
  persistida sem repetir o processamento de entrada.
- A identidade da RPC é persistida antes do envio, usando processo, tipo e valor
  do identificador. Uma confirmação tardia pode recuperar a operação correta.
  Falha ao persistir essa associação impede o envio.
- O transporte fecha a janela entre timeout e retirada da RPC pendente. Uma
  resposta recebida nesse intervalo é consumida pelo chamador ou encaminhada
  como resposta tardia; ela não fica em uma fila abandonada.
- O Gateway conserva a associação do turno depois de um timeout. Eventos tardios
  de início e término ainda podem concluir a execução, recuperar a resposta e
  liberar a reserva de capacidade exatamente uma vez.
- Resultados confirmados não são rebaixados por um timeout processado depois.
  A conclusão de um turno comprova seu início, mas não comprova a aceitação de
  uma entrada adicional de steering. Texto final e identidade de entrega já
  persistidos são preservados.
- A recuperação de marcadores usa o método certificado em G2:
  `thread/items/list`, com paginação limitada, ou `thread/read` quando esse método
  estiver explicitamente certificado. A consulta exige a mesma thread e o mesmo
  turno. Um turno já identificado por confirmação de início pode ter seu término
  consultado sem depender da capacidade opcional de recuperar marcadores.
- `/resolver` informa as categorias reais de pendência: execução, entrada,
  recebimento, resposta aguardando entrega/limpeza, recibo e envio sem confirmação.
  O descarte só anuncia a liberação quando não restam bloqueios.
- Diagnósticos registram etapa, motivo, classe da exceção e identidade da execução,
  sem copiar o conteúdo da conversa. Falhas repetidas de consulta ao histórico
  têm emissão limitada por execução e motivo.

## Limites preservados

Timeout não é prova de que uma operação deixou de acontecer. O Gateway não repete
automaticamente uma entrada que pode ter sido enviada. Ausência de marcador no
histórico, identidade divergente ou histórico indisponível mantêm a incerteza.

Respostas de outro processo, notificações que tentem imitar o transporte local,
RPCs inválidas e confirmações de outro turno não liberam a execução. Descarte,
cancelamento e `/new` não podem ser desfeitos por uma resposta tardia. Uma rejeição
definitiva de steering só pode seguir para a fila se a conversa e a execução
ainda permitirem esse encaminhamento.

Uma resposta já concluída com entrega pendente é recuperada separadamente.
Um envio ao Telegram cujo resultado é desconhecido continua registrado sem
reenvio automático. Descartar a solicitação local não desfaz efeitos externos.

## Persistência e atualização

O esquema do Gateway passa de 5 para 6, adicionando `input_operation_rpcs` sem
alterar as colunas de `input_operations`. Os caminhos de migração anteriores
continuam disponíveis. A migração mantém o backup SQLite existente por versão e
a proteção que recusa um leitor antigo diante de um esquema mais novo.

Depois de atualizar o código, é necessário reiniciar o Gateway da instância para
usar a correção. A recuperação de inicialização reavalia os falsos positivos
identificáveis; pendências antigas sem prova continuam dependendo de
reconciliação ou descarte explícito após conferir os efeitos externos.

## Validação

Os testes usam bancos temporários e transportes simulados. Nenhum pedido de DNS,
túnel Cloudflare ou mensagem real ao Telegram foi executado nesta validação.

Os cenários incluem reinício antes e depois da fronteira de envio, ACK tardio,
conclusão antes do catch do timeout, término durante o fechamento do worker,
confirmação de outro turno/processo, descarte durante leitura do histórico,
steering incerto após término, `/new`, preservação da resposta final e migração
aditiva. Também foram verificados os contratos de entrega, recursos, cancelamento,
login, geração de conversa, notificações e esquema de usuários.

O conjunto selecionado reuniu 497 casos: **496 passaram e um apresentou falha
preexistente**, em 23,61 segundos. A seleção incluiu os 20 arquivos abaixo; ela
não representa a suíte completa do repositório.

```bash
PYTHONPATH=tmp/test-deps:onmyoji-daemon/tests PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -B -m pytest -q --basetemp=tmp/pytest-recovery-release \
  onmyoji-daemon/tests/test_codex_app_server.py \
  onmyoji-daemon/tests/test_delegation_contracts.py \
  onmyoji-daemon/tests/test_notification_gateway.py \
  onmyoji-daemon/tests/test_notification_recovery.py \
  onmyoji-daemon/tests/test_state_layout.py \
  onmyoji-daemon/tests/test_telegram_acceptance.py \
  onmyoji-daemon/tests/test_telegram_context_generation.py \
  onmyoji-daemon/tests/test_telegram_controls_generation.py \
  onmyoji-daemon/tests/test_telegram_delivery_dispatch.py \
  onmyoji-daemon/tests/test_telegram_execution.py \
  onmyoji-daemon/tests/test_telegram_gateway_async.py \
  onmyoji-daemon/tests/test_telegram_login.py \
  onmyoji-daemon/tests/test_telegram_process.py \
  onmyoji-daemon/tests/test_telegram_recovery_persistence.py \
  onmyoji-daemon/tests/test_telegram_recovery_protocol_edges.py \
  onmyoji-daemon/tests/test_telegram_resource_races.py \
  onmyoji-daemon/tests/test_telegram_user_menus.py \
  onmyoji-daemon/tests/test_telegram_user_registration.py \
  onmyoji-daemon/tests/test_telegram_user_schema.py \
  onmyoji-daemon/tests/test_telegram_users.py
```

`tmp/test-deps` contém apenas dependências de teste desta validação, incluindo
pytest 8.4.2 e croniter 7.0.0. Em um ambiente com essas dependências instaladas, o
primeiro caminho pode ser removido de `PYTHONPATH`. O caminho dos testes é
necessário para a importação absoluta usada pelo teste de menus já existente.

A falha conhecida é
`test_second_owner_stale_menu_cannot_overwrite_approved_roles`, em
`test_telegram_user_menus.py`: um callback incompatível com a tela `stale` remove
a sessão do segundo proprietário antes de retornar, provocando `KeyError: 19`.
As permissões aprovadas permanecem corretas. A falha foi reproduzida isoladamente
tanto com esta correção quanto com os componentes originais da versão
`2026.11.13.1`; o fluxo de menus não foi alterado nesta correção de recuperação.
