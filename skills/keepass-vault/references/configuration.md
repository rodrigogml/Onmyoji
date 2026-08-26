# Configuração

O arquivo real é `CODEX_HOME/configs/keepass.toml`; ele é local e ignorado pelo Git. Comece copiando o modelo com `py -3 setupSkill.py --action init`.

Cada perfil aponta para um vault, delimita operações, caminhos de entradas e diretórios permitidos para anexos. Nenhuma senha é armazenada no TOML. Em Windows, prefira `windows_credential_manager`; em Linux, configure `command` com um provedor local que escreva somente a senha na saída padrão.
