# BIS10CMD no MCP interno

A integração executa o cliente Java BISCMD contra a fachada EJB remota do BIS10. O servidor MCP abre a conexão e a sessão automaticamente, resolve separadamente as credenciais JNDI/WildFly e BIS10 no vault compartilhado e injeta os quatro valores somente no ambiente do subprocesso.

Cada perfil define contexto de uso, JAR, diretório de trabalho, host, porta, locale e duas referências KeePass. Um único perfil habilitado é implícito; com vários, `bis10cmd.list_profiles` informa nome e contexto.

## Ferramentas

| Ferramenta | Comportamento |
| --- | --- |
| `bis10cmd.ping` | Valida conexão, autenticação e comunicação com a fachada. |
| `bis10cmd.get_session` | Consulta dados básicos da sessão autenticada. |
| `bis10cmd.list_companies` | Lista empresas permitidas para o usuário. |
| `bis10cmd.select_company` | Valida que uma empresa pode ser selecionada. |
| `bis10cmd.list_accounts` | Lista contas financeiras da empresa. |
| `bis10cmd.list_categories` | Lista categorias financeiras da empresa. |
| `bis10cmd.list_cost_centers` | Lista centros de custo ou o rateio de uma categoria. |
| `bis10cmd.get_account_statement` | Consulta um lançamento pelo ID. |
| `bis10cmd.list_account_statements` | Lista lançamentos com filtros e paginação. |
| `bis10cmd.create_account_statement` | Cria lançamento manual. |
| `bis10cmd.update_account_statement` | Altera lançamento manual. |
| `bis10cmd.create_transfer` | Cria transferência entre contas. |
| `bis10cmd.update_transfer` | Altera transferência. |
| `bis10cmd.delete_account_statement` | Exclui lançamento manual ou transferência. |
| `bis10cmd.analyze_bill_code` | Analisa boleto ou conta de consumo e informa dados extraídos, sugestões e pendências, sem cadastrar. |
| `bis10cmd.create_bill_from_code` | Cadastra conta a pagar com uma parcela pendente, usando o código e os complementos necessários. |
| `bis10cmd.get_bill` | Consulta uma conta e suas parcelas por `bill_id`. |
| `bis10cmd.list_bills` | Lista contas por código, estado e intervalo de vencimento, com paginação. |
| `bis10cmd.list_people` | Localiza sacador, concessionária ou beneficiário por ID, nome ou documento. |
| `bis10cmd.get_bill_request` | Consulta uma tentativa no diário local e reconcilia resultado incerto sem reenviar a inclusão. |

As operações dependentes de empresa recebem `company_id`; o serviço seleciona a empresa dentro do mesmo processo antes de executar o comando. Isso é necessário porque a sessão Java existe somente durante cada chamada MCP.

Os comandos técnicos `facade`, `login` e `connect` não são publicados como ferramentas: o serviço sempre os orquestra internamente. A ajuda nativa foi substituída pelos contratos e descrições das ferramentas. `ping`, sessão, seleção de empresa, cadastros e todas as operações de lançamentos permanecem acessíveis.

As operações de extrato e os lookups legados recebem parâmetros em `args`, como pares nome/valor. As ferramentas de contas a pagar recebem campos tipados, sem `args`. As ferramentas de escrita exigem `confirm=true`; o serviço acrescenta o marcador nativo `confirm` ao comando Java somente depois dessa validação. Propriedades Java, variáveis `BISCMD_*`, credenciais e um marcador `confirm` manual são recusados em `args`.

## Consultas financeiras

`get_account_statement` recebe `statement_id`. `list_account_statements` exige em `args` `accountId`, `dateFrom` e `dateTo`; aceita ainda `categoryId`, `operation`, `type`, `value` ou `valueMin`/`valueMax`, `offset` e `limit`.

As páginas estruturadas são validadas antes de serem devolvidas. Valores monetários permanecem como texto decimal, e `hasMore=true` exige continuar com `nextOffset` e os mesmos filtros. No extrato, lançamentos `BILLS` permanecem consultivos; as ferramentas de extrato alteram somente `MANUAL` e `TRANSFER`. O cadastro de uma conta a pagar usa o fluxo próprio descrito abaixo.

## Contas a pagar por código de barras

A implementação usa o BISClient e a fachada remota atuais. Análise e montagem do `BillVO` ficam no BISCMD; a gravação usa `persistBill`. Não é necessária alteração no servidor BIS10, na UI, no RFW ou no schema. A conta é criada com operação `DEBIT` e uma parcela `WAITING`; a operação não efetua pagamento nem exige uma conta financeira de saída.

O cliente BISCMD 10.1.0 oferece esse protocolo. O nome de execução `BISCMD-10.0.jar` foi preservado para os perfis existentes; confira `client_version: "10.1.0"` e `api_version: 1` em `-bill capabilities`. Instale o JAR e `libs` do mesmo pacote.

1. Execute `analyze_bill_code` com `company_id` e `code`. Aceita código de barras de 44 dígitos, linha digitável de boleto com 47 ou de consumo com 48. Espaços, pontos e hífens são normalizados, preservando zeros à esquerda.
2. Examine `pending_fields`, `warnings`, `duplicates` e as origens dos dados. Consulte `list_people`, `list_categories` e `list_cost_centers` para resolver referências.
3. Envie `create_bill_from_code` com os complementos escolhidos, um `request_id` único para a solicitação e `confirm=true`. O MCP verifica a capacidade do JAR antes de iniciar a escrita, repete a análise e exige resposta estruturada com os IDs persistidos.
4. Confira o resultado em `get_bill`. A resposta contém a conta, suas parcelas e a classificação financeira efetiva.

