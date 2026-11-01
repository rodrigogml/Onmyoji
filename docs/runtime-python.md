# Runtime Python do Shikigami

Cada instância usa uma única seleção Python, declarada em `configs/onmyoji-system.toml`.

| Modo | Execução |
| --- | --- |
| `isolated` | Cria e usa somente `venv/` na raiz da instância. |
| `global` | Usa e instala dependências no executável Python configurado. |

Daemon, MCP, OAuth, e-mail, BTG, Mini Apps e OCR resolvem o mesmo interpretador. O modo `global` nunca cria nem utiliza `venv/`.

> [!IMPORTANT]
> Ambientes antigos em `configs/daemon/venv`, `configs/mcp/venv` e `configs/mcp/ocr/advanced` não são removidos automaticamente, mas não são mais selecionados pelo Onmyōji.

O setup inicial solicita o modo e o Python. A opção **Codex-CLI > Runtime Python** permite alterar a escolha; depois da troca, execute a instalação ou reparação do runtime para instalar as dependências exigidas no novo destino.
