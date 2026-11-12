# Inventário de implementação e aceite: delegação e Gateway

Revisão de acompanhamento: 2026-11-05. Especificação: [plano técnico](../plans/delegacao-gateway-subagentes.md). Evidência do binário: [relatório de capacidades](../validation/delegacao-app-server.md). Contrato de uso: [Gateway Telegram](../Telegram.md).

> [!NOTE]
> A integração no checkout principal em 2026-11-06 e sua validação estão
> registradas em [Integração no projeto principal](integracao-delegacao-gateway.md).
> As contagens e caminhos `tmp/` históricos abaixo se referem à origem desta
> implementação. O registro novo identifica as provas preservadas por hash e
> as mudanças necessárias para manter a localização fixa do principal.

> [!IMPORTANT]
> A conclusão anterior de entrega integral foi superestimada e está preservada
> como retificação histórica abaixo. Agora há provas reais de volumes distintos
> e de identidade/lease num kernel Linux em VM. Elas não certificam o engine
> Codex em Linux. A regressão Windows atual e coverage dos novos hashes também
> passaram. O aceite funcional integral dos contratos do plano está concluído
> no escopo comprovado, com fallbacks expressamente previstos. A limpeza dos
> alvos descartáveis também foi concluída por uma nova operação nominal,
> não recursiva e autorizada, mantendo as evidências. E10 está concluída nos
> aspectos funcional e operacional descritos neste inventário.

## Retificação histórica: Linux, volumes e aceite integral

Esta retificação preserva o texto original do plano, sem reinterpretar uma
obrigação não executada como V/Q concluído. A auditoria read-only confirmou
`git diff HEAD -- onmyoji-daemon/docs/plans/delegacao-gateway-subagentes.md`
com exit 0.

| Texto original | Obrigação e classificação corrigida |
| --- | --- |
| Plano, RF10, linha 140: “Materialização de perfis é opcional” | Uso/habilitação podem permanecer vazios. Uma vez E8 incluída na entrega integral, seus critérios de aceite não desaparecem |
| §12.2, linha 516: “workspace pode estar em outro volume” e “Não assumir rename atômico” | A publicação deve funcionar entre volumes distintos. Não exige outro disco físico: volume virtual real com identidade distinta pode servir; alias do mesmo volume não comprova a condição |
| E7, linha 560: “Cenários da seção 11 passam”; §11.3, linha 468 | Recuperação/posse do processo do Gateway são centrais, incluindo órfão sem matar processo alheio; não são a extensão opcional G6 |
| E8, linha 561: “colisão/crash/volume distinto preservam posse” | Critério obrigatório para concluir E8 incluída. Estava P na auditoria inicial; o ensaio real aprovado está registrado abaixo |
| E10, linha 563: “E8 quando incluída” e “Aceitação funcional completa da modalidade publicada” | O plano admite Gateway separado de E8 (linha 565), mas não foi usado para reduzir o objetivo integral: T28, Linux identidade/lease e regressão atual foram executados antes do encerramento |
| §15.2, linha 586: “Casos obrigatórios”; T28, linha 617: “crash/volumes distintos” | Testes genéricos de OSError e inspeção do staging não substituíam essa execução; T28 não podia ser aceito antes do ensaio real |
| T29, linha 618: “perfil descoberto de fato ou distribuição desabilitada” | Fallback G4 é permitido nesta linha; não revoga T28 |
| §15.3, linhas 631–632 | Instância sem perfis deve funcionar; G6 condiciona sobrevivência de filhos após o processo, não suporte ao SO nem recuperação de órfão |
| Checklist §18, linhas 691 e 695–696 | Retomada vivo/parado, publicação recuperável e testes concluídos conforme a modalidade publicada continuam exigidos; obrigação pendente não vira conclusão por receber Q |

As referências acima são do [plano original](../plans/delegacao-gateway-subagentes.md).
O plano não exige nominalmente Linux, mas o código Linux implementado é
alcançável: [telegram_execution.py][execution] chama recover_process/record_process
sem gate (linhas 1068 e 1103 da revisão auditada), e o wrapper seleciona POSIX
com /proc. [codex_capabilities.py][cap], linhas 31–33, liga G4 a agent_profiles e
G6 a process_resume do monitor de filhos. Portanto **G4/G6 não desabilitam o
wrapper Linux**. Seu aceite nativo estava P naquela auditoria; não era mera
qualificação de uma modalidade bloqueada. A prova nova qualifica identidade e
lease no kernel Linux da VM, sem ampliar G0–G7 para Linux.

Os resultados históricos de **662 testes com coverage** e **2024 testes/301
subtests na suíte Windows** não foram apagados. O conjunto exato de 36 hashes
está em `tmp/coverage/final-ac92707ef1ac4ec5a237704511cfdac7/fingerprints.json`.
Naquela execução, telegram_process.py tinha SHA-256
`101592ecf1a1fae94f67072ff27d466fbfd8a2d3f7c5bedc9df21b1b516bfe39` e
shikigami_agents.py tinha
`1ec6981b8f5200cc398041673802648e564321334bef46923589da4b7cf97b6f`.
As correções de recuperação produziram novo código e nova evidência nativa. A
regressão Windows dos novos hashes está separada dos resultados antigos e está
registrada como atual nesta revisão.

## Evidência nova: volumes distintos e Linux nativo

Escopo, referências exatas do plano, hashes e limites estão em
[Validação de plataformas](../validation/plataformas-delegacao-gateway.md).

