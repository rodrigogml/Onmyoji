# Arquitetura do Onmyōji e dos Shikigamis

## Papéis

Uma instalação do Onmyōji é o `CODEX_HOME` dedicado a um Shikigami. Ela contém o catálogo de skills de integração, seus modelos de configuração, scripts de setup e o estado local gerenciado pelo Codex. O repositório versiona somente a base reutilizável; perfis reais, autenticação, caches, logs, bancos de estado, links ativos e outros dados da instância são ignorados pelo Git.

O diretório do Shikigami é o workspace exclusivo do agente. Ele contém o projeto e o conhecimento operacional próprio daquele Shikigami, incluindo skills de conhecimento e playbooks em `.agents/skills/`. Ele não abriga as skills de integração fornecidas pelo Onmyōji.

## Arquivos de atividade do agente

`CODEX_HOME` abriga definições, modelos, configurações locais e estado de integração; não é o destino de arquivos produzidos pelo trabalho do agente. Toda skill que gerar, baixar, transcrever, exportar, materializar ou usar arquivos temporários deve direcioná-los a uma subpasta dedicada dentro do workspace do Shikigami. A subpasta deve ser específica da integração e incluída explicitamente nas raízes permitidas da skill e no sandbox quando necessário. Esse padrão permite que Shikigamis sem permissão de escrita em seu próprio `CODEX_HOME` continuem trabalhando normalmente e impede que dados operacionais se confundam com configurações. A skill EccoVox usa por padrão `WORKPATH/.onmyoji/eccovox` como sua área de leitura e escrita.

```text
Onmyōji (CODEX_HOME de uma instância)
├── available-skills/             # catálogo versionado de skills de integração
├── skills/                       # links locais apenas das skills habilitadas; varrido pelo Codex
│   └── .system/                  # skills de sistema instaladas pelo Codex
├── configs/                      # perfis reais locais; ignorados pelo Git
├── configs/daemon/               # endpoint, estado e dados dos serviços locais; ignorados pelo Git
├── config.toml                   # defaults locais do Codex; ignorado pelo Git
└── estado gerenciado pelo Codex   # autenticação, sessões, logs e cache; ignorado pelo Git

Shikigami (workspace)
├── projeto e arquivos de trabalho
└── .agents/skills/
    ├── knowledge/                # conhecimento próprio do Shikigami
    └── playbooks/                # procedimentos próprios do Shikigami
```

## Configurações

Cada skill de integração mantém seu modelo versionado, com extensão `.model`, dentro da própria pasta no Onmyōji. O setup da skill copia ou atualiza esse modelo na pasta `configs/` da instância do Onmyōji. Arquivos reais nunca contêm segredos em texto; eles guardam somente parâmetros, perfis, caminhos e referências a provedores de segredo.

Uma mesma base versionada pode ser usada para preparar várias instâncias. Cada instância possui seu próprio `CODEX_HOME`, seus perfis locais e suas skills habilitadas, sem compartilhar estado com outras. O setup registra a habilitação em `configs/onmyoji-skills.toml` e cria junctions no Windows ou links simbólicos no Linux de `skills/<nome>` para `available-skills/<nome>`.

## Limites de responsabilidade

- O Onmyōji fornece integrações, modelos, setup, políticas reutilizáveis e estado local do Codex.
- O Shikigami fornece o workspace, o código de trabalho, conhecimento específico e playbooks.
- A configuração local do Onmyōji decide quais integrações ficam disponíveis naquele Shikigami.
- O daemon Onmyōji supervisiona somente serviços registrados para a instância atual; nenhum serviço recebe configuração ou estado de outra instância.
- ACLs do sistema operacional delimitam o que a identidade de execução pode ler ou gravar em cada diretório.
