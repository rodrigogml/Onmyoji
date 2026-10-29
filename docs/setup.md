# Setup do Onmyōji

## Convenção de scripts

O setup é executado diretamente com Python. Não há wrappers PowerShell ou shell.

```text
python setupOnmyoji.py
python shikigami-skills/<nome-da-skill>/setupSkill.py
```

`setupOnmyoji.py` é o configurador geral da instância. Ele descobre dinamicamente somente as skills próprias procurando por `setupSkill.py` dentro de `shikigami-skills/`; nenhum nome de skill é codificado no menu central. O menu principal usa `X` para sair. Ao escolher uma skill, o submenu oferece somente habilitar/desabilitar conforme o estado atual, configurar e `X` para voltar. A habilitação é registrada em `configs/shikigami/skills.toml`. Antes de abrir o Codex pelo CLI, Console ou Gateway, o launcher copia cada skill habilitada para `<workspace>/.agents/skills/<id>/`, sem links simbólicos.

Na primeira execução sem `--action`, o setup abre o wizard de instalação inicial e solicita o nome do Shikigami. Ao concluir, ele grava a identidade e o estado de instalação em `configs/onmyoji-system.toml` e inicializa o estado local das skills quando necessário. Ações não interativas, como `--action list` e `--action status`, retornam que a instalação está pendente até que o wizard seja concluído. As fontes em `shikigami-skills/` pertencem ao repositório da instância e podem ser revisadas e versionadas pelo operador; `configs/onmyoji-system.toml` e `configs/shikigami/skills.toml` são locais e ignorados pelo Git. Consulte o [guia de instalação](installation.md) e o [README](../README.md#camadas-da-instância) para a classificação completa de cada área.

O menu `A. Configurar Codex-CLI` administra o sistema da instância nesta ordem: executável, login, modelo, esforço de raciocínio, pasta do projeto, sandbox, política de aprovação, diretórios adicionais de escrita e início interativo do Codex. Ao configurar o executável, o menu procura instalações visíveis no `PATH` e oferece os caminhos encontrados; a última opção aceita um caminho completo informado manualmente. Seus dados locais ficam em `configs/onmyoji-system.toml`; os campos compatíveis são aplicados ao `config.toml` do `CODEX_HOME`.

Tanto a configuração quanto o wizard inicial confirmam o executável com `codex --version`, verificam o login com `codex login status` e consultam o App Server local com `model/list`. Portanto, a lista exibida contém apenas os modelos visíveis para a conta autenticada, e cada modelo apresenta somente seus esforços de raciocínio compatíveis, com o padrão sugerido pelo próprio Codex. Após a escolha, uma chamada efêmera (`codex exec`) em sandbox somente leitura valida a combinação; ela pode consumir a franquia normal da conta. Sem login, ou se o catálogo não puder ser consultado, o setup não oferece nem salva uma seleção de modelo. O menu principal também oferece `! Diagnóstico`, que apresenta essa cadeia — nome, executável, login, catálogo, modelo, raciocínio e validação — antes dos itens de pasta de trabalho, sandbox, política e diretórios adicionais.

Ao escolher um modelo no menu ou no wizard, o setup apresenta imediatamente os esforços de raciocínio compatíveis e sugere o valor inicial retornado para aquele modelo. A validação envia também o esforço escolhido, para que o diagnóstico identifique uma incompatibilidade configurada ou uma rejeição pelo Codex da conta atual.

O wizard também exige uma pasta de trabalho. Sem uma pasta já configurada, ele sugere uma pasta irmã do `CODEX_HOME`, com o mesmo nome e o sufixo `-Work` — por exemplo, `C:\opt\MeuShikigami` usa `C:\opt\MeuShikigami-Work`. A opção 4 do menu Codex-CLI aplica a mesma sugestão. Se a pasta não existir, o setup pede confirmação antes de criá-la; se a criação for recusada ou falhar, nenhuma configuração é salva.

`Executar Codex-CLI` inicia o executável configurado com o diretório de trabalho igual à pasta do projeto e `CODEX_HOME` apontando para a instância atual. Modelo, esforço de raciocínio, sandbox e aprovação vêm do bloco gerenciado em `config.toml`; cada diretório adicional de escrita também é enviado por `--add-dir`. A mesma composição-base de developer instructions do Onmyōji é enviada ao CLI por `--config`. `Console interativo Onmyōji` é uma opção diferente: inicia o App Server, mas recebe a mesma composição-base, workspace, modelo, raciocínio, política de aprovação e sandbox estruturado — incluindo os diretórios adicionais, relativos ou absolutos. Quando o App Server pedir aprovação, o console mostra os detalhes ao operador e permite aprovar uma vez, aprovar para a sessão quando suportado ou recusar; recusa é o padrão e nada é persistido na configuração.

As fontes em `shikigami-skills/` nunca são usadas diretamente pelo Codex nem recebem links a partir do `CODEX_HOME`. Elas são a origem versionada. A projeção no workspace possui um manifesto local de posse em `configs/shikigami/skills-runtime.json`: alterações locais são substituídas pela fonte na próxima sincronização, e uma skill desabilitada remove somente sua cópia registrada. Uma pasta preexistente que não pertença ao Onmyōji interrompe a sincronização em vez de ser sobrescrita.

O Codex-CLI expõe configuração de raízes adicionais de escrita, não uma lista pública equivalente de raízes exclusivas de leitura. Em `workspace-write`, a pasta do projeto e `writable_roots` podem ser alteradas; a restrição de leitura para fora de uma lista permitida continua sendo responsabilidade da ACL do sistema operacional aplicada ao usuário que executa o Shikigami. Não cadastre diretórios sensíveis como graváveis apenas para que possam ser lidos.

O submenu de diretórios adicionais de escrita não faz parte do wizard. Ele permite incluir uma raiz por vez, remover uma entrada pelo número ou limpar toda a lista com confirmação. Caminhos relativos são permitidos e interpretados a partir da pasta de trabalho configurada; o menu mostra seu destino efetivo. Caminhos inexistentes, repetidos após resolução, a própria instância (`CODEX_HOME`), a pasta de trabalho e qualquer diretório ancestral dessas áreas são recusados. Uma raiz já coberta por outra é recusada; uma raiz que abrange uma entrada existente exige confirmação de ampliação. Diretórios que abrangem o perfil do usuário ou uma unidade inteira exigem confirmação reforçada.

O wizard e a opção Sandbox do menu Codex-CLI mostram os caminhos reais da instância (`CODEX_HOME`) e do workspace antes da escolha. `read-only` serve para inspeção sem alterações automáticas; `workspace-write` permite editar o workspace e raízes adicionais, mas não o repositório da instância; `danger-full-access` remove as restrições locais do sandbox e deve ser usado somente em ambiente controlado. O sandbox limita comandos gerados pelo Codex, não substitui as permissões do sistema operacional nem impede automaticamente a leitura fora do workspace.

O wizard e a opção Política de aprovação explicam as duas políticas aceitas pelo Codex-CLI. `on-request` (recomendada) faz o CLI pausar e apresentar localmente uma solicitação quando uma ação ultrapassa os limites do sandbox; `never` não amplia o acesso e faz o Codex continuar apenas com ações já permitidas pelo sandbox, recusando ou bloqueando as que exigiriam aprovação. A política não substitui nem amplia o sandbox.

Cada `setupSkill.py` de `shikigami-skills/` é responsável exclusivamente pela configuração da sua skill. Ele recebe a pasta da instância do Onmyōji, apresenta seu próprio menu e preserva perfis existentes até haver confirmação explícita. Todo novo ou alterado fluxo de gravação deve validar o resultado, manter backup temporário quando substituir dados e restaurar a configuração anterior em caso de falha. Wizards aceitam `X` ou `Esc` (quando o terminal o transmite, normalmente seguido de Enter) para cancelar sem gravar alterações parciais.

Quando uma skill precisa da referência `vault_profile`, o configurador lê os perfis em `configs/mcp/credentials.toml` e oferece um seletor numérico. Não solicite esse identificador como texto livre; se ainda não houver perfis de vault MCP, informe que o operador deve criar um no menu `MCP > Perfis de vault`.

Após a escolha do perfil de vault MCP, sugira a entrada de credencial no formato `APIs/<Integração>:<perfil-da-skill-em-CamelCase>`, por exemplo `APIs/Notion:Laveli` para o perfil local `laveli`. O usuário pode ajustar a sugestão ao criar uma configuração; não substitua uma entrada já configurada durante edição.

Quando uma skill oferecer teste de perfil, ele deve selecionar um perfil existente e executar somente uma operação de leitura mínima. O resultado deve confirmar a autenticação ou descrever a falha sem imprimir credenciais, tokens nem a resposta completa da API.

## Protocolo de discovery e perfis

Todo `setupSkill.py` em `shikigami-skills/` aceita `--action describe --json` e retorna, no mínimo, os campos `id`, `title` e `description`. O configurador geral usa somente esses campos para descobrir e exibir a skill. Campos adicionais são opcionais e não fazem parte do contrato consumido pelo setup central.

Uma skill própria pode também aceitar estas ações:

- `status`: faz um diagnóstico sem gravar a configuração da skill; retorna ao menos `configured` e `valid` em JSON.
- `configure`: abre o menu interativo da própria skill. Ele cria ou altera arquivos somente após a confirmação e a validação apropriadas.

Todoist, Omie, BTG Empresas, EccoVox, Cloudflare, Forward Email, Notion, AWS, MySQL, SSH, BIS2CMD, BIS10CMD, Google e Spotify são configurados exclusivamente pelo menu MCP e consumidos pelo servidor MCP interno. Consulte a documentação específica em `onmyoji-daemon/docs/mcp-*.md`, incluindo a [integração Google MCP](../onmyoji-daemon/docs/mcp-google.md) e a [integração Spotify MCP](../onmyoji-daemon/docs/mcp-spotify.md).

## Funções do setup geral

Além de encaminhar chamadas ao setup de cada skill própria, `setupOnmyoji.py` permite listar o estado, habilitar ou desabilitar skills em `configs/shikigami/skills.toml` e abrir um menu interativo. As integrações compartilhadas são configuradas no menu MCP e publicadas pelo servidor MCP interno. O arquivo `config.toml` contém somente o bloco gerenciado de configurações nativas do Codex; a gravação preserva opções não gerenciadas e cria backup temporário antes da validação.
