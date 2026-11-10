# Validação de eventos e agendamentos

Auditoria da implementação em 2026-11-10. O contrato e os procedimentos de uso estão em [eventos e agendamentos](eventos-agendamentos.md). Esta matriz relaciona os critérios de entrega ao código e às verificações executáveis; os checklists do rascunho antigo são históricos.

Evidências da versão consolidada:

- Linux nativo, baseline `b84d522`: 75 testes aprovados, zero falhas, erros ou ignorados; 118,95 segundos. JUnit preservado localmente em `tmp/events-posix-vm/linux-release.xml`.
- Validador do evento: 100% das instruções e dos ramos cobertos, com 28 testes de contrato aprovados; a rodada conjunta com a CLI aprovou 35 testes.
- Retenção: sete testes focados aprovados e verificação dos dois watchers com relógio monotônico em 0,5 segundo, confirmando limpeza já na primeira iteração.
- Publicação de skills: 24 testes focados aprovados após corrigir um bloqueio transitório Windows detectado pela suíte completa. O materializador usa a publicação atômica com tolerância limitada já existente no projeto; teste de injeção comprova recuperação de `WinError 5` durante reparo da pasta gerenciada.
- Wheel construído e importado diretamente do arquivo: schema carregado por `importlib.resources` e evento validado; 228 arquivos Python/JSON comparados à fonte. SHA-256: `3cc49bd974d874818cbf00bce315251277f275d5b58d017e6e8a256438b93351`.

## Critérios verificados

| Etapa | Entrega e evidência |
| --- | --- |
| 1. Contrato | Schema v1 fechado incluído no wheel; validador comum para ingresso e ação do agendador. `test_notification_contract.py` cobre tipos, campos ausentes/adicionais, versão, UUIDv7 e erros sem eco do prompt. |
| 2. Núcleo | `execution_gateway/execution.py` contém o ciclo compartilhado; o módulo antigo mantém compatibilidade de importação. Regressões `test_telegram_execution.py`, `test_telegram_gateway_async.py`, controles, gerações, delegação, recursos e entregas permanecem na suíte integrada. |
| 3. Multiorigem | Banco, lease, inbox e App Server únicos; sequência global durável; Telegram prioritário e destinos ocupados pulados. `test_notification_gateway.py` e `test_notification_recovery.py` verificam seleção, exclusão por destino, sequência após limpeza e bloqueio durante entrega recuperada. |
| 4. RPC e lifecycle | Via longa nos dois servidores, limite de acompanhamentos, controles serializados separados, desligamento com graça do turno. `test_rpc_concurrent.py`, `test_supervisor_telegram_timeout.py` e as regressões de shutdown verificam saturação, desconexão e controles. Bind público é recusado. |
| 5. Telegram | Evento destinado a Telegram entra antes da seleção de contexto e usa as mesmas ferramentas, instruções, preferências e associação do chat/tópico. Testes com `reuse` e `new` cobrem continuação cruzada entre mensagem e evento, repetição do mesmo UUID, falha do turno, falha da entrega e preferência de áudio. |
| 6. Entregas e recuperação | Conta SMTP capturada por recebimento, chave privada por recepção, estados de confirmação/falha/incerteza e incidente sem recursão. Testes injetam desconexão durante envio, reinício em `sending`, falha entre journal e JSONL, append truncado, falha transacional e recuperação de incidente pendente. Retenção recusa diretório redirecionado e preserva arquivos alheios. |
| 7. Configuração | Notificações e receptor Telegram são independentes; diagnóstico valida contrato e disponibilidade das configurações de SMTP/incidente sem envio. Testes cobrem conta removida, receptor desligado com TOTP antigo e escolhas do setup em fluxo. |
| 8. Administração | SQLite independente, revisão esperada, CLI/RPC e setup de consulta. `test_task_scheduler.py` verifica CRUD, conflito de revisão, snapshots, resolução manual, backup e recusa de versão mais nova; testa as oito ordenações nas duas direções, nulos ao final, desempate e páginas. `tests/test_setup_scheduler.py` verifica navegação, `X`, ausência de escrita de definições e apresentação no timezone escolhido. |
| 9. Calendário e dispatcher | `once`, intervalo ancorado e cron numérico de cinco campos; timezone configurável nas três regras. Testes com relógio controlado cobrem atraso consolidado, sobreposição, quatro slots globais, uma ação por definição, relógio retrocedendo, DST, salto de dia civil completo, calendário impossível e retenção sem eliminar incertezas. |
| 10. Ações | Processo sem shell implícito, barreira antes do comando, contenção, temporários próprios e dois fluxos limitados a 10 MiB. Testes nativos verificam sucesso, timeout, parada, encerramento de neto, saída truncada e recusa da contenção antes de qualquer comando. A ação local funciona sem gateway. O teste TCP integrado percorre agendador → Supervisor → gateway compartilhado → App Server/canal falsos. |
| 11. Integração | Migração, recuperação, shutdown, empacotamento e execução nativa são verificados pelos comandos abaixo. Recursos novos permanecem desabilitados por padrão; testes usam destinos e processos de laboratório. |

Os testes citados sem caminho completo pertencem a `onmyoji-daemon/tests/`. A suíte usa `tmp_path`, relógios controlados, canais/App Server falsos e sinais observáveis de prontidão. O teste de descendentes confirma por socket que o neto foi criado antes de validar timeout ou parada.

## Reprodução

Windows, na raiz do repositório, com o Python e as dependências do projeto:

```text
python -B -m pytest -q --tb=short --basetemp=tmp/pytest-events-acceptance --junitxml=tmp/events-acceptance.xml
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

A VM é transitória: imagem, código, dependências e resultados ficam em `tmp/` no host. A execução ocorre no kernel Linux, incluindo grupos de processos e descendentes; não é uma simulação das chamadas POSIX. A preparação habilita `lo` e instala `croniter` da série 6.2, `tzdata`, `jsonschema` e `pytest`. A validação nativa descrita cobre Windows e Linux; não certifica macOS ou outros sistemas POSIX.

O wheel é construído a partir de uma cópia de staging em `tmp/`, sem artefatos de build na fonte. A inspeção verifica o schema distribuído, os três pacotes e a igualdade dos arquivos Python/JSON com a árvore de código validada.

## Fronteiras operacionais

Antes de migrações são mantidos backups SQLite consistentes junto ao estado persistente. O agendador distingue reserva durável de início de ação: reserva interrompida pode retomar; ação iniciada sem resultado torna-se incerta. O gateway conserva submissões possivelmente enviadas e entregas em andamento sem repetição automática.

A ativação operacional é uma ação explícita posterior: configurar destinos, selecionar conta SMTP, habilitar o serviço e cadastrar definições. Nenhum envio a destinatário real nem processo operacional integra estes testes. Não restaurar banco antigo após aceitar trabalho novo; desabilitar a capacidade afetada e aplicar correção progressiva.
