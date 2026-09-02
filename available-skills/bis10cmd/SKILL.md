---
name: bis10cmd
description: Integração com o BISCMD 10.0 para operar o BIS10 por fachada EJB remota. Use para diagnosticar conexão e sessão e para consultar ou manipular lançamentos financeiros, com perfis locais e credenciais lidas do KeePassVault.
---

# BIS10CMD

Use `scripts/bis10cmd.py` para executar sequências validadas de comandos do BISCMD 10.0.

Leia `references/configuration.md` antes de criar ou alterar perfis e `references/commands.md` antes de executar operações.

O perfil contém somente dados locais de conexão e referências a duas entradas KeePass: uma para o ApplicationRealm/WildFly (JNDI) e outra para o usuário BIS10. Nunca grave credenciais no TOML nem em argumentos do comando.

## Execução

Envie JSON v1 pela entrada padrão:

```json
{
  "version": 1,
  "commands": [{ "name": "ping", "args": [] }]
}
```

O wrapper aceita `help`, `facade`, `login`, `connect`, `ping`, `session` e `accountStatement`. Para `ping`, `session` e `accountStatement`, ele inclui `-connect` quando a sequência ainda não tiver estabelecido conexão.

O BIS10CMD ainda não produz saída estruturada. O resultado retorna mensagens textuais de `stdout` e, quando houver, `stderr`; a skill não interpreta valores financeiros a partir desse texto.

## Segurança

- Não passar senhas, propriedades Java ou variáveis `BISCMD_*` nos argumentos.
- Confirmar alvo, valores e o argumento literal `confirm` antes de operações de escrita.
- Nunca remover o `confirm` exigido pelo BIS10CMD.
- Não alterar lançamentos `BILLS` diretamente; eles pertencem aos fluxos de contas e pagamentos de origem.
- Preservar mensagens funcionais sem expor valores recuperados do KeePassVault.
