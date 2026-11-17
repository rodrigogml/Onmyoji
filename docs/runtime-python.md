# Runtime Python do Shikigami

Cada instância usa uma única seleção Python, declarada em `configs/onmyoji-system.toml`.

| Modo | Execução |
| --- | --- |
| `isolated` | Cria e usa somente `venv/` na raiz da instância. |
| `global` | Usa e instala dependências no executável Python configurado. |

Daemon, MCP, OAuth, e-mail, BTG, Mini Apps e OCR resolvem o mesmo interpretador. O modo `global` nunca cria nem utiliza `venv/`.

> [!WARNING]
> Escolher `global` permite instalar e atualizar bibliotecas compartilhadas com o sistema operacional. Para cumprir essa escolha em Python protegido por PEP 668, o runtime define `PIP_BREAK_SYSTEM_PACKAGES=1` somente no processo de instalação do pip, equivalente a [`--break-system-packages`](https://pip.pypa.io/en/stable/cli/pip_install/#cmdoption-break-system-packages). O setup informa esse efeito antes da instalação; a permissão não é persistida no terminal nem na configuração do pip e não se aplica ao modo `isolated`. As permissões de escrita do sistema continuam necessárias. Prefira `isolated` para manter as dependências separadas das bibliotecas do sistema.

> [!IMPORTANT]
> Ambientes antigos em `configs/daemon/venv`, `configs/mcp/venv` e `configs/mcp/ocr/advanced` não são removidos automaticamente, mas não são mais selecionados pelo Onmyōji.

O setup inicial solicita o modo e o Python. A opção **Python e bibliotecas** permite alterar a escolha, consultar os pacotes instalados e instalar ou reparar os grupos de componentes usados pelo daemon, MCPs, PDF e OCR. Depois da troca de runtime, use esse mesmo menu para preparar os componentes exigidos no novo destino.

## Instalação global sem permissão de escrita

Uma mensagem como `Permission denied: '/usr/local/lib/python3.13/dist-packages/tzdata'` indica que a conta atual não pode gravar no destino global. A liberação de PEP 668 não concede essa permissão.

No Linux, a instalação dos componentes do daemon verifica os diretórios de bibliotecas e executáveis do Python selecionado. Quando precisa de privilégios administrativos, o setup solicita autorização para usar `sudo`; a senha é solicitada somente pelo próprio `sudo`. Apenas o pip é elevado, e a validação continua sob a conta operacional. Em uma instância com conta dedicada, a etapa administrativa parte da conta instaladora, sem conceder `sudo` à conta do Shikigami. Na CLI, a autorização explícita corresponde à opção `install-runtime --use-sudo`.

Após atualizar o Onmyōji, repita a opção **1** do menu **Daemon** ou use **Python e bibliotecas > Instalar ou reparar componentes do daemon**. Em uma instância com conta dedicada, use o menu **Daemon** pela conta instaladora para a instalação global administrativa. Se essa conta não puder usar `sudo`, peça ao administrador para preparar as dependências no Python configurado ou selecione `isolated` em **Python e bibliotecas** e prepare os componentes nesse ambiente. Outros instaladores que não oferecem elevação apresentam a orientação operacional e preservam a escolha de runtime.

> [!NOTE]
> Não use `pip install --user` para corrigir essa falha: o Shikigami executa as verificações com `-I`, que exclui os pacotes do usuário. As dependências precisam estar disponíveis no interpretador e no ambiente selecionados.
