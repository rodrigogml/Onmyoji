---
name: laveli
description: Conhecimento empresarial da Laveli Engenharia, incluindo dados, cadastros, definições e procedimentos financeiros e operacionais. Use quando a tarefa mencionar Laveli, Lavelinha, suas obras, operações financeiras, contas, categorias, departamentos, projetos, fornecedores, clientes ou regras internas da empresa.
---

# Laveli

# Regras de Organização

- Dados mutáveis — cadastros, aliases, anotações, decisões, estado de sincronização e demais informações operacionais — pertencem exclusivamente à memória estruturada local.
- Estrutura, migrations e validações dos cadastros pertencem exclusivamente aos wrappers versionados desta skill.
- Instruções de uso, regras de negócio e definição dos campos pertencem a `SKILL.md` e às referências da skill.
- Não registrar dados operacionais, sementes de cadastro ou cópias da memória no código, em documentação ou em arquivos versionados da skill.
- A localização da memória é uma configuração local, não versionada, em `configs/laveli.json`. `memory_dir: null` usa o local padrão da `memory-store`; um caminho explícito deve ser absoluto e nunca deve ser registrado nesta skill.


## Requisitos

- Exigir que a skill `memory-store` esteja habilitada no Shikigami para todo cadastro local estruturado. Se ela não estiver disponível, orientar sua habilitação; não criar armazenamento paralelo.
- Usar a memória estruturada somente por wrappers de domínio versionados nesta skill. Consultar a referência do domínio aplicável antes de provisionar ou operar uma tabela.

## Memória Estruturada local

Use exclusivamente o wrapper de memória da skill Laveli para provisionar, migrar, sincronizar e consultar os cadastros locais. O agente não deve criar, alterar ou preencher tabelas diretamente.

Antes de usar os cadastros, o wrapper deve verificar a versão do schema e o estado da última sincronização. Quando as tabelas não existirem, houver migrations pendentes, a cache estiver desatualizada ou faltar um cadastro necessário, execute a operação adequada do wrapper.


## Lançamentos Financeiros e Contábeis

- Para assuntos sobre lançamentos financeiros e/ou contábeis leia `references/financeiro.md`.
