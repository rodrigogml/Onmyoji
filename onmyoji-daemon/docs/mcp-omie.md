# Omie no MCP interno

A integração oferece operações de cadastros, financeiro e NF-e. O servidor
stdio publica os parâmetros aceitos e o contexto de uso nas próprias tools; o
código não depende de scripts de integração nem expõe o cofre como ferramenta.

## Configuração e descoberta

Use **K. KeePass** no menu principal para configurar o cofre compartilhado e depois
**MCP → Omie** para criar um perfil da integração. O perfil
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

Os cadastros e NF-e preservam nomes como `omie.customers_list` e
`omie.inbound_nfe_receipts_update_completed`. O módulo financeiro foi
deliberadamente substituído por `omie.financeiro.*`: ele publica ferramentas
por finalidade, por exemplo `omie.financeiro.lancamentos_conta.listar_por_periodo`
e `omie.financeiro.contas_pagar.registrar_pagamento`. Clientes devem renovar
`tools/list`; os nomes financeiros anteriores não são aliases.

As escritas financeiras exigem `confirmar: true`. Isso integra o contrato da
operação; o servidor já é pré-aprovado na configuração do Codex.

As tools financeiras usam `filtros` nas leituras e `dados` nas escritas. Cada
consulta por período aceita exclusivamente `data_inicial` e `data_final` para
sua finalidade; não use nomes internos da Omie como `dtPagInicial`. Listagens
paginadas recebem `pagina` e `tamanho_pagina` (máximo 50) e retornam `paginacao` com
`proxima_pagina`. Defina `incluir_dados_omie: true` apenas quando os campos
nativos forem indispensáveis para auditoria. Todas as leituras publicadas,
incluindo cadastros e NF-e, aceitam `atualizar_cache: true` para exigir uma
leitura remota elegível; esse controle não ignora os intervalos da Omie.

### Tipos de documento: catálogo sem paginação

`omie.financeiro.tipos_documento.listar` retorna o catálogo completo em uma
única chamada. Com um único perfil habilitado, use argumentos `{}`. Com vários
perfis, informe somente `profile`; `incluir_dados_omie` continua opcional para
auditoria. Não envie `pagina`, `tamanho_pagina` nem `filtros`.

