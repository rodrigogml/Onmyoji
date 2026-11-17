# Omie no MCP interno

A integração oferece 72 operações de cadastros, financeiro e NF-e. O servidor
stdio publica os parâmetros aceitos e o contexto de uso nas próprias tools; o
código não depende de scripts de integração nem expõe o cofre como ferramenta.

## Configuração e descoberta

Use **MCP → V. Perfis de vault** para configurar o cofre compartilhado e depois
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
nativos forem indispensáveis para auditoria.

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

A alteração aceita uma atualização parcial: informe `id` ou
`codigo_integracao` e pelo menos um campo editável. Os dois identificadores
podem ser enviados juntos. O MCP envia apenas os campos solicitados; para
alterar o rateio, não é necessário repetir conta, data, valor, categoria ou
tipo de documento. Campos omitidos permanecem fora da requisição, preservando
os dados que não fazem parte da correção.

Criação e alteração publicam somente os campos que conseguem traduzir, com os
tipos de documento aceitos pelo MCP e os limites específicos de cada campo.
Cada operação financeira de escrita tem seu próprio schema; um campo aceito
em outra operação não passa a ser aceito automaticamente. Ausência ou
invalidade de campos obrigatórios gera erro com o nome público, por exemplo
`dados.codigo_integracao`, antes de qualquer acesso externo.

O schema público de `dados.departamentos` publica **um único formato** para
lançamentos, títulos a pagar/receber e transferências que aceitam rateio:

```json
{"departamentos":[{"codigo":"8532594895","percentual":100}]}
```

`codigo` é **sempre string**, mesmo quando contém apenas números; não envie
`8532594895` sem aspas. `percentual` é numérico, positivo e até 100. Para
um departamento, use `100`; para vários, a soma deve ser `100`, sem repetir
o código de um departamento. **Não**
envie `valor`, `cCodDep`, `nPerDep` nem `nValDep` nos itens da tool
pública. O MCP valida os percentuais e **envia à Omie somente `cCodDep` e
`nPerDep`**. A API calcula o valor do departamento; enviar `nPerDep` junto com `nValDep` provoca
`SOAP-ENV:Client-727` ("Preencha apenas a tag [nPerDep] ou [nValDep]!").

Em `lancamentos_conta`, o envelope nativo é `departamentos`; em
`contas_pagar` e `contas_receber`, é `distribuicao`. O agente usa sempre
`dados.departamentos` nessas operações da fachada. As operações específicas
de rateio de contas a receber mantêm seu contrato próprio publicado.

Para compatibilidade com integrações antigas, o normalizador interno continua
aceitando `codigo/valor` e `cCodDep/nPerDep/nValDep` quando chamado
programaticamente e houver os dados necessários à validação. Quando o chamador informar apenas o valor, o conector
transmite `cCodDep` e `nValDep`; quando informar percentual (mesmo com
valor consistente), transmite `cCodDep` e `nPerDep`. Nunca transmite ambas
as tags no mesmo departamento. Esses formatos **não são apresentados no
schema público**, para não confundir o agente.

