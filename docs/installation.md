# Instalação de um Shikigami

Este guia prepara uma instância de Shikigami a partir da base Onmyōji. A instância terá seu próprio repositório Git e será o `CODEX_HOME`; o workspace de trabalho do Codex ficará em outra pasta e nunca será versionado pela instância.

## Primeiros passos

Os exemplos usam `C:\opt` como pasta pai, `MeuShikigami` como nome da instância e `MeuShikigami-Work` como workspace. Substitua os caminhos e URLs pelos valores reais da sua organização.

### 1. Criar a pasta da instância e o workspace

O clone abaixo criará `C:\opt\MeuShikigami` como diretório da instância. Crie separadamente o workspace em que o Codex trabalhará:

```powershell
New-Item -ItemType Directory -Force C:\opt\MeuShikigami-Work
```

Não crie o workspace dentro de `MeuShikigami`: a pasta da instância é o `CODEX_HOME` e armazena a base, a configuração local e as definições versionadas; os arquivos produzidos pelo agente pertencem exclusivamente ao workspace.

### 2. Clonar o Onmyōji como `upstream`

Crie o diretório local da instância ao clonar a base. A opção `--origin upstream` registra o repositório Onmyōji com o nome correto desde o início:

```powershell
git clone --origin upstream git@github.com:rodrigogml/Onmyoji.git C:\opt\MeuShikigami
Set-Location C:\opt\MeuShikigami
git remote -v
```

O resultado esperado é um repositório Git local em `C:\opt\MeuShikigami`, com `upstream` apontando para a base Onmyōji. A branch principal da instância começa em `main`.

> [!NOTE]
> Não é necessário executar `git init` antes do `git clone`: o clone já cria o diretório e o repositório local. Se a pasta já existir e precisar ser reaproveitada, ela deve estar vazia para este comando.

### 3. Criar e conectar o repositório remoto do Shikigami

Crie, no seu provedor Git, um repositório vazio chamado `MeuShikigami`, sem README, `.gitignore` ou licença iniciais. Ele receberá somente as alterações próprias da instância. Depois, no diretório local, registre-o como `origin` e publique a branch:

```powershell
Set-Location C:\opt\MeuShikigami
git remote add origin git@github.com:<organização>/MeuShikigami.git
git push -u origin main
```

Confira a separação dos remotos:

```powershell
git remote -v
```

| Remoto | Finalidade |
| --- | --- |
| `upstream` | Base Onmyōji, da qual a instância recebe atualizações. |
| `origin` | Repositório de `MeuShikigami`, onde são publicadas somente as personalizações da instância. |

Nunca execute `git push upstream` dentro de uma instância. Para receber mudanças da base, use `git fetch upstream`, revise `git merge upstream/main` e publique o resultado em `origin`.

### 4. Concluir a instalação inicial

Execute o setup sem argumentos. Enquanto a instalação estiver pendente, ele abrirá o wizard inicial, pedirá o nome do Shikigami e gravará a identidade local em `configs/onmyoji-system.toml`. Ao terminar, configure `C:\opt\MeuShikigami-Work` como pasta do projeto no menu do Codex-CLI.

```powershell
Set-Location C:\opt\MeuShikigami
py -3 setupOnmyoji.py
```

Em seguida, personalize e versione os arquivos portáveis em `shikigami/`. Não versione `configs/`, `skills/`, sessões, logs ou o workspace.
