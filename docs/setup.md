# Setup do Onmyōji

## Convenção de scripts

O setup é executado diretamente com Python. Não há wrappers PowerShell ou shell.

```text
python setupOnmyoji.py
python skills/<nome-da-skill>/setupSkill.py
```

`setupOnmyoji.py` é o configurador geral da instância. Ele descobre dinamicamente as skills procurando por `setupSkill.py` dentro de `skills/`; nenhum nome de skill é codificado no menu central. O menu principal usa `X` para sair. Ao escolher uma skill, o submenu oferece somente habilitar/desabilitar conforme o estado atual, configurar e `X` para voltar.

O menu `A. Configurar Codex-CLI` administra o sistema da instância: executável, modelo, esforço de raciocínio, pasta do projeto, sandbox, política de aprovação, diretórios adicionais de escrita, login e início interativo do Codex. Seus dados locais ficam em `configs/onmyoji-system.toml`; os campos compatíveis são aplicados ao `config.toml` do `CODEX_HOME`.

O início usa `codex -C <pasta-do-projeto>` com `CODEX_HOME` apontando para a instância atual e transmite explicitamente modelo, esforço de raciocínio, sandbox, política de aprovação e cada diretório adicional de escrita. Os diretórios adicionais são registrados em `[sandbox_workspace_write].writable_roots` e também enviados por `--add-dir`, para que a sessão iniciada pelo menu não dependa apenas da leitura do arquivo de configuração.

O Codex-CLI expõe configuração de raízes adicionais de escrita, não uma lista pública equivalente de raízes exclusivas de leitura. Em `workspace-write`, a pasta do projeto e `writable_roots` podem ser alteradas; a restrição de leitura para fora de uma lista permitida continua sendo responsabilidade da ACL do sistema operacional aplicada ao usuário que executa o Shikigami. Não cadastre diretórios sensíveis como graváveis apenas para que possam ser lidos.

Cada `setupSkill.py` é responsável exclusivamente pela configuração da sua skill. Ele recebe a pasta da instância do Onmyōji, apresenta seu próprio menu e preserva perfis existentes até haver confirmação explícita. Cada alteração usa backup temporário, validação automática e restauração em caso de falha. Wizards aceitam `X` ou `Esc` (quando o terminal o transmite, normalmente seguido de Enter) para cancelar sem gravar alterações parciais.

Quando uma skill precisa da referência `vault_profile`, o configurador lê os perfis já definidos em `configs/keepass.toml` e oferece um seletor numérico. Não solicite esse identificador como texto livre; se ainda não houver perfis KeePass, informe que a skill KeePass Vault deve ser configurada primeiro.

Após a escolha do perfil KeePass, sugira a entrada de credencial no formato `APIs/<Integração>:<perfil-da-skill-em-CamelCase>`, por exemplo `APIs/Omie:Laveli` para o perfil local `laveli`. O usuário pode ajustar a sugestão ao criar uma configuração; não substitua uma entrada já configurada durante edição.

## Protocolo de descoberta

Todo `setupSkill.py` deve aceitar a ação `describe` e retornar metadados estruturados da skill, incluindo identificador, nome de exibição, ações disponíveis, nome do modelo e caminho do perfil local esperado. O configurador geral usa esses dados para montar o menu e o status.

As ações mínimas são:

- `describe`: informa os metadados usados pelo menu.
- `status`: informa a situação da configuração sem alterá-la.
- `configure`: abre o menu próprio da skill; ele cria os arquivos necessários ao salvar uma configuração válida e nunca sobrescreve um perfil sem confirmação.

## Funções do setup geral

Além de encaminhar chamadas ao setup de cada skill, `setupOnmyoji.py` permite listar o estado, habilitar ou desabilitar skills em `config.toml` e abrir um menu interativo. A gravação preserva quaisquer opções não gerenciadas pelo Onmyōji e cria backup temporário do `config.toml` local antes de substituí-lo; o backup é removido quando a validação termina com sucesso.
