# Memória financeira local

## Finalidade

Manter uma cache estruturada dos cadastros financeiros da Laveli para reconhecer os termos dos usuários, resolver códigos e nomes e reduzir consultas repetidas à Omie. A cache não substitui a Omie nem autoriza alterações no ERP.

O estado materializado pertence somente ao workspace, no diretório administrado pela skill `memory-store`. A fonte versionada da skill guarda o manifesto, o wrapper e estas regras; nunca guarda registros reais, credenciais ou caminhos locais.

## Uso da memória

- Usar somente o wrapper de domínio Laveli; não executar `schema.*` diretamente nas tarefas cotidianas.
- Exigir confirmação explícita antes de provisionar schema ou gravar, atualizar, arquivar ou restaurar registros.
- Não criar arquivos SQLite, JSON ou planilhas paralelas para esses cadastros.
- Não usar a cache para disparar escrita na Omie sem nova confirmação específica para a operação no ERP.

## Namespace e fonte de verdade

- Fixar o namespace em `laveli/financeiro`.
- Obter os registros somente pelo perfil lógico `laveli` da skill `omie`.
- Registrar data e hora da sincronização e a origem `omie` em cada registro.
- Considerar a Omie autoritativa. Antes de mostrar um saldo, escolher um código para uma escrita, ou confirmar que um cadastro ainda está ativo, atualizar ou consultar a Omie.
- Tratar a ausência de um registro na cache apenas como cache desatualizada ou incompleta; não inferir exclusão no ERP.

## Tabelas iniciais

### Projetos

Usar uma linha por projeto Omie, identificada pelo código numérico da Omie. Manter estes atributos:

| Coluna | Uso |
| --- | --- |
| `id` | chave interna da memória |
| `omie_code` | identificador numérico único na Omie |
| `integration_code` | `codInt`, quando informado; único quando presente |
| `name` | nome do projeto para busca e apresentação |
| `inactive` | situação retornada pela Omie |
| `source_updated_at` | data de alteração informada pela Omie, quando houver |
| `synced_at` | instante UTC da última leitura bem-sucedida |
| `source_ref` | referência à evidência textual ou à consulta de origem, quando registrada |

### Categorias

Usar uma linha por categoria Omie, identificada pelo código numérico da Omie. Manter estes atributos:

| Coluna | Uso |
| --- | --- |
| `id` | chave interna da memória |
| `omie_code` | identificador numérico único na Omie |
| `parent_code` | código da categoria/grupo superior, quando informado |
| `description` | descrição para reconhecimento do pedido do usuário |
| `category_type` | tipo financeiro retornado pela Omie, como receita ou despesa |
| `inactive` | situação retornada pela Omie |
| `source_updated_at` | data de alteração informada pela Omie, quando houver |
| `synced_at` | instante UTC da última leitura bem-sucedida |
| `source_ref` | referência à evidência textual ou à consulta de origem, quando registrada |

Não criar campos JSON para atributos que precisem ser filtrados, validados ou atualizados repetidamente. Acrescentar colunas e migrations versionadas quando um novo atributo passar a ter uso estável.

## Sincronização

1. Consultar a Omie com `projects.list` ou `categories.list` usando o perfil `laveli` e paginar até concluir.
2. Validar o retorno e normalizar somente campos documentados; conservar a referência da consulta e o instante de sincronização.
3. Fazer `record.upsert` por `omie_code` no wrapper Laveli, com confirmação explícita para as escritas locais.
4. Arquivar registros ausentes somente após uma sincronização completa, bem-sucedida e explicitamente confirmada; uma página parcial nunca permite concluir que um cadastro foi removido.
5. Informar ao usuário quando a resposta vier da cache e quando foi a última sincronização. Consultar a Omie em caso de dúvida, dado ausente ou possível desatualização.

## Limites

- Não usar a memória para guardar credenciais, documentos fiscais, dados bancários, saldos, dados pessoais desnecessários ou o conteúdo integral de respostas da Omie.
- Não permitir que um nome aproximado da cache escolha sozinho um projeto ou categoria para uma escrita. Mostrar a resolução proposta e pedir confirmação quando houver ambiguidade ou efeito financeiro.
- Não expor operações de schema ou SQL arbitrário ao usuário final; o wrapper Laveli é responsável por validar o domínio e encaminhar as operações permitidas à `memory-store`.
