# Onmyōji

Esta é a base comum e versionada do Onmyōji. Ela fornece o daemon, os scripts de setup e as skills de integração para uma instância de Shikigami.

## Hierarquia da instância

Quando a pasta `shikigami/` existir, leia `shikigami/README.md` e `shikigami/AGENTS.md` antes de trabalhar. Eles definem a identidade, as instruções particulares e as regras daquele Shikigami. Suas instruções complementam este arquivo para aquela instância.

Os arquivos da base — `available-skills/`, `onmyoji-daemon/`, `setupOnmyoji.py`, `docs/` e os recursos na raiz — pertencem ao upstream `Onmyoji`. Não os altere como personalização de um Shikigami: crie ou altere arquivos em `shikigami/`. Melhorias reutilizáveis da base devem ser feitas e publicadas no upstream.

`configs/`, `skills/`, `sessions/`, `state/`, `log/`, `tmp/` e diretórios equivalentes são locais e não versionados. Nunca coloque segredos, tokens, credenciais, bancos de conversas, logs ou staging em `shikigami/`.

O workspace do agente é externo ao `CODEX_HOME`. Arquivos de trabalho, saídas, anexos temporários e dados de atividade devem permanecer no workspace configurado, nunca em `configs/` nem em `shikigami/`.

## Alteração de instruções e skills

Antes de alterar `README.md`, `AGENTS.md`, `SKILL.md` ou qualquer arquivo de skill:
	1. Exiba a alteração proposta em formato diff.
	2. Aguarde autorização explícita do usuário.
	3. Só então aplique a alteração.

No diff, apresente as remoções com `-` e as adições com `+`. Ordene os blocos pelo número da linha e, quando houver substituição na mesma linha, mostre primeiro a remoção e depois a adição. Use cores, quando a interface suportar.
