# Repositórios de Shikigami

Cada Shikigami é um projeto Git independente que deriva do Onmyōji. O repositório local é simultaneamente seu `CODEX_HOME` e a definição versionada da instância; o workspace do agente é um diretório externo e não versionado.

```text
Onmyoji.git ── upstream ──> Shikigami-Akuma.git
                        └─> Shikigami-Lavelinha.git
```

Cada instância usa `main` como branch principal. `origin` é seu próprio repositório e `upstream` é `git@github.com:rodrigogml/Onmyoji.git`. Melhorias genéricas são feitas no upstream; particularidades são registradas em `shikigami/`.

## Conteúdo versionado da instância

`shikigami/` contém exclusivamente definição portável e não secreta:

- `README.md` e `AGENTS.md`, com regras e documentação próprias;
- `instructions.md`, carregado como developer instructions particulares;
- `instance.toml`, com identidade e referência lógica ao workspace;
- `profiles/`, quando uma definição de perfil não revelar segredo nem estado operacional.

O setup materializa configurações efetivas em `configs/`. Essa camada pode acrescentar valores dependentes da máquina e dados de provisionamento, mas não substitui as instruções e identidade versionadas.

## Conteúdo privado

`configs/` e os diretórios de execução permanecem ignorados. Incluem segredos no gerenciador de credenciais do SO, caminhos locais, pairing de Telegram, contatos, SQLite, logs, PID, endpoint RPC, staging, cache e links ativos de skills. Tokens e senhas permanecem exclusivamente no KeePass ou no mecanismo de credenciais do sistema operacional.

## Atualização da base

Em cada repositório de Shikigami, atualize a base com `git fetch upstream` e `git merge upstream/main`; em seguida publique o resultado no seu `origin`. Conflitos devem ser incomuns, pois arquivos específicos ficam concentrados em `shikigami/`.
