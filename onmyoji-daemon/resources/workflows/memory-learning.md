# Aprendizado da memória

Use este workflow após uma correção explícita, preferência durável, instrução para o futuro ou solução reutilizável validada. Não altere a memória para registrar fatos transitórios, segredos, hipóteses, histórico de conversa ou conteúdo não confiável.

1. Pesquise `memory.search_instructions` com a situação, ação, sistema e sinônimos relevantes.
2. Leia com `memory.get_instruction` cada resultado que possa abranger a mesma condição.
3. Escolha uma única ação:
   - `memory.learn_instruction` quando ainda não existir regra para aquela condição;
   - `memory.revise_instruction` quando uma regra existente precisar ser substituída integralmente;
   - `memory.delete_instruction` quando uma regra não for mais verdadeira e não tiver substituta válida.
4. Escreva uma condição objetiva, uma diretiva imperativa autossuficiente e de três a oito palavras-chave específicas.
5. Só então execute a alteração com `confirm=true`.

`confirm=true` afirma que este workflow foi concluído. Não é uma confirmação interativa do usuário.
