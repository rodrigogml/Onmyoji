# Validação de eventos e agendamentos

Auditoria da implementação atualizada em 2026-11-11. O contrato e os procedimentos de uso estão em [eventos e agendamentos](eventos-agendamentos.md). Esta matriz relaciona os critérios de entrega ao código e às verificações executáveis; os checklists do rascunho antigo são históricos.

Evidências da versão consolidada:

- Windows, suíte completa executada depois da integração na `main`: 3.278 testes e 301 subtestes aprovados, 46 ignorados, zero falhas; 164,60 segundos. Dois avisos existentes dizem respeito a `record_property` no formato JUnit xunit2. Relatório local: `tmp/main-merge.xml`.
- A evidência Linux do baseline anterior não foi repetida nesta atualização; os testes de permissões POSIX permanecem explicitamente restritos ao sistema compatível.
- Matriz focada de setup, contrato, gateway HTTP, prioridade, recuperação, logs, incidentes, agendador, CLI e credenciais: 126 testes aprovados e três ignorados antes da rodada integral.
- Validador do evento: 29 testes aprovados e 100% das instruções e ramos cobertos.

## Critérios verificados

| Requisito | Entrega e evidência |
| --- | --- |
| Contrato JSON v1 | Schema fechado, inteiro 1, UUIDv7 canônico, prompt não vazio, canais condicionais e campos extras recusados. `test_notification_contract.py` tem 100% de instruções e ramos. |
| Núcleo e escalonamento compartilhados | Processo `telegram`, inbox, SQLite, App Server, semáforo e timeout permanecem únicos. `test_notification_gateway.py` comprova prioridade estrita do Telegram, FIFO por classe, não preempção, salto de destino ocupado e limites 50/1.024; as regressões do gateway comprovam o teto `max_parallel_conversations` e proteção de timeout/incerteza. |
| Destinos e threads | Chaves Telegram e `(email,account,value)` ficam isoladas. Testes cobrem `reuse`, `new`, UUID repetido, associação publicada somente após sucesso, falha de entrega sem rollback e resultado vazio `empty_agent_result`. |
| Listener HTTP | Bind fixo em loopback, 1 MiB, `wait=true|false`, limite de 32 acompanhamentos por padrão, persistência antes do 202, desconexão sem cancelamento e somente 400/413/429/503 antes do aceite. Testes cobrem ainda HTTP 200 com `result=completed` e `delivery=failed`. |
| Entregas | Telegram reutiliza o catálogo vigente. E-mail captura conta/assunto por evento, usa o MCP existente, SMTPUTF8 e `gateway:<inbox-id>:delivery`; não existe retry automático. |
| Recuperação do gateway | Reserva sem operação retorna a `pending`; execução interrompida vira `uncertain`, bloqueia o destino e não reinicia o agente; entrega `sending` vira incerta sem reenvio. Migrações são aditivas e recusam downgrade. |
| Logs e incidentes | JSONL diário confinado, journal transacional, retenção 20 dias, limpeza inicial/diária, input/output/erro/UUID/UTC e ausência de headers/tokens. Incidente usa o mesmo modelo de destino, inclui referência ao log e tenta uma vez sem recursão. |
| Arquivos de agendamento | Um TOML v1 por ID em `schedules/`; mtime revalidado a cada cinco segundos. Inválido pausa com diagnóstico, alteração afeta somente a próxima ocorrência e exclusão impede novo início preservando histórico por 20 dias. |
| Cron e concorrência | Apenas `once` com offset e cron de cinco campos com timezone IANA, `croniter>=7,<8`, DST inexistente pulado e duplicado executado na primeira ocorrência. Atrasos coalescem uma vez; cada definição mantém no máximo uma ativa e uma pendente. |
| Ações do agendador | `event` persiste UUIDv7 por ocorrência e usa HTTP local. `script` usa o Python do daemon, caminho relativo ao workspace, args sem shell, timeout padrão configurável e stdout/stderr limitados. Parada anterior à fronteira deixa retomada limpa; queda posterior nunca repete cegamente. |
| Setup | Menus separados “Eventos do agente” e “Agendamentos”. Eventos configura habilitação, porta, incidente e diagnóstico. A lista somente leitura tem 20 itens, P/N/X, dez colunas 1–0, inversão por repetição, nulos por último e ID como desempate. |
| Validação integrada | A suíte completa acima foi executada depois da última correção funcional. Recursos permanecem desabilitados por padrão e todos os canais/processos dos testes são de laboratório. |

Os testes citados sem caminho completo pertencem a `onmyoji-daemon/tests/`. A suíte usa `tmp_path`, relógios controlados, canais/App Server falsos e sinais observáveis de prontidão. O teste de descendentes confirma por socket que o neto foi criado antes de validar timeout ou parada.

## Reprodução

Windows, na raiz do repositório, com o Python e as dependências do projeto:

```text
python -B -m pytest -q --tb=short --basetemp=tmp/pytest-main-merge --junitxml=tmp/main-merge.xml
python -B -m pytest onmyoji-daemon/tests/test_task_scheduler.py -q --tb=short --basetemp=tmp/pytest-task-scheduler
```

Cobertura do validador, incluindo ramos:

```text
python -B -m coverage run --branch --include=*/agent_notification/contract.py --data-file=tmp/events-contract-coverage -m pytest onmyoji-daemon/tests/test_notification_contract.py -q --basetemp=tmp/pytest-events-contract-coverage
python -B -m coverage report --data-file=tmp/events-contract-coverage --fail-under=100
```

Linux nativo, em uma VM Alpine 3.22 x86_64 com kernel 6.12.31 e Python 3.12.16:

```text
python3 -B -m pytest onmyoji-daemon/tests/test_task_scheduler.py onmyoji-daemon/tests/test_notification_contract.py onmyoji-daemon/tests/test_rpc_concurrent.py -q --tb=short --junitxml=tmp/linux-release.xml
```

A VM usada no baseline anterior era transitória e executava no kernel Linux real. O comando acima é o roteiro para repetir essa verificação com `croniter` 7; ele não foi executado nesta auditoria e, portanto, a evidência atual certifica somente Windows. macOS e outros sistemas POSIX também não estão certificados.

O wheel `onmyoji_daemon-0.1.0-py3-none-any.whl` foi construído em staging isolado a partir do commit publicado. A inspeção confirmou o schema distribuído e igualdade SHA-256 dos 18 arquivos Python/JSON de `agent_notification`, `execution_gateway` e `task_scheduler` com a árvore validada, sem divergências.

## Fronteiras operacionais

Antes de migrações são mantidos backups SQLite consistentes junto ao estado persistente. O agendador distingue reserva durável de início de ação: reserva interrompida pode retomar; ação iniciada sem resultado torna-se incerta. O gateway conserva submissões possivelmente enviadas e entregas em andamento sem repetição automática.

A ativação operacional é uma ação explícita posterior: configurar destinos, informar conta SMTP por evento/incidente, habilitar os serviços e criar os TOMLs. Nenhum envio a destinatário real nem processo operacional integra estes testes. O listener não possui TLS/autenticação por decisão de contrato e deve permanecer em loopback. Não restaurar banco antigo após aceitar trabalho novo; desabilitar a capacidade afetada e aplicar correção progressiva.
