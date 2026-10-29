# Arquitetura do Onmyōji e dos Shikigamis

## Papéis

Uma instalação do Onmyōji é o `CODEX_HOME` dedicado a um Shikigami e é um repositório Git próprio, derivado do upstream `Onmyoji`. Ela contém o servidor MCP de integrações, seus modelos de configuração, scripts de setup, as fontes versionáveis de skills próprias e o estado local gerenciado pelo Codex. A base reutilizável e `shikigami-skills/` podem ser versionados; autenticação, caches, logs, bancos de estado e demais dados operacionais permanecem ignorados pelo Git.

O workspace do Shikigami é externo ao `CODEX_HOME`, não é versionado por este projeto e é exclusivo para o trabalho do agente. A organização canônica das camadas — inclusive fontes versionadas de skills próprias em `shikigami-skills/` — está no [README](../README.md#camadas-da-instância). Esta arquitetura não a replica para evitar duas fontes de verdade.

## Arquivos de atividade do agente

`CODEX_HOME` abriga definições, modelos, configurações locais e estado de integração; não é o destino de arquivos produzidos pelo trabalho do agente. Toda integração que gerar, baixar, transcrever, exportar, materializar ou usar arquivos temporários deve direcioná-los a uma subpasta dedicada dentro do workspace do Shikigami. A subpasta deve ser específica da integração e incluída explicitamente nas raízes autorizadas do serviço e no sandbox quando necessário. Esse padrão permite que Shikigamis sem permissão de escrita em seu próprio `CODEX_HOME` continuem trabalhando normalmente e impede que dados operacionais se confundam com configurações. O serviço MCP EccoVox usa as raízes explícitas configuradas em `configs/mcp/eccovox.toml`.

```text
Shikigami-<Nome> (CODEX_HOME e repositório da instância)
├── shikigami/                    # identidade, instruções e definição versionada sem segredos
├── configs/                      # perfis e estado locais; ignorados pelo Git
├── configs/daemon/               # endpoint, estado e dados dos serviços locais; ignorados pelo Git
├── config.toml                   # defaults locais do Codex; ignorado pelo Git
└── estado gerenciado pelo Codex   # autenticação, sessões, logs e cache; ignorado pelo Git

Shikigami-<Nome>-Work (workspace externo)
├── .agents/skills/<skill>/         # cópia gerenciada de skill própria habilitada
├── .agents/skills/onmyoji-memory/  # workflow MCP, quando a memória está habilitada
├── .agents/skills/onmyoji-data-tables/ # workflow MCP, quando há schemas habilitados
└── projeto e arquivos de trabalho
```

O Codex descobre skills locais em `.agents/skills/`. Por isso, antes de cada abertura gerenciada, o launcher projeta nesse local cópias de todas as skills próprias habilitadas. A fonte continua em `shikigami-skills/`; `configs/shikigami/skills.toml` controla a habilitação e `configs/shikigami/skills-runtime.json` registra a posse e o hash da cópia. Uma divergência no workspace é reparada e jamais retorna para a fonte versionada. O launcher também projeta workflows MCP com registros independentes: memória e tabelas de dados. O banco de memória permanece fora do workspace, em `configs/mcp/memory/instructions.sqlite3`.

## Configurações

Cada módulo MCP mantém seus contratos e modelos versionados no daemon. O setup MCP materializa a configuração efetiva em `configs/mcp/`. A configuração do Gateway Telegram fica em `configs/daemon/services/telegram/config.toml`; o arquivo opcional `instructions.md`, na raiz da instância, personaliza as developer instructions. Arquivos reais nunca contêm segredos em texto; eles guardam somente parâmetros, perfis, caminhos e referências a provedores de segredo.

Cada instância possui seu próprio `origin`, seu próprio `CODEX_HOME`, seus perfis locais, integrações MCP e skills próprias habilitadas, sem compartilhar estado com outras. O setup registra a habilitação das skills do Shikigami em `configs/shikigami/skills.toml`; os perfis MCP ficam em `configs/mcp/`. Atualizações da base são recebidas com `git fetch upstream` e `git merge upstream/main`; uma instância nunca faz `git push upstream`.

O workspace não é a definição versionada da instância. Para manter ou reconstruir um Shikigami, use seu repositório e `shikigami-skills/`; trate o workspace como área de trabalho descartável ou administrada por uma política de backup independente.

## Limites de responsabilidade

- O Onmyōji fornece integrações, modelos, setup, políticas reutilizáveis e estado local do Codex.
- O Shikigami fornece o workspace e o código de trabalho; sua definição portátil, conhecimento próprio e playbooks versionados seguem a organização definida no README.
- A configuração local do Onmyōji decide quais integrações ficam disponíveis naquele Shikigami.
- O daemon Onmyōji supervisiona somente serviços registrados para a instância atual; nenhum serviço recebe configuração ou estado de outra instância.
- ACLs do sistema operacional delimitam o que a identidade de execução pode ler ou gravar em cada diretório.