Após uma inclusão confirmada, a conferência usa `.consultar` pelo ID retornado
ou pelo código de integração original. Em resposta incerta, consulte antes de
repetir a escrita. Referência dos campos nativos:
[lançamentos de conta corrente da Omie](https://api.omie.com.br/docs/apis/financas/contacorrentelancamentos).

### Rateio: alteração e confirmação de persistência

Para corrigir somente departamentos, envie o identificador e o novo rateio,
com `confirmar: true`. Exemplos fictícios, com um único perfil:

```json
{"tool":"omie.financeiro.lancamentos_conta.alterar","arguments":{"confirmar":true,"dados":{"id":9000000001,"departamentos":[{"codigo":"DEP-01","percentual":100}]}}}
{"tool":"omie.financeiro.contas_pagar.alterar","arguments":{"confirmar":true,"dados":{"id":9000000002,"departamentos":[{"codigo":"DEP-01","percentual":100}]}}}
```

As alterações de lançamentos, contas a pagar e contas a receber que incluam
`departamentos` consultam o mesmo objeto após a escrita. O retorno contém
`verificacao.rateio_confirmado: true` somente quando o rateio lido confere
com o solicitado. `departamentos: []` solicita a remoção do rateio e passa
pela mesma conferência. O retorno `cCodStatus: "0"` da escrita, sozinho,
não confirma a persistência da distribuição.
Falhas de negócio são reconhecidas tanto em `cCodStatus`/`cDesStatus` dos
lançamentos quanto em `codigo_status`/`descricao_status` dos títulos; uma
rejeição não é tratada como processamento bem-sucedido.

| Resultado da conferência | Comportamento |
| --- | --- |
| Rateio confirmado | A operação retorna o sucesso da escrita com `verificacao.rateio_confirmado: true`. |
| Consulta concluída com rateio diferente | Retorna `allocation_not_persisted`, sem afirmar que o objetivo foi cumprido. |
| Consulta não concluída ou resposta inconclusiva | Retorna `write_verification_failed`; o resultado do rateio exige nova consulta. |

Os dois erros de conferência informam `escrita_processada: true` e
`retryable: false`. A escrita não é repetida automaticamente. Consulte o
cadastro antes de decidir a próxima ação; repetir um payload mais completo
não é uma correção para uma distribuição que a Omie não manteve.

Um lançamento de conta corrente pode apontar para o título de origem em
`diversos.nCodLancCP` ou `diversos.nCodLancCR`. Se a conferência do movimento
divergir, o diagnóstico orienta a consulta do título vinculado; o MCP não
redireciona a escrita automaticamente para outro objeto. Ao corrigir o
rateio de um título já pago ou recebido, confira também o movimento
correspondente: a confirmação do título não garante propagação para uma
baixa existente. Não cancele nem refaça pagamentos para corrigir rateio.

> [!NOTE]
> `BARP` identifica uma conta a pagar importada por código de barras;
> `BAXP` identifica o pagamento de uma conta a pagar. Essas origens e o status
> financeiro não demonstram, por si, que o rateio será aceito ou ignorado.
> O contrato de `AlterarLancCC` admite departamentos; não há garantia pública
> de propagação entre o título e sua baixa já existente.

Referências: [campos do título a pagar](https://app.omie.com.br/api/v1/financas/contapagar/),
[origens dos movimentos financeiros](https://app.omie.com.br/api/v1/financas/mf/)
e [rateio de lançamentos na conciliação](https://ajuda.omie.com.br/pt-BR/articles/5222014-atualizacao-em-lote-de-lancamentos-na-conciliacao-de-contas-correntes).

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

Quando um filtro de conta é usado, a primeira consulta completa é mantida por
até **5 minutos em cache de memória** (até 6 períodos/perfis e até 25 mil
lançamentos por consulta). Páginas públicas seguintes e outras contas no mesmo
período reutilizam o conjunto já validado, sem refazer todas as requisições
nativas. Erros e consultas parciais nunca entram no cache. Consultas distintas
ou expiradas fazem nova leitura; operações financeiras de escrita invalidam o
cache do perfil, inclusive quando a resposta da escrita é incerta. Reiniciar o
daemon também limpa esse cache.

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

Erros de contrato HTTP 500 não recebem retry automático. Excepcionalmente,
o HTTP 500 com a descrição da Omie "Já existe uma requisição desse método
sendo executada" recebe até `max_retries` novas tentativas **somente em
consultas**, com atrasos de 2, 4 e até 8 segundos. Esgotadas as tentativas,
a tool devolve `omie_busy` com `retry_after_seconds: 10` e aplica cooldown
temporário antes de permitir nova consulta. Nunca ocorre retry de escrita
nesse cenário. HTTP 425 também interrompe as tentativas: a Omie documenta
bloqueio temporário após requisições incorretas repetidas. O MCP registra o cooldown de 30 minutos para a combinação
App Key + método em `state/mcp/omie/cooldowns.json`, identificada somente por
hash (sem credenciais), para impedir novas chamadas mesmo após reiniciar o
daemon. Revise os parâmetros antes de retomar; reduzir o intervalo de datas não
corrige um campo rejeitado.

O controle de tráfego do daemon serializa requisições da mesma App Key + método,
inclusive entre processos que utilizam a **mesma raiz de instalação**,
por meio de travas locais em `state/mcp/omie/method-locks/` com nomes hash
sem credenciais ou dados financeiros. No Windows, a espera pela trava não lê
nem altera o byte protegido por outra chamada. A disputa entre processos
aguarda até 60 segundos; ao esgotar esse prazo, retorna `omie_local_busy` com
`retry_after_seconds: 3`. Falhas de preparação ou aquisição da trava retornam
`omie_local_lock_error`; nenhuma dessas falhas realiza uma chamada à Omie.
Se a liberação falhar após uma operação concluída, o erro inclui
`phase: "release"`; nas escritas financeiras, retorna `outcome_unknown` para
orientar a consulta do resultado antes de qualquer repetição. Se a operação
já falhou, a falha de liberação preserva o diagnóstico original.
Também usa janela móvel para evitar rajadas e aplica por padrão intervalo mínimo de
0,35 segundo entre chamadas desse método, limite preventivo de 180 chamadas
por minuto por método e 900 por minuto por processo. Há margem em relação aos
limites padrão publicados pela Omie. Para contas Trial/FIT (60 chamadas/minuto),
ajuste os defaults do perfil MCP para valores mais conservadores:

```toml
[defaults]
min_request_interval_seconds = 1.1
method_requests_per_minute = 55
process_requests_per_minute = 900
```

HTTP 429 respeita `Retry-After` (segundos ou data HTTP) e utiliza backoff
exponencial sem repetir escrita automaticamente. Aguardas superiores a 8 segundos
são expostas como cooldown, sem prender a execução da tool no tempo de espera.
As janelas móveis de ritmo ainda são compartilhadas apenas no mesmo processo;
a exclusão mútua por método também abrange processos da mesma instalação.
Outras instalações e integrações no mesmo IP precisam de coordenação externa. O cache é local ao processo e não substitui a
conferência de dados atualizados antes de operações sensíveis.

| Família | Comportamento preservado |
| --- | --- |
| Clientes e fornecedores | Cadastro compartilhado; filtros em `clientesFiltro`, detalhe por identificador, páginas de até 50 registros. |
| Tipos de documento | Catálogo sem paginação; consulta por código de até cinco caracteres. |
| Departamentos, projetos, categorias e contas | Campos permitidos, limites e identificadores de cada ação. Paginação de cadastros aceita nomes simplificados ou nativos. |
| Lançamentos e transferências | A fachada `omie.financeiro` converte campos semânticos para `cabecalho`, `detalhes` e `transferencia`; origem e destino diferentes. `transferencias.criar` exige a categoria de saída, fixa `cTipo` como `TRA` e cria uma única transferência por `IncluirLancCC`. |
| Contas a pagar/receber | Preserva tipos de documento, baixas, conciliação, distribuição e lotes. Linha digitável somente em contas a pagar, texto não vazio de até 70 caracteres sem reformatação. |
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
Os testes cobrem os contratos das 72 operações e usam respostas simuladas: não
criam títulos, pagamentos, notas nem cadastros em uma conta Omie real.
