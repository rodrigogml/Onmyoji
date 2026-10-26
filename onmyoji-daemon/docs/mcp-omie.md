# Omie no MCP interno

A integração oferece 72 operações de cadastros, financeiro e NF-e sem executar
scripts ou consultar contratos da skill legada. O servidor stdio publica os
parâmetros aceitos e o contexto de uso nas próprias tools; o código não depende
de `available-skills/omie` nem da skill KeePass Vault.

## Configuração e descoberta

Use **MCP → Perfis de vault** para configurar o cofre compartilhado e depois
**MCP → Integrações MCP → Omie** para criar um perfil da integração. O perfil
tem nome escolhido pelo operador, descrição de contexto, estado habilitado e
referência a um perfil de vault e uma entrada KeePass. Os campos padrão são
`username` para `app_key` e `password` para `app_secret`; os valores permanecem
no cofre, enquanto as referências ficam em `configs/mcp/omie.toml`.

Sem perfil habilitado com vault configurado, nenhuma tool Omie é publicada.
Com um perfil, ele é selecionado automaticamente e sua descrição aparece nas
tools. Com vários, `omie.list_profiles` publica nome e contexto de cada perfil;
as operações exigem `profile`.

O setup oferece reutilizar, substituir ou informar cada credencial. Testar a
conexão executa somente uma leitura mínima de departamentos. Editar, testar ou
cancelar uma remoção mantém o operador no perfil; remover volta à sua listagem.

## Contratos e operações

Os nomes publicados anteriormente são preservados, por exemplo
`omie.customers_list`, `omie.payables_update` e
`omie.inbound_nfe_receipts_update_completed`. As 46 escritas exigem o booleano
`confirm: true`. Isso integra o contrato da operação; o servidor já é
pré-aprovado na configuração do Codex.

Cada tool publica somente `params` ou `body`, conforme a operação, com seus
campos aceitos. A validação e normalização também são executadas pelo serviço
antes de ler credenciais ou fazer HTTP. Nenhuma operação aceita endpoint,
método Omie ou credenciais fornecidos pelo agente.

| Família | Comportamento preservado |
| --- | --- |
| Clientes e fornecedores | Cadastro compartilhado; filtros em `clientesFiltro`, detalhe por identificador, páginas de até 50 registros. |
| Tipos de documento | Catálogo sem paginação; consulta por código de até cinco caracteres. |
| Departamentos, projetos, categorias e contas | Campos permitidos, limites e identificadores de cada ação. Paginação de cadastros aceita nomes simplificados ou nativos. |
| Lançamentos e transferências | Converte campos simplificados para `cabecalho`, `detalhes` e `transferencia`; origem e destino diferentes. |
| Contas a pagar/receber | Preserva tipos de documento, baixas, conciliação, distribuição e lotes. Linha digitável somente em contas a pagar, texto não vazio de até 70 caracteres sem reformatação. |
| Recebimentos e NF-e | Converte identificadores, etapa, itens e informações financeiras para os envelopes específicos da Omie. |

Exemplos com um único perfil habilitado:

```json
{"tool":"omie.customers_list","arguments":{"params":{"page":1,"page_size":50,"clientesFiltro":{"cnpj_cpf":"012.345.678-90"}}}}
{"tool":"omie.payables_update","arguments":{"confirm":true,"body":{"codigo_lancamento_omie":123,"codigo_tipo_documento":"FAT"}}}
```

As listagens retornam uma página por chamada em `data`, preservando os campos e
metadados nativos. O agente deve percorrer as demais páginas quando necessário.
CPF/CNPJ e nomes pertencem à listagem de clientes; a consulta individual aceita
somente IDs. Busca parcial de nome exige comparação local das páginas, sem
presumir suporte a curingas pela API.

## Criação idempotente de clientes

`omie.customers_create` aceita CPF/CNPJ numérico ou mascarado, razão social/nome
completo e código de integração opcional. Normaliza o documento e gera um código
estável quando omitido. A validação local verifica formato, não dígitos
verificadores ou situação fiscal.

Antes da inclusão, percorre todo o cadastro sem restringir documento, nome,
situação ou tags, comparando o documento normalizado. Reutiliza um único ativo
com `created: false`; bloqueia duplicidade, cadastro inativo e consulta
incompleta ou inconsistente. Se não houver correspondência, inclui com
`created: true` e exige status de sucesso e ID válido.

> [!IMPORTANT]
> A inclusão não tem retry automático. Em timeout ou resposta incerta, consulte
> antes de repetir com o mesmo código de integração. A verificação anterior à
> inclusão não é uma trava contra alterações concorrentes.

Falhas locais, do cofre, de rede, de HTTP e de negócio geram erros sanitizados;
o conteúdo de exceções externas e as credenciais não são devolvidos ao agente.
Os testes cobrem os contratos das 72 operações e usam respostas simuladas: não
criam títulos, pagamentos, notas nem cadastros em uma conta Omie real.