| Ensaio aceito | Prova obtida | Limite |
| --- | --- | --- |
| T28, C:→X: no Windows | Volumes NTFS com Win32/st_dev distintos e discos distintos; nove checks aprovados. Crash os._exit(86) deixou dois manifestos pending, recuperação em novo processo, quatro processos publicaram uma revisão; sete replaces staging X:→destino X: no mesmo volume | Certificado G4 fictício só para exercitar o materializador. Não certifica startup real nos três clientes nem altera G4 |
| Identidade/lease Linux | Kernel 6.12.114-0-virt x86_64, Python 3.12.16, QEMU 11.1.0 TCG; 41/41 testes de processo, zero skips/erros, e 15 checks entre processos aprovados | VM sem rede/mounts do host; não certifica Codex G0–G7 Linux, outros kernels, distribuições, bare metal ou contas |

Relatórios privados: `tmp/volume-lab-60448c85d60c421ab813cbbff3d13b14/cross-volume-report.json`
e `tmp/linux-native-474b62f760a648ca860f0cc49e4d6995/qualification-report.json`.
PIDs/caminhos brutos não foram copiados para fontes versionadas. O mecanismo de
perfis conservou SHA `1ec6981b8f5200cc398041673802648e564321334bef46923589da4b7cf97b6f`;
o wrapper Linux qualificado agora tem SHA
`7a80999d3728f238e6b8af04e9818618ca1e8f58db547f522385fa6030b71fbc`.
G4/G6 reais Windows permanecem negativos, com seus fallbacks próprios.

### Regressão e cobertura Windows atuais

Com o mesmo wrapper qualificado no Linux, SHA
`7a80999d3728f238e6b8af04e9818618ca1e8f58db547f522385fa6030b71fbc`, a execução
conjunta de suíte completa e coverage terminou com **2033 testes e 301 subtests
aprovados, 17 skips, zero warnings, exit 0, em 144,77 s**. Foram monitorados
**36 fingerprints idênticos antes/depois**, sem source_changed.

Artefatos atuais:
`tmp/coverage/final-575c4e19313a4f30828d0473f7e09b47/report.json`,
`fingerprints.json` e `data` no mesmo diretório; JUnit em
`tmp/delegation-platform-full-suite.xml`. O comando completo está no arquivo de
fingerprints; usa Python -B, coverage --branch, configuração pytest.ini da raiz
e export JUnit legacy. Nenhum pytest concorrente foi executado.

| Módulo atual | Statements cobertos | Branches cobertos | Branches parciais |
| --- | --- | --- | --- |
| [codex_app_server.py][transport] | 454/495 (91,72%) | 116/146 (79,45%) | 28 |
| [codex_capabilities.py][cap] | 167/167 (100%) | 64/64 (100%) | 0 |
| [config_publication.py][publication] | 14/14 (100%) | 2/2 (100%) | 0 |
| [shikigami_agents.py][profiles] | 392/421 (93,11%) | 166/194 (85,57%) | 26 |
| [telegram_delivery.py][delivery] | 154/160 (96,25%) | 40/44 (90,91%) | 4 |
| [telegram_execution.py][execution] | 950/1210 (78,51%) | 366/534 (68,54%) | 108 |
| [telegram_persistence.py][store] | 306/332 (92,17%) | 72/92 (78,26%) | 18 |
| [telegram_process.py][process] | 179/207 (86,47%) | 54/72 (75%) | 12 |
| [telegram_work.py][work] | 214/230 (93,04%) | 80/96 (83,33%) | 14 |

Os 100% são diretos e restritos aos dois utilitários indicados. Não há afirmação
de 100% global nem conversão de teste nativo aprovado em cobertura indireta:
os 41 testes Linux e 15 checks entre processos são evidência separada da
coverage Windows acima.

Os 17 skips são os 13 ambientais da rodada anterior (oito PyMuPDF, quatro
links/symlinks sem privilégio Windows e um Tesseract/idiomas), mais quatro casos
Linux nativos que não executam em Windows. **Todos esses quatro passaram no
Linux**, dentro dos 41/41, sem skip. Nenhum requisito obrigatório foi encerrado
apenas por receber Q: T28 foi executado entre volumes reais, e o wrapper Linux
alcançável foi corrigido e ensaiado nativamente.

## Estados e evidência disponível

| Estado | Significado |
| --- | --- |
| V | Verificado nesta execução com resultado registrado abaixo |
| F | Limite do runtime comprovado e fallback concreto adotado |
| C | Código e teste nomeado presentes; aceite integrado ainda deve ser confirmado |
| P | Falta evidência exata do contrato ou do cenário completo |
| Q | Qualificação ambiental não executada; não substitui P em critério obrigatório do objetivo atual |

Evidências históricas preservadas desta revisão:

