# Onmyōji

Base de integração local para Shikigamis. Cada instância é um repositório próprio, derivado deste repositório: `upstream` aponta para `Onmyoji` e `origin` aponta para o repositório daquele Shikigami. O diretório é o `CODEX_HOME` de uma única identidade; o workspace de trabalho do Codex é externo e não é versionado por esta base.

## Camadas da instância

- `available-skills/`, `onmyoji-daemon/`, `setupOnmyoji.py` e `docs/`: base versionada do Onmyōji. Personalizações não devem ser feitas nesses arquivos.
- `shikigami/`: definição versionada da instância. Contém sua identidade, instruções particulares, documentação e declarações sem segredos. Leia sempre `shikigami/README.md` e `shikigami/AGENTS.md`, quando presentes.
- `configs/`: sobreposição privada e estado operacional. Contém referências locais, integrações provisionadas, credenciais do SO, pairing, bancos de conversa, logs e dados de execução; permanece ignorada pelo Git.
- workspace externo: área exclusiva de trabalho do agente, como `C:\opt\Shikigami-Akuma-Work`. Não fica dentro deste repositório nem do `CODEX_HOME`.

O conteúdo de `shikigami/` nunca pode conter tokens, senhas, chaves privadas, bancos de dados, logs ou state. Segredos ficam no KeePass e os dados operacionais em `configs/`.

| Classe de conteúdo | Local | Git |
| --- | --- | --- |
| Base comum, setup e skills de integração | raiz do repositório | versionado no upstream Onmyōji |
| Identidade, instruções e política Telegram | `shikigami/` | versionado no repositório do Shikigami |
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
