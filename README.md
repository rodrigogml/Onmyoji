# Onmyōji


<p align="center">
  <img src="etc/ID%20Visual/logo.png" alt="Logotipo do Onmyōji" width="320">
</p>

## Sobre o projeto

### Origem dos Nomes

O **Onmyōji** é uma figura do folclore e da tradição japonesa ligada ao *onmyōdō*, um sistema que combina cosmologia, adivinhação e práticas espirituais. Na corte imperial, o onmyōji interpretava calendários, presságios e desequilíbrios entre as forças de *yin* e *yang*, buscando orientar decisões e preservar a harmonia.

O **Shikigami** é um espírito auxiliar invocado e controlado por um onmyōji para executar uma tarefa delimitada. Nesta metáfora, o Onmyōji fornece a base, as regras e os meios de integração; cada Shikigami é uma instância especializada que atua sob essa orientação, com identidade, conhecimento e configuração próprios.

#### Sobre o logo

O logotipo do **Onmyōji** combina elementos da tradição espiritual japonesa com uma estética tecnológica, refletindo a proposta do projeto de criar uma plataforma central para integração, configuração e orquestração de agentes de inteligência artificial. Inspirado nos **Onmyōji**, antigos praticantes do Onmyōdō, o emblema apresenta uma estrela de cinco pontas, conhecida como **Seimei Kikyō**, associada a Abe no Seimei e aos cinco elementos da cosmologia tradicional japonesa: madeira, fogo, terra, metal e água. A estrela é envolvida por círculos concêntricos que remetem a selos ritualísticos, simbolizando proteção, equilíbrio e governança. Os cinco símbolos distribuídos ao redor representam as forças que compõem o sistema, reinterpretadas como capacidades e integrações modulares, enquanto os detalhes geométricos evocam circuitos e conexões digitais. As nuvens vermelhas estilizadas fazem referência à iconografia japonesa tradicional, reforçando a dimensão mística da composição. A paleta de **dourado, vermelho e preto** transmite conhecimento, poder e sofisticação, enquanto a tipografia ornamentada e o selo japonês **陰陽師 (Onmyōji)** consolidam a identidade cultural do projeto. O conjunto representa o Onmyōji como o núcleo responsável por organizar, capacitar e supervisionar seus **Shikigami**, os agentes auxiliares que executam tarefas e interagem com diferentes sistemas.

### Da Tradição ao Projeto

O Onmyōji é um projeto centralizado para criar, configurar e operar agentes de IA chamados Shikigamis. Ele fornece uma base reutilizável com scripts de setup, serviços locais, integrações com sistemas externos, skills e diretrizes operacionais. Cada Shikigami deriva dessa base para formar uma instância especializada, ativando apenas as integrações e capacidades necessárias ao seu contexto, enquanto preserva sua própria identidade, conhecimento e configurações específicas.

| Conceito | Papel no projeto |
| --- | --- |
| Onmyōji | Base reutilizável: serviços MCP de integração, setup, daemon e documentação. |
| Shikigami | Repositório e `CODEX_HOME` de uma instância específica, como um Shikigami de exemplo. Recebe a base como `upstream` e mantém suas próprias definições. |

## Como funciona

### Organização de Repositórios (Upstream)

Uma instância de Shikigami começa ao clonar esta base em um repositório local. Recomenda-se configurar o repositório padrão do Onmyōji como `upstream` e o repositório particular da instância como `origin`: assim, a instância pode receber evoluções reutilizáveis da base sem confundir seu código e suas definições próprias com o projeto central.

As alterações de implementação, identidade, conhecimento ou configuração que sejam específicas de um Shikigami devem ser versionadas e, quando desejado, enviadas somente ao repositório desse Shikigami. Elas não devem ser publicadas no `upstream` do Onmyōji. Em sentido inverso, melhorias genéricas na base — como integrações, scripts, daemon, skills compartilhadas e documentação comum — pertencem ao repositório central e podem ser incorporadas pelas instâncias a partir do `upstream`.

## Estrutura

### Estrutura do Onmyōji

O repositório do Onmyōji concentra os componentes reutilizáveis. Ele não armazena dados operacionais nem personalizações de uma instância; esses elementos pertencem ao repositório e ao ambiente local de cada Shikigami.

```text
Onmyōji/
├── docs/                   # Documentação da base
├── etc/                    # Documentos e elementos adicionais do projeto
├── onmyoji-daemon/         # Serviço de supervisão local
├── setupOnmyoji.py         # Assistente de instalação e configuração
└── tests/                  # Testes automatizados da base
```

| Diretório ou arquivo | Papel |
| --- | --- |
| `docs/` | Reúne a documentação de arquitetura, instalação, setup e decisões da base. |
| `etc/` | Armazena documentos e elementos adicionais diversos relacionados ao projeto, incluindo seus recursos de identidade visual. |
| `onmyoji-daemon/` | Contém o daemon que supervisiona serviços locais de uma instância. |
| `setupOnmyoji.py` | Executa o setup inicial e administra a ativação de integrações e skills. |
| `tests/` | Verifica o comportamento dos componentes versionados da base. |

