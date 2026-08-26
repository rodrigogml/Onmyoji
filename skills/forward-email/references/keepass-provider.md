# Provedor KeePassVault

O token é obtido com uma requisição `read` para a entrada e campo definidos no perfil. O valor permanece em memória e é usado somente para montar a autenticação Basic da API.

Não registrar o token, a requisição do Vault, cabeçalhos HTTP ou respostas que contenham senhas de aliases. A senha retornada por `aliases.generate_password` deve ser tratada como segredo operacional.

