# Provisionamento IAM com KeePass

O wrapper `available-skills/aws/scripts/aws.py` mantém as operações existentes pela AWS CLI e oferece operações IAM pelo SDK Boto3. O SDK é utilizado quando a API precisa receber uma senha ou códigos MFA: esses valores não passam por argumentos da CLI, arquivos temporários em texto claro ou respostas do wrapper.

## Dependências e perfil

Instale as dependências dos arquivos `available-skills/aws/requirements.txt` e `available-skills/keepass-vault/requirements.txt` no Python que executa os wrappers. A CLI continua necessária para as operações anteriores.

O perfil AWS mantém `vault_profile` e `vault_entry_path` para a chave de acesso. `region` é um padrão opcional, sobrescrito pelo campo `region` da requisição. Quando ambos estiverem ausentes, o wrapper retorna `region_required`. Alterações IAM exigem `expected_account_id` configurado, `confirm: true` e conferência STS antes de qualquer efeito externo.

Os novos comandos são:

| Operação | Campos adicionais | Comportamento |
| --- | --- | --- |
| `iam.user.get` | `user_name` | Consulta o usuário. |
| `iam.user.create` | `user_name`, `confirm` | Cria o usuário sem atribuir permissões ou chaves. Não substitui usuários existentes. |
| `iam.user.login.get` | `user_name` | Consulta o perfil de acesso ao console. |
| `iam.user.login.create` | `user_name`, `password_vault_profile`, `password_vault_entry_path`, `confirm` | Lê a senha do cofre e cria o perfil de console. `password_reset_required` é booleano e opcional, com padrão `false`. Não troca senhas existentes. |
| `iam.user.mfa.list` | `user_name` | Lista dispositivos MFA atribuídos, com paginação. |
| `iam.user.mfa.provision` | `user_name`, `device_name`, `password_vault_profile`, `password_vault_entry_path`, `confirm` | Cria, salva e ativa o dispositivo virtual; retorna somente seu identificador e estado. |
| `iam.user.policy.list` | `user_name` | Lista políticas gerenciadas anexadas. |
| `iam.user.inline-policy.list` | `user_name` | Lista nomes das políticas inline. |
| `iam.user.inline-policy.get` | `user_name`, `policy_name` | Consulta o documento de uma política inline para verificar a concessão. |
| `iam.user.groups.list` | `user_name` | Lista os grupos do usuário. |
| `iam.user.policy.attach` | `user_name`, `policy_arn`, `confirm` | Anexa a política explicitamente indicada. |
| `iam.user.policy.put` | `user_name`, `policy_name`, `policy_document`, `confirm` | Salva uma política inline; pode substituir a política com esse nome. |

Toda requisição continua exigindo `version: 1` e `operation`. Nomes IAM seguem o contrato AWS, com até 64 caracteres. As consultas de listas consolidam paginação; falhas da API retornam apenas o código AWS, evitando mensagens que possam repetir segredos enviados.

## Senha e MFA

O caminho da senha de console é informado explicitamente na requisição e pode ser diferente da entrada da API. Um perfil KeePass somente leitura basta para criar o acesso ao console. O MFA exige `read` e `edit` em um perfil KeePass `read_write` autorizado para a entrada de destino.

A operação de MFA verifica o usuário e a capacidade de acesso ao cofre antes de criar o dispositivo. A semente é recebida em memória, gravada no atributo protegido `otp` do KDBX e relida para verificação antes de enviar dois códigos consecutivos à AWS. O período é 30 segundos, com seis dígitos e SHA1. A espera até o próximo código é limitada a aproximadamente 31 segundos.

O wrapper KeePass aceita `read` com `field: "otp"` para integrações internas e `edit` com `values: {"totp": "<URI otpauth>"}` e `confirm: true`. A URI deve ser transmitida somente por stdin e consumida em memória: ela é um segredo, não uma resposta apropriada para exibir ao usuário. A alteração de TOTP deve ser separada de outros campos. `edit` com `values: {}`, `validate_only: true` e `confirm: true` valida a entrada sem modificá-la.

O KDBX é recriptografado e validado antes da substituição atômica. Uma comparação do conteúdo original impede sobrescrever alterações detectadas durante a operação; não substitui coordenação com editores externos. Sincronize/reabra o cofre em outros editores antes de salvá-lo novamente. Somente staging criptografado é usado, no diretório do próprio cofre.

> [!IMPORTANT]
> Não existe transação distribuída entre AWS e KeePass. Se o TOTP foi salvo e a ativação falhou, a mesma operação pode retomar o dispositivo não atribuído. Se a gravação falhou após criar o dispositivo, ele permanece não atribuído. Nesse caso, `recreate_unassigned: true` autoriza explicitamente remover somente esse dispositivo não atribuído e recriá-lo. O wrapper não substitui outro MFA ou TOTP automaticamente. Não utilize essa recuperação para remover dispositivos de outros fluxos.

Após provisionar, consulte o perfil de console, o MFA e as políticas anexadas para confirmar o resultado. O root não recebe políticas IAM adicionais. O provisionamento não habilita acesso IAM ao console de faturamento: essa preferência da conta é independente das políticas e deve ser verificada no console AWS.

## Validação

Os testes em `available-skills/aws/scripts/test_iam_access.py` usam clientes simulados, sem alterar contas reais. Os testes em `available-skills/keepass-vault/tests/test_totp_edit.py` exercitam cofres descartáveis criptografados e verificam preservação de campos e detecção de alterações concorrentes.