As duas etapas aceitam `due_date`, `original_value`, `recipient_person_id`, `payee_person_id`, `categories`, `notes`, `entry_date`, `allow_past_due` e os componentes monetários `fine_value`, `discount_value`, `other_value`, `add_value` e `fees_value`. As datas usam ISO; `entry_date` aceita data e hora local, como `2026-11-07T14:30:00`. Valores e percentuais são strings decimais, com até duas casas. O payload MCP é limitado a 128 KiB e enviado como JSON UTF-8 por `stdin`.

Exemplo de complementos de classificação, usando IDs que devem ser obtidos nos lookups da empresa:

```json
{
  "recipient_person_id": 15,
  "payee_person_id": 15,
  "due_date": "2026-12-10",
  "categories": [
    {
      "category_id": 98,
      "value": "125.00",
      "cost_centers": [
        {"cost_center_id": 7, "ratio": "100.00"}
      ]
    }
  ]
}
```

Boleto exige sacador e beneficiário, que podem ser pessoas distintas. Consumo convencional exige a concessionária/sacador e não recebe `payee_person_id`. As sugestões de pessoas usam o histórico, não uma consulta bancária. O primeiro fluxo contempla boleto e consumo convencional; tipos reconhecidos sem contrato de cadastro retornam `supported=false` e os dados extraídos.

O vencimento calculado pela biblioteca aparece em `parser_due_date`; um fator de boleto pode representar ciclos diferentes. Quando a data não puder ser determinada com segurança, informe `due_date` obtido do documento ou do contexto. Para consumo com valor de referência, `encoded_reference_value` não deve ser copiado como reais: forneça `original_value` efetivo em BRL quando esse dado estiver disponível. Sem o complemento necessário, o cadastro permanece pendente. `allow_past_due=true` permite reconhecer explicitamente um vencimento passado.

`list_categories` aceita `bill_type` e `bill_payment_type` para filtrar as categorias compatíveis, por exemplo `MANUAL` e `BOLETO` para boleto. As categorias precisam compor o valor final, respeitando suas operações; o rateio de cada item deve totalizar 100%. A geração automática pode sugerir itens incompletos quando faltar configuração, exigindo complementação.

`list_bills` recebe `code`, `state`, `date_from`, `date_to`, `offset` e `limit` opcionais; as datas filtram o vencimento e incluem os extremos. `list_people` recebe `id`, `name`, `document`, `offset` e `limit`. Os recursos novos usam `has_more` e `next_offset`, com limite máximo de 1000 itens por página. Continue com os mesmos filtros quando `has_more=true`.

### Diário local de tentativas

O workspace externo configurado em `configs/onmyoji-system.toml`, campo `codex.project_directory`, deve estar disponível para cadastrar ou consultar tentativas. O diário fica em `<workspace>/.onmyoji/bis10cmd/requests.sqlite3`, fora do repositório e de `tmp/`. Ele guarda a entrada financeira efetiva, seu hash, estado e resultado sanitizado; não guarda credenciais.

A chave identifica a solicitação por host/porta configurados, empresa e `request_id`, independentemente do nome do perfil. Perfis com o mesmo host/porta compartilham o controle local; aliases DNS distintos não são identificados automaticamente como o mesmo servidor. O conteúdo normalizado deve permanecer igual ao repetir uma chave. Data de lançamento padrão e demais valores efetivos ficam congelados na primeira tentativa.

| Estado | Comportamento |
| --- | --- |
| `SENDING` | A intenção foi registrada antes do envio; outra tentativa do código permanece bloqueada. |
| `SUCCEEDED` | IDs e dados retornados foram validados; repetir a solicitação devolve o resultado conhecido sem outra inclusão. |
| `REJECTED` | Houve rejeição comprovada, sem inclusão; repetir a chave devolve o erro conhecido. Uma solicitação corrigida usa outra chave. |
| `UNKNOWN` | A escrita pode ter ocorrido, mas o resultado não foi confirmado; o código continua bloqueado para novo envio local. |

> [!IMPORTANT]
> Após `outcome_unknown`, consulte `get_bill_request` com a mesma empresa e chave. A reconciliação apresenta candidatos pelo código sem atribuir automaticamente a criação à tentativa. Nenhuma ausência de resultado, expiração de prazo ou chave nova libera o reenvio automático.

Esse controle coordena somente processos que usam o mesmo diário local. Não oferece execução única no servidor nem coordena concorrência com a UI e outros clientes. O uso direto do BISCMD não recebe a proteção do diário. Remover o arquivo ou trocar de workspace perde o vínculo local das tentativas e não cancela uma possível inclusão remota.

Códigos reutilizáveis são identificados, mas o BIS10 atual pode rejeitar um segundo cadastro por suas regras de unicidade. O MCP apresenta essa limitação; não modifica os códigos nem contorna o validador remoto.

Erros BIS10CMD chegam ao MCP com `code`, `message` e dados funcionais sanitizados. Uma resposta sem o recurso esperado ou sem IDs não é tratada como cadastro confirmado. A sequência e os limites de implementação estão no [plano de contas a pagar](plans/bis10cmd-contas-a-pagar.md).

> [!IMPORTANT]
> O JAR e a pasta `libs` permanecem dependências operacionais externas. O MCP armazena somente configuração sem segredos em `configs/mcp/bis10cmd.toml`.
