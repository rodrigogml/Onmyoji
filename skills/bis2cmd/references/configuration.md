# Configuração de perfis

O perfil deve conter as seções `biscmd`, `auth` e `execution`.

```ini
[biscmd]
jar_path = C:\opt\BISCMD\BISCMD-9.0.jar
working_dir = C:\opt\BISCMD
java_path = java
host = 192.168.3.64
port = 8080

[auth]
provider_command = python C:\path\keepass_vault.py --config C:\path\keepass.ini
entry = Servidores / Turing:WildFly:BIS2:biscmd
credential_target = Akuma/KeePassXC/KeeVault
username_field = username
password_field = password

[execution]
timeout = 180
encoding = utf-8
```

`jar_path` deve apontar para um JAR executável com a pasta `libs` compatível ao lado. `working_dir` deve ser a pasta que contém o JAR e as bibliotecas.

O wrapper fornece `BISCMD_HOST`, `BISCMD_PORT`, `BISCMD_USER` e `BISCMD_PASSWORD` somente ao processo Java. As credenciais não são gravadas no perfil nem passadas na linha de comando.

Perfis reais devem ficar em `configs/` com o nome da skill:

```text
configs/bis2cmd_turing.ini
configs/bis2cmd_outro.ini
```
