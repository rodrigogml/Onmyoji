# Integração da delegação e do Gateway no projeto principal

Data: 2026-11-06. Este registro acompanha o [plano original](../plans/delegacao-gateway-subagentes.md), o [inventário de aceite](delegacao-gateway-subagentes.md) e os [contratos de uso](../Telegram.md).

**Estado: integrado e validado no projeto principal**, com as limitações G4/G6
mantidas e a aplicação à instância ainda sob responsabilidade do operador.

> [!IMPORTANT]
> O destino desta integração é o checkout do projeto, `C:/x/Onmyoji`.
> A instância operacional não foi atualizada, reconfigurada ou reiniciada.
> Sua aplicação e os testes com contas reais ficam com o operador.

## Origem e preservação do principal

| Item | Estado observado antes da integração |
| --- | --- |
| Origem | `C:/Users/rodri/.codex/worktrees/d958/Onmyoji`, HEAD `dd971dc51ddb72f6af26621401c0564d7db93593`, implementação local sem commit |
| Principal | `C:/x/Onmyoji`, branch `main`, HEAD `80f0eb2a71798458e648c57bd81d8dede398af62`, árvore e staging inicialmente limpos |
| Ancestral comum | `7fdf786f60283fec65f662c1efc51ae10e4c8a2b` |
| Delta inicial | 56 arquivos: 24 modificados, 30 novos e dois já presentes no snapshot da origem — plano e entrada do índice global |
| Estratégia | Comparação de três vias, propostas intermediárias e backups nominais em `tmp/`; nenhum merge/commit Git |

O manifesto nominal, hashes antes/depois, backups e decisões automáticas estão
em `tmp/integration-delegation-20261106/manifest.json`, no principal. Somente os
artefatos da funcionalidade foram integrados. Configurações efetivas, sessões,
credenciais, logs operacionais e os laboratórios da origem ficaram fora do
delta versionável. As evidências privadas selecionadas foram copiadas para
`tmp/integration-delegation-20261106/evidence/`, com SHA-256 conferido e sem
remover os originais. Dependências de teste permanecem somente em `tmp/`.

As evoluções posteriores do principal foram mantidas: habilitação MCP pelo
ciclo do daemon, descoberta de destinos registrados, correção das raízes
autorizadas de anexos, localização fixa bidirecional e teste controlado do
Cloudflare Tunnel. Os arquivos exclusivos dessas evoluções não foram
substituídos por versões antigas da origem.

## Resoluções de integração

| Sobreposição | Decisão incorporada |
| --- | --- |
| Gateway extraído e localização fixa | Localização estruturada percorre preparação, inbox durável, reabertura, início e steering; métodos legados não sobrepõem o coordenador novo |
| Persistência | Banco no esquema 3, com migração 2→3 e snapshot quando há entradas. TOML permanece no esquema 2. Versões futuras são recusadas antes de DDL/configuração |
| Ferramentas de localização ativa | Capacidade efêmera vinculada à geração única do turno; cancelamento, reset e término revogam autorização. Envios usam ledger e identidade do RPC |
| Localização externa | Recibo confirmado mantém sucesso; falha local posterior produz aviso. Reset durante criação de tópico ou envio não atribui dados à nova geração |
| Histórico e retenção | Pontos sem texto também são expurgados da inbox encerrada após sete dias; incertezas continuam preservadas |
| Revisões de mensagem | O contrato de revisão do plano §7.1 passa a valer também para localização: novo update produz nova entrada identificada, sem alterar uma entrada aceita. Replay do mesmo update é deduplicado |
| MCP | Habilitação pelo estado do serviço, instruções e localização do principal convivem com idempotência opcional e resultado incerto de `telegram.send` |
| Thread e instruções | Revisão `telegram-tools-v4` reúne os contratos; retomada mantém a thread e atualiza instruções sem presumir substituição das ferramentas persistidas |
| Setup | Opções de fila/steering, preparação, prazo de interrupção e pós-turno convivem com o novo teste de publicação externa |
| Testes anteriores | Adaptadas expectativas de exclusão da inbox à retenção durável, fakes à correlação epoch/thread/turn e habilitação MCP ao contrato vigente. As verificações de segurança foram mantidas |

As fontes protegidas `README.md`, `AGENTS.md` e equivalentes versionados
permanecem byte a byte iguais ao principal inicial. O plano original conserva
seu conteúdo integral. Nenhum arquivo de definição de skill foi alterado.

## Validação do conjunto integrado

