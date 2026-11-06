# Validação de plataformas: identidade, lease e volumes

Revisão: 2026-11-05. Esta evidência complementa o [plano original](../plans/delegacao-gateway-subagentes.md), o [inventário de aceite](../implementation/delegacao-gateway-subagentes.md) e a [certificação do App Server Windows](delegacao-app-server.md). O plano não foi alterado.

> [!IMPORTANT]
> Foram executados ensaios reais de volumes distintos no Windows e de identidade
> de processo/lease em um kernel Linux dentro de VM. O segundo ensaio não
> certifica o engine Codex G0–G7 em Linux, contas/modelos, outros kernels,
> distribuições ou bare metal. G4/G6 continuam com os resultados negativos
> observados no App Server Windows; nenhum certificado foi ampliado por estas
> provas de filesystem e processo.

## Contratos do plano e correção do aceite

| Referência no plano | Contrato e consequência |
| --- | --- |
| RF10, linha 140 | Uso de perfis é opcional e vazio por padrão; isso não dispensa os critérios de E8 quando o mecanismo é incluído |
| §12.2, linha 516 | Workspace pode estar em outro volume; copiar candidatos ao staging do workspace e substituir no mesmo volume. Não pressupor rename atômico entre instância e workspace |
| E7, linha 560; §11.3, linha 468 | Recuperar posse do processo e tratar órfão sem matar processo alheio é contrato do Gateway, independente de G6 |
| E8, linha 561; §15.2/T28, linhas 586 e 617 | Colisão, crash e volumes distintos devem preservar posse; T28 é um caso obrigatório da E8 incluída |
| E10, linha 563 | Aceitação funcional completa da modalidade publicada; resultados de uma modalidade não substituem os de outra |
| T29, linha 618 | Perfil descoberto de fato ou distribuição desabilitada. Este fallback não revoga T28 |
| §15.3, linhas 631–632; checklist §18, linhas 691 e 695–696 | Instância sem perfis válida, recuperação e publicação comprovadas; G6 condiciona sobrevivência de filhos, não suporte ao SO |

A conclusão anterior de aceite integral sem estes ensaios foi retificada. A
evidência agora satisfaz os contratos de processo/lease e volume descritos
abaixo; regressão Windows e coverage dos novos hashes são registradas
separadamente no inventário.

## Windows: materializador entre volumes distintos

O ensaio usou instância descartável em C: e workspace externo em X:, ambos
NTFS, com identidade Win32 e st_dev diferentes. A inspeção do host confirmou C:
no disco 1/partição 3 e X: no disco 0/partição 2. O relatório registra somente
comparações de identidade necessárias à prova; seriais brutos não são copiados
para esta documentação.

O materializador e sua dependência instance.py foram copiados por allowlist para
um checkout isolado no outro volume. Os hashes do código ativo, overlay e
código após o ensaio coincidiram. O runner de biblioteca padrão não executou
pytest nem acessou integrações ou configurações de produção.

**Nove verificações passaram:**

- Criar, reparar, atualizar e remover somente cópias gerenciadas.
- Preservar arquivo local e configuração nativa não gerenciados.
- Matar o processo publicador com os._exit(86) após a primeira substituição,
  deixando os dois manifestos pending, e recuperar em novo processo.
- Abrir por quatro processos independentes e obter uma única revisão publicada.
- Trocar de workspace conservando posse separada.

Foram observadas sete substituições: em todas, staging e destino estavam no
workspace X: e no mesmo volume. Portanto, a prova não dependeu de rename entre
C: e X:.

O certificado G4 do laboratório era **fictício e explícito**, para exercitar o
mecanismo de publicação. Isso não comprova startup/snapshot real de perfis nos
três clientes. A distribuição real continua desabilitada pelo G4 Windows.

Artefato operacional privado:
`tmp/volume-lab-60448c85d60c421ab813cbbff3d13b14/cross-volume-report.json`.

| Fonte do mecanismo | SHA-256 antes/overlay/depois, idênticos |
| --- | --- |
| shikigami_agents.py | `1ec6981b8f5200cc398041673802648e564321334bef46923589da4b7cf97b6f` |
| instance.py | `5be04ffaf9c26e7448b31b56864f7c69e783a38dfb7ce1ee84624fb1314cfe8c` |

## Linux: identidade nativa e lease entre processos

O laboratório executou Linux **6.12.114-0-virt, x86_64**, Python **3.12.16**,
em QEMU **11.1.0 TCG** portátil. Rede e montagens do host estavam desabilitadas;
nenhuma feature do Windows foi instalada ou alterada. Bootstrap, downloads
verificados e saídas ficaram em tmp/. Uma primeira tentativa de bootstrap
precisou de correção da instalação do pacote em RAM; não foi apresentada como
teste aprovado. A rodada qualificada terminou com exit 0 em **39,83 s**.

O módulo [telegram_process.py](../../src/onmyoji_daemon/telegram_process.py) é
alcançável no Gateway Linux: record/recover não são condicionados por G4/G6.
G4 controla perfis; G6 controla a modalidade de retomada de filhos. Por isso,
seu ensaio nativo era necessário para qualificar esta implementação, e não podia
ser substituído por uma descrição de fallback.

Os **41 testes de processo passaram, sem skips/falhas/erros, em 4,001 s**. Os
casos nativos usaram /proc e pidfd reais, incluindo executável vivo renomeado,
executável removido, confirmação de morte pelo pidfd e indisponibilidade de
boot_id com o processo ainda vivo. Mesmo PID/criação com path/hash divergente
mantém bloqueio; arquivo indisponível não é prova de morte.

