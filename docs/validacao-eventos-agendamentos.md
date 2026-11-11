# Validação de eventos e agendamentos

Auditoria da implementação atualizada em 2026-11-11. O contrato e os procedimentos de uso estão em [eventos e agendamentos](eventos-agendamentos.md). Esta matriz relaciona os critérios de entrega ao código e às verificações executáveis; os checklists do rascunho antigo são históricos.

Evidências da versão consolidada:

- Windows, suíte completa: 3.246 testes e 301 subtestes aprovados, 46 ignorados, zero falhas; 179,46 segundos. Dois avisos existentes dizem respeito a `record_property` no formato JUnit xunit2. Relatório local: `tmp/events-full-latest.xml`.
- A evidência Linux do baseline anterior não foi repetida nesta atualização; os testes de permissões POSIX permanecem explicitamente restritos ao sistema compatível.
- Contrato, gateway HTTP, recuperação, agendador, CLI e setup: 186 testes e 18 subtestes focados aprovados antes da rodada integral.
- Validador do evento: 29 testes aprovados e 100% das instruções e ramos cobertos.
- Retenção: sete testes focados aprovados e verificação dos dois watchers com relógio monotônico em 0,5 segundo, confirmando limpeza já na primeira iteração.
- Publicação de skills: 24 testes focados aprovados após corrigir um bloqueio transitório Windows detectado pela suíte completa. O materializador usa a publicação atômica com tolerância limitada já existente no projeto; teste de injeção comprova recuperação de `WinError 5` durante reparo da pasta gerenciada.
- Agendador TOML e setup: 139 testes e 18 subtestes focados aprovados em 7,86 segundos, incluindo contrato temporal, observação de arquivos, ações, HTTP integrado, CLI somente leitura e as dez colunas do setup.

## Critérios verificados

| Etapa | Entrega e evidência |
| --- | --- |
| 1. Contrato | Schema v1 fechado incluído no wheel; validador comum para CLI emissora, ingresso e ação do agendador. `test_notification_contract.py` cobre tipos, campos ausentes/adicionais, versão, UUIDv7 e erros com caminho completo, sem eco do prompt. |
| 2. Núcleo | `execution_gateway/execution.py` contém o ciclo compartilhado; o módulo antigo mantém compatibilidade de importação. Regressões `test_telegram_execution.py`, `test_telegram_gateway_async.py`, controles, gerações, delegação, recursos e entregas permanecem na suíte integrada. |
| 3. Multiorigem | Banco, lease, inbox e App Server únicos; sequência global durável; Telegram prioritário e destinos ocupados pulados. `test_notification_gateway.py` e `test_notification_recovery.py` verificam seleção, exclusão por destino, sequência após limpeza e bloqueio durante entrega recuperada. |
| 4. HTTP e lifecycle | Listener HTTP concorrente preso a loopback, aceite assíncrono durável e acompanhamento terminal; o RPC interno permanece compatível. `test_notification_http.py`, `test_rpc_concurrent.py`, `test_supervisor_telegram_timeout.py` e as regressões de shutdown verificam 200/202/400/413/429/503, saturação e desconexão sem cancelamento. |
| 5. Telegram | Evento destinado a Telegram entra antes da seleção de contexto e usa as mesmas ferramentas, instruções, preferências e associação do chat/tópico. Testes com `reuse` e `new` cobrem continuação cruzada entre mensagem e evento, repetição do mesmo UUID, falha do turno, falha da entrega e preferência de áudio. |
| 6. Entregas e recuperação | Conta e assunto SMTP capturados no envelope, chave `gateway:<inbox-id>:delivery`, estados de confirmação/falha/incerteza, resultado vazio rejeitado e incidente sem recursão. Testes injetam desconexão durante envio, reinício em `sending`, falha entre journal e JSONL, append truncado, falha transacional e recuperação de incidente pendente. Retenção recusa diretório redirecionado e preserva arquivos alheios. |
| 7. Configuração | Notificações e receptor Telegram são independentes; diagnóstico valida contrato e disponibilidade das configurações de SMTP/incidente sem envio. Testes cobrem conta removida, receptor desligado com TOTP antigo e escolhas do setup em fluxo. |
| 8. Administração | Um TOML versionado por ID é a fonte de verdade; SQLite guarda somente runtime e histórico. `test_task_scheduler.py` verifica mtime, pausa/diagnóstico de arquivo inválido, correção, exclusão, snapshots e recusa de autoria remota. |
| 9. Calendário e dispatcher | Apenas `once` com offset e cron de cinco campos com timezone IANA. Testes com relógio controlado cobrem atraso consolidado, uma ativa mais uma pendência, DST inexistente/duplicado e retenção de 20 dias. |
| 10. Ações | Script Python relativo ao workspace, sem shell, com barreira, contenção e captura limitada; evento com UUIDv7 por ocorrência via HTTP loopback. O teste integrado percorre agendador → listener local → gateway compartilhado → App Server/canal falsos. |
| 11. Integração | Migração, recuperação, shutdown, empacotamento e execução nativa são verificados pelos comandos abaixo. Recursos novos permanecem desabilitados por padrão; testes usam destinos e processos de laboratório. |

Os testes citados sem caminho completo pertencem a `onmyoji-daemon/tests/`. A suíte usa `tmp_path`, relógios controlados, canais/App Server falsos e sinais observáveis de prontidão. O teste de descendentes confirma por socket que o neto foi criado antes de validar timeout ou parada.

## Reprodução

Windows, na raiz do repositório, com o Python e as dependências do projeto:

```text
python -B -m pytest -q --tb=short --basetemp=tmp/pytest-full-events-latest --junitxml=tmp/events-full-latest.xml
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

A VM é transitória: imagem, código, dependências e resultados ficam em `tmp/` no host. A execução ocorre no kernel Linux, incluindo grupos de processos e descendentes; não é uma simulação das chamadas POSIX. A preparação habilita `lo` e instala `croniter` da série 7, `tzdata`, `jsonschema` e `pytest`. A validação nativa descrita cobre Windows e Linux; não certifica macOS ou outros sistemas POSIX.

O wheel é construído a partir de uma cópia de staging em `tmp/`, sem artefatos de build na fonte. A inspeção verifica o schema distribuído, os três pacotes e a igualdade dos arquivos Python/JSON com a árvore de código validada.

## Fronteiras operacionais

Antes de migrações são mantidos backups SQLite consistentes junto ao estado persistente. O agendador distingue reserva durável de início de ação: reserva interrompida pode retomar; ação iniciada sem resultado torna-se incerta. O gateway conserva submissões possivelmente enviadas e entregas em andamento sem repetição automática.

A ativação operacional é uma ação explícita posterior: configurar destinos, selecionar conta SMTP, habilitar o serviço e cadastrar definições. Nenhum envio a destinatário real nem processo operacional integra estes testes. Não restaurar banco antigo após aceitar trabalho novo; desabilitar a capacidade afetada e aplicar correção progressiva.
