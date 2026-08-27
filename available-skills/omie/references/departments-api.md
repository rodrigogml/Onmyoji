# API Omie — Departamentos

Endpoint JSON:

```text
https://app.omie.com.br/api/v1/geral/departamentos/
```

O corpo usa `call`, `app_key`, `app_secret` e `param`, com `param` como array de um objeto.

## Métodos

| Operação do wrapper | Método Omie | Parâmetros |
|---|---|---|
| `departments.list` | `ListarDepartamentos` | `pagina`, `registros_por_pagina` |
| `departments.get` | `ConsultarDepartamento` | `codigo` |
| `departments.create` | `IncluirDepartamento` | `codigo`, `descricao` |
| `departments.update` | `AlterarDepartamento` | `codigo`, `descricao` |
| `departments.delete` | `ExcluirDepartamento` | `codigo` |

## Campos

O cadastro documentado inclui `codigo`, `descricao`, `estrutura`, `inativo` e `nivel_totalizador`. O wrapper retorna também quaisquer campos adicionais fornecidos pela API, além de `cCodStatus` e `cDesStatus` quando presentes.

`cCodStatus` igual a `0` indica sucesso nos métodos de escrita; códigos maiores indicam falha descrita por `cDesStatus`.
