# AWS no MCP interno

A integração AWS publica identidade, S3 e IAM pelo servidor MCP local. O Access Key ID e o Secret Access Key permanecem no vault compartilhado e são entregues somente em memória ao AWS CLI ou ao Boto3.

## Perfis e segurança

Cada perfil define descrição de uso, entrada KeePass, executável AWS CLI, região padrão opcional, conta esperada e raízes locais de leitura e escrita. Com um único perfil habilitado ele é implícito; com vários, `aws.list_profiles` informa nome e contexto.

- `expected_account_id` impede operar na conta errada e é obrigatório em mutações IAM.
- Todas as mutações exigem `confirm=true`.
- Uploads e manifestos só leem `readable_roots`; downloads, status e falhas só escrevem em `writable_roots`.
- O processo AWS recebe credenciais por ambiente isolado, sem perfil, session token, configuração ou histórico herdados.
- Falhas IAM retornam somente códigos sanitizados da AWS.

> [!IMPORTANT]
> O perfil aceita somente credenciais IAM permanentes com Access Key ID e Secret Access Key. Session tokens e perfis externos do AWS CLI não são herdados.

## Superfície

As ferramentas cobrem identidade; listagem, localização, consulta, upload, download, cópia e exclusão no S3; downloads em lote; consulta e criação de jobs de restauração; roles e políticas inline; usuários IAM, login de console, MFA, grupos e políticas.

A região informada na chamada substitui a região padrão. Se nenhuma existir, a chamada falha antes de consultar o cofre. O download em lote rejeita chaves que escapem do diretório de destino e publica progresso e falhas somente nas raízes autorizadas.

## Senha de console e MFA

`aws.iam_user_login_create` recebe o perfil e caminho KeePass da senha de console. A senha vai diretamente ao SDK e não aparece na resposta.

`aws.iam_user_mfa_provision` exige uma entrada KeePass existente. A URI TOTP é salva como campo protegido `otp` e relida antes da ativação. Se houver um dispositivo não associado sem semente disponível, somente `recreate_unassigned=true` autoriza recriá-lo. MFA ou TOTP existentes nunca são substituídos implicitamente.

> [!WARNING]
> AWS e KeePass não formam uma transação distribuída. Uma falha após criar o dispositivo pode deixar um MFA não associado; repita a operação para retomá-lo ou use a recuperação explícita quando a semente não estiver disponível.