- O runner [validate_codex_delegation.py](../../tools/validate_codex_delegation.py) concluiu a rodada final sem `probe_failure`, em 5,77 segundos, com App Server real 0.166.0 e provider falso loopback, incluindo compatibilidade de phase ausente. Relatório: `tmp/codex-probe-03d666848120473695a6cca1a9226f5b/report.json`. G0, G1, G2, G3, G5 e G7 passaram; G4 e G6 tiveram limitações observadas. Não houve uso de contas ou serviços de negócio.
- `python -B -m pytest -c pytest.ini onmyoji-daemon/tests/test_codex_capabilities.py onmyoji-daemon/tests/test_delegation_policy.py --basetemp=tmp/pytest-capabilities`: **40 testes passaram**.
- Após fechar os caminhos de permissão negada na raiz, workspace e certificado, `coverage 7.17.2 --branch` mediu `codex_capabilities.py`: **167 statements, zero ausentes, 64 branches, zero parciais, 100%**. Foram **59 testes aprovados** de capabilities + contratos; dados em `tmp/coverage/data-capabilities-permissions` e relatório em `tmp/coverage/report-capabilities-permissions.json`. Esse resultado é somente desse módulo e não afirma cobertura total do Gateway. A primeira medição por tracing foi substituída por esta prova direta de statements/branches.
- Uma consulta extra ao runtime real confirmou que `thread/read(includeTurns=true)` devolve `userMessage.clientId` e itens de resposta no histórico, além da rota `thread/items/list` exercitada pelo runner. Não foi usado `resume` como substituto de geração.
- O runner [validate_telegram_delegation.py](../../tools/validate_telegram_delegation.py) passou com Gateway real, App Server real, provider loopback e Telegram falso: steering durante turno ativo preservou pensamento; após terminal houve limpeza; filho tardio gerou uma continuação `SYSTEM_EVENT/data_only`. A história real confirmou `functionCallOutput` sem `userMessage` no turno de resultado. A rodada ampliada teve 24 execuções em três threads, incluindo vinte menus `ask_menu` reais com callbacks sintéticos e outra conversa concluindo durante menu pendente. Com vinte amostras por fronteira, p95 local: input pronto→steering aceito 69,584 ms; recepção→persistência 7,552 ms; callback→ACK na API falsa 0,707 ms. Esses valores não incluem polling/rede externa, anexo/transcrição ou startup inicial.
- Os **34 testes suplementares** de [test_delegation_contracts.py][contract-tests] passaram no corpus final, incluindo recusa conservadora quando filesystem/binário/certificado estão inacessíveis, CAS concorrente, backup/restauração, proteção de esquema, replay de saída sem modelo, HTTP tardio, cleanup recuperado em Gateway reaberto, cancelamento e recusa de ferramenta de canal a filho conhecido.
- O wrapper [telegram_process.py][process] passou nos **28 testes** de [test_telegram_process.py][process-tests], incluindo encerramento de um Popen Windows descartável real, PID/path substituído preservado, hash de arquivo alterado sem liberação indevida, instância divergente recusada, identidade inacessível bloqueante, falha de gravação e CAS por epoch. O runner real confirmou checkpoint antes do modelo e `stopped` após shutdown; a matriz de crash/perda/idle foi consolidada no corpus final. A implementação Linux por `/proc` e pidfd não foi executada neste ambiente Windows.
- Os **12 casos adicionais** de contratos passaram isoladamente e no corpus final: reconciliação start/steer com marcador presente/ausente, preferências dinâmicas sem publicação retroativa e múltiplos finais/phase ausente sob completed/failed/interrupted. O probe real também observou `phase:null` persistido após omitir phase na resposta do provider; caso incorporado ao runner E0 e certificado somente como evidência adicional.
- A primeira cobertura ampla executou 651 itens: **650 aprovados e uma falha de setup** em 53,29 s. O Windows retornou `WinError 5` no replace atômico da configuração durante `protect_legacy_gateway`, antes de executar o contrato de chave inválida do teste. Os **34 fingerprints ficaram idênticos**. Dados e comando estão em `tmp/coverage/final-42f4369c5c584dedbfeae64db73f094f/`, com `report.json` e `fingerprints.json`. A rodada não foi declarada aprovada; motivou robustez limitada de publicação de arquivos, sem retry de RPC/HTTP. Nenhuma causa como antivírus foi comprovada.


A suíte completa e os checks finais estão registrados ao fim deste inventário. Uma execução inicial sem `-c pytest.ini` criou cache fora de tmp; uma remoção recursiva foi rejeitada. O histórico foi preservado, mas a pendência foi encerrada pela operação nominal descrita abaixo. As execuções seguintes usaram a configuração da raiz.

### Cobertura Windows do snapshot anterior

A repetição após a correção de publicação de arquivos passou com **662 testes,
exit 0, em 59,74 s**, sem warnings. Foram executados 22 arquivos de testes com
`python -B -m coverage run --branch ... -m pytest -c pytest.ini`. O comando
completo, os **36 fingerprints idênticos antes/depois**, os dados e o relatório
estão em `tmp/coverage/final-ac92707ef1ac4ec5a237704511cfdac7/`:
`fingerprints.json`, `data` e `report.json`. A execução foi serial, usando
`coverage 7.17.2` e dependências isoladas em `tmp/`.

| Módulo | Statements cobertos | Branches cobertos | Branches parciais |
| --- | --- | --- | --- |
| [codex_app_server.py][transport] | 454/495 (91,72%) | 116/146 (79,45%) | 28 |
| [codex_capabilities.py][cap] | 167/167 (100%) | 64/64 (100%) | 0 |
| [config_publication.py][publication] | 14/14 (100%) | 2/2 (100%) | 0 |
| [shikigami_agents.py][profiles] | 392/421 (93,11%) | 166/194 (85,57%) | 26 |
| [telegram_delivery.py][delivery] | 154/160 (96,25%) | 40/44 (90,91%) | 4 |
| [telegram_execution.py][execution] | 948/1210 (78,35%) | 364/534 (68,16%) | 106 |
| [telegram_persistence.py][store] | 306/332 (92,17%) | 72/92 (78,26%) | 18 |
| [telegram_process.py][process] | 158/200 (79%) | 47/68 (69,12%) | 9 |
| [telegram_work.py][work] | 214/230 (93,04%) | 80/96 (83,33%) | 14 |

Os 100% são medidos diretamente nos dois módulos indicados; não representam
100% global nem cobertura indireta. A implementação operacional tem cobertura
parcial, com os comportamentos críticos nomeados nesta matriz. A alternativa
Linux de identidade nativa e um segundo volume físico não foram exercitados.
A publicação de arquivos limita retries a `PermissionError.winerror` 5/32/33,
cinco tentativas com esperas de 10/20/30/40 ms. Não repete RPC, HTTP nem efeitos
de negócio. Seus onze casos unitários estão em
[test_config_publication.py][publication-tests].

### Baseline executável do supervisor

