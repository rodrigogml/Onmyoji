# Provedor KeePassVault

O token é obtido com uma requisição `read` para a entrada e campo definidos no perfil. A autenticação do próprio KeePassVault é descrita em `auth_json`, normalmente usando Windows Credential Manager.

O token nunca é colocado em argumentos, logs, arquivos temporários ou respostas JSON. O wrapper recebe o valor apenas em memória e envia-o no cabeçalho `Authorization: Bearer`.

