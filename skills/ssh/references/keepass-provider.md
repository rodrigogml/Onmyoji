# Provedor KeePassVault

A skill SSH chama um wrapper KeePassVault configurado no perfil. Para senha, usa `read` no campo configurado. Para chave privada, usa `attachment.export`; os bytes do anexo nunca entram no JSON da skill SSH, apenas o caminho temporário retornado pela operação.

O perfil não armazena senha, chave privada ou passphrase. A autenticação da própria KeePassVault deve ser configurada por um método seguro, como Windows Credential Manager. A skill SSH não deve imprimir a requisição, a resposta bruta do provedor ou exceções contendo segredos.