O runner adicional de processos independentes aprovou **15 verificações**.
Elas demonstraram lease flock real recusando concorrente sem alterar o SQLite,
crash do dono deixando o órfão vivo, liberação do lease pelo SO, reabertura,
identidade persistida correspondente, término somente do órfão pelo pidfd,
preservação de um processo testemunha, confirmação de morte, repetição
idempotente e CAS por epoch. Nenhum processo de produção participou.

| Fonte qualificada na VM | SHA-256, igual à fonte fornecida e ao fim do ensaio |
| --- | --- |
| telegram_process.py | `7a80999d3728f238e6b8af04e9818618ca1e8f58db547f522385fa6030b71fbc` |
| test_telegram_process.py | `f5cdeaf113b33e566198781bd9ff37e872b6040cac1e2cc296461eafb2c8b0eb` |

Artefato operacional privado:
`tmp/linux-native-474b62f760a648ca860f0cc49e4d6995/qualification-report.json`.
O relatório bruto contém PIDs/caminhos privados e permanece em tmp/; somente
contagens, escopo e hashes sanitizados aparecem nesta documentação.

## Reexecutar e interpretar os resultados

O teste versionado do wrapper pode ser executado no SO alvo com:

```powershell
python -B -m pytest -c pytest.ini onmyoji-daemon/tests/test_telegram_process.py
```

Os casos Linux nativos pulados em Windows não são substituídos por mocks nem
contados como executados naquele SO; a VM acima fornece sua evidência separada.
Os harnesses de bootstrap e volume usados nesta rodada são artefatos privados
do laboratório. Repetições devem manter instâncias descartáveis, hashes
conferidos, processos testemunha e identificação real dos volumes.

O certificado do [engine App Server](delegacao-app-server.md) continua limitado
ao binário/SO/transporte ali registrados. Estes ensaios não habilitam novos
gates, não provam sobrevivência de worker e não substituem regressão dos novos
hashes do produto no Windows.

## Regressão atual e limpeza operacional

Depois dos ensaios, a suíte completa Windows com coverage dos nove módulos
terminou com **2033 testes e 301 subtests aprovados, 17 skips, zero warnings,
exit 0, em 144,77 s**, conservando os 36 fingerprints monitorados. O wrapper
tem o mesmo SHA qualificado na VM. Dados e hashes:
`tmp/coverage/final-575c4e19313a4f30828d0473f7e09b47/`; JUnit:
`tmp/delegation-platform-full-suite.xml`. Os quatro casos Linux pulados no
Windows passaram no Linux dentro dos 41/41; não foram contados como executados
no Windows nem convertidos em cobertura indireta.

### Encerramento dos alvos do laboratório

As remoções recursivas amplas anteriores foram rejeitadas antes da execução
(“blocked by policy”); aquelas operações não removeram nada. Depois houve nova
autorização para uma operação materialmente mais estreita, não recursiva, com
posse, origem e identidade de cada arquivo conferidas. Não foi retry da mesma
operação nem troca de shell para contornar a rejeição.

Um ISO foi removido nominalmente primeiro, após guards de caminho, hash,
timestamp, reparse e ausência de versionamento. A auditoria read-only posterior
conferiu 107/107 arquivos 7zip e 3389/3389 QEMU contra instaladores verificados,
24 APKs, três downloads e um kernel contra seus hashes, e 759 entradas do
rootfs.cpio contra os pacotes/fontes/harnesses preservados. Os cinco arquivos
gerados do cache foram identificados pelo próprio pytest; o README de máquina
era byte a byte igual ao CACHEDIR_FILES, sem autoria de usuário. Fontes README,
AGENTS, plano e índice permaneceram intactas.

O manifesto fechado, sem unknown, tinha **3530 arquivos**, SHA-256
`cc21f6b9cc04d01f14b4cdba130a6a6df71ff1c4a89b1a5d161e670b6e7e18fc`.
O mesmo PowerShell removeu os 3530 arquivos de forma literal, não recursiva,
sem glob/force e com hash reconferido. Depois foram removidos **173 diretórios
vazios** com Directory.Delete(path,false), limitados às origens do manifesto.
Com o ISO inicial foram **3531 arquivos removidos**. Tudo terminou com exit 0,
sem outro processo encerrado ou código/teste alterado.

Cache e assets auditados estão ausentes. Foram preservados dez arquivos de
evidência no laboratório — index-main.tar.gz, index-community.tar.gz,
alpine.sha256, rootfs.sha256 e seis relatórios/logs/XML/hashes — e os runners
externos a ele. Evidências permaneceram
idênticas e o drift dos 36 hashes de fonte/teste foi vazio. A checagem read-only
registrou VM desligada com exit 0, helpers Popen 7z aguardados com exit 0 e
ausência de QEMU/7z/7za. Os processos Python existentes não expunham command/path
nas consultas limitadas; não foram atribuídos ao laboratório nem tocados. Não
se afirma que não havia Python ativo ou que toda identidade foi comprovada.

O laboratório X: foi removido após guard de caminho/UUID/HEAD/quiescência;
destino e registro Git ficaram ausentes. Seu runner foi copiado e teve SHA
conferido em C: antes da remoção.

Relatórios privados: `tmp/cleanup-verified-files.json`,
`tmp/cleanup-nonrecursive-result.json`,
`tmp/cleanup-empty-directories-result.json`,
`tmp/cleanup-final-verification.json`,
`tmp/cleanup-process-verification.json` e
`tmp/volume-lab-60448c85d60c421ab813cbbff3d13b14/cleanup-result.json`.

A limpeza exigida na §16, linha 649 do plano, está concluída para esses alvos;
não há pendência operacional deles. E10 está encerrada nos aspectos funcional
e operacional, mantendo os limites já descritos: T28 não certifica G4 real, e
identidade/lease Linux não certifica engine Codex Linux nem sobrevivência G6.
