# Onmyōji


<p align="center">
  <img src="etc/ID%20Visual/logo.png" alt="Logotipo do Onmyōji" width="320">
</p>

> [!CAUTION]
> Este README não deve ser atualizado por agentes sem solicitação explícita do usuário.

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
├── configs/                # Configuração local efetiva da instância
├── docs/                   # Documentação da base
├── etc/                    # Documentos e elementos adicionais do projeto
├── onmyoji-daemon/         # Daemon e servidor MCP interno
│   └── src/onmyoji_daemon/
│       └── mcp/            # Módulos das integrações MCP
├── setupOnmyoji.py         # Assistente de instalação e configuração
└── tests/                  # Testes automatizados da base
```

| Diretório ou arquivo | Papel |
| --- | --- |
| `configs/` | Armazena a configuração local efetiva da instância, incluindo Codex CLI, perfis MCP e estado dos serviços. Seu conteúdo não é versionado. |
| `docs/` | Reúne a documentação de setup, operação do daemon, interface dos scripts e diretrizes de desenvolvimento. |
| `etc/` | Armazena documentos e elementos adicionais diversos relacionados ao projeto, incluindo seus recursos de identidade visual. |
| `onmyoji-daemon/` | Contém o daemon, o servidor MCP interno e os módulos das integrações disponíveis para uma instância. |
| `setupOnmyoji.py` | Executa o setup inicial e administra a configuração do Codex CLI, do MCP, do cofre e dos serviços locais. |
| `tests/` | Verifica o comportamento dos componentes versionados da base. |

> [!WARNING]
> **Conteúdo local da instância:** configuração efetiva, credenciais e dados operacionais devem permanecer locais e nunca ser versionados.
>
> **Conteúdo exclusivo do Shikigami:** `shikigami-skills/` contém as fontes próprias da instância. Ela existe somente no repositório particular de um Shikigami e nunca deve ser criada ou versionada no repositório padrão do Onmyōji, para não misturar a base compartilhada com uma personalização específica.

### Estrutura do Shikigami

Cada Shikigami é uma instância derivada da base, com repositório e `CODEX_HOME` próprios. As fontes de suas skills ficam em `shikigami-skills/`; já informações dependentes da máquina, credenciais, logs e artefatos de trabalho permanecem fora das fontes versionadas.

```text
Shikigami-<Nome>/       # Repositório e CODEX_HOME da instância
├── AGENTS.md           # Regras específicas do Shikigami
├── README.md           # Documentação da instância
├── instructions.md     # Objetivos, tom e limites
├── shikigami-skills/   # Fontes das skills próprias
├── configs/            # Estado e segredos locais (ignorado pelo Git)
│   └── shikigami/skills.toml # Habilitação local das skills próprias
└── config.toml         # Defaults locais do Codex (ignorado pelo Git)
Shikigami-<Nome>-Work/  # Workspace externo e não versionado
└── .agents/skills/     # Cópias gerenciadas das skills habilitadas
```

| Diretório | Papel |
| --- | --- |
| `AGENTS.md`, `README.md` e `instructions.md` | Definem, respectivamente, regras, documentação e instruções próprias da instância; são versionados pelo repositório particular do Shikigami. |
| `shikigami-skills/` | Mantém as fontes de skills criadas especificamente para a instância. |
| `configs/` | Guarda referências locais, perfis efetivos, estado operacional e segredos; nunca deve ser enviado ao Git. |
| `configs/shikigami/skills.toml` | Registra localmente quais skills próprias estão habilitadas para projeção no workspace. |
| `Shikigami-<Nome>-Work/.agents/skills/` | Contém cópias reparáveis das skills habilitadas; não é fonte de verdade nem deve ser versionado. |

> [!WARNING]
> Nenhuma pasta herdada do Onmyōji deve receber conteúdo específico de uma instância de Shikigami. Manter essa distinção é essencial para que o repositório da base permaneça reutilizável e o repositório do Shikigami possa evoluir sem introduzir conflitos.

### Estrutura de pastas

Uma instância utiliza duas áreas distintas: a pasta do repositório, onde o Onmyōji é clonado e o Shikigami é definido, e uma pasta externa com o sufixo `-Work`. Essa separação impede que arquivos de trabalho alterem o `CODEX_HOME` ou se confundam com a configuração versionada da instância.

| Local | Finalidade |
| --- | --- |
| `Shikigami-<Nome>/` | É a pasta do repositório da instância e seu `CODEX_HOME`. Ela reúne a base recebida do Onmyōji, os arquivos versionáveis da instância e as superfícies locais necessárias para operar o agente. Não deve ser usada como área de produção de arquivos do trabalho cotidiano. |
| `Shikigami-<Nome>-Work/` | É o workspace externo do agente. Nele, o Shikigami pode criar, editar e organizar os arquivos dos projetos em que trabalha, quando essa capacidade estiver habilitada por suas configurações. Essa pasta não pertence ao repositório nem ao `CODEX_HOME`. |

Em resumo, a pasta do repositório define **quem é e como funciona** o Shikigami; a pasta `-Work` é o local onde ele pode **executar o trabalho**. A configuração da instância determina se o agente pode usar esse workspace e sob quais limites.

## Serviços MCP e skills

As integrações compartilhadas do Onmyōji são implementadas e publicadas pelo servidor MCP interno. A disponibilidade de cada integração depende de sua configuração local em `configs/mcp/`; o Codex CLI e o App Server recebem o mesmo servidor MCP na abertura centralizada.

As skills pertencem exclusivamente ao Shikigami. Suas fontes ficam em `shikigami-skills/<nome>/`, podem conter um `SKILL.md` e, opcionalmente, um `setupSkill.py`. O setup descobre apenas essa pasta e grava a habilitação em `configs/shikigami/skills.toml`. Antes de cada abertura gerenciada do Codex, o Onmyōji copia as skills habilitadas para `<workspace>/.agents/skills/<nome>/`; nunca usa links simbólicos. A cópia é reparada quando divergir da fonte e não pode alterar a definição versionada.

Workflows fornecidos por serviços MCP, como memória e autoria de schemas Data Tables, permanecem no servidor MCP e são lidos sob demanda por `workflows.read` quando sua configuração independente estiver habilitada. Eles coexistem com as skills do Shikigami, mas nunca são copiados para o workspace.

### MCPs disponíveis

| MCP | O que faz |
| --- | --- |
| [AWS](onmyoji-daemon/docs/mcp-aws.md) | Consulta identidade e administra objetos S3 e recursos IAM, incluindo usuários, políticas e MFA. |
| [BIS2CMD](onmyoji-daemon/docs/mcp-bis2cmd.md) | Consulta e altera dados do BIS2 pelo cliente Java BISCMD, incluindo itens, preços, documentos fiscais e operações de NFC-e. |
| [BIS10CMD](onmyoji-daemon/docs/mcp-bis10cmd.md) | Consulta empresas, contas e categorias do BIS10 e gerencia lançamentos financeiros e transferências. |
| [BTG Empresas](onmyoji-daemon/docs/mcp-btg.md) | Consulta informações bancárias e gerencia pagamentos, com autorização OAuth e acompanhamento de pendências. |
| [Cloudflare](onmyoji-daemon/docs/mcp-cloudflare.md) | Consulta zonas e administra registros DNS, incluindo importação e exportação. |
| [Data Tables](onmyoji-daemon/docs/mcp-data-tables.md) | Oferece tabelas e registros estruturados para skills do Shikigami, com schemas e migrações aplicados pelo setup. |
| [EccoVox](onmyoji-daemon/docs/mcp-eccovox.md) | Transcreve arquivos de áudio e sintetiza fala usando o runtime local EccoVox. |
| [E-mail](onmyoji-daemon/docs/mcp-email.md) | Acessa contas IMAP, POP3 e SMTP com senha ou OAuth Google/Microsoft. Permite consultar e organizar mensagens, tratar anexos, manter rascunhos e enviar e-mails sob demanda. |
| [Forward Email](onmyoji-daemon/docs/mcp-forward-email.md) | Administra domínios e aliases do provedor Forward Email, incluindo verificações DNS e SMTP e geração de senha de alias. |
| [Google](onmyoji-daemon/docs/mcp-google.md) | Integra Gmail, contatos, Drive e Calendar com autenticação OAuth. |
| [Memória](onmyoji-daemon/docs/mcp-memory.md) | Pesquisa e mantém instruções duráveis que orientam decisões e aprendizados do agente. |
| [MiniApps](onmyoji-daemon/docs/mcp-mini-apps.md) | Publica aplicações por referência a diretórios do workspace e administra o gateway, TLS, Cloudflare Tunnel e callbacks OAuth. |
| [MySQL](onmyoji-daemon/docs/mcp-mysql.md) | Executa consultas, comandos e scripts SQL pelo cliente nativo, com credenciais obtidas do cofre interno. |
| [Notion](onmyoji-daemon/docs/mcp-notion.md) | Consulta e altera páginas, blocos, databases e data sources do Notion, além de operações com arquivos e comentários. |
| [Omie](onmyoji-daemon/docs/mcp-omie.md) | Integra cadastros, operações financeiras e NF-e do ERP Omie. |
| [Spotify](onmyoji-daemon/docs/mcp-spotify.md) | Consulta o catálogo e gerencia biblioteca, playlists, seguimento e reprodução. |
| [SSH](onmyoji-daemon/docs/mcp-ssh.md) | Executa comandos remotos e transfere arquivos por SFTP, com autenticação por senha ou chave privada. |
| [Telegram](onmyoji-daemon/docs/mcp-telegram.md) | Consulta destinos registrados e envia mensagens e arquivos por meio do Gateway Telegram. |
| [Todoist](onmyoji-daemon/docs/mcp-todoist.md) | Consulta e gerencia tarefas, projetos, seções, etiquetas, comentários e lembretes, além de operações de sincronização e arquivos. |
| [Workflows](onmyoji-daemon/docs/mcp-workflows.md) | Entrega ao agente os procedimentos completos dos serviços ativos, sob demanda e conforme os gatilhos das instruções. |

## Criar e configurar um Shikigami

### Instalar uma nova instância

Baixe `bootstrapShikigami.py` e coloque-o na pasta que deverá conter a instância e o seu workspace. Execute-o com o Python disponível na máquina:

```powershell
py -3 bootstrapShikigami.py
```

No Linux, use:

```bash
python3 bootstrapShikigami.py
```

O instalador solicita o nome do Shikigami, a pasta-base, os nomes das pastas da instância e do workspace, o repositório do Onmyōji e, opcionalmente, um repositório próprio ainda vazio. Ele clona a base como `upstream`, cria o workspace externo, aplica a identidade inicial da instância e configura o `origin` quando informado.

Ao concluir essas etapas, o instalador limpa a tela e inicia automaticamente o setup da nova instância. O setup recebe o nome já escolhido e conduz a configuração inicial sem perguntar a identidade outra vez.

> [!IMPORTANT]
> O repositório próprio informado ao bootstrap precisa estar vazio. Se ele já contiver histórico ou arquivos, configure a migração manualmente para resolver os conflitos com segurança.

### Reabrir a configuração do Shikigami

Para alterar a configuração depois da instalação, abra um terminal na pasta da instância e execute o setup:

```powershell
Set-Location Shikigami-<Nome>
py -3 setupOnmyoji.py
```

No Linux, use `python3 setupOnmyoji.py`. O menu central permite revisar o Codex CLI, o daemon, o MCP, os serviços instalados e as skills próprias da instância. Um guia detalhado de configuração será incluído nesta página em etapa posterior.