As quatro expectativas antigas de [test_supervisor.py](../../tests/test_supervisor.py)
foram comparadas por execução, com Python 3.12 e as mesmas dependências, contra
o checkout completo de `HEAD dd971dc51ddb72f6af26621401c0564d7db93593`, exportado
com `git archive`. O arquivo e a comparação permanecem em
`tmp/baseline-supervisor-55d9ed813d1e4773a6e7d40a859d0eca/`:
`comparison.json`, JUnits e stdout das duas versões. A prova usa código
executável do baseline, não apenas comparação de blobs.

| Execução serial | Resultado | Interpretação |
| --- | --- | --- |
| Primeira tentativa original, paths mais longos | Exit 1, quatro falhas em 12,93 s | A primeira falhou na instalação antes da asserção esperada; a causa exata dessa falha anterior não foi diagnosticada |
| Repetição dos quatro originais com temporários em `tmp/bs-55d9ed81/` | Exit 1, quatro falhas em 21,21 s | Os quatro motivos esperados foram reproduzidos, inclusive as expectativas antigas de caminho/runtime e mensagem de manutenção |
| Quatro testes ajustados, sequencialmente na mesma máquina | Exit 0, quatro aprovados em 0,62 s | Alinhados ao contrato já documentado de [runtime Python](../../../docs/runtime-python.md); fake pip/venv evita subprocessos reais |

As mudanças desse ajuste ficaram somente nos testes do supervisor; nenhum
runtime de produção foi alterado para tornar essas expectativas válidas. A
primeira tentativa continua preservada e não é apresentada como confirmação
dos quatro motivos exatos.

## Mapa de componentes

| Responsabilidade | Implementação | Testes principais |
| --- | --- | --- |
| Certificação, fingerprint e fallback | [codex_capabilities.py][cap], [certificado versionado][cert] | [test_codex_capabilities.py][cap-tests] |
| Publicação de configuração e JSON | [config_publication.py][publication], lock compartilhado de leitura/gravação | [test_config_publication.py][publication-tests] |
| Política compartilhada e contrato do canal | [onmyoji.md][policy], [telegram.md][channel-policy] | [test_delegation_policy.py][policy-tests], [test_instructions.py][instruction-tests] |
| Transporte, reader, RPC e epoch | [codex_app_server.py][transport] | [test_codex_app_server.py][transport-tests] |
| Recepção, coordenação, steering, `/new` e ciclo do processo | [telegram_execution.py][execution], [telegram.py][gateway] | [test_telegram_execution.py][execution-tests], [test_telegram.py][gateway-tests] |
| Corridas integradas de ACK, terminal, slots e controles | Laboratórios fake no pipeline do Gateway, sem clientes de produção | [test_telegram_gateway_async.py][async-tests], [test_telegram_acceptance.py][acceptance-tests], [test_telegram_context_generation.py][context-tests], [test_telegram_controls_generation.py][control-tests] |
| Migrações, inbox, operações, entregas e recuperação | [telegram_persistence.py][store] | [test_telegram_execution.py][execution-tests] |
| Efeitos de canal, recibos e limpeza | [telegram_delivery.py][delivery] | [test_telegram_execution.py][execution-tests], [test_telegram_channels.py][channel-tests] |
| Acompanhamento pós-turno de filhos conhecidos | [telegram_work.py][work] | [test_telegram_work.py][work-tests] |
| Identidade nativa e órfão após crash | [telegram_process.py][process], checkpoint privado por epoch | [test_telegram_process.py][process-tests] |
| Perfis opcionais TOML e posse | [shikigami_agents.py][profiles] | [test_shikigami_agents.py][profile-tests] |
| Contexto único de abertura | [codex_execution.py][launch], [instance.py][instance] | [test_codex_execution.py][launch-tests] |
| Contratos suplementares de recuperação e autorização | Migração/backup, CAS de ingress, replay de final, cleanup tardio e cancelamento | [test_delegation_contracts.py][contract-tests] |
| Configuração e setup | [telegram.toml.model][model-config], [setupDaemon.py][daemon-setup], [setupOnmyoji.py][setup] | [test_setup_daemon.py][daemon-setup-tests], [test_setup_onmyoji.py][setup-tests] |

O workspace continua externo ao `CODEX_HOME`. As fontes opcionais são `shikigami-agents/<id>.toml`; config e manifesto privados ficam em `configs/shikigami/`, e cópias runtime são destinadas a `<workspace>/.codex/agents/` somente com certificado de publicação segura. Não há catálogo genérico de especialistas.

## Requisitos funcionais RF01–RF10

| ID | Implementação e prova executada | Limite | Estado |
| --- | --- | --- | --- |
| RF01 | Ingest/checkpoint, CAS, replay e polling interrompido no [aceite][acceptance-tests] | Updates sintéticos; sem API Telegram real | V |
| RF02 | G1/G2 reais; ACK/terminal/recusa/incerteza em [async][async-tests] e [contratos][contract-tests] | Padrão queue; steering exige configuração e elegibilidade | V |
| RF03 | Runner real preservou pensamentos; wakeup/FIFO e preferências testados | Aceitação não promete interromper efeito externo já iniciado | V |
| RF04 | G3 real, política e [monitor][work-tests] com vínculo/procedência | Sem catálogo obrigatório ou autoridade de canal para filho | V |
| RF05 | Finais por ID/ordem, parcial honesto, ledger e replay sem modelo | Sem exactly-once externo | V |
| RF06 | Retomada/revisão/ausência em [Gateway][gateway-tests] e [contexto][context-tests]; G0 real | Retomar thread não inicia geração | V |
| RF07 | Pensamentos, tópicos, voz, HTML, arquivos, figurinha e TTS fallback | Serviços reais não participaram | V |
| RF08 | EOF/ACK perdido, reconciliação, recibos incertos e monitor | G6 impede promessa de worker durável | V/F |
| RF09 | Geração capturada em preparo, RPC, TTS, menus, HTTP e filhos | HTTP já admitido mantém dono antigo | V |
| RF10 | 77 casos de perfis no snapshot Windows e ensaio real T28 entre volumes | Mecanismo qualificado no filesystem; uso vazio válido e publicação real bloqueada pelo G4 | V/F |

