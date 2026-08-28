# Status da migração Akuma → Onmyōji

Última revisão: 2026-08-28.

Esta lista compara o código legado do Akuma com os componentes nativos do Onmyōji. “Migrado” significa que a capacidade passou a ter implementação e configuração próprias do Onmyōji; não significa compatibilidade com arquivos de configuração legados.

## Migrado

| Componente legado | Destino no Onmyōji | Situação |
| --- | --- | --- |
| KeePass Vault | `available-skills/keepass-vault` | Migrado, incluindo TOTP, anexos, cópia e setup seguro. |
| Todoist | `available-skills/todoist` | Migrado. |
| Omie | `available-skills/omie` | Migrado, incluindo teste de perfil. |
| AWS | `available-skills/aws` | Migrado. |
| BIS2CMD | `available-skills/bis2cmd` | Migrado. |
| Cloudflare | `available-skills/cloudflare` | Migrado. |
| EccoVox | `available-skills/eccovox` | Migrado, com health, TTS e STT; os arquivos de atividade usam o workspace. |
| Forward Email | `available-skills/forward-email` | Migrado. |
| Google | `available-skills/google` | Migrado. |
| MySQL | `available-skills/mysql` | Migrado. |
| Notion | `available-skills/notion` | Migrado. |
| SSH | `available-skills/ssh` | Migrado. |
| Supervisor, RPC e serviço do SO | `onmyoji-daemon` | Migrado para processo único por instância, RPC loopback autenticado e adaptadores Windows/systemd. |
| Gateway Telegram principal | serviço `telegram` do daemon | Migrado: pairing, owners em DM, comandos privados, `/new`, `/config`, TOTP, threads persistentes, typing, App Server, fila por conversa e anexos. |

## Migrado, com ajustes de validação e operação ainda necessários

| Área | Situação atual | Próximo ajuste recomendado |
| --- | --- | --- |
| Gateway Telegram em produção | O fluxo nativo está implementado, mas requer teste real de foto, documento, voz, `/new`, reinício do processo e reinício pelo serviço em cada instância. | Executar roteiro de homologação em Akuma e Lavelinha e corrigir diferenças observadas. |
| Anexos e EccoVox | Foto, documento e voz são retidos no workspace; a transcrição de voz é usada quando o perfil EccoVox autoriza a área de staging. | O setup EccoVox deve facilitar a inclusão de `<workspace>/.onmyoji/telegram/staging` em `readable_roots`, quando essa integração for desejada. |
| Pensamentos do agente | `/config` já persiste `Compartilha Pensamentos` e `Excluir Pensamentos`. | Ligar essas opções aos eventos de raciocínio/itens intermediários do App Server, com mensagens temporárias e limpeza confiável. |
| Ferramentas do canal | `telegram_gateway` fornece listagem e materialização segura de anexos. | Migrar, se ainda necessários, os recursos legados de envio controlado de mensagem e menus interativos. |
| Observabilidade | O diagnóstico mostra configuração, comandos privados e último erro sanitizado. | Adicionar health check de fila/anexos e teste controlado do App Server com ferramenta dinâmica. |

## Pendente de migração

| Componente legado | Escopo para o Onmyōji |
| --- | --- |
| `scheduler.py` e `task_scheduler_manager.py` | Criar o serviço registrado `task-scheduler`, com configuração-modelo própria, persistência local, RPC `task.*`, execução somente de componentes permitidos e sem aceitar comandos arbitrários por configuração. |
| Executor genérico (`executor.py`) | Reavaliar antes de portar. O daemon novo não deve recuperar a capacidade implícita de executar comandos arbitrários; somente contratos explícitos de serviços e jobs devem utilizá-lo. |
| Gerenciador de múltiplos bots/subbots | Não migrado. O desenho atual é deliberadamente uma instância Onmyōji → um Shikigami → uma identidade Telegram. Caso subbots sejam necessários, devem nascer como instâncias independentes, não como múltiplos `CODEX_HOME` criados pelo gateway. |
| Importação de `manager.json`, `bot.json` e arquivos legados | Não migrado por decisão: o provisionamento é nativo e reprovisionado no Onmyōji. |
| Testes de regressão do legado | Converter os cenários relevantes de `akuma-daemon/tests` para a suíte do `onmyoji-daemon`, sem reutilizar contratos de configuração obsoletos. |

## Ordem recomendada

1. Homologar o gateway Telegram e anexos nas instâncias Akuma e Lavelinha.
2. Ajustar os pontos encontrados no uso real, sobretudo EccoVox, pensamentos e ferramentas dinâmicas.
3. Projetar e migrar o `task-scheduler` como segundo serviço do daemon.
4. Decidir explicitamente se o antigo modelo de subbots será substituído por novas instâncias Onmyōji ou se será descartado.
