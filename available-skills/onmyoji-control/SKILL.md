---
name: onmyoji-control
description: Administra, por meio das ações não interativas oficiais do setup Onmyōji, as skills de integração desta instância. Use para listar, habilitar, desabilitar, validar e consultar perfis de skills quando esta skill estiver explicitamente habilitada pelo operador.
---

# Controle do Onmyōji

Use exclusivamente `scripts/onmyoji_control.py`. Não edite `configs/`, links em `skills/` ou arquivos TOML diretamente.

Execute primeiro `skills list` ou `skills status`. Habilite e desabilite somente quando o pedido do usuário for explícito. Não tente administrar sandbox, ACLs, KeePass, daemon, serviços do SO, segredos ou configurações do Codex-CLI.

Para perfis, use `profiles status <skill>` para consultar a validação que a própria skill expõe. Criação, edição e remoção só devem ser oferecidas quando a skill declarar uma ação não interativa específica; nunca simule entrada em menu interativo.

Os resultados são JSON. Reporte falhas de validação de forma descritiva e não inclua segredos nas respostas.