O MCP chama `PesquisarTipoDocumento` com `codigo` vazio, conforme o
[contrato oficial de tipos de documento](https://api.omie.com.br/docs/apis/geral/tiposdoc).
O retorno normalizado contém os tipos em `dados`, sem `paginacao` nem próxima
página. O catálogo não recebe valores padrão de paginação internamente.

Consultas individuais terminadas em `.consultar` também não recebem
`pagina` nem `tamanho_pagina`. A consulta para conciliação reúne suas páginas
internamente. Esses métodos recusam parâmetros de paginação antes de acessar
credenciais ou HTTP, com orientação para removê-los; reduzir o tamanho de
100 para 50 não é uma correção para uma operação sem paginação.

### Identificadores de consultas e exclusões

Cada `.consultar` exige exatamente um identificador em `filtros`. As exclusões
financeiras usam os mesmos identificadores em `dados`, com `confirmar: true`.
O contrato publicado e a validação seguem o recurso consultado:

| Recurso | Identificador aceito |
| --- | --- |
| Categorias | `codigo`: texto de até 20 caracteres. |
| Departamentos | `codigo`: texto de até 40 caracteres, preservando zeros à esquerda. |
| Contas correntes e projetos | `id`: inteiro positivo; ou `codigo_integracao`: texto de até 20 caracteres. |
| Contas a pagar e a receber | `id`: inteiro positivo; ou `codigo_integracao`: texto de até 60 caracteres. |
| Lançamentos de conta corrente | `id`: inteiro positivo; ou `codigo_integracao`: texto de até 20 caracteres. |

Em `lancamentos_conta.consultar`, `filtros.id` corresponde a `nCodLanc`, o
identificador gerado pela Omie, e `filtros.codigo_integracao` corresponde a
`cCodIntLanc`, definido pela integração na inclusão. Uma integração pode conter
somente dígitos; por isso, a fachada usa a chave explícita e preserva seu valor.
O campo genérico `codigo` não é aceito para lançamentos. Campos ambíguos, IDs
em texto, valores vazios ou múltiplos identificadores são recusados antes do
acesso ao perfil ou à API, com orientação sobre os campos corretos.
Consultas individuais preservam o objeto completo em `dados`, incluindo suas
listas internas. Um rateio `departamentos` vazio ou preenchido não substitui o
lançamento retornado nem indica ausência de resultado.

Exemplo de conferência de um lançamento existente, usando um ID fictício:

```json
{"tool":"omie.financeiro.lancamentos_conta.consultar","arguments":{"filtros":{"id":9000000001},"incluir_dados_omie":true}}
```

### Projetos: nome, ID e integração

`omie.financeiro.projetos.listar` aceita `filtros.nome`: texto não vazio de até
100 caracteres, enviado como `nome_projeto` à Omie. Sem esse filtro, a operação
lista os projetos do perfil. O MCP preserva em `dados` os campos do cadastro:

| Campo retornado | Uso |
| --- | --- |
| `codigo` | ID numérico: informe em `filtros.id` de `projetos.consultar` ou em `projeto_id`/`project_id` nas operações financeiras e de NF-e. |
| `codInt` | Código de integração: informe em `filtros.codigo_integracao` somente quando esse valor estiver preenchido e confirmado no cadastro. |
| `nome` | Nome do projeto: pode conter códigos de negócio como `STRB15`, independentemente de `codInt`. |

Para localizar um projeto conhecido pelo nome:

1. Liste com `filtros.nome` e percorra `paginacao.proxima_pagina`, conferindo os
   nomes retornados. O MCP não define correspondência parcial nem suporte a
   curingas para o filtro da Omie. Para buscar por parte do nome sem depender
   dessa semântica, percorra as páginas sem filtro e compare localmente.
2. Confirme um único projeto e use seu `codigo` numérico. Em caso de
   ambiguidade, confirme o cadastro correto antes de classificar uma operação.
3. Se precisar do detalhe, consulte com `filtros.id`. Não deduza o código de
   integração pelo formato do nome.

Exemplo fictício com um único perfil, supondo que a listagem confirmou
`{"codigo":789,"codInt":"","nome":"STRB15"}`:

```json
{"tool":"omie.financeiro.projetos.listar","arguments":{"filtros":{"nome":"STRB15"},"pagina":1,"tamanho_pagina":50}}
{"tool":"omie.financeiro.projetos.consultar","arguments":{"filtros":{"id":789}}}
```

> [!IMPORTANT]
> Uma falha "Projeto não cadastrado" na consulta significa que o projeto não
> foi encontrado pelo identificador informado. Ela não comprova que o projeto
> inexiste por nome nem que `codInt` está vazio; confirme esses dados na listagem.
> O erro `project_not_found` orienta a descoberta por nome e preserva o
> diagnóstico remoto, sem repetir automaticamente a consulta que recebeu essa
> falha. Confira o campo indicado antes de mudar o identificador.

Listagens financeiras recusam filtros não publicados com `invalid_parameter`
antes de acessar credenciais ou HTTP. Use `filtros.nome` para projetos; a chave
nativa `nome_projeto` não pertence ao contrato público. Contas correntes,
categorias e departamentos aceitam apenas `filtros` vazio ou omitido.

Referência: [contrato oficial de projetos](https://app.omie.com.br/api/v1/geral/projetos/).

### Criação e alteração de lançamentos

`omie.financeiro.lancamentos_conta.criar` exige em `dados`: `codigo_integracao`,
`conta_corrente_id`, `data`, `valor`, `categoria_codigo` e `tipo_documento`.
A obrigatoriedade da integração é uma regra do MCP e aparece no schema da tool.
O código deve ser um texto estável de até 20 caracteres; guarde-o para consultar
o resultado da inclusão. O ID é gerado pela Omie e não é um campo de criação.

A alteração geral aceita atualização parcial: informe `id` ou
`codigo_integracao` e pelo menos um campo editável. Os dois identificadores
podem ser enviados juntos. O MCP envia somente os campos solicitados.
Para mudar apenas uma observação, por exemplo, informe o identificador e
`observacao`; não reenvie valor, conta, data, categoria ou tipo de documento.

**Rateio por departamentos de um registro existente usa
`definir_departamentos`.** As ferramentas
`lancamentos_conta.alterar`, `contas_pagar.alterar` e `contas_receber.alterar`
não publicam nem aceitam `dados.departamentos`. Se esse campo chegar à chamada,
o MCP recusa antes do HTTP e indica a ferramenta específica da mesma família.
A separação evita que um sucesso de processamento da edição geral seja
interpretado como confirmação de que o rateio foi persistido.

Em títulos a pagar e a receber, `dados.conta_corrente_id` é traduzido para
`id_conta_corrente`: a **conta prevista do título**. Essa alteração não troca a
conta usada por um pagamento ou recebimento já executado. Para ratear um título,
use a ferramenta específica sem reenviar esse campo.

Criações e inclusões ou alterações unitárias (`incluir_ou_alterar`) que
aceitam `departamentos` continuam disponíveis, incluindo a criação de uma
transferência. Quando o rateio é enviado, a operação confere sua persistência
antes de apresentá-lo como confirmado. Lotes não aceitam `departamentos` nos
itens: crie os registros primeiro e depois defina o rateio pelos IDs retornados.
Um aceite de lote não confirma o estado final de cada distribuição.

Cada escrita publica apenas os campos que consegue traduzir. Ausência ou
invalidade de campos obrigatórios gera erro com o nome público, por exemplo
`dados.codigo_integracao`, antes de acessar credenciais ou HTTP. Os nomes
financeiros legados não são atalhos de execução: chamadas a esses nomes são
recusadas com orientação para a fachada publicada.

### Pagamentos e recebimentos com desconto

O desconto pertence à própria operação de baixa:

| Ferramenta MCP | Método Omie |
| --- | --- |
| `omie.financeiro.contas_pagar.registrar_pagamento` | `LancarPagamento` |
| `omie.financeiro.contas_receber.registrar_recebimento` | `LancarRecebimento` |

Essas ferramentas exigem `confirmar: true` e recebem em `dados` o identificador
do título (`id` ou `codigo_integracao`), `conta_corrente_id`, `valor` e `data`.
O contrato do MCP mantém `valor` como número finito maior que zero. A data usa
o formato `dd/mm/aaaa`.

Os campos monetários opcionais são `desconto`, `juros` e `multa`: números
finitos maiores ou iguais a zero. Informe cada campo somente quando precisar
registrá-lo; sua omissão não acrescenta um valor padrão à requisição. Zero
explícito é aceito e transmitido.

O MCP envia `valor` sem transformação e transmite os ajustes em campos nativos
separados, com os mesmos nomes. Não soma o desconto ao valor nem recalcula
automaticamente a baixa. Percentuais e aliases como `valor_desconto`,
`desconto_valor` ou `percentual_desconto` não pertencem ao contrato: o campo
canônico é `desconto`, em dinheiro.

Exemplo fictício da forma da chamada, com os valores informados explicitamente:

```json
{"tool":"omie.financeiro.contas_pagar.registrar_pagamento","arguments":{"confirmar":true,"dados":{"id":12,"conta_corrente_id":34,"valor":950,"desconto":50,"data":"17/11/2026"}}}
```

Para uma conta a receber, use `registrar_recebimento` com o mesmo formato de
dados. Após atualizar a instalação, reinicie os processos MCP e renove
`tools/list` para que o cliente descubra os campos publicados.

Observe no retorno `codigo_baixa`, `liquidado` e `valor_baixado`. O aceite da
requisição não comprova, sozinho, a quitação integral nem o saldo final.
Consulte o título por `contas_pagar.consultar` ou `contas_receber.consultar`
e confira os movimentos vinculados para confirmar os valores e a situação da
baixa.

> [!IMPORTANT]
> Não repita a baixa para fazer essa conferência. Guarde o identificador
> retornado e consulte o estado atual antes de decidir qualquer nova escrita.

Referências: [baixa de conta a pagar pela API](https://ajuda.omie.com.br/pt-BR/articles/8257476-baixando-uma-conta-a-pagar-via-api)
e [baixa de conta a receber pela API](https://ajuda.omie.com.br/pt-BR/articles/8255357-baixando-uma-conta-a-receber-via-api).

### Categorias: substituir o rateio de uma conta a pagar

`omie.financeiro.contas_pagar.definir_categorias` solicita a substituição da
distribuição completa de categorias de um título existente e confere o título
e suas baixas. Use essa ferramenta para consolidar categorias antigas, inclusive em
um título pago; `contas_pagar.alterar` com `categoria_codigo` altera somente
o campo de categoria única e não confirma a remoção de um rateio anterior.
Categorias e departamentos são classificações diferentes: a distribuição
`distribuicao` das ferramentas de departamentos não substitui `categorias`.
Após atualizar a instalação, reinicie os processos MCP existentes e renove
`tools/list` para descobrir a operação.

A chamada exige `confirmar: true` e, em `dados`, exatamente um entre `id`
(inteiro positivo) e `codigo_integracao` (texto não vazio, até 60 caracteres),
mais `categorias`. Cada item contém somente `codigo` (texto não vazio de até
20 caracteres) e `percentual` (número finito, positivo, até 100). Os códigos
não se repetem e os percentuais somam 100. A lista não pode ser vazia: para
eliminar um rateio antigo, informe a categoria que deve receber 100%.
Não envie valores, nomes de categorias nem os campos nativos da Omie.

Exemplo fictício, com um único perfil habilitado:

```json
{"tool":"omie.financeiro.contas_pagar.definir_categorias","arguments":{"confirmar":true,"dados":{"id":9000000002,"categorias":[{"codigo":"2.01.03","percentual":100}]}}}
```

A ferramenta resolve o título, identifica suas baixas por `ListarMovimentos`
e percorre todas as páginas necessárias, com até 50 registros por página.
Usa `nCodTitulo` para manter a identidade do título, `cTpLancamento: BXCP`
para identificar baixas de contas a pagar e a consulta `CP` para conferir o
registro financeiro do título e o resumo do pagamento. Não exige que o agente
descubra previamente o ID de cada baixa. Cada baixa identificada é conferida
por seu `nCodBaixa`, sem confundi-lo
com o identificador do título ou do movimento de conta corrente.
Quando `ListarMovimentos` omite `categorias` de uma baixa `BXCP`, ou a retorna
como `null` ou lista vazia, a conferência consulta o lançamento vinculado por
`ConsultaLancCC`, usando `nCodMovCC`. Lê o rateio em `detalhes.aCodCateg` e
valida o ID do movimento, o título de origem, a conta efetiva e o valor da
baixa. Dados de conciliação retornados também são confrontados. Essa consulta
não edita o lançamento nem refaz o pagamento.
Se o rateio do lançamento contiver somente `nValor`, a comparação usa os
valores por código e o líquido da baixa, com tolerância de R$ 0,01. Nessa
representação, a comparação usa valores sem reconstruir percentuais a partir
dos centavos. A ausência da coleção `aCodCateg` continua
inconclusiva: a categoria escalar não comprova a distribuição completa.

As consultas usam a data de emissão efetiva do título para evitar a janela
padrão de 30 dias da Omie. Se essa data estiver ausente ou inválida, ou se a
soma das baixas não conferir com o resumo financeiro do título, a operação
recusa a escrita em vez de assumir que encontrou todos os pagamentos.
O resumo deve ser consistente entre valor pago, desconto, juros, multa e
líquido; um título indicado como liquidado deve ter saldo aberto zero.
Nas baixas `BXCP`, os campos `detalhes.nJuros`, `detalhes.nMulta` e
`detalhes.nDesconto` omitidos pela Omie são lidos como zero. Essa regra vale
somente para a ausência da chave; `null`, texto vazio e valores inválidos
continuam impedindo a conferência. O resumo `CP`, `nValorMovCC` e os demais
valores essenciais continuam obrigatórios.
A comparação anterior e posterior considera ausência e zero equivalentes
somente nesses três campos opcionais. Mudanças para valores positivos e
valores efetivos de desconto, juros ou multa continuam sendo conferidos,
inclusive pela soma das baixas em relação ao resumo financeiro.
A baixa deve informar `dDtConcilia`: vazio ou `null` significa não conciliada;
uma data preenchida exige também hora e usuário de conciliação.

Antes e depois da escrita, as consultas exigem atualização remota e respeitam
o cache compartilhado, a elegibilidade e o cooldown da Omie. A operação envia
uma única chamada `AlterarContaPagar`, com o ID confirmado e a lista completa
`categorias`. Quando há uma única categoria, envia também `codigo_categoria`
com o mesmo código e exige que o campo persistido corresponda à solicitação.
Não reenvia condições de pagamento, não cancela nem refaz baixas.
Depois da gravação, compara a distribuição efetivamente consultada no título,
no registro financeiro `CP` e em todas as baixas, além de conferir identidades,
conta, valores, desconto, juros, multa, datas e conciliação presentes nos
registros necessários.
Respostas incompletas ou sem vínculo comprovado impedem uma confirmação de
sucesso; uma falha na pré-consulta impede a escrita.
Na pós-conferência, a ausência de `categorias` no título não encerra a
investigação: o MCP também consulta o registro `CP` e todas as baixas `BXCP`
identificáveis, sem enviar outra escrita. Assim, uma coleção ausente não
oculta um rateio antigo que ainda possa ser observado nos outros registros.

O resultado separa `verificacao.titulo_confirmado`,
`verificacao.titulo_financeiro_confirmado`, `verificacao.baixas_confirmadas`
e `verificacao.campos_preservados`. Retorna `categorias` do título,
`categorias_financeiro` do registro `CP` e as categorias e identificadores de
cada baixa observada. O aceite da Omie, sozinho, não confirma a distribuição
nem sua propagação. Os campos de `verificacao` continuam booleanos: `true`
significa confirmado e `false` significa não confirmado. Nos erros, as
coleções `categorias_titulo`, `categorias_financeiro` e
`baixas[i].categorias` usam `null` quando a conferência ficou inconclusiva.
Uma coleção ausente não significa distribuição vazia nem remoção confirmada
de categorias. `verificacao.categorias_financeiro_disponiveis` distingue a
presença da distribuição no registro `CP`. Se esse endpoint não fornecer a coleção, a
classificação pode ser conferida no título e em todas as baixas sem exigir
que `CP` repita a mesma informação. O MCP não copia categorias do título para
apresentá-las como uma leitura de `CP`.
Cada baixa informa `fonte_categorias`: `financial_movements_list` indica a
leitura no movimento financeiro; `account_transactions_get` indica a consulta
ao lançamento vinculado. Na auditoria, `dados_omie.lancamentos` contém os
lançamentos consultados cujo vínculo foi validado.
As falhas de conferência são reunidas em `details.falhas_conferencia`, como
objetos com `code`, `message` e `details`. O diagnóstico de uma coleção
ausente identifica `field: categorias`, a operação e, em movimentos
financeiros, `tipo_movimento: CP` ou `BXCP` e `id_baixa` quando aplicável.
`collection_issue` distingue chave ausente (`missing`), nulo explícito
(`null`), lista vazia (`empty`) e tipo incompatível (`invalid_type`).
Esse contexto permite distinguir a origem da conferência inconclusiva.
Use
`incluir_dados_omie: true` apenas quando precisar dos registros nativos de
auditoria, sem credenciais. O retorno público informa zero para os campos
opcionais omitidos nas baixas; a auditoria nativa preserva as omissões do
retorno da Omie. Quando solicitado, um erro de pós-conferência também inclui
em `details.dados_omie` os registros posteriores disponíveis em `registro`,
`financeiro` e `baixas`; a requisição permanece em
`details.requisicao_omie`. Esses dados permitem a análise sem repetir a
gravação. Uma consulta que falhou continua sendo registrada como falha; a
auditoria não inventa o registro ausente.

| Situação | Resultado do MCP |
| --- | --- |
| Pré-consulta incompleta ou inconsistente | `category_allocation_preflight_failed`; nenhuma escrita enviada. |
| Pagamento ou outro campo preservado difere entre as consultas | `category_allocation_side_effect_detected`, com prioridade sobre o resultado das categorias. |
| Título consultado mantém rateio diferente do solicitado | `category_allocation_not_persisted`. |
| Título confirmado, mas rateio do registro financeiro `CP` ou de uma baixa não corresponde | `category_allocation_propagation_not_confirmed`. |
| Consulta posterior falha ou não permite concluir a conferência | `category_allocation_verification_failed`. |

Uma divergência efetivamente observada no título resulta em
`category_allocation_not_persisted` mesmo quando a conferência de `CP` ou de
uma baixa fica inconclusiva. As falhas adicionais continuam disponíveis no
diagnóstico agregado; não substituem uma divergência já comprovada por uma
mensagem genérica de coleção ausente.

Os erros de pré e pós-conferência informam `escrita_processada` e
`retryable: false`; os posteriores à escrita trazem também as confirmações
já obtidas em `verificacao`. Quando a gravação já foi processada, consulte o
estado atual antes de qualquer decisão;
não repita a escrita para tentar forçar a propagação. A ferramenta não faz
reversão nem compensação automática. Diferenças entre os estados consultados
também podem decorrer de alteração concorrente no ERP.
Falhas com `outcome_unknown` exigem essa mesma consulta: o retorno não permite
afirmar se a escrita foi concluída.

Quando a conferência numérica de uma resposta remota falha, o diagnóstico
mantém `reason: invalid_number` e identifica o campo em `details.field`, como
`valor_documento`, `resumo.nJuros`, `detalhes.nValorTitulo` ou
`detalhes.nDesconto`. O caminho também pode apontar um item do rateio, como
`categorias[0].percentual`, `categorias[0].valor`,
`categorias[0].nDistrPercentual` ou `categorias[0].nDistrValor`.
`operation: payables_get` identifica campos consultados no título;
`operation: financial_movements_list`, com `tipo_movimento: CP` ou `BXCP`,
identifica o registro financeiro ou a baixa. Os demais metadados distinguem
a causa:

| Metadado | Valores |
| --- | --- |
| `number_issue` | `missing` para campo ausente; `null` para nulo explícito; `invalid_type` para tipo incompatível; `invalid_value` para conteúdo numérico inválido. |
| `received_type` | `missing`, `null`, `boolean`, `number`, `string`, `array`, `object` ou `unsupported` para tipos fora do contrato JSON. |

Esse diagnóstico informa presença e tipo, sem reproduzir o valor recebido nem
registros nativos sensíveis. Na pré-conferência, mantém
`escrita_processada: false` e impede a gravação; na pós-conferência, informa
`escrita_processada: true` e não repete a escrita. A conversão de ausência em
zero fica restrita aos três campos opcionais de baixas descritos acima;
`null` e ausências em outros campos numéricos mantêm esse diagnóstico.
O diagnóstico, sozinho, não comprova que o número esteja incorreto no ERP.

> [!IMPORTANT]
> A API publica o rateio de categorias no título, mas não garante que toda
> alteração de um título pago atualize as baixas existentes. Esta operação
> só confirma o que conseguiu consultar depois da escrita; uma divergência
> deve ser avaliada antes de considerar qualquer reversão do pagamento.

Em uma validação real, `AlterarContaPagar` retornou processamento bem-sucedido,
mas leituras posteriores ainda mostraram o rateio antigo no título e na
baixa, com pagamento, desconto e conciliação preservados. Essa observação
comprova a ausência de efeito naquele caso; não estabelece uma proibição
geral para alterar categorias de títulos pagos.
O contrato oficial publica `valor` e `percentual` nos itens de `categorias`,
sem esclarecer que `valor` seja obrigatório para essa alteração. Acrescentar
valores monetários à requisição não é uma solução comprovada nem altera o
contrato público desta ferramenta, que continua recebendo código e percentual.
Para esse tipo de divergência, use as consultas já obtidas para validar o
comportamento com o suporte Omie ou em um ambiente de teste da API. A interface
do ERP também pode ser utilizada se permitir o ajuste. Não repita a escrita
para tentar forçar a propagação, nem cancele a baixa automaticamente.

#### Conferir categorias sem uma nova gravação

Após uma escrita processada cujo rateio não foi confirmado, use
`omie.financeiro.contas_pagar.conferir_categorias`. Essa ferramenta é somente
leitura: consulta título, resumo financeiro e baixas, incluindo o lançamento
vinculado quando a lista de movimentos omitir sua distribuição. Ela não
chama `AlterarContaPagar` nem altera baixa, desconto ou conciliação.

Informe `filtros` com exatamente um entre `id` e `codigo_integracao` e a lista
completa `categorias_esperadas` no nível principal. A lista usa os mesmos
códigos e percentuais da definição de categorias. Não envie `confirmar`,
`dados`, `pagina` nem `tamanho_pagina`. `incluir_dados_omie` e
`atualizar_cache` são controles booleanos opcionais; a conferência exige
leituras remotas e respeita o cooldown da Omie.

Exemplo fictício para conferir uma categoria única, sem escrever:

```json
{"tool":"omie.financeiro.contas_pagar.conferir_categorias","arguments":{"filtros":{"id":9000000002},"categorias_esperadas":[{"codigo":"2.01.03","percentual":100}],"incluir_dados_omie":true,"atualizar_cache":true}}
```

O retorno informa `conforme_esperado`, as categorias observadas,
`baixas[i].fonte_categorias` e eventuais falhas em `falhas_conferencia`.
Uma baixa cujo rateio só contém valores retorna `codigo` e `valor`, sem
inventar `percentual`. Se a coleção de `CP` estiver ausente,
`verificacao.categorias_financeiro_disponiveis` é `false`; a confirmação do
título e de todas as baixas pode ser suficiente para `conforme_esperado`,
sem exigir `titulo_financeiro_confirmado: true` para uma coleção não fornecida.

Os valores de pagamento, desconto e conciliação são os observados nas
consultas. Essa leitura não inclui `verificacao.campos_preservados`: não
comprova preservação entre dois instantes nem substitui a comparação anterior
e posterior feita por uma operação de escrita. Leituras financeiras
essenciais inconclusivas retornam erro de conferência, sem escrita.

Na validação da versão `2026.11.17.10`, o título permaneceu com o rateio
anterior após `AlterarContaPagar`. Para acompanhar esse caso, a próxima ação é
`conferir_categorias`, sem uma nova chamada de definição. O payload de escrita
continua baseado no contrato já publicado; a conferência por outro endpoint
não presume que uma nova tentativa de gravação resolverá a divergência.

Referências: [contas a pagar](https://app.omie.com.br/api/v1/financas/contapagar/),
[movimentos financeiros](https://app.omie.com.br/api/v1/financas/mf/) e
[lançamentos de conta corrente](https://app.omie.com.br/api/v1/financas/contacorrentelancamentos/).

### Departamentos: cadastro e rateio são responsabilidades diferentes

`omie.financeiro.departamentos.listar` e `.consultar` identificam o cadastro
do centro de custo. Alterar esse cadastro não atribui o departamento a um
lançamento ou título. Para distribuir o valor de um registro já existente,
use a ferramenta correspondente:

| Objeto que precisa ser rateado | Ferramenta MCP | Contrato Omie utilizado |
| --- | --- | --- |
| Lançamento direto de conta corrente | `omie.financeiro.lancamentos_conta.definir_departamentos` | `AlterarLancCC`, com `departamentos`. |
| Conta a pagar | `omie.financeiro.contas_pagar.definir_departamentos` | `AlterarContaPagar`, com `distribuicao`. |
| Conta a receber | `omie.financeiro.contas_receber.definir_departamentos` | `AlterarContaReceber`, com `distribuicao`. |

Essas ferramentas são operações semânticas do MCP, implementadas sobre as
APIs publicadas pela Omie. Não existe um novo endpoint nativo inventado para
rateio de baixas ou de contas a pagar.

Cada chamada exige `confirmar: true` e, em `dados`, **exatamente um** entre
`id` e `codigo_integracao`, mais a lista completa `departamentos` desejada.
As ferramentas de títulos também aceitam `lancamento_conta_id`, opcional,
para conferir um movimento vinculado. Campos de valor, conta, data, categoria,
projeto, fornecedor, documento ou conciliação não pertencem a essa operação.
A ferramenta compara os dados relevantes antes e depois para detectar
alterações fora do rateio solicitado.
Depois de resolver um código de integração, a escrita e a conferência usam
o ID numérico identificado, mantendo o mesmo alvo durante a operação.

O formato público do rateio é único:

```json
{"departamentos":[{"codigo":"DEP-01","percentual":100}]}
```

`codigo` é **texto**, inclusive quando contém apenas dígitos, e deve ser o
código verdadeiro do cadastro. `percentual` é numérico, positivo e até 100.
Com vários departamentos, os percentuais somam 100 e cada código aparece uma
única vez. A lista **substitui o rateio completo**; `departamentos: []` solicita
removê-lo, com a mesma conferência posterior. Não envie nomes de departamento
como identificadores nem os campos nativos `cCodDep`, `nPerDep` ou `nValDep`.

O MCP transmite `cCodDep` e `nPerDep`; a Omie calcula o valor distribuído.
Em lançamentos, enviar percentual e valor simultaneamente pode provocar
`SOAP-ENV:Client-727`. Formatos legados por valor continuam restritos aos
normalizadores internos quando houver os dados necessários à validação;
não são formatos adicionais da tool pública.

Exemplos fictícios, com um único perfil:

```json
{"tool":"omie.financeiro.lancamentos_conta.definir_departamentos","arguments":{"confirmar":true,"dados":{"id":9000000001,"departamentos":[{"codigo":"DEP-01","percentual":100}]}}}
{"tool":"omie.financeiro.contas_pagar.definir_departamentos","arguments":{"confirmar":true,"dados":{"id":9000000002,"lancamento_conta_id":9000000003,"departamentos":[{"codigo":"DEP-01","percentual":100}]}}}
```

### Baixas vinculadas e confirmação do resultado

A ferramenta de conta corrente consulta o lançamento antes de escrever.
Quando encontra vínculo com uma conta a pagar ou receber em
`diversos.nCodLancCP`/`nCodLancCR`, ou origem de baixa `BAXP`/`BAXR`, recusa a
alteração direta e orienta a chamada ao título correspondente. O diagnóstico
inclui o ID e os argumentos sugeridos quando o vínculo é conhecido.
**Essa restrição é uma decisão do contrato MCP**, não uma alegação de que a
Omie proíba todo rateio em baixas. Ela evita tentar corrigir um registro
derivado como se fosse um lançamento independente.

O MCP não redireciona uma escrita ao título automaticamente: um título pode
ter mais de uma baixa, e a mudança da distribuição da origem pode afetar
outros movimentos. Use o título e o movimento corretos na chamada indicada.
Nas ferramentas de títulos, `lancamento_conta_id` faz a validação desse vínculo
**antes da gravação**, além da conferência de propagação depois dela.
Um ID de movimento sem vínculo correspondente é recusado antes de alterar o
título.

A gravação do título envia somente seu identificador e `distribuicao`; não
cancela, recria ou refaz pagamento ou recebimento. O status `PAGO` e a origem
`BARP` não são tratados como proibições automáticas. `BARP` significa conta a
pagar importada por código de barras; `BAXP` significa pagamento de conta a
pagar. São informações distintas.

O resultado de rateio apresenta `alvo`, `departamentos` **observados**,
`verificacao` e `versao_mcp`. `verificacao.rateio_confirmado` indica a
comparação da distribuição persistida; `verificacao.campos_preservados`
informa a conferência dos demais campos relevantes. O aceite nativo é
exibido somente quando `incluir_dados_omie: true` for solicitado para auditoria.
O texto `cDesStatus` ou `descricao_status` da API não substitui essas
verificações.
`verificacao.campos_verificados` explicita os campos comparados entre o estado
inicialmente consultado e a conferência. A pré-consulta pode aproveitar o cache
válido; diferenças não provam que o MCP causou uma alteração externa. Respostas
sem os dados financeiros mínimos são recusadas antes de escrever.
Para auditoria, `dados_omie.requisicao` contém o método e os parâmetros nativos
efetivamente enviados, sem credenciais. Nenhum corpo HTTP autenticado é gravado
em log por esse fluxo.

| Situação | Resultado do MCP |
| --- | --- |
| Rateio e preservação dos dados conferidos | Retorna o alvo, os departamentos observados e a verificação confirmada. |
| Consulta concluída, mas rateio diferente do solicitado | Retorna `allocation_not_persisted`. |
| Consulta de confirmação falhou ou trouxe resposta inconclusiva | Retorna `write_verification_failed`, com o prazo seguro quando disponível. |
| Outros campos relevantes diferem entre as consultas | Retorna `allocation_side_effect_detected`, com os campos observados; não faz compensação automática. |
| Título confirmado, mas movimento vinculado divergente ou inconclusivo | Retorna `allocation_propagation_not_confirmed`, descrevendo o resultado parcial; não declara a tarefa inteira concluída. |

O retorno da conferência não repete a escrita automaticamente. Quando houve
processamento, o diagnóstico mantém essa informação; consulte o estado atual
antes de decidir qualquer nova gravação. Se a origem foi confirmada e somente
a propagação continua pendente, não reenvie a definição do título para tentar
forçar uma atualização do movimento. As consultas seguem o cache compartilhado,
a invalidação por escrita e a janela remota de elegibilidade.

> [!IMPORTANT]
> O catálogo atual da Omie descontinuou em 21/10/2026 os métodos nativos
> `IncluirDistribuicaoDepartamento`, `AlterarDistribuicaoDepartamento` e
> `ExcluirDistribuicaoDepartamento`. As antigas tools MCP
> `contas_receber.criar_rateio_departamento`, `.alterar_rateio_departamento`
> e `.excluir_rateio_departamento` deixam de ser publicadas e não são atalhos
> de execução. Use `contas_receber.definir_departamentos` com a lista completa;
> para remover o rateio, envie `[]`.

Referências: [lançamentos de conta corrente](https://api.omie.com.br/docs/apis/financas/contacorrentelancamentos),
[contas a pagar](https://api.omie.com.br/docs/apis/financas/contapagar),
[contas a receber](https://api.omie.com.br/docs/apis/financas/contareceber),
[descontinuações do catálogo](https://api.omie.com.br/docs/novidades)
e [rateio na conciliação](https://ajuda.omie.com.br/pt-BR/articles/5222014-atualizacao-em-lote-de-lancamentos-na-conciliacao-de-contas-correntes).

### Conta corrente e período

Use `omie.financeiro.lancamentos_conta.listar_por_periodo` para consultar os
lançamentos de uma conta no período do extrato. Informe `conta_corrente_id`,
`data_inicial` e `data_final` dentro de `filtros`; obtenha o ID da conta em
`omie.financeiro.contas_correntes.listar`. As datas usam `dd/mm/aaaa` e o ID é
um inteiro positivo. O filtro de conta também está disponível nas consultas
de lançamentos por inclusão e alteração.

A operação Omie `ListarLancCC` aceita o período, mas **não aceita `nCodCC`
como filtro da requisição**. A conta aparece em
`listaLancamentos[].cabecalho.nCodCC` no retorno. Quando o agente informa
`conta_corrente_id`, o MCP consulta todas as páginas do período, filtra essa
conta e só depois aplica `pagina` e `tamanho_pagina`. Os totais e
`proxima_pagina` correspondem apenas à conta escolhida. Mesmo com
`incluir_dados_omie: true`, a resposta contém somente os lançamentos da conta
e da página solicitadas; os campos nativos são preservados, com a paginação
recalculada. Sem filtro de conta, cada chamada continua consultando somente
uma página da Omie.

As páginas nativas bem-sucedidas usam o [cache compartilhado](#consumo-cache-e-cooldown)
das leituras Omie. Páginas públicas seguintes e outras contas no mesmo período
podem reutilizar essas respostas dentro da validade do cache, sem refazer as
requisições nativas. A conferência da população e a filtragem da conta continuam
obrigatórias: uma página armazenada não representa, sozinha, todo o período.

Essa filtragem exige percorrer o período completo na primeira consulta não
armazenada; use o período necessário à tarefa. Uma página remota sem lançamentos da conta
não encerra a busca. Se houver erro, conta ausente em um registro ou paginação
inconsistente, o MCP falha explicitamente em vez de apresentar uma consulta
parcial como completa. A validação aceita contadores inteiros representados
como texto decimal e a convenção de resposta vazia com `nPagina=0` somente
quando a primeira página não contém registros e todos os totais são zero.
Uma página realmente diferente da solicitada continua sendo recusada, com
diagnóstico sanitizado `reason`, `requested_page` e, quando numérico,
`returned_page` para identificar a divergência sem expor o corpo remoto.
**Se a listagem falhar, não afirme ausência de lançamentos ou duplicidade.**
A consulta de título sem baixa não comprova ausência de lançamento direto,
transferência ou movimento independente no extrato.

`omie.financeiro.movimentos.listar_por_pagamento` consulta outra operação,
`ListarMovimentos`, que aceita o filtro de conta diretamente. Já
`omie.financeiro.movimentos.listar_para_conciliacao` exige conta e período de
pagamento, reúne todas as páginas retornadas e não recebe `pagina` nem
`tamanho_pagina`. Esses métodos preservam a população de movimentos da Omie;
não substituem automaticamente a consulta de lançamentos do extrato. A Omie
documenta uma janela padrão de emissão de 30 dias para MovimentosFinanceiros
quando os filtros de emissão são omitidos; não presuma cobertura temporal
idêntica entre os dois endpoints.

Os nomes `omie.financeiro.lancamentos_conta.listar_por_pagamento` e
`omie.financeiro.movimentos.listar_por_periodo` não são publicados. Para
lançamentos do extrato use `lancamentos_conta.listar_por_periodo`; para
movimentos por pagamento use `movimentos.listar_por_pagamento`.

Contratos de referência:
[ListarLancCC](https://api.omie.com.br/docs/apis/financas/contacorrentelancamentos),
[ListarMovimentos](https://api.omie.com.br/docs/apis/financas/mf) e
[limites e regras de consulta da Omie](https://ajuda.omie.com.br/pt-BR/articles/8112984-limites-de-consumo-da-api-do-omie).

As consultas `omie.financeiro.movimentos.listar_por_*` retornam, em cada item,
`conciliacao.conciliado`. O valor é `true` quando a Omie informa a data de
conciliação e `false` quando não a informa; não use o status financeiro do
título para deduzir a conciliação. Quando conciliado, `conciliacao` também
traz `data`, `hora` e `usuario`.

A validação e normalização são executadas antes de ler credenciais ou fazer
HTTP. Erros retornam `error.code`, mensagem e detalhes sanitizados, incluindo
o campo e sugestão quando aplicável. Escritas nunca sofrem retry automático. Erros HTTP 4xx (exceto 408)
preservam o diagnóstico de rejeição, inclusive bloqueios 425 e limite 429.
Falhas HTTP 5xx, timeout 408, problemas de rede e respostas inválidas
após uma escrita são identificados por `outcome_unknown`, conservando
código HTTP e descrição remota sanitizada quando disponíveis. Esse
resultado exige consulta antes de repetir. Nenhuma operação aceita endpoint,
método Omie ou credenciais fornecidos pelo agente.

### Consumo, cache e cooldown

O intervalo mínimo entre chamadas distintas não substitui a proteção contra
consultas redundantes. A Omie documenta uma janela de 60 segundos para consultar
novamente o mesmo ID. Uma nova chamada com erro durante essa janela pode
reiniciá-la; na décima requisição incorreta para IP + App Key + método,
o serviço pode impor HTTP 425 por 30 minutos.

O MCP coordena leituras, consumo e cooldown entre threads do gateway, CLIs e
processos MCP que usam a **mesma raiz de instalação**. O estado compartilhado
fica em `state/mcp/omie/traffic.sqlite3` e `responses.sqlite3`, fora das fontes
versionadas. Contadores e prazos continuam válidos após reiniciar um processo.
A coordenação usa a identidade da aplicação, não somente o nome do perfil:
perfis com a mesma App Key compartilham seus limites. Credenciais não são
gravadas no cache nem usadas em texto puro como nomes de arquivo.

O cache de leituras bem-sucedidas tem validade padrão de **61 segundos**:
a janela oficial de 60 segundos mais um segundo de margem local.
A chave distingue aplicação, endpoint, método e parâmetros nativos em formato
canônico; paginação e filtros diferentes não se confundem. Essa janela permite
reutilizar uma resposta recente em outra chamada ou processo sem consultar
novamente a Omie. Erros não são armazenados como respostas bem-sucedidas.
Os dados do cache são privados da instalação e podem conter informações do ERP.

Cada leitura retorna `cache` no nível principal, junto aos dados:

```json
{"cache":{"reutilizado":true,"consultado_em":"2026-11-17T15:00:00+00:00","valido_ate":"2026-11-17T15:01:01+00:00"}}
```

Os horários são UTC. Em uma resposta composta por várias páginas,
`consultado_em` indica a consulta mais antiga, `valido_ate` a menor validade e
`reutilizado` é `true` somente quando todas as páginas vieram do cache. Para
exigir atualização, use o controle no nível principal, por exemplo:

```json
{"tool":"omie.financeiro.lancamentos_conta.consultar","arguments":{"filtros":{"id":9000000001},"atualizar_cache":true}}
```

`atualizar_cache` não pertence a `filtros` ou `params` e não se aplica às
escritas. Ele impede o aproveitamento da resposta armazenada, aguarda a
janela preventiva restante e então consulta a Omie. O cache não é uma promessa
de atualização em tempo real do ERP.

Escritas invalidam as leituras afetadas por meio de uma geração compartilhada,
inclusive quando o resultado é incerto. A invalidação remove a possibilidade
de aproveitar uma resposta anterior, mas **preserva o prazo de elegibilidade
da consulta remota**. Alterar um registro não autoriza consultar o mesmo ID
novamente antes da janela da Omie. A confirmação posterior à gravação exige
dados obtidos depois da escrita; o cache anterior não comprova persistência.

Quando uma leitura precisa aguardar a janela preventiva restante, o MCP pode
esperar localmente por até 65 segundos, em intervalos curtos, antes do HTTP.
Isso evita provocar um erro remoto apenas para descobrir que ainda era cedo.
Um bloqueio remoto de 30 minutos é respeitado e devolvido como `omie_cooldown`
com o prazo restante; a tool não fica presa durante todo esse bloqueio. Não
repita chamadas em sequência nem troque os filtros apenas para contornar o
prazo. Um resultado de cache válido continua representando a leitura anterior,
não uma nova verificação na Omie.
As esperas Omie não ocupam o loop do servidor MCP. Consultas com cache válido
podem ser atendidas enquanto outra chamada aguarda. Se um processo for
interrompido, reservas persistentes preservam o timeout da requisição e a
janela de repetição antes de autorizar outro envio.

> [!IMPORTANT]
> Rejeições locais como `dados.conciliado` ou `dados.observacoes` não pertencem
> à chamada HTTP e não consomem a cota da Omie. Para texto do lançamento, use
> `dados.observacao`; `lancamentos_conta.alterar` não oferece um campo para
> definir a conciliação. Desde `2026.11.17.2`, essa alteração aceita somente
> identificador e os campos que precisam mudar. Se ainda exigir
> `dados.conta_corrente_id` ao alterar apenas a observação, confira a versão
> instalada, atualize a base, reinicie os processos MCP existentes e renove
> `tools/list` no cliente. Esse sintoma é compatível com uma instância antiga;
> o relatório, sozinho, não identifica a versão efetivamente em execução.

Os limites oficiais também se aplicam às chamadas com parâmetros diferentes:

| Escopo | Limite documentado pela Omie |
| --- | --- |
| IP | 960 chamadas por minuto. |
| IP + App Key + método | 240 por minuto e até quatro simultâneas; alguns métodos permitem somente uma. |
| Aplicativo Trial | 60 por minuto e 100 por dia. |
| Aplicativo FIT | 60 por minuto e 500 por dia. |

O MCP mantém margem preventiva por padrão: intervalo mínimo de 0,35 segundo,
180 chamadas por minuto por método e 900 por minuto no conjunto compartilhado
da instalação. O nome histórico `process_requests_per_minute` continua aceito,
mas o contador passa a abranger os processos coordenados. Para Trial/FIT,
configure também a quota por aplicação; limitar cada método isoladamente não
garante o limite do aplicativo. Outras instalações ou integrações que saem pelo
mesmo IP precisam de coordenação externa: o MCP não observa seu tráfego.

Os defaults ficam em `configs/mcp/omie.toml`. O exemplo abaixo mantém margem
para um aplicativo Trial; para FIT, pode-se adotar `app_requests_per_day = 450`:

```toml
[defaults]
min_request_interval_seconds = 1.1
method_requests_per_minute = 55
process_requests_per_minute = 900
app_requests_per_minute = 55
app_requests_per_day = 90
cache_ttl_seconds = 61
```

`app_requests_per_minute` e `app_requests_per_day` são opcionais e aceitam
inteiros positivos; ausentes, não acrescentam uma quota por aplicação às
proteções gerais. `cache_ttl_seconds` aceita de 61 a 300 segundos; aumentar
esse valor prolonga o reaproveitamento de dados e a possível defasagem de
mudanças externas. Para atualização pontual, use `atualizar_cache`.
Salvar ou editar perfis pelo setup preserva esses defaults quando informados;
opções opcionais removidas não são reinseridas automaticamente.

Erros de contrato não recebem retry automático. HTTP 429 respeita
`Retry-After`, quando presente; respostas de redundância ou método ocupado
não são reenviadas rapidamente. Os prazos são compartilhados para evitar que
outro CLI reinicie a janela. Escritas nunca recebem retry automático.
Se a conferência posterior de uma escrita ficar bloqueada, o erro
`write_verification_failed` preserva `retry_after_seconds` e o motivo seguro
em `cause_reason`. Aguarde esse prazo antes de consultar novamente; a escrita
já processada não deve ser repetida apenas por falta de conferência.

Para manter uma base local atualizada, prefira carga inicial paginada e, depois,
listagens incrementais pelos filtros de alteração disponíveis na operação.
Para conferir um registro conhecido, use sua consulta individual e aproveite
o cache. Não refaça a listagem completa apenas para reler um único objeto.
Os limites específicos de 50 registros dos endpoints financeiros prevalecem
sobre a recomendação geral de até 100 registros por página.

Webhooks permitem invalidar ou atualizar o cadastro que mudou e consultar o
detalhe indicado pelo evento. A Omie publica eventos de lançamentos, títulos,
baixas, rateios e cadastros. Esta integração não configura um receptor de
webhooks: mudanças feitas fora do MCP só são percebidas em uma nova leitura
elegível ou por uma integração de eventos configurada separadamente. TTL e
escritas próprias não oferecem detecção instantânea de alterações externas.

Referências oficiais:
[limites de consumo](https://ajuda.omie.com.br/pt-BR/articles/8112984-limites-de-consumo-da-api-do-omie),
[boas práticas e listagens incrementais](https://ajuda.omie.com.br/pt-BR/articles/12607801-boas-praticas-de-integracao-com-as-apis-do-omie),
[características das APIs](https://ajuda.omie.com.br/pt-BR/articles/5412721-caracteristicas-e-recomendacoes-das-apis-do-omie),
[tratamento de erros](https://ajuda.omie.com.br/pt-BR/articles/8001888-tratando-os-erros-de-api)
e [webhooks](https://api.omie.com.br/docs/webhooks).

| Família | Comportamento preservado |
| --- | --- |
| Clientes e fornecedores | Cadastro compartilhado; filtros em `clientesFiltro`, detalhe por identificador, páginas de até 50 registros. |
| Tipos de documento | Catálogo sem paginação; consulta por código de até cinco caracteres. |
| Departamentos, projetos, categorias e contas | Campos permitidos, limites e identificadores de cada ação. Paginação de cadastros aceita nomes simplificados ou nativos. |
| Lançamentos e transferências | A fachada `omie.financeiro` converte campos semânticos para `cabecalho`, `detalhes` e `transferencia`; origem e destino diferentes. `transferencias.criar` exige a categoria de saída, fixa `cTipo` como `TRA` e cria uma única transferência por `IncluirLancCC`. |
| Contas a pagar/receber | Edição parcial de títulos, conta prevista, baixas e lotes com contratos próprios. Rateio de departamentos usa `definir_departamentos`; categorias de conta a pagar usam `definir_categorias`, com conferência do título e das baixas. Nenhuma definição refaz baixa. Linha digitável somente em contas a pagar, texto não vazio de até 70 caracteres sem reformatação. |
| Recebimentos e NF-e | Converte identificadores, etapa, itens e informações financeiras para os envelopes específicos da Omie. |

### Correção dos recebimentos de NF-e

A tool `omie.inbound_nfe_receipts_update` utiliza `AlterarRecebimento` para
recebimentos **ainda não concluídos**. A requisição deve conter `body`
(não `data`) e um `receipt_id` ou `access_key`, além de pelo menos uma
alteração real. Para dados financeiros, utilize `purchase_category_code`,
`bank_account_id`, `project_id`, `registration_date`, `buyer_id` e/ou
`departments`. O MCP envia os cinco primeiros em `infoAdicionais` e o
rateio em `departamentos`. Não envia mais `itensRecebimentoEditar: {}`
quando somente dados financeiros forem solicitados.

O MCP também aceita os nomes nativos `ide`, `infoAdicionais` e `departamentos`,
ou os aliases `additional_info` e `department_allocations`. O rateio semântico
usa `department_code`, `percentage` e `amount`; também são válidos os campos
nativos na lista. Representações duplicadas de um mesmo campo são rejeitadas.

`project_id`/`nIdProjeto` recebe o **ID numérico Omie** de `dados[].codigo`.
Para localizar um projeto pelo nome, siga o fluxo de [projetos](#projetos-nome-id-e-integração).
O departamento `Pisos` também exige seu código textual verdadeiro
(obtenha-o com `omie.financeiro.departamentos.listar`), não o nome.

Exemplo fictício, a ser enviado **apenas após consultar a NF-e e validar** seus
dados e sua etapa:

```json
{"tool":"omie.inbound_nfe_receipts_update","arguments":{"confirm":true,"body":{"receipt_id":123,"purchase_category_code":"2.01.03","bank_account_id":456,"project_id":789,"departments":[{"cCodDepartamento":"DEP-01","pDepartamento":100,"vDepartamento":2545.40}]}}}
```

`cCodDepartamento` é o **código textual** do departamento na Omie, não
necessariamente o ID; mantenha zeros à esquerda. O percentual do rateio deve
somar 100%. Fornecedor (`nIdFornecedor`) e valor da nota (`nValorNFe`)
são conferidos com `omie.inbound_nfe_receipts_get`, não enviados como campos
de alteração. Antes de escrever, confirme que o valor do rateio corresponde
ao valor consultado da NF-e. Após escrever, consulte novamente e confira os
campos efetivamente atualizados. Erros locais de estrutura são recusados antes
do acesso ao cofre ou à rede.

Para recebimentos **concluídos**, use
`omie.inbound_nfe_receipts_update_completed`; a Omie documenta para essa
operação somente `ide` e `infoAdicionais`. Ela **não** aceita
`departments` ou `categories`, e o MCP os rejeita em vez de enviá-los
silenciosamente. Se o rateio precisar ser alterado em uma nota concluída,
não reverta ou reabra automaticamente: peça autorização específica.


Exemplos com um único perfil habilitado:

```json
{"tool":"omie.financeiro.tipos_documento.listar","arguments":{}}
{"tool":"omie.customers_list","arguments":{"params":{"page":1,"page_size":50,"clientesFiltro":{"cnpj_cpf":"012.345.678-90"}}}}
{"tool":"omie.financeiro.lancamentos_conta.listar_por_periodo","arguments":{"filtros":{"conta_corrente_id":123,"data_inicial":"01/11/2026","data_final":"30/11/2026"},"pagina":1,"tamanho_pagina":50}}
{"tool":"omie.financeiro.transferencias.criar","arguments":{"confirmar":true,"dados":{"codigo_integracao":"TR-20261111-001","conta_origem_id":123,"conta_destino_id":456,"data":"11/11/2026","valor":100.00,"categoria_codigo":"0.01.02","observacao":"Transferência entre contas da empresa"}}}
```

Na transferência, `category_code` é convertido para `detalhes.cCodCateg`, o
conector define `detalhes.cTipo` como `TRA` e a conta de destino é enviada em
`transferencia.nCodCCDestino`. A categoria deve ser a categoria de saída válida
na empresa; consulte o cadastro de categorias quando não houver certeza. A Omie
gera os reflexos de débito e crédito a partir dessa única inclusão, portanto o
agente não deve criar dois lançamentos manuais.

As listagens financeiras paginadas retornam a página solicitada em `dados` e
indicam a seguinte em `paginacao.proxima_pagina`; o catálogo de tipos de documento
retorna todos os tipos em `dados`, sem paginação, e a consulta de conciliação
retorna todos os movimentos coletados. As demais listagens retornam uma página
por chamada em `data`, preservando os campos e metadados nativos. O agente deve
percorrer as demais páginas quando necessário.
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
Os testes cobrem os contratos nativos, os schemas publicados e os fluxos de rateio,
com respostas simuladas: não
criam títulos, pagamentos, notas nem cadastros em uma conta Omie real.
