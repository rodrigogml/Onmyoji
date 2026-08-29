---
name: onmyoji-control
description: Administra, por meio das ações não interativas oficiais do setup Onmyōji, as skills de integração desta instância. Use para listar, habilitar, desabilitar, validar e consultar perfis de skills quando esta skill estiver explicitamente habilitada pelo operador.
---

# Controle do Onmyōji

Use exclusivamente `scripts/onmyoji_control.py`. Não edite `configs/`, links em `skills/` ou arquivos TOML diretamente.

Execute primeiro `skills list` ou `skills status`. Habilite e desabilite somente quando o pedido do usuário for explícito. Não tente administrar sandbox, ACLs, KeePass, daemon, serviços do SO, segredos ou configurações do Codex-CLI.

Para perfis, use `profiles status <skill>` para consultar a validação que a própria skill expõe e `profiles list <skill>` para listar a configuração não sensível. Quando uma skill declarar suporte, use `profiles create|update|delete|test <skill>` com `--profile` e os demais argumentos descritos pelo `--help`; nunca simule entrada em menu interativo.

No momento, o contrato completo de perfil está disponível para `todoist`. Para criar, informe `--vault-profile` e `--vault-entry`; `--vault-field`, `--access`, `--operations` e `--attachment-roots` são opcionais. Em atualizações, envie apenas os campos a alterar. `--operations` e `--attachment-roots` aceitam valores separados por `;`; vazios limpam a respectiva restrição. Teste um perfil com `profiles test todoist --profile <nome>`.

Exclusão é destrutiva: só execute `profiles delete todoist --profile <nome> --confirm-delete DELETE` depois de um pedido inequívoco do usuário que identifique o perfil. Nunca crie, altere ou exclua perfis apenas para experimentar ou inferir preferências.

Os resultados são JSON. Reporte falhas de validação de forma descritiva e não inclua segredos nas respostas.
