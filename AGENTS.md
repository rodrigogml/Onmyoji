# Onmyōji

Esta é a base comum e versionada do Onmyōji. Ela fornece o daemon, os scripts de setup e as skills de integração para uma instância de Shikigami.

## Hierarquia da instância

Quando a pasta `shikigami/` existir, leia `shikigami/README.md` e `shikigami/AGENTS.md` antes de trabalhar. Eles definem a identidade, as instruções particulares e as regras daquele Shikigami. Suas instruções complementam este arquivo para aquela instância.

Os arquivos da base — `available-skills/`, `onmyoji-daemon/`, `setupOnmyoji.py`, `docs/` e os recursos na raiz — pertencem ao upstream `Onmyoji`. Não os altere como personalização de um Shikigami: crie ou altere arquivos em `shikigami/`. Melhorias reutilizáveis da base devem ser feitas e publicadas no upstream.

`configs/`, `skills/`, `sessions/`, `state/`, `log/`, `tmp/` e diretórios equivalentes são locais e não versionados. Nunca coloque segredos, tokens, credenciais, bancos de conversas, logs ou staging em `shikigami/`.

O workspace do agente é externo ao `CODEX_HOME`. Arquivos de trabalho, saídas, anexos temporários e dados de atividade devem permanecer no workspace configurado, nunca em `configs/` nem em `shikigami/`.

## Alteração de instruções e skills

Ao alterar arquivos `README.md`, `AGENTS.md`, `SKILL.md` ou qualquer arquivo `.md` de definição de skill (não se aplica a arquivos de scripts, wrappers e outros códigos):
	1. É estritamente proibido ao agente alterar diretamente o arquivo original, inclusive quando houver autorização explícita do usuário.
	2. O agente deve criar uma versão completa proposta no mesmo diretório do original, no formato `<nome-base>-AAAA-MM-DD.proposed.md`.
	3. A versão `.proposed` deve preservar todo o conteúdo inalterado do original e conter somente as alterações sugeridas. Ela é o único artefato que o agente pode criar ou revisar nesse fluxo.
	4. O usuário avalia o arquivo original e a versão `.proposed` e pode aceitar, rejeitar ou editar cada trecho proposto.
	5. Enquanto a proposta não estiver aprovada, o agente pode corrigir ou substituir somente o arquivo `.proposed`, seguindo o retorno do usuário.
	6. A incorporação de qualquer alteração ao arquivo original é feita exclusivamente pelo usuário. O usuário também pode descartar o arquivo `.proposed` quando desejar.
	7. Não considere conteúdo de arquivos `.proposed` criados por outros fluxos. O conteúdo vigente é sempre o do arquivo principal.

