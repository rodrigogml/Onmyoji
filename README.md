# Onmyōji

Base de integração local para Shikigamis. Cada instância é um repositório próprio, derivado deste repositório: `upstream` aponta para `Onmyoji` e `origin` aponta para o repositório daquele Shikigami. O diretório é o `CODEX_HOME` de uma única identidade; o workspace de trabalho do Codex é externo e não é versionado por esta base.

## Camadas da instância

- `available-skills/`, `onmyoji-daemon/`, `setupOnmyoji.py` e `docs/`: base versionada do Onmyōji. Personalizações não devem ser feitas nesses arquivos.
- `shikigami/`: definição versionada da instância. Contém sua identidade, instruções particulares, documentação e declarações sem segredos. Leia sempre `shikigami/README.md` e `shikigami/AGENTS.md`, quando presentes.
- `configs/`: sobreposição privada e estado operacional. Contém referências locais, integrações provisionadas, credenciais do SO, pairing, bancos de conversa, logs e dados de execução; permanece ignorada pelo Git.
- workspace externo: área exclusiva de trabalho do agente, como `C:\opt\Shikigami-Akuma-Work`. Não fica dentro deste repositório nem do `CODEX_HOME`.

O conteúdo de `shikigami/` nunca pode conter tokens, senhas, chaves privadas, bancos de dados, logs ou state. Segredos ficam no KeePass e os dados operacionais em `configs/`.

Execute `py -3 setupOnmyoji.py` para inicializar e configurar as skills disponíveis. Consulte [a arquitetura](docs/architecture.md) e [o guia de setup](docs/setup.md).

O projeto `onmyoji-daemon/` supervisiona os serviços locais de uma instância, começando pelo gateway Telegram. Seus dados operacionais ficam em `configs/daemon/` e nunca são versionados.
