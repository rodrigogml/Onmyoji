# {{SHIKIGAMI_NAME}}

## Alteração de instruções e skills

Ao alterar arquivos `README.md`, `AGENTS.md`, `SKILL.md` ou qualquer arquivo `.md` de definição de skill (não se aplica a arquivos de scripts, wrappers e outros códigos):
	1. É estritamente proibido ao agente alterar diretamente o arquivo original, inclusive quando houver autorização explícita do usuário.
	2. O agente deve criar uma versão completa proposta no mesmo diretório do original, no formato `<nome-base>-AAAA-MM-DD.proposed.md`.
	3. A versão `.proposed` deve preservar todo o conteúdo inalterado do original e conter somente as alterações sugeridas. Ela é o único artefato que o agente pode criar ou revisar nesse fluxo.
	4. O usuário avalia o arquivo original e a versão `.proposed` e pode aceitar, rejeitar ou editar cada trecho proposto.
	5. Enquanto a proposta não estiver aprovada, o agente pode corrigir ou substituir somente o arquivo `.proposed`, seguindo o retorno do usuário.
	6. A incorporação de qualquer alteração ao arquivo original é feita exclusivamente pelo usuário, ou aplicadas pelo agente quando a mando do usuário. O usuário também pode descartar o arquivo `.proposed` quando desejar.
	7. Não considere conteúdo de arquivos `.proposed` criados por outros fluxos. O conteúdo vigente é sempre o do arquivo principal.
	8. Quando o usuário aprovar totalmente a proposta do arquivo `.proposed`, o arquivo `.proposed` deve ser eliminado.
	9. Se ao subir o código para o repositório existirem arquivos `.proposed` no repositório, ainda pendentes de aprovação ou descarte, avise o usuário para que pendências de edição não fiquem para trás.

---
