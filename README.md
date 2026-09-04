# Onmyōji

Base de integração local para Shikigamis. Cada instância é um repositório próprio, derivado deste repositório: `upstream` aponta para `Onmyoji` e `origin` aponta para o repositório daquele Shikigami. O diretório é o `CODEX_HOME` de uma única identidade; o workspace de trabalho do Codex é externo e não é versionado por esta base.

O nome vem do imaginário japonês: o *onmyōji* era um especialista da corte em onmyōdō, encarregado de interpretar calendários, presságios e equilíbrios; um *shikigami* é seu espírito auxiliar, chamado para uma tarefa delimitada. Aqui, Onmyōji é a base que oferece integração, setup e guardrails; cada Shikigami é uma instância de Codex com identidade, conhecimento e configuração próprios.

| Conceito | Papel no projeto |
| --- | --- |
| Onmyōji | Base reutilizável: catálogo de integrações, setup, daemon e documentação. |
| Shikigami | Repositório e `CODEX_HOME` de uma instância específica, como Akuma. Recebe a base como `upstream` e mantém suas próprias definições. |
| Workspace | Diretório externo onde o Shikigami trabalha em projetos e produz artefatos. Não é parte do `CODEX_HOME`. |

## Camadas da instância

- `available-skills/`, `onmyoji-daemon/`, `setupOnmyoji.py` e `docs/`: base versionada do Onmyōji. Personalizações não devem ser feitas nesses arquivos.
- `skills/`: superfície ativa que o Codex varre. É ignorada pelo Git e contém links locais para as skills ativas.
- `shikigami/`: definição versionada da instância, criada somente no repositório de cada Shikigami. Contém sua identidade, instruções particulares, documentação, declarações sem segredos e fontes de skills próprias. Leia sempre `shikigami/README.md` e `shikigami/AGENTS.md`, quando presentes. O upstream Onmyōji não contém nem versiona essa pasta.
- `configs/`: sobreposição privada e estado operacional. Contém referências locais, integrações provisionadas, credenciais do SO, pairing, bancos de conversa, logs e dados de execução; permanece ignorada pelo Git.
- workspace externo: área exclusiva de trabalho do agente, como `C:\opt\Shikigami-Akuma-Work`. Não fica dentro deste repositório nem do `CODEX_HOME`.

O conteúdo de `shikigami/` nunca pode conter tokens, senhas, chaves privadas, bancos de dados, logs ou state. Segredos ficam no KeePass e os dados operacionais em `configs/`.

| Classe de conteúdo | Local | Git |
| --- | --- | --- |
| Base comum, setup e skills de integração | raiz do repositório | versionado no upstream Onmyōji |
| Skills ativas do Codex | `skills/` | local e ignorado; links para fontes versionadas |
| Identidade, instruções, política Telegram e skills próprias | `shikigami/` | versionado no repositório do Shikigami |
| Perfis de integração, caminhos dependentes da máquina e referências locais | `configs/` | privado e ignorado |
| Tokens, senhas e chaves | KeePass e provedor seguro do SO | nunca versionado |
| Conversas, logs, endpoint, PID, cache e staging | `configs/` e workspace | privado e ignorado |
| Arquivos produzidos pelo agente | `Shikigami-<Nome>-Work/` | fora deste repositório |

## Padrão de nomes e repositórios

O repositório da base é `git@github.com:rodrigogml/Onmyoji.git` e é mantido em `C:\x\Onmyoji`. Cada Shikigami possui um repositório próprio, que também é seu `CODEX_HOME`; o workspace não pertence ao repositório.

| Shikigami | CODEX_HOME e repositório | Workspace não versionado | `origin` |
| --- | --- | --- | --- |
| Akuma | `C:\opt\Shikigami-Akuma` | `C:\opt\Shikigami-Akuma-Work` | `git@github.com:rodrigogml/Shikigami-Akuma.git` |
| Lavelinha | `C:\opt\Shikigami-Lavelinha` | `C:\opt\Shikigami-Lavelinha-Work` | `git@github.com:rodrigogml/Shikigami-Lavelinha.git` |

Em qualquer repositório de Shikigami, `upstream` é sempre `git@github.com:rodrigogml/Onmyoji.git`. A branch principal é `main` em todos os repositórios.

## Skills ativas

### Codex CLI

O Codex CLI da instância lê as skills a partir de `skills/` no `CODEX_HOME`. As integrações fornecidas pela base ficam em `available-skills/` e são ativadas pelo `setupOnmyoji.py`, que cria links locais em `skills/` e registra essa ativação em `configs/onmyoji-skills.toml`.

