---
name: onmyoji-control
description: Administra, por meio das ações não interativas oficiais do setup Onmyōji, as skills de integração desta instância. Use para listar, habilitar, desabilitar, validar e consultar perfis de skills quando esta skill estiver explicitamente habilitada pelo operador.
---

# Controle do Onmyōji

Use exclusivamente `scripts/onmyoji_control.py`. Não edite `configs/`, links em `skills/` ou arquivos TOML diretamente.

Execute primeiro `skills list` ou `skills status`. Habilite e desabilite somente quando o pedido do usuário for explícito. Nunca habilite ou desabilite `onmyoji-control` por esta própria skill. Não tente administrar sandbox, ACLs, KeePass, daemon, serviços do SO, segredos ou configurações do Codex-CLI.

Para perfis, use `profiles status <skill>` para consultar a validação e `profiles schema <skill>` antes de criar ou alterar um perfil. Use `profiles list <skill>` para listar a configuração não sensível. Crie e altere com `profiles create|update <skill> --profile <nome> --set campo=valor`; listas devem ser JSON, por exemplo `--set readable_roots='["C:/work/.onmyoji/eccovox"]'`. Nunca simule entrada em menu interativo.

O contrato de perfil está disponível para `todoist`, `omie`, `eccovox`, `cloudflare`, `forward-email`, `notion`, `mysql`, `ssh`, `aws`, `google` e `bis2cmd`. Em Todoist, os campos também podem ser informados pelos argumentos específicos existentes; prefira `--set` para um fluxo uniforme. Teste perfis somente nas skills que retornarem suporte para isso; o teste sempre é uma leitura inofensiva pelo wrapper oficial.

`keepass-vault` é deliberadamente somente consulta: use `profiles status|schema|list keepass-vault`. Nunca crie, edite ou remova Vaults, perfis ou credenciais KeePass por esta skill.

Exclusão é destrutiva: só execute `profiles delete todoist --profile <nome> --confirm-delete DELETE` depois de um pedido inequívoco do usuário que identifique o perfil. Nunca crie, altere ou exclua perfis apenas para experimentar ou inferir preferências.

Os resultados são JSON. Reporte falhas de validação de forma descritiva e não inclua segredos nas respostas.
