# Configuração

O wrapper lê um perfil INI com estas seções:

```ini
[mysql]
executable = mysql
host = 127.0.0.1
port = 3306
database = exemplo
user = app
connect_timeout = 10

[auth]
provider_command = python path/to/keepass_vault.py --config configs/keepass.ini
entry = APIs/MySQL:app
username_field = username
password_field = password
credential_target = Akuma/KeePassXC/KeeVault

[execution]
timeout = 120
allow_client_commands = true
```

`provider_command` recebe uma requisição JSON pela entrada padrão, sem segredos nos argumentos. Quando `auth.username_field` é definido, o usuário também é lido do Vault e `mysql.user` pode ficar vazio:

```json
{"version":1,"operation":"read","entry":{"path":"APIs/MySQL:app"},"field":"password","auth":{"mode":"windows_credential_manager","target":"Akuma/KeePassXC/KeeVault"}}
```

O provedor deve devolver JSON contendo `ok: true` e `data.value`. Arquivos reais de configuração devem ficar em `configs/`, ser ignorados pelo Git e nunca conter senha.