V qualifica o comportamento local indicado, sem certificar conta ou serviço de produção.

## Invariantes 1–9

| Invariante | Implementação e prova | Limite | Estado |
| --- | --- | --- | --- |
| 1 — dono | DeliveryOwner, epoch/thread/turno/geração, eventos antigos e tópicos | Identidades de laboratório | V |
| 2 — procedência | Inputs separados de developer instructions; spoof em transcrição; filho sem canal; G7 real | Resultado permanece data_only | V |
| 3 — ordem/aceitação | CAS, FIFO voz/foto, /fila, preferências e ACK monotônico | Ausência de marcador conserva incerteza | V |
| 4 — terminalidade | Final sem completed, interrupção/EOF e estado dos filhos | G6 não permite presumir sobrevivência | V/F |
| 5 — entrega | Ledger por parte, final/aviso, TTS fallback e continuação pelo principal | Sem transação distribuída | V |
| 6 — limpeza | HTTP tardio, cleanup após reabrir, share/delete e reset em preparo/TTS | Entrega incerta conserva pensamentos | V |
| 7 — concorrência | Slots saturados, steering sem novo slot, preparo limitado e dispatcher | Limites locais; não SLA externo | V |
| 8 — privacidade | Redação de TOTP antes de persistir, erros sanitizados e auditoria de fontes | Não afirma todos os branches de erro cobertos | V |
| 9 — incerteza | Timeout não reenvia efeito; recibo confirmado reaproveitado; resolução explícita | Restore não desfaz ação externa | V |

## Gates G0–G7

| Gate | Resultado da execução real | Consequência no produto | Estado |
| --- | --- | --- | --- |
| G0 | Handshake, turnos, thread retomada e reinício de processo verificados | Certifica apenas fingerprint exato; thread persistida não é processo nem turno | V |
| G1 | Steering, ID divergente, múltiplos inputs, anexos wire e recusa pós-terminal verificados | Config explícita e elegibilidade ainda necessárias; padrão continua queue | V |
| G2 | Marcadores persistidos na história, inclusive steering | Reconciliação é permitida; ausência do marcador não autoriza replay | V |
| G3 | Dois filhos, espera de todos, erro controlado e interrupção coordenada | Delegação acompanhada no turno é capacidade do engine certificado | V |
| G4 | Seleção real do TOML funciona, mas V2 aparece em filho da mesma thread viva | Snapshot startup reprovado; sem leases/imutabilidade comprovados, recusar distribuição habilitada | F |
| G5 | Filho termina após principal e resultado continua legível no processo vivo | Monitor pode iniciar continuação explícita; não existe despertar nativo automático comprovado | V |
| G6 | Matar processo ativo interrompe execução filha; resume não a reinicia | Sem sobrevivência automática de worker após perda do processo; recuperar como indeterminado/interrompido | F |
| G7 | `toolOutput` gera turno e permanece `functionCallOutput` | Resultado pode ser dado de continuação dentro do escopo anterior, sem autoridade de owner | V |

O [relatório E0](../validation/delegacao-app-server.md) delimita o provider falso, os workspaces de laboratório e o que não foi certificado para conta/modelo real ou para os três clientes de perfis. Esses limites não devem ser apagados ao marcar uma etapa como entregue.

## Etapas E0–E10

| Etapa | Entrega e evidência | Limite | Estado |
| --- | --- | --- | --- |
| E0 | CapabilitySet, certificado por hash, runner e G0–G7 reais | G4/G6 usam fallback; conta/modelo não certificados | V/F |
| E1 | Migração, backups, preflight de futuro e lease antes da mutação | Restore não desfaz efeitos externos | V |
| E2 | Reader/eventos/RPC isolados, epoch, ordem, limites e resposta única | Stdio do fingerprint validado | V |
| E3 | Ingestão durável, preparo limitado, FIFO, dispatcher e wakeup | Updates/fetch/Telegram falsos | V |
| E4 | Ledger, multipart, pensamentos, terminal salvo, cleanup e recursos | Confirmação perdida permanece incerta | V |
| E5 | Queue/steer, controles, turno esperado e marcadores | Owner em DM/input certificado; demais entradas enfileiram | V |
| E6 | Política, acompanhamento de filhos e recusa de canal ao subagente | Principal coordena e consolida | V |
| E7 | Reset/cancel, grace único, recuperação, identidade nativa e lease até saída | Linux identidade/lease aprovados na VM; regressão Windows dos mesmos hashes aprovada | V |
| E8 | TOML, manifesto redundante, posse/revisões, recuperação, launchers e T28 real | Mecanismo entre volumes qualificado; certificado fictício não habilita publicação real, mantida desligada por G4 | V/F |
| E9 | Monitor por vínculo/versão/deadline, inbox única e continuação data_only real | G6 limita worker; dado salvo continua recuperável | V/F |
| E10 | Docs, matriz, T01–T30, provas nativas, suíte/coverage atuais e limpeza auditada | Aceite funcional e operacional concluído; evidências privadas preservadas e limites de certificação explícitos | V |

Os contratos obrigatórios T01–T30 estão resolvidos pelas provas indicadas ou
pelas alternativas expressas do plano: G4 permite distribuição desabilitada
(linhas 117/122 e T29/618), e G6 limita retomada de filhos após parada (119/122
e E9/562). Esses fallbacks não foram usados para dispensar T28 ou o wrapper
Linux. Os contratos funcionais e a limpeza operacional do laboratório (§16,
linha 649) estão resolvidos nesse escopo; **E10 está concluída**. Não se amplia
a certificação do engine Windows para Linux ou a outros ambientes.