As execuções foram seriais no principal, com Python 3.12.1, `-B`, configuração
`pytest.ini` da raiz e todos os temporários/cache/dependências dentro de `tmp/`.
Logs, JUnit, coverage e fingerprints ficam em
`tmp/integration-delegation-20261106/`.

| Execução atual | Resultado | Evidência privada no diretório de integração |
| --- | --- | --- |
| Suíte completa com coverage de nove módulos | **2.403 testes e 301 subtestes aprovados, 17 skips, zero warnings**, exit 0; pytest 182,72 s, comando completo 183,953 s | `full-final.log`, `full-final.xml`, `full-final-result.json`, `coverage-data-full-final`, `coverage-report-full-final.json` |
| Estabilidade das fontes/testes | 63 fingerprints iguais antes/depois da suíte e do laboratório; `source_changed=[]` | `full-final-result.json`, `gateway-result.json` |
| Gateway e App Server reais, provider loopback e Telegram falso | **24 execuções em três threads**, dez checks aprovados, exit 0 em 16,094 s; steering, pensamentos, menu/callback, outra conversa, continuação de filho como dados e saída nativa confirmada | `gateway-live/report.json`, `gateway.log`, `gateway-result.json` |

No laboratório, 20 amostras por métrica deram p95 de 69,085 ms para steering,
9,547 ms para ingresso persistido e 0,510 ms para confirmação local de callback.
São medidas locais com portas externas fictícias, sem equivalência com SLA do
Telegram ou de um provider remoto. Nenhuma conta ou configuração operacional
foi carregada pelo laboratório.

Os 17 skips são os oito casos dependentes de PyMuPDF, quatro dependentes de
privilégio de links Windows, um de Tesseract/idiomas e quatro Linux nativos.
Estes quatro Linux integram os 41 casos previamente aprovados com os mesmos
hashes do wrapper/testes. Os demais são qualificações ambientais já indicadas
no inventário, sem transformá-las em prova de um fluxo obrigatório.

A primeira rodada focada teve 340 aprovações e duas expectativas incompatíveis
com a composição dos contratos; ambas foram ajustadas explicitamente na tabela
de resoluções. A primeira suíte completa teve 2.398 aprovações, 301 subtestes,
17 skips, três falhas e um warning: bloqueios transitórios `WinError 5` em
publicação JSON e em dois fluxos Cloudflare não alterados. Os três passaram
isoladamente, sem mudança de código, e a suíte completa seguinte passou
integralmente. Esses resultados estão preservados em `focused*`, `full*` e
`recheck*`; não se substituiu o resultado inicial por uma declaração de sucesso.

Comandos efetivamente utilizados, a partir de `C:/x/Onmyoji`:

```powershell
python -B tmp/integration-delegation-20261106/run_validation.py full full-final
python -B tmp/integration-delegation-20261106/run_validation.py gateway
```

O primeiro invoca `python -B -m coverage run --branch ... -m pytest -c pytest.ini`
com JUnit legacy e caminhos exclusivos de cache/basetemp em `tmp/`; a lista
completa de argumentos e os hashes estão no JSON de resultado. Os dois
utilitários mantiveram 100% de instruções e ramos; os módulos de integração
continuam com metas realistas:

| Módulo | Instruções cobertas | Ramos cobertos |
| --- | --- | --- |
| codex_app_server | 454/495 | 116/146 |
| codex_capabilities | 167/167 | 64/64 |
| config_publication | 14/14 | 2/2 |
| shikigami_agents | 392/421 | 166/194 |
| telegram_delivery | 154/160 | 40/44 |
| telegram_execution | 959/1221 | 374/538 |
| telegram_persistence | 315/342 | 76/96 |
| telegram_process | 179/207 | 54/72 |
| telegram_work | 214/230 | 80/96 |

Os testes adicionais de combinação estão em
[localização e delegação](../../tests/test_telegram_location_delegation.py).
Cobrem migração 2→3 com backup, reabertura, start/steer e procedência, ACK
incerto sem replay, isolamento por geração, cancelamento, ledger de RPC,
recusa a filho e expurgo de geodados. Outros dois testes cobrem
[reset durante criação de tópico e envio](../../tests/test_telegram_location_generation.py).
Os testes de localização já existentes continuam cobrindo entrada, saída, MCP
e tópicos.

## Vínculo com as provas anteriores

`proof-reuse.json` relaciona os 36 fingerprints da regressão anterior com as
fontes integradas; 28 conservam identidade byte a byte. As alterações
necessárias à combinação com o principal
recebem regressão nova; a contagem histórica de 2.033 testes não é apresentada
como resultado atual deste checkout.

