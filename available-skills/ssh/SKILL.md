---
name: ssh
description: Conectar e executar comandos em servidores SSH usando perfis TOML explícitos, autenticação por senha ou chave privada e credenciais/anexos obtidos pela KeePassVault. Use para executar comandos remotos, transferir arquivos e administrar servidores sem expor senhas ou chaves no contexto, nos argumentos ou nos logs.
---

# SSH

Use `scripts/ssh.py` como wrapper JSON para conexões SSH. O wrapper usa Paramiko para suportar senha e chave privada no Windows, aceita comandos remotos livremente e oferece upload/download.

## Perfil

Os perfis ficam no arquivo local e não versionado `configs/ssh.toml`, criado a partir de `configs/ssh.toml.model`. Informe sempre `--config configs/ssh.toml --profile <perfil>`; o perfil contém host, porta, usuário, backend SSH e a referência à entrada KeePass, nunca senha, chave privada ou passphrase.

```toml
[profiles.servidor]
host = "192.168.3.64"
port = 22
username = "usuario"
auth_mode = "password"
vault_profile = "pessoal"
vault_entry_path = "Servidores/Turing:SSH:servidor"
known_hosts = "C:/Users/usuario/.ssh/known_hosts"
```

Para `auth_mode = key`, informe também `key_attachment` com o nome exato do anexo da chave privada e, opcionalmente, `key_passphrase_field = password`. A chave é exportada para um arquivo temporário, usada somente na sessão e removida em `finally`.

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