## Cenários T01–T30

| ID | Origem da prova executada | Resultado e limite | Estado |
| --- | --- | --- | --- |
| T01 | test_poll_restarts_interrupted_batch_without_losing_or_duplicating_ingress; CAS/replay | Polling reiniciado e lote interrompido preservam ingestão única | V |
| T02 | test_empty_and_bot_suffixed_routes_use_ingress_without_invented_input; setup/capabilities | Vazio/sufixo corretos; config ausente/sem gate mantém queue | V |
| T03 | test_gateway_accepts_events_and_completion_before_start_ack | Eventos/terminal antes do ACK conciliados sem duplicação | V |
| T04 | ACK após terminal, recusa conclusiva e ID divergente em async/G1 | Aceito não reinjeta; só recusa conclusiva libera próximo turno | V |
| T05 | test_lost_ack_reconciles_only_persisted_history_without_restarting_input; EOF/G2 | Start/steer e marcador presente/ausente; recuperação só da entrega | V |
| T06 | Voz lenta/foto no aceite; barreira /fila; callback durante preparo | Ordem preservada sem elevar transcrição a instrução | V |
| T07 | Slots saturados no aceite e regressões DM/tópicos | Steering não adquire novo slot; endereço não se mistura | V |
| T08 | test_attachment_staging_rejects_unsafe_source_and_ancestor; anexo removido/transcrição indisponível | Falha antes da RPC; reparse simulado. Symlinks sem privilégio nos skips Q | V/Q |
| T09 | test_slow_voice_then_photo_steer_in_order_without_promoting_transcription; composição | Conteúdo forjado não entra em developer instructions | V |
| T10 | Matriz por item; test_thought_sharing_toggles_without_retroactive_publication_or_early_cleanup | Itens iguais distintos legítimos; share dinâmico sem retroativo | V |
| T11 | Quatro pares share/delete, final sem completed e steering ativo | Sem cleanup antecipado; preferência do fechamento respeitada | V |
| T12 | HTTP tardio async/contratos, envio incerto e cleanup após reabrir | Barreira e replay sem repetir final/apagar turno seguinte | V |
| T13 | test_multiple_final_items_preserve_history_and_select_last_with_honest_status; G0 phase:null real | Último final selecionado; failed/interrupted parciais; histórico por ID/ordem | V |
| T14 | Menu pendente/owner errado/duplicado/expiração/steering/reset/EOF no aceite; G0 | Seleção não fabricada; runner real confirmou callback e outra conversa durante espera | V |
| T15 | Multipart incerto e TTS fallback true/false no aceite; mídia/HTML em regressão | Parte confirmada não repete; parte incerta bloqueia; serviços reais não usados | V |
| T16 | G3 real e estados incompletos/falhos/bloqueados no monitor | Spawn concluído não prova filho concluído; principal consolida | V |
| T17 | Epoch/filho desconhecido; test_channel_tool_from_known_child_is_not_authorized_by_spawn_relationship | Vínculo conhecido também não concede envio direto | V |
| T18 | G5/G6/G7, monitor de versões/restart e runner Gateway real | Uma continuação de dado; worker perdido usa fallback G6 | V/F |
| T19 | Grace/ACK/turno vizinho, 41 testes Linux, 15 checks entre processos e regressão Windows atual | Identidade/lease qualificados no kernel da VM; sem certificar engine Linux ou outros kernels | V |
| T20 | Dois testes [resource_races][resource-tests], MCP/skill e thread retida | Reciclagem adiada durante turno/steering; perfis usam G4 | V/F |
| T21 | Recover antes/depois da operação, ACK perdido, final replay, recibo incerto e processo nativo | Fronteiras locais injetadas separadamente; sem transação distribuída | V |
| T22 | Preparo/TTS/menu/HTTP/filho, contexto e epoch anterior | Geração nova isolada; consumidores antigos mantêm dono/recursos | V |
| T23 | Cancel/fila nos contratos/aceite; TTS/recovery/cancel em async | Thread/fila preservadas, aviso próprio, sem repetir geração/resultado cancelado | V |
| T24 | Backups/restore, legado running, futuro recusado e migração repetida | Idempotência/incertezas preservadas; sem replay de legado running | V |
| T25 | Overflow transporte/ingress/payload e preparação saturada | Sem checkpoint adiantado ou descarte silencioso; callback separado | V |
| T26 | Perfis ausentes/desabilitados, TOML e opcionais em [profile-tests][profile-tests] | Zero válido; sem ampliar permissão; campos sem prova rejeitados | V/F |
| T27 | IDs/case, colisão, reparse, manifesto e posse | Sem overwrite/remoção de arquivo alheio | V |
| T28 | Nove checks reais C:→X:, crash/reabertura, quatro processos e mudança de workspace | Volumes distintos comprovados; G4 fictício qualifica só o mecanismo, mantendo a distribuição real desabilitada | V/F |
| T29 | Três launchers, Git/plain e CODEX_HOME customizado; descoberta/reload G4 real | Distribuição desabilitada é alternativa expressa de T29, sem dispensar T28 | V/F |
| T30 | [channel-tests][channel-tests], idempotência/uncertainty e monitor de filho conhecido | Contratos outbound/E9 verificados separadamente; envio não confere autoridade nem cria filho | V |

A matriz informa a origem das provas. Aprovação Windows não comprova condições
não executadas; Q não encerra critérios obrigatórios P. Contas de produção não
participaram e não estão autorizadas por este registro.

