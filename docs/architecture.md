# Arquitetura do Onmyōji e dos Shikigamis

## Papéis

Uma instalação do Onmyōji é o `CODEX_HOME` dedicado a um Shikigami e é um repositório Git próprio, derivado do upstream `Onmyoji`. Ela contém o catálogo de skills de integração, seus modelos de configuração, scripts de setup, a definição versionada da identidade e o estado local gerenciado pelo Codex. A base reutilizável e `shikigami/` são versionados; autenticação, caches, logs, bancos de estado, links ativos e demais dados operacionais permanecem ignorados pelo Git.

O workspace do Shikigami é externo ao `CODEX_HOME`, não é versionado por este projeto e é exclusivo para o trabalho do agente. Ele pode conter projeto, conhecimento operacional, skills de conhecimento e playbooks, mas não abriga as skills de integração fornecidas pelo Onmyōji.

## Arquivos de atividade do agente

`CODEX_HOME` abriga definições, modelos, configurações locais e estado de integração; não é o destino de arquivos produzidos pelo trabalho do agente. Toda skill que gerar, baixar, transcrever, exportar, materializar ou usar arquivos temporários deve direcioná-los a uma subpasta dedicada dentro do workspace do Shikigami. A subpasta deve ser específica da integração e incluída explicitamente nas raízes permitidas da skill e no sandbox quando necessário. Esse padrão permite que Shikigamis sem permissão de escrita em seu próprio `CODEX_HOME` continuem trabalhando normalmente e impede que dados operacionais se confundam com configurações. A skill EccoVox usa por padrão `WORKPATH/.onmyoji/eccovox` como sua área de leitura e escrita.

```text
Shikigami-<Nome> (CODEX_HOME e repositório da instância)
├── available-skills/             # catálogo versionado de skills de integração
├── skills/                       # links locais apenas das skills habilitadas; varrido pelo Codex
│   └── .system/                  # skills de sistema instaladas pelo Codex
├── shikigami/                    # identidade, instruções e definição versionada sem segredos
├── configs/                      # perfis e estado locais; ignorados pelo Git
├── configs/daemon/               # endpoint, estado e dados dos serviços locais; ignorados pelo Git
├── config.toml                   # defaults locais do Codex; ignorado pelo Git
└── estado gerenciado pelo Codex   # autenticação, sessões, logs e cache; ignorado pelo Git

Shikigami-<Nome>-Work (workspace externo)
├── projeto e arquivos de trabalho
└── .agents/skills/
    ├── knowledge/                # conhecimento próprio do Shikigami
    └── playbooks/                # procedimentos próprios do Shikigami
```

## Configurações

Cada skill de integração mantém seu modelo versionado, com extensão `.model`, dentro da própria pasta no Onmyōji. O setup da skill materializa sua configuração efetiva em `configs/`. A definição versionada da instância fica em `shikigami/`: hoje inclui identidade, instruções e a política do gateway em `shikigami/daemon/telegram.toml`. Arquivos reais nunca contêm segredos em texto; eles guardam somente parâmetros, perfis, caminhos e referências a provedores de segredo.

Cada instância possui seu próprio `origin`, seu próprio `CODEX_HOME`, seus perfis locais e suas skills habilitadas, sem compartilhar estado com outras. O setup registra a habilitação em `configs/onmyoji-skills.toml` e cria junctions no Windows ou links simbólicos no Linux de `skills/<nome>` para `available-skills/<nome>`. Atualizações da base são recebidas com `git fetch upstream` e `git merge upstream/main`; uma instância nunca faz `git push upstream`.

O workspace pode conter conhecimento e playbooks próprios, mas não é a definição versionada da instância. Para manter ou reconstruir um Shikigami, use seu repositório e `shikigami/`; trate o workspace como área de trabalho descartável ou administrada por uma política de backup independente.

## Limites de responsabilidade

- O Onmyōji fornece integrações, modelos, setup, políticas reutilizáveis e estado local do Codex.
- O Shikigami fornece o workspace, o código de trabalho, conhecimento específico e playbooks.
- A configuração local do Onmyōji decide quais integrações ficam disponíveis naquele Shikigami.
- O daemon Onmyōji supervisiona somente serviços registrados para a instância atual; nenhum serviço recebe configuração ou estado de outra instância.
- ACLs do sistema operacional delimitam o que a identidade de execução pode ler ou gravar em cada diretório.