| Prova reaproveitada | Identidade e alcance |
| --- | --- |
| Wrapper nativo e seus testes | `telegram_process.py`: SHA-256 `7a80999d3728f238e6b8af04e9818618ca1e8f58db547f522385fa6030b71fbc`; testes: `f5cdeaf113b33e566198781bd9ff37e872b6040cac1e2cc296461eafb2c8b0eb`. Bytes idênticos aos 41 casos Linux aprovados |
| T28 entre C: e X: | `shikigami_agents.py`: `1ec6981b8f5200cc398041673802648e564321334bef46923589da4b7cf97b6f`; `instance.py`: `5be04ffaf9c26e7448b31b56864f7c69e783a38dfb7ce1ee84624fb1314cfe8c`. Mesmos bytes das nove verificações reais de volumes, crash e concorrência |
| Lease nativo | Os métodos de adquirir, abrir e liberar lease conservam AST idêntica à origem. O ensaio Linux anterior de 15 checks entre processos permanece evidência desse mecanismo; não é uma execução Linux nova da migração do banco 3 |
| Compatibilidade do engine | Executável, schema, Windows, stdio, revisão do adaptador e certificado conservados. G0/G1/G2/G3/G5/G7 continuam certificados somente para essa identidade |

Os relatórios nativos completos permanecem privados. A VM Linux e o laboratório
de volumes não foram reconstruídos nesta integração: os componentes
qualificados são os mesmos. Isso não certifica o engine Codex em Linux nem
transporta resultados antigos para os módulos modificados.

## Auditoria de escopo e limites

| Contrato | Evidência de integração |
| --- | --- |
| RF01–RF03: ingresso, concorrência e pensamentos | Inbox/checkpoint/FIFO e preferências seguem o coordenador durável; localização também atravessa start/steer |
| RF04–RF06: delegação, consolidação e contexto | Principal acompanha filhos conhecidos, saída terminal é persistida e thread é retomada por contrato certificado |
| RF07–RF09: mídia, falhas e gerações | Localização e canais existentes preservados; ledger, incerteza e cancel/reset não ganham replay automático |
| RF10/E8/T26–T29 | Materializador completo incluído, com as mesmas provas T28 e bloqueio G4 mantido |
| Invariantes 1–9 | Posse, procedência, ordem, terminalidade, deduplicação, limpeza, concorrência, segredos e recuperação preservam a matriz do inventário; novos testes cobrem as sobreposições de localização |
| E0–E10 e T01–T30 | Artefatos completos do inventário presentes; suíte atual aprovada, laboratório atual aprovado e provas nativas vinculadas por hash. Nenhuma pendência C/P nem checkbox aberto na matriz vigente |
| G4 | Distribuição habilitada de perfis continua recusada: o engine relê perfis durante sessão viva, sem snapshot de startup garantido |
| G6 | Perda do App Server interrompe filhos ativos; retomada de histórico não comprova worker sobrevivente. Estado permanece indeterminado/interrompido |

Durante a integração e a validação não houve commit, push, PR, deploy, ativação
da instância nem efeito real em Telegram, e-mail, contas ou operações de
negócio. A publicação versionável é uma etapa posterior, solicitada pelo
operador, e não ativa a instância. Limitações ambientais e
qualificações continuam explícitas no inventário e no relatório de plataformas.
Ao fechar a integração, o índice/staging e o HEAD do principal ainda eram os
iniciais, com o delta disponível como alterações locais. A auditoria final e o
manifesto registram os 62 arquivos versionáveis, ausência de alterações alheias
ao escopo, fontes protegidas intactas e links locais válidos.

## Aplicação posterior pelo operador

Aplique a revisão publicada deste conjunto à base da sua instância pelo
procedimento de atualização que você administra. Preserve as configurações
efetivas, credenciais e o estado privado da instância. A publicação do código
não executa essa aplicação nem reinicia os serviços.

Na aplicação, mantenha fila como política inicial, pós-turno desabilitado e
distribuição de perfis desabilitada. A abertura operacional fará as migrações
documentadas sob lease e preservará os backups privados. Consulte o
[contrato de migração e rollback](../Telegram.md#migração-e-rollback-local)
antes de qualquer volta de versão. Depois da aplicação pelo operador, os
próximos testes podem verificar o comportamento no ambiente real; a validação
local não equivale a essa ativação.