## Entregas nomeadas e decisões que permanecem visíveis

| Entrega do plano | Artefato/contrato | Estado |
| --- | --- | --- |
| Documento principal/índice | Plano/índice idênticos a HEAD; inventário complementa evidência | V |
| Relatório de capacidades | E0, certificado sanitizado por hash e runner reproduzível | V/F |
| Fixtures mínimas | Transporte/store/monitor/perfis/provider; sessões/esquemas só em tmp | V |
| Migração/backup/recovery | Checkpoints, inbox/operações, ledger, config 1→2 e snapshots | V |
| Fila/steering/controles | Config, setup, /fila, /direcionar, /cancel e /resolver | V |
| Entrega/cleanup | pending→sent→done; multipart, final salvo e cleanup recuperável | V |
| Monitor opcional | Vínculo, versões, deadline, dado e chave única | V/F |
| Distribuição opcional | TOML, posse e T28 real aprovados; publicação real continua recusada por G4 | V/F |
| Permissões/procedência | Perfil não amplia sandbox/aprovação/MCP; filho não ganha canal | V |
| Processo órfão | Identidade/lease aprovados em Linux VM e regressão Windows dos novos hashes | V |
| Publicação local robusta | Retry limitado a compartilhamento Windows e lock JSON | V |
| Observabilidade/métricas | Diagnóstico sanitizado e p95 local de vinte amostras/fronteira | V/Q |
| Rollout/rollback | Queue e pós-turno false; revogação privada e downgrade recusado | V/F |
| Documentação/protegidos | Guias atualizados; nenhum protegido/proposta alterado; sem publicação externa | V |

## Histórico Windows e checklist concluído

A suíte do snapshot anterior terminou com **2024 aprovados, 301 subtests aprovados e 13 skips,
exit 0, em 146,72 s**. Foi executado python -B -m pytest -c pytest.ini -q -ra
com JUnit em tmp/delegation-full-suite.xml e dependências em tmp/test-dependencies.
Os dois warnings foram exclusivamente record_property incompatível com o export
JUnit xunit2 padrão (OCR corpus e métricas de latência); nenhum warning de
thread/async ocorreu.

O export separado com junit_family=legacy terminou com **um aprovado, um skip e
zero warnings em 1,19 s**, em tmp/delegation-metrics-results.xml. Latência passou;
OCR nativo foi pulado por Tesseract/idiomas. Essa repetição não oculta os dois
warnings do export original.

Esses resultados históricos não são usados para validar os hashes novos. O
ensaio obrigatório de volume distinto, a prova Linux VM e a regressão/coverage
Windows atuais foram executados depois e estão registrados acima.

Os 13 skips no XML completo são **oito por PyMuPDF ausente** (sete casos e uma
coleção), **quatro por links/symlinks indisponíveis sem privilégio Windows** e
**um por Tesseract/idiomas ausentes**. A identidade/lease Linux no kernel da VM
e o volume distinto foram comprovados em ensaios posteriores. G4/G6 não os
substituíram nem foram habilitados por eles. Contas/serviços externos não
participaram da validação.

Novas execuções usam a configuração da raiz, que já coloca cache e temporários
em tmp; não exigem basetemp ad hoc:

```powershell
python -B -m pytest -c pytest.ini
python -B onmyoji-daemon/tools/validate_codex_delegation.py
python -B onmyoji-daemon/tools/validate_telegram_delegation.py
```

- [x] Coverage Windows histórico: 662/662, nove módulos e 36 fingerprints estáveis; 100% direto nos utilitários daquele snapshot.
- [x] Suíte Windows histórica, skips, subtests e warnings registrados.
- [x] E0 e Gateway reais com provider loopback/Telegram falso aprovados.
- [x] Reconciliação, ACK/terminal, cancelamento, gerações, recursos, menus e backup/recovery exercitados.
- [x] Auditoria de 46 fontes: UTF-8 sem BOM, AST e padrão de segredos sem issues; tmp/delegation-source-audit.json.
- [x] git diff --check limpo; quatro README/AGENTS, plano e índice iguais a HEAD dd971dc51ddb72f6af26621401c0564d7db93593; nenhuma proposta protegida.
- [x] Sem commit, push, PR, deploy ou ação real de negócio.
- [x] Conclusão anterior superestimada retificada, sem apagar evidências históricas.
- [x] Executar e aceitar T28/E8 com volumes distintos, qualificando o mecanismo sem certificar G4 fictício.
- [x] Validar identidade/lease Linux no kernel da VM, incluindo rename/unlink e processo testemunha.
- [x] Reexecutar regressão/coverage dos novos hashes: 2033 aprovados, 301 subtests, 17 skips, zero warnings e 36 fingerprints estáveis.
- [x] Resolver T01–T30 por prova executada ou fallback explicitamente previsto no plano, sem converter obrigação pendente em Q concluído.
- [x] Concluir o aceite funcional de E10 após plataformas e regressão atual, preservando limites de certificação.
- [x] Remover o laboratório X: com guard de caminho/UUID/HEAD e quiescência; destino e registro Git ausentes após git worktree remove --force, exit 0.
- [x] Encerrar a limpeza dos alvos identificados por nova operação não recursiva autorizada; cache/assets ausentes, evidências preservadas e nenhum drift de fonte/teste.

### Limpeza concluída e histórico das rejeições

As tentativas anteriores de remoção recursiva ampla do cache e dos assets Linux
foram rejeitadas automaticamente antes da execução (“blocked by policy”). Nada
foi removido por aquelas tentativas; o motivo genérico não foi reinterpretado
como autorização. O usuário autorizou depois uma alternativa materialmente
mais estreita, baseada na origem e identidade de cada arquivo.

