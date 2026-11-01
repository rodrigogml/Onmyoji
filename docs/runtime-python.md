# Runtime Python do Shikigami

Cada instância usa uma única seleção Python, declarada em `configs/onmyoji-system.toml`.

| Modo | Execução |
| --- | --- |
| `isolated` | Cria e usa somente `venv/` na raiz da instância. |
| `global` | Usa e instala dependências no executável Python configurado. |

Daemon, MCP, OAuth, e-mail, BTG, Mini Apps e OCR resolvem o mesmo interpretador. O modo `global` nunca cria nem utiliza `venv/`.

> [!IMPORTANT]
> Ambientes antigos em `configs/daemon/venv`, `configs/mcp/venv` e `configs/mcp/ocr/advanced` não são removidos automaticamente, mas não são mais selecionados pelo Onmyōji.

O setup inicial solicita o modo e o Python. A opção **Python e bibliotecas** permite alterar a escolha, consultar os pacotes instalados e instalar ou reparar os grupos de componentes usados pelo daemon, MCPs, PDF e OCR. Depois da troca de runtime, use esse mesmo menu para preparar os componentes exigidos no novo destino.
