# Provisionamento IAM com KeePass

O módulo AWS do servidor MCP interno mantém as operações AWS CLI e oferece operações IAM pelo SDK Boto3. O SDK é utilizado quando a API precisa receber uma senha ou códigos MFA: esses valores não passam por argumentos da CLI, arquivos temporários em texto claro ou respostas da ferramenta.

## Dependências e perfil

As dependências Python pertencem ao runtime MCP gerenciado pelo projeto. A AWS CLI continua necessária para S3, roles e Batch Operations.

O perfil AWS mantém a referência de vault e entrada para a chave de acesso. `region` é um padrão opcional, sobrescrito pelo campo `region` da requisição. Quando ambos estiverem ausentes, o módulo retorna `region_required`. Alterações IAM exigem `expected_account_id` configurado, `confirm: true` e conferência STS antes de qualquer efeito externo.

Os novos comandos são:

| Operação | Campos adicionais | Comportamento |
| --- | --- | --- |
| `aws.iam_user_get` | `user_name` | Consulta o usuário. |
| `aws.iam_user_create` | `user_name`, `confirm` | Cria o usuário sem atribuir permissões ou chaves. Não substitui usuários existentes. |
| `aws.iam_user_login_get` | `user_name` | Consulta o perfil de acesso ao console. |
| `aws.iam_user_login_create` | `user_name`, `password_vault_profile`, `password_vault_entry_path`, `confirm` | Lê a senha do cofre e cria o perfil de console. `password_reset_required` é booleano e opcional, com padrão `false`. Não troca senhas existentes. |
| `aws.iam_user_mfa_list` | `user_name` | Lista dispositivos MFA atribuídos, com paginação. |
| `aws.iam_user_mfa_provision` | `user_name`, `device_name`, `password_vault_profile`, `password_vault_entry_path`, `confirm` | Cria, salva e ativa o dispositivo virtual; retorna somente seu identificador e estado. |
| `aws.iam_user_policy_list` | `user_name` | Lista políticas gerenciadas anexadas. |
| `aws.iam_user_inline_policy_list` | `user_name` | Lista nomes das políticas inline. |
| `aws.iam_user_inline_policy_get` | `user_name`, `policy_name` | Consulta o documento de uma política inline para verificar a concessão. |
| `aws.iam_user_groups_list` | `user_name` | Lista os grupos do usuário. |
| `aws.iam_user_policy_attach` | `user_name`, `policy_arn`, `confirm` | Anexa a política explicitamente indicada. |
| `aws.iam_user_policy_put` | `user_name`, `policy_name`, `policy_document`, `confirm` | Salva uma política inline; pode substituir a política com esse nome. |

Cada operação é publicada como ferramenta MCP `aws.<nome_da_operação>`. Nomes IAM seguem o contrato AWS, com até 64 caracteres. As consultas de listas consolidam paginação; falhas da API retornam apenas o código AWS, evitando mensagens que possam repetir segredos enviados.

## Senha e MFA

O caminho da senha de console é informado explicitamente na requisição e pode ser diferente da entrada da API. Um perfil KeePass somente leitura basta para criar o acesso ao console. O MFA exige `read` e `edit` em um perfil KeePass `read_write` autorizado para a entrada de destino.

A operação de MFA verifica o usuário e a capacidade de acesso ao cofre antes de criar o dispositivo. A semente é recebida em memória, gravada no atributo protegido `otp` do KDBX e relida para verificação antes de enviar dois códigos consecutivos à AWS. O período é 30 segundos, com seis dígitos e SHA1. A espera até o próximo código é limitada a aproximadamente 31 segundos.

O serviço interno de vault lê e grava o campo protegido `otp` diretamente no KDBX criptografado. Essa função não é publicada como ferramenta MCP. A URI é consumida somente em memória: ela é um segredo e nunca integra a resposta ao agente.

O KDBX é recriptografado e validado antes da substituição atômica. Uma comparação do conteúdo original impede sobrescrever alterações detectadas durante a operação; não substitui coordenação com editores externos. Sincronize/reabra o cofre em outros editores antes de salvá-lo novamente. Somente staging criptografado é usado, no diretório do próprio cofre.

> [!IMPORTANT]
> Não existe transação distribuída entre AWS e KeePass. Se o TOTP foi salvo e a ativação falhou, a mesma operação pode retomar o dispositivo não atribuído. Se a gravação falhou após criar o dispositivo, ele permanece não atribuído. Nesse caso, `recreate_unassigned: true` autoriza explicitamente remover somente esse dispositivo não atribuído e recriá-lo. O módulo não substitui outro MFA ou TOTP automaticamente. Não utilize essa recuperação para remover dispositivos de outros fluxos.

Após provisionar, consulte o perfil de console, o MFA e as políticas anexadas para confirmar o resultado. O root não recebe políticas IAM adicionais. O provisionamento não habilita acesso IAM ao console de faturamento: essa preferência da conta é independente das políticas e deve ser verificada no console AWS.

## Validação

Os testes em `onmyoji-daemon/tests/test_mcp_aws_iam.py` usam clientes simulados, sem alterar contas reais. Os testes do resolvedor de credenciais exercitam cofres descartáveis criptografados e verificam preservação de campos e detecção de alterações concorrentes.
