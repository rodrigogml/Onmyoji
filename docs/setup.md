# Setup do Onmyōji

## Convenção de scripts

O setup é executado diretamente com Python. Não há wrappers PowerShell ou shell.

```text
python setupOnmyoji.py
python skills/<nome-da-skill>/setupSkill.py
```

`setupOnmyoji.py` é o configurador geral da instância. Ele descobre dinamicamente as skills procurando por `setupSkill.py` dentro de `skills/`; nenhum nome de skill é codificado no menu central.

Cada `setupSkill.py` é responsável exclusivamente pela configuração da sua skill. Ele recebe a pasta da instância do Onmyōji, cria ou atualiza o perfil local em `configs/`, valida pré-requisitos e preserva perfis existentes até haver confirmação explícita.

## Protocolo de descoberta

Todo `setupSkill.py` deve aceitar a ação `describe` e retornar metadados estruturados da skill, incluindo identificador, nome de exibição, ações disponíveis, nome do modelo e caminho do perfil local esperado. O configurador geral usa esses dados para montar o menu e o status.

As ações mínimas são:

- `describe`: informa os metadados usados pelo menu.
- `status`: informa se o perfil existe, está válido e está atualizado em relação ao modelo.
- `init`: cria o perfil a partir do arquivo `.model`, sem sobrescrever um arquivo existente.
- `configure`: cria o perfil a partir do modelo quando ele ainda não existe e indica o arquivo local para edição; nunca sobrescreve um perfil existente.
- `validate`: valida a configuração e os pré-requisitos da skill.
- `migrate`: propõe e aplica uma atualização de modelo após criar backup do perfil local.

## Funções do setup geral

Além de encaminhar chamadas ao setup de cada skill, `setupOnmyoji.py` permite listar o estado, habilitar ou desabilitar skills em `config.toml` e abrir um menu interativo. A gravação preserva quaisquer opções não gerenciadas pelo Onmyōji e cria backup do `config.toml` local antes de substituí-lo.