> [!WARNING]
> **Conteúdo local ignorado nos dois repositórios:** `configs/`, `sessions/`, `state/`, `log/`, `tmp/`, `plugins/`, `cache/` e `thread-writer-locks/`, além de arquivos locais `*.config.toml`. Esses itens guardam configuração efetiva, autenticação, estado operacional, logs e caches. Como o `.gitignore` da base é herdado por cada Shikigami, eles não devem ser versionados nem no Onmyōji nem no repositório particular da instância.
>
> **Conteúdo exclusivo do Shikigami, permitido pelo Git:** `shikigami/`. Essa pasta guarda a identidade, as instruções, a documentação e as skills próprias da instância, podendo ser versionada somente no repositório daquele Shikigami. Ela jamais deve ser criada ou versionada no repositório padrão do Onmyōji, para não misturar a base compartilhada com uma personalização específica.

### Estrutura do Shikigami

Cada Shikigami é uma instância derivada da base, com repositório e `CODEX_HOME` próprios. Sua definição versionada fica em `shikigami/`; já informações dependentes da máquina, credenciais, logs e artefatos de trabalho permanecem fora dessa definição.

```text
Shikigami-<Nome>/       # Repositório e CODEX_HOME da instância
├── shikigami/          # Identidade e definições da instância
│   ├── AGENTS.md       # Regras específicas do Shikigami
│   ├── README.md       # Documentação da instância
│   ├── instructions.md # Objetivos, tom e limites
│   └── skills/         # Fontes das skills próprias
├── configs/            # Estado e segredos locais (ignorado pelo Git)
└── onmyoji-skills.config.toml # Perfil local de skills do Codex (ignorado pelo Git)
Shikigami-<Nome>-Work/  # Workspace externo e não versionado
```

| Diretório | Papel |
| --- | --- |
| `shikigami/` | Contém a identidade, as instruções, a documentação e as skills próprias da instância; seu conteúdo pode ser versionado no repositório particular do Shikigami. |
| `shikigami/skills/` | Mantém as fontes de skills criadas especificamente para a instância. |
| `configs/` | Guarda referências locais, perfis efetivos, estado operacional e segredos; nunca deve ser enviado ao Git. |
| `onmyoji-skills.config.toml` | Perfil local do Codex gerado pelo setup. Registra, para cada skill disponível, seu caminho de origem e se está habilitada. |

> [!WARNING]
> Nenhuma pasta herdada do Onmyōji deve receber conteúdo específico de uma instância de Shikigami. Manter essa distinção é essencial para que o repositório da base permaneça reutilizável e o repositório do Shikigami possa evoluir sem introduzir conflitos.

### Estrutura de pastas

Uma instância utiliza duas áreas distintas: a pasta do repositório, onde o Onmyōji é clonado e o Shikigami é definido, e uma pasta externa com o sufixo `-Work`. Essa separação impede que arquivos de trabalho alterem o `CODEX_HOME` ou se confundam com a configuração versionada da instância.

| Local | Finalidade |
| --- | --- |
| `Shikigami-<Nome>/` | É a pasta do repositório da instância e seu `CODEX_HOME`. Ela reúne a base recebida do Onmyōji, a definição versionável em `shikigami/` e as superfícies locais necessárias para operar o agente. Não deve ser usada como área de produção de arquivos do trabalho cotidiano. |
| `Shikigami-<Nome>-Work/` | É o workspace externo do agente. Nele, o Shikigami pode criar, editar e organizar os arquivos dos projetos em que trabalha, quando essa capacidade estiver habilitada por suas configurações. Essa pasta não pertence ao repositório nem ao `CODEX_HOME`. |

Em resumo, a pasta do repositório define **quem é e como funciona** o Shikigami; a pasta `-Work` é o local onde ele pode **executar o trabalho**. A configuração da instância determina se o agente pode usar esse workspace e sob quais limites.

## Serviços MCP e skills

As integrações compartilhadas do Onmyōji são implementadas e publicadas pelo servidor MCP interno. A disponibilidade de cada integração depende de sua configuração local em `configs/mcp/`; o Codex CLI e o App Server recebem o mesmo servidor MCP na abertura centralizada.

As skills pertencem exclusivamente ao Shikigami. Suas fontes ficam em `shikigami/skills/<nome>/`, podem conter um `SKILL.md` e, opcionalmente, um `setupSkill.py`. O setup descobre apenas essa pasta, permite habilitar cada skill no profile nativo `onmyoji-skills.config.toml` e não copia nem cria links em `skills/`.

O workflow de memória é uma exceção intencional: quando a memória MCP estiver habilitada, o launcher copia sua definição gerenciada para `.agents/skills/onmyoji-memory/` no workspace. Essa cópia não é fonte de verdade e é restaurada automaticamente quando necessário.

