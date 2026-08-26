---
name: ssh
description: Conectar e executar comandos em servidores SSH usando perfis INI, autenticação por senha ou chave privada e credenciais/anexos obtidos pela KeePassVault. Use para executar comandos remotos, transferir arquivos e administrar servidores sem expor senhas ou chaves no contexto, nos argumentos ou nos logs.
---

# SSH

Use `scripts/ssh.py` como wrapper JSON para conexões SSH. O wrapper usa Paramiko para suportar senha e chave privada no Windows, aceita comandos remotos livremente e oferece upload/download.

## Perfil

Cada perfil é um arquivo INI local e não versionado. O perfil contém host, porta, usuário, backend SSH e a referência à entrada KeePass; nunca contém senha, chave privada ou passphrase.

```ini
[ssh]
host = 192.168.3.64
port = 22
username = usuario
auth_mode = password
timeout_seconds = 30
keepass_wrapper = C:\\project\\skills\\keepass-vault\\scripts\\keepass_vault.py
keepass_config = C:\\project\\configs\\keepass-personal.ini
keepass_entry = Servidores/Turing:SSH:servidor
keepass_password_field = password
keepass_auth_mode = windows_credential_manager
keepass_auth_target = Company/KeePassVault
known_hosts = C:\\Users\\usuario\\.ssh\\known_hosts
temp_dir = C:\\Users\\usuario\\AppData\\Local\\Temp
```

Para `auth_mode = key`, informe também `keepass_key_attachment` com o nome exato do anexo da chave privada e, opcionalmente, `keepass_key_passphrase_field = password` se a passphrase estiver em outra entrada. A chave é exportada para um arquivo temporário, usada somente na sessão e removida em `finally`.

## Operações

Entrada sempre contém `version: 1` e uma operação registrada:

- `exec`: executar qualquer comando remoto informado em `command`;
- `upload`: enviar `source` local para `destination` remoto;
- `download`: baixar `source` remoto para `destination` local.

Exemplo:

```json
{"version":1,"operation":"exec","command":"uname -a"}
```

O resultado é JSON com `stdout`, `stderr` e `exit_code`. O wrapper não imprime credenciais, passphrases, chave privada nem o JSON bruto da KeePassVault. Arquivos locais precisam existir para upload e o destino de download não pode ser sobrescrito sem `overwrite: true`.

## Segurança

Use `known_hosts` para validar a identidade do servidor. A política padrão rejeita hosts desconhecidos; aceitar um host novo deve ser uma decisão explícita do perfil. Nunca coloque segredos em argumentos, comandos remotos, logs ou arquivos versionados. A skill KeePass deve ser chamada como provedor externo e o refresh/arquivo temporário deve ser apagado ao final.

Leia [references/profile.md](references/profile.md) para o contrato completo e [references/keepass-provider.md](references/keepass-provider.md) para a integração com anexos.
