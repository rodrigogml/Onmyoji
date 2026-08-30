# Setup do Onmyōji

## Convenção de scripts

O setup é executado diretamente com Python. Não há wrappers PowerShell ou shell.

```text
python setupOnmyoji.py
python available-skills/<nome-da-skill>/setupSkill.py
```

`setupOnmyoji.py` é o configurador geral da instância. Ele descobre dinamicamente as skills procurando por `setupSkill.py` dentro de `available-skills/`; nenhum nome de skill é codificado no menu central. O menu principal usa `X` para sair. Ao escolher uma skill, o submenu oferece somente habilitar/desabilitar conforme o estado atual, configurar e `X` para voltar. A habilitação é registrada localmente em `configs/onmyoji-skills.toml` e cria ou remove links em `skills/`, que é o único diretório de skills varrido pelo Codex.

O menu `A. Configurar Codex-CLI` administra o sistema da instância: executável, modelo, esforço de raciocínio, pasta do projeto, sandbox, política de aprovação, diretórios adicionais de escrita, login e início interativo do Codex. Seus dados locais ficam em `configs/onmyoji-system.toml`; os campos compatíveis são aplicados ao `config.toml` do `CODEX_HOME`.

`Executar Codex-CLI` inicia o executável configurado com o diretório de trabalho igual à pasta do projeto e `CODEX_HOME` apontando para a instância atual. Modelo, esforço de raciocínio, sandbox e aprovação vêm do bloco gerenciado em `config.toml`; cada diretório adicional de escrita também é enviado por `--add-dir`. Esse é o CLI nativo do Codex. `Console interativo Onmyōji` é uma opção diferente: inicia o App Server e aplica a composição de developer instructions do Onmyōji.

O Codex-CLI expõe configuração de raízes adicionais de escrita, não uma lista pública equivalente de raízes exclusivas de leitura. Em `workspace-write`, a pasta do projeto e `writable_roots` podem ser alteradas; a restrição de leitura para fora de uma lista permitida continua sendo responsabilidade da ACL do sistema operacional aplicada ao usuário que executa o Shikigami. Não cadastre diretórios sensíveis como graváveis apenas para que possam ser lidos.

Cada `setupSkill.py` é responsável exclusivamente pela configuração da sua skill. Ele recebe a pasta da instância do Onmyōji, apresenta seu próprio menu e preserva perfis existentes até haver confirmação explícita. Todo novo ou alterado fluxo de gravação deve validar o resultado, manter backup temporário quando substituir dados e restaurar a configuração anterior em caso de falha. Wizards aceitam `X` ou `Esc` (quando o terminal o transmite, normalmente seguido de Enter) para cancelar sem gravar alterações parciais.

Quando uma skill precisa da referência `vault_profile`, o configurador lê os perfis já definidos em `configs/keepass.toml` e oferece um seletor numérico. Não solicite esse identificador como texto livre; se ainda não houver perfis KeePass, informe que a skill KeePass Vault deve ser configurada primeiro.

Após a escolha do perfil KeePass, sugira a entrada de credencial no formato `APIs/<Integração>:<perfil-da-skill-em-CamelCase>`, por exemplo `APIs/Omie:Laveli` para o perfil local `laveli`. O usuário pode ajustar a sugestão ao criar uma configuração; não substitua uma entrada já configurada durante edição.

Quando uma skill oferecer teste de perfil, ele deve selecionar um perfil existente e executar somente uma operação de leitura mínima. O resultado deve confirmar a autenticação ou descrever a falha sem imprimir credenciais, tokens nem a resposta completa da API.

## Protocolo de descoberta

Todo `setupSkill.py` deve aceitar a ação `describe` e retornar metadados estruturados da skill, incluindo identificador, nome de exibição, ações disponíveis, nome do modelo e caminho do perfil local esperado. O configurador geral usa esses dados para montar o menu e o status.

As ações mínimas são:

- `describe`: informa os metadados usados pelo menu.
- `status`: informa a situação da configuração sem alterá-la.
- `configure`: abre o menu próprio da skill; ele cria os arquivos necessários ao salvar uma configuração válida e nunca sobrescreve um perfil sem confirmação.

## Funções do setup geral

Além de encaminhar chamadas ao setup de cada skill, `setupOnmyoji.py` permite listar o estado, habilitar ou desabilitar skills em `configs/onmyoji-skills.toml` e abrir um menu interativo. O estado é aplicado por links locais em `skills/`. O arquivo `config.toml` contém somente o bloco gerenciado de configurações nativas do Codex; a gravação preserva opções não gerenciadas e cria backup temporário antes da validação.