## Criar e configurar um Shikigami

Antes de criar uma instância, leia o [guia de instalação](docs/installation.md). Ele reúne os primeiros passos para criar o clone com `upstream`, separar o workspace e conectar o repositório remoto da identidade.

### 1. Fazer checkout de um Shikigami existente

```bash
git clone git@github.com:<organização>/Shikigami-<Nome>.git Shikigami-<Nome>
cd Shikigami-<Nome>
git remote -v
```

Confirme que `origin` aponta para o repositório do Shikigami e que `upstream` aponta para `git@github.com:<organização>/Onmyoji.git`. Se o clone não tiver `upstream`, registre-o uma vez:

```powershell
git remote add upstream git@github.com:<organização>/Onmyoji.git
```

### 2. Criar um Shikigami novo a partir da base

Crie primeiro um repositório Git vazio para a nova instância. Depois, clone a base com o nome de remoto `upstream`, conecte o repositório da instância como `origin` e publique a branch principal:

```bash
git clone --origin upstream git@github.com:<organização>/Onmyoji.git Shikigami-<Nome>
cd Shikigami-<Nome>
git remote add origin git@github.com:<organização>/Shikigami-<Nome>.git
git push -u origin main
```

### 3. Instalação inicial e workspace

Execute o setup uma vez para concluir o wizard inicial e definir a identidade local do Shikigami em `configs/onmyoji-system.toml`. Em seguida, preencha `shikigami/instructions.md` com tom, objetivos e limites sem segredos; mantenha `shikigami/README.md` e `shikigami/AGENTS.md` para a documentação e as regras versionadas da instância.

```bash
python3 setupOnmyoji.py
```

Crie ou escolha um workspace externo, como `Shikigami-<Nome>-Work/`, e configure-o no menu do setup. Nunca use o `CODEX_HOME` para artefatos do trabalho do agente.

### 4. Instalar uma skill própria da instância

Crie a fonte versionada em `shikigami/skills/<nome>/` com `SKILL.md` e recursos opcionais. Uma skill própria também pode incluir `setupSkill.py`, que será descoberta pelo setup e exibida em **Skills do Shikigami**. Habilite-a pelo menu central; o setup a registra no profile nativo do Codex. Use a fonte versionada para conhecimento e playbooks próprios, sem segredos, caminhos dependentes da máquina, logs ou estado operacional.

### 5. Configurar integrações MCP

Use **MCP** no menu para configurar perfis de vault e integrações disponíveis. O setup guarda o estado em `configs/mcp/` e mantém segredos no cofre configurado; o servidor MCP só publica as ferramentas cuja integração tenha perfil habilitado.

```bash
python3 setupOnmyoji.py
python3 setupOnmyoji.py --action list
```

Para uma integração com credenciais, configure primeiro seu provedor de segredo, depois crie o perfil local da integração no MCP e execute o teste de acesso somente leitura oferecido pelo setup. Por exemplo, uma política própria do Shikigami pode exigir que a Omie possua um perfil lógico `laveli`; a associação desse nome às credenciais fica exclusivamente em `configs/mcp/omie.toml` e no KeePass.

### 6. Versionar a definição e atualizar a base

Versione somente a definição portátil do Shikigami. Não adicione `skills/`, `configs/`, `config.toml`, sessões, logs ou dados do workspace.

```powershell
git add shikigami
git commit -m "Configura Shikigami <Nome>"
git push origin main
```

Para atualizar a base, incorpore `upstream/main` e publique o resultado no repositório da instância:

```powershell
git fetch upstream
git merge upstream/main
git push origin main
```

## Fluxo Git obrigatório

Melhorias gerais — daemon, servidor MCP, setup e documentação — são implementadas exclusivamente no repositório Onmyōji:

```bash
cd Onmyoji
git pull --ff-only origin main
# alterar, testar, commit e publicar a base
git push origin main
```

Em um Shikigami, altere somente `shikigami/` para personalizações da instância. O `git push` simples publica no `origin` daquele Shikigami, nunca no Onmyōji.

```bash
cd Shikigami-Exemplo
git add shikigami
git commit -m "Descreve a personalização do Shikigami de exemplo"
git push
```

Para receber uma atualização da base, não use `git pull upstream`. Busque e incorpore o upstream explicitamente, revise o merge e então publique somente no repositório do Shikigami:

```powershell
git fetch upstream
git merge upstream/main
git push origin main
```

Nunca execute `git push upstream` dentro de um Shikigami. Isso tentaria enviar personalizações para o repositório Onmyōji e viola a separação entre base e instância.

Execute `py -3 setupOnmyoji.py` para inicializar e configurar as skills disponíveis. Consulte [a arquitetura](docs/architecture.md) e [o guia de setup](docs/setup.md).

O projeto `onmyoji-daemon/` supervisiona os serviços locais de uma instância, começando pelo gateway Telegram. Seus dados operacionais ficam em `configs/daemon/` e nunca são versionados.