Uma skill específica da instância deve ter a fonte em `shikigami/skills/<nome>/` e um link de ativação em `skills/<nome>/`. Esse link não é gerenciado pelo catálogo nem precisa aparecer em `configs/onmyoji-skills.toml`; ele apenas permite que o Codex encontre uma definição que permanece versionada no repositório do Shikigami. Não coloque na fonte ou no link segredos, perfis efetivos, caminhos dependentes da máquina, logs ou estado operacional.

### Codex Desktop

O Codex Desktop lê as skills em `.agents/skills/` no diretório da instância do Shikigami. Para manter os ambientes sempre sincronizados, use `B. Codex Desktop` no setup. Ao habilitar, o setup cria `<CODEX_HOME>/.agents/skills` como um link para `<CODEX_HOME>/skills`; assim, o Desktop usa exatamente o conjunto de skills já ativo no CLI, sem cópias ou configuração duplicada.

`.agents/` é local: mantenha-a ignorada pelo Git no projeto configurado. Não aponte `.agents/skills` diretamente para `shikigami/skills/`: `skills/` é o destino canônico porque reúne as integrações ativas e as skills de domínio habilitadas.

## Criar e configurar um Shikigami

### 1. Fazer checkout de um Shikigami existente

```powershell
git clone git@github.com:<organização>/Shikigami-<Nome>.git C:\opt\Shikigami-<Nome>
Set-Location C:\opt\Shikigami-<Nome>
git remote -v
```

Confirme que `origin` aponta para o repositório do Shikigami e que `upstream` aponta para `git@github.com:rodrigogml/Onmyoji.git`. Se o clone não tiver `upstream`, registre-o uma vez:

```powershell
git remote add upstream git@github.com:rodrigogml/Onmyoji.git
```

### 2. Criar um Shikigami novo a partir da base

Crie primeiro um repositório Git vazio para a nova instância. Depois, clone a base com o nome de remoto `upstream`, conecte o repositório da instância como `origin` e publique a branch principal:

```powershell
git clone --origin upstream git@github.com:rodrigogml/Onmyoji.git C:\opt\Shikigami-<Nome>
Set-Location C:\opt\Shikigami-<Nome>
git remote add origin git@github.com:<organização>/Shikigami-<Nome>.git
git push -u origin main
```

### 3. Definir a identidade e o workspace

Execute o setup uma vez para materializar a definição mínima da instância. Em seguida, preencha `shikigami/instructions.md` com identidade, tom, objetivos e limites sem segredos; mantenha `shikigami/README.md`, `shikigami/AGENTS.md` e `shikigami/instance.toml` como documentação e declaração portáveis.

```powershell
py -3 setupOnmyoji.py --action status
```

Crie ou escolha um workspace externo, por exemplo `C:\opt\Shikigami-<Nome>-Work`, e configure-o no menu do setup. Nunca use o `CODEX_HOME` para artefatos do trabalho do agente.

### 4. Instalar uma skill própria da instância

Crie a fonte versionada em `shikigami/skills/<nome>/` com `SKILL.md` e recursos opcionais. Uma skill própria também pode incluir `setupSkill.py`, que será descoberta pelo setup e exibida no grupo de skills de domínio. Depois, crie o link de ativação. No Windows, uma junction não exige privilégio de administrador:

```powershell
New-Item -ItemType Junction `
  -Path .\skills\<nome> `
  -Target .\shikigami\skills\<nome>
```

Use a fonte versionada para conhecimento e playbooks próprios. Dependências como a Omie devem referir perfis lógicos — por exemplo, `laveli` — sem conter credenciais, caminhos de vault ou detalhes da máquina.

### 5. Habilitar e configurar integrações da base

Use o menu para configurar o Codex e as integrações disponíveis. O setup cria os links das integrações habilitadas em `skills/`, guarda o estado em `configs/` e mantém segredos nos provedores seguros apropriados.

```powershell
py -3 setupOnmyoji.py
py -3 setupOnmyoji.py --action list
```

Para uma integração com credenciais, configure primeiro seu provedor de segredo, depois crie o perfil local da integração e execute o teste de acesso somente leitura oferecido pelo setup. Por exemplo, a skill Laveli pode exigir que a Omie possua um perfil lógico `laveli`; a associação desse nome às credenciais fica exclusivamente em `configs/omie.toml` e no KeePass.

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

Melhorias gerais — daemon, setup, documentação e skills de integração — são implementadas exclusivamente no repositório Onmyōji:

```powershell
Set-Location C:\x\Onmyoji
git pull --ff-only origin main
# alterar, testar, commit e publicar a base
git push origin main
```

Em um Shikigami, altere somente `shikigami/` para personalizações da instância. O `git push` simples publica no `origin` daquele Shikigami, nunca no Onmyōji.

```powershell
Set-Location C:\opt\Shikigami-Akuma
git add shikigami
git commit -m "Descreve a personalização do Akuma"
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
