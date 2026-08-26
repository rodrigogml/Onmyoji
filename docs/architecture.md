# Arquitetura do Onmyōji e dos Shikigamis

## Papéis

Uma instalação do Onmyōji é o `CODEX_HOME` dedicado a um Shikigami. Ela contém as skills de integração, seus modelos de configuração, scripts de setup e o estado local gerenciado pelo Codex. O repositório versiona somente a base reutilizável; perfis reais, autenticação, caches, logs, bancos de estado e outros dados da instância são ignorados pelo Git.

O diretório do Shikigami é o workspace exclusivo do agente. Ele contém o projeto e o conhecimento operacional próprio daquele Shikigami, incluindo skills de conhecimento e playbooks em `.agents/skills/`. Ele não abriga as skills de integração fornecidas pelo Onmyōji.

```text
Onmyōji (CODEX_HOME de uma instância)
├── skills/                       # skills de integração fornecidas pela base
├── configs/                      # perfis reais locais; ignorados pelo Git
├── config.toml                   # skills habilitadas e defaults locais; ignorado pelo Git
└── estado gerenciado pelo Codex   # autenticação, sessões, logs e cache; ignorado pelo Git

Shikigami (workspace)
├── projeto e arquivos de trabalho
└── .agents/skills/
    ├── knowledge/                # conhecimento próprio do Shikigami
    └── playbooks/                # procedimentos próprios do Shikigami
```

## Configurações

Cada skill de integração mantém seu modelo versionado, com extensão `.model`, dentro da própria pasta no Onmyōji. O setup da skill copia ou atualiza esse modelo na pasta `configs/` da instância do Onmyōji. Arquivos reais nunca contêm segredos em texto; eles guardam somente parâmetros, perfis, caminhos e referências a provedores de segredo.

Uma mesma base versionada pode ser usada para preparar várias instâncias. Cada instância possui seu próprio `CODEX_HOME`, seus perfis locais e suas skills habilitadas, sem compartilhar estado com outras.

## Limites de responsabilidade

- O Onmyōji fornece integrações, modelos, setup, políticas reutilizáveis e estado local do Codex.
- O Shikigami fornece o workspace, o código de trabalho, conhecimento específico e playbooks.
- A configuração local do Onmyōji decide quais integrações ficam disponíveis naquele Shikigami.
- ACLs do sistema operacional delimitam o que a identidade de execução pode ler ou gravar em cada diretório.
