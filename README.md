# Onmyōji

Base de integração local para um Shikigami. Cada cópia configurada deste diretório é o `CODEX_HOME` de um único Shikigami; o diretório do Shikigami é somente o seu workspace de trabalho.

Execute `py -3 setupOnmyoji.py` para inicializar e configurar as skills disponíveis. Consulte [a arquitetura](docs/architecture.md) e [o guia de setup](docs/setup.md).

O projeto `onmyoji-daemon/` supervisiona os serviços locais de uma instância, começando pelo gateway Telegram. Seus dados operacionais ficam em `configs/daemon/` e nunca são versionados.