Primeiro foi aprovada a remoção não recursiva de um único ISO, com caminho
literal absoluto, hash do manifesto, timestamp, guard de reparse e ausência de
arquivo versionado. Depois a auditoria read-only confirmou byte a byte 107/107
arquivos 7zip e 3389/3389 QEMU contra os instaladores verificados; 24 APKs, três
downloads e o kernel contra hashes conhecidos; e 759 entradas do rootfs.cpio
contra pacotes, fontes e harnesses preservados. Os cinco arquivos de cache
pytest foram identificados pelo runtime, JSONs e timestamps. O README gerado
era idêntico ao CACHEDIR_FILES do pytest; nenhum README de fonte/autoria do
usuário, AGENTS, plano ou índice foi alterado.

O manifesto fechado continha **3530 arquivos, zero unknown**, SHA-256
`cc21f6b9cc04d01f14b4cdba130a6a6df71ff1c4a89b1a5d161e670b6e7e18fc`.
No mesmo PowerShell, sem glob/force/recursão, cada arquivo teve hash conferido
novamente antes da remoção literal. A operação terminou com exit 0 e **3530
arquivos removidos**, além do ISO inicial (**3531 arquivos no total**). Em seguida, Directory.Delete com
recursive=false removeu **173 diretórios comprovadamente vazios**, originados
apenas dos arquivos desse manifesto. Não houve troca de shell, escalonamento,
rmtree em Python ou repetição da operação recursiva rejeitada.

O cache, QEMU, 7zip, ISO, pacotes, kernel e rootfs.cpio dos alvos auditados estão
ausentes. Permaneceram intencionalmente **dez arquivos de evidência**: quatro
manifestos HASH/INDEX (index-main.tar.gz, index-community.tar.gz, alpine.sha256
e rootfs.sha256) e seis relatórios/logs/XML/hashes, todos intactos; runners
fora do laboratório também foram preservados. Os 36 hashes de fontes/testes
não tiveram drift. A verificação read-only registrou VM desligada com exit 0,
helpers Popen 7z terminados com wait/exit 0 e ausência de QEMU/7z/7za. Processos
Python existentes sem command/path nas consultas limitadas não puderam ser
atribuídos ao laboratório e não foram tocados; não se afirma ausência de Python
ativo nem identidade comprovada de todos os processos.

O laboratório X: também foi removido com sucesso após guard de
caminho/UUID/HEAD/quiescência, com destino e registro Git ausentes. O runner foi
copiado para C: e teve SHA conferido antes da remoção.

Evidências privadas preservadas em tmp:

- `tmp/cleanup-verified-files.json` e `tmp/cleanup-nonrecursive-result.json`.
- `tmp/cleanup-empty-directories-result.json` e `tmp/cleanup-final-verification.json`.
- `tmp/cleanup-process-verification.json`.
- `tmp/volume-lab-60448c85d60c421ab813cbbff3d13b14/cleanup-result.json` e runner preservado no mesmo diretório.

A remoção conforme as fronteiras de posse da §16, linha 649, está concluída para
esses alvos. Não há pendência de limpeza deles; evidências e diretórios de
esquema anteriores foram preservados deliberadamente. Nenhum teste, fonte de
produto ou arquivo protegido foi modificado durante a limpeza.

[cap]: ../../src/onmyoji_daemon/codex_capabilities.py
[publication]: ../../src/onmyoji_daemon/config_publication.py
[publication-tests]: ../../tests/test_config_publication.py
[cert]: ../../resources/codex-capabilities/9d563ee4e0441ff9d59d612013e0460ddf8e5709735c2d9f6b98b7ef8ca4eb42.json
[policy]: ../../resources/instructions/onmyoji.md
[channel-policy]: ../../resources/instructions/telegram.md
[transport]: ../../src/onmyoji_daemon/codex_app_server.py
[execution]: ../../src/onmyoji_daemon/telegram_execution.py
[gateway]: ../../src/onmyoji_daemon/telegram.py
[store]: ../../src/onmyoji_daemon/telegram_persistence.py
[delivery]: ../../src/onmyoji_daemon/telegram_delivery.py
[work]: ../../src/onmyoji_daemon/telegram_work.py
[process]: ../../src/onmyoji_daemon/telegram_process.py
[process-tests]: ../../tests/test_telegram_process.py
[profiles]: ../../src/onmyoji_daemon/shikigami_agents.py
[launch]: ../../src/onmyoji_daemon/codex_execution.py
[instance]: ../../src/onmyoji_daemon/instance.py
[model-config]: ../../configs/telegram.toml.model
[daemon-setup]: ../../setupDaemon.py
[setup]: ../../../setupOnmyoji.py
[contract-tests]: ../../tests/test_delegation_contracts.py
[cap-tests]: ../../tests/test_codex_capabilities.py
[policy-tests]: ../../tests/test_delegation_policy.py
[instruction-tests]: ../../tests/test_instructions.py
[transport-tests]: ../../tests/test_codex_app_server.py
[execution-tests]: ../../tests/test_telegram_execution.py
[async-tests]: ../../tests/test_telegram_gateway_async.py
[acceptance-tests]: ../../tests/test_telegram_acceptance.py
[context-tests]: ../../tests/test_telegram_context_generation.py
[control-tests]: ../../tests/test_telegram_controls_generation.py
[gateway-tests]: ../../tests/test_telegram.py
[channel-tests]: ../../tests/test_telegram_channels.py
[work-tests]: ../../tests/test_telegram_work.py
[profile-tests]: ../../tests/test_shikigami_agents.py
[launch-tests]: ../../tests/test_codex_execution.py
[daemon-setup-tests]: ../../tests/test_setup_daemon.py
[setup-tests]: ../../../tests/test_setup_onmyoji.py

[resource-tests]: ../../tests/test_telegram_resource_races.py
