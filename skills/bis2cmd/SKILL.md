---
name: bis2cmd
description: Integração com o BISCMD, cliente de linha de comando Java que acessa o BIS2 por fachadas EJB remotas via JNDI/WildFly. Use para consultar, alterar, exportar, importar, validar e manipular dados do BIS2 através dos comandos oficiais do BISCMD, usando perfis locais e credenciais obtidas pelo KeePassVault.
---

# BIS2CMD

Use `scripts/bis2cmd.py` para executar comandos do BISCMD configurados em um perfil INI.

## Configuração

Leia `references/configuration.md` antes de criar ou ajustar perfis.

O perfil define:

- caminho do JAR BISCMD;
- diretório de trabalho;
- executável Java;
- host e porta do WildFly;
- entrada e campos de autenticação no KeePassVault;
- timeout e codificação da saída.

O perfil `turing` utiliza a entrada:

```text
Servidores / Turing:WildFly:BIS2:biscmd
```

Nunca grave usuário ou senha no perfil. O wrapper lê ambos do KeePassVault e injeta os valores somente no ambiente do processo Java.

## Execução

Entrada:

```json
{
  "version": 1,
  "command": "companiesList",
  "args": []
}
```

Invocação:

```text
python scripts/bis2cmd.py --config configs/bis2cmd.toml --profile turing
```

O wrapper inicializa automaticamente `-facade`, executa o comando BISCMD solicitado e converte linhas `BISJSON` e `BISMETA` para JSON.

## Comandos

Consulte `references/commands.md` para os comandos identificados no código-fonte e os respectivos argumentos.

Os comandos oficiais podem consultar ou alterar dados. Antes de executar uma operação externa, fiscal ou destrutiva, confirmar o alvo e os parâmetros. Não remover o `confirm` exigido pelo próprio BISCMD.

## Segurança

- Não passar credenciais em argumentos Java.
- Não imprimir credenciais, variáveis sensíveis ou respostas brutas do KeePassVault.
- Não alterar argumentos, IDs, datas ou filtros silenciosamente.
- Preservar mensagens e erros funcionais do BISCMD sem expor segredos.
- Usar `dryRun` quando a operação oferecer esse modo e a solicitação for de simulação.
