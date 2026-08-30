# Overview

Playbook e orientações sobre a organização de lançamentos financeiros e contabeis da Laveli.

## Requisitos
- A empresa utiliza o ERP da Omie para lançamentos financeiros e contábeis.
	- Para integração com o Omie, use o perfil lógico `laveli` da skill `omie`. Esse perfil é um pré-requisito local: se não estiver configurado, oriente a configurá-lo na skill Omie e peça a intervenção necessária. Não suponha caminhos, crie credenciais, nem tente descobrir ou preencher configurações locais.

## Memória Estruturada local

A Omie permanece a fonte de verdade.

### Projetos

O cadastro local de projetos serve para reconhecer a referência do usuário e localizar o projeto correspondente na Omie. Ele não substitui nem reproduz integralmente o cadastro do ERP.

- **Código Omie:** identidade definitiva do projeto; use-o em consultas e propostas de escrita no ERP.
- **Código de integração (`codInt`):** identificador alternativo, quando informado pela Omie.
- **Nome:** nome oficial do projeto, como registrado na Omie.
- **Termos de pesquisa:** nomes alternativos, condomínio, endereço, apelidos ou outras expressões confirmadas que ajudem a reconhecer o projeto nas conversas.
- **Inativo:** indica que o projeto não deve ser sugerido para novos lançamentos, mas pode continuar necessário para consulta histórica.
- **Atualização na origem:** data de alteração retornada pela Omie, quando disponível.
- **Última sincronização:** instante em que o registro foi confirmado na Omie; use-o para identificar cache possivelmente desatualizada.
- **Anotações do agente:** decisões, lembretes e contexto confirmado sobre o projeto. Não registre dados bancários, documentos fiscais, credenciais ou dados pessoais desnecessários.

Não escolha um projeto apenas por semelhança de nome ou termo de pesquisa quando houver ambiguidade ou efeito financeiro. Apresente as opções, confirme a resolução e, antes de escrever na Omie, consulte ou sincronize o cadastro pelo wrapper.
