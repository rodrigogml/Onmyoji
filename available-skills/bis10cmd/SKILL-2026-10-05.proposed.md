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

O wrapper aceita `help`, `facade`, `login`, `connect`, `ping`, `session`, `accountStatement`, `accounts`, `categories`, `costCenters`, `companies` e `company`. Para comandos autenticados, ele inclui `-connect` quando a sequência ainda não tiver estabelecido conexão. Selecione a empresa desejada explicitamente na mesma sequência antes das consultas financeiras.

As consultas cadastrais e `accountStatement get/list` retornam JSON v1 em `data.lookups`. As consultas de lançamentos usam o recurso `accountStatements`, com valores decimais em texto e paginação validada. Consuma todas as páginas até `hasMore=false` antes de concluir uma conferência de duplicidade. Mensagens funcionais e `stderr` continuam disponíveis; não extraia valores financeiros de texto livre.

Se uma consulta não retornar resposta estruturada, verifique a versão do JAR instalado. A sincronização desta skill não atualiza o executável Java.

## Segurança

- Não passar senhas, propriedades Java ou variáveis `BISCMD_*` nos argumentos.
- Confirmar alvo, valores e o argumento literal `confirm` antes de operações de escrita.
- Nunca remover o `confirm` exigido pelo BIS10CMD.
- Não alterar lançamentos `BILLS` diretamente; eles pertencem aos fluxos de contas e pagamentos de origem.
- Preservar mensagens funcionais sem expor valores recuperados do KeePassVault.
