"""Menus e roteiros administrativos do MCP Excel, sem manipular a interface Office."""
from __future__ import annotations

import copy
import os
from pathlib import Path

from .configuration import POLICIES, profile_defaults, validate_configuration


LABELS = {"description": "Descrição", "readable_roots": "Diretórios de leitura", "writable_roots": "Diretórios de escrita",
          "work_directory": "Diretório de trabalho", "allow_write": "Publicar alterações", "allow_vba_source": "Ler e editar fonte VBA",
          "allow_macros": "Executar VBA e UDF", "allow_refresh": "Atualizar fontes externas", "allow_object_api": "Usar adaptador de objetos",
          "allow_signature_changes": "Alterar pastas assinadas", "allow_unverified_active_content": "Abrir conteúdo sem inspeção integral",
          "allow_addins": "Abrir arquivos de suplemento", "trusted_content": "Conteúdo explicitamente confiável"}
LIMIT_LABELS = {"max_input_bytes": "Tamanho máximo da entrada (bytes)", "max_cells": "Máximo de células",
                "max_operations": "Máximo de operações por lote", "max_result_bytes": "Tamanho máximo do resultado (bytes)",
                "max_package_bytes": "Tamanho máximo do pacote descomprimido (bytes)", "max_zip_entries": "Máximo de partes no pacote",
                "max_memory_bytes": "Memória máxima do processamento (bytes)", "timeout_seconds": "Prazo de execução (segundos)",
                "retention_hours": "Retenção dos jobs (horas)", "max_queue": "Máximo de jobs na fila"}


def _boolean(ui, label, current):
    value = ui.prompt(f"{label}? [{'S/n' if current else 's/N'}]: ").strip().casefold()
    if value in {"x", "\x1b"}:
        return None
    if value in {"", "s", "sim", "n", "não", "nao"}:
        return current if not value else value in {"s", "sim"}
    raise ValueError("Responda S ou N; X cancela a alteração.")


def _directories(profile):
    for value in [*profile["readable_roots"], *profile["writable_roots"], profile["work_directory"]]:
        Path(value).mkdir(parents=True, exist_ok=True)


def diagnostics(config, ui):
    """Mostra diagnóstico estático sem iniciar Excel nem carregar suplementos."""
    from ..service.execution import get_capabilities
    capabilities = get_capabilities()
    ui.flow_heading("Diagnóstico Excel", "Consulta estática; não inicia a aplicação")
    native = capabilities["native"]
    ui.render_item("·", "Leitura direta OOXML", "disponível" if capabilities["direct"]["available"] else "indisponível")
    ui.render_item("·", "Excel desktop registrado", "sim" if native["registered"] else "não")
    ui.render_item("·", "Bibliotecas Windows", "disponíveis" if native["pywin32"] else "pendentes")
    ui.render_item("·", "Build instalado", native["installed_build"] or "não identificado")
    ui.render_item("·", "Arquitetura do Office", native["architecture"] or "não identificada")
    ui.note("O modo nativo exige Windows, Excel desktop ativado e sessão de usuário. O diagnóstico não altera o Trust Center.")
    return capabilities


def install_components(config, ui):
    """Prepara pywin32 no runtime Python compartilhado da instância."""
    if os.name != "nt":
        raise ValueError("O backend Excel desktop exige Windows; a inspeção OOXML direta permanece disponível.")
    from .... import runtime
    ui.flow_heading("Preparar Excel MCP", "Bibliotecas no Python compartilhado")
    runtime.ensure(config.root, ["pywin32>=306", "jsonschema>=4,<5"], "import pythoncom, win32com.client; from jsonschema import Draft202012Validator", ui.note)
    ui.result(True, "Bibliotecas Excel preparadas. Instale e ative o Excel desktop separadamente.")


def _edit(config, ui, name):
    while True:
        data = config.excel()
        profile = data["profiles"].get(name)
        if profile is None:
            return
        ui.screen("MCP · Perfil Excel", name)
        fields = ["description", "readable_roots", "writable_roots", "work_directory", *POLICIES]
        for index, field in enumerate(fields, 1):
            value = profile[field]
            value = "; ".join(value) if isinstance(value, list) else "habilitado" if value is True else "desabilitado" if value is False else str(value)
            ui.render_item(f"{index}.", LABELS[field], value)
        ui.render_item("H.", "Desabilitar" if profile["enabled"] else "Habilitar")
        ui.render_item("D.", "Diagnóstico estático")
        ui.render_item("R.", "Remover perfil")
        ui.render_item("X.", "Voltar")
        choice = ui.prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        try:
            if choice == "d":
                diagnostics(config, ui)
                continue
            if choice == "r":
                if (ui.ask("Digite REMOVER para confirmar", required=True) or "").casefold() != "remover":
                    continue
                del data["profiles"][name]
                config.save_excel(data)
                ui.result(True, "Perfil removido; documentos e resultados preservados.")
                return
            if choice == "h":
                profile["enabled"] = not profile["enabled"]
            elif choice.isdigit() and 1 <= int(choice) <= len(fields):
                field = fields[int(choice) - 1]
                updated = copy.deepcopy(profile)
                if field in POLICIES:
                    if field == "allow_macros":
                        ui.note("VBA executa com as permissões do usuário. Habilite conteúdo confiável antes desta opção; macros não ficam confinadas às raízes MCP.")
                    value = _boolean(ui, LABELS[field], profile[field])
                else:
                    current = ";".join(profile[field]) if isinstance(profile[field], list) else profile[field]
                    value = ui.ask(LABELS[field] + (" separados por ;" if field.endswith("roots") else ""), current, True)
                    if value is not None and field.endswith("roots"):
                        value = [part.strip() for part in value.split(";") if part.strip()]
                if value is None:
                    continue
                updated[field] = value
                data["profiles"][name] = updated
            else:
                ui.result(False, "Opção inválida.")
                continue
            validate_configuration(config.root, data)
            _directories(data["profiles"][name])
            config.save_excel(data)
            ui.result(True, "Perfil Excel atualizado.")
        except (ValueError, OSError, RuntimeError) as error:
            ui.result(False, str(error))


def excel_menu(config, ui):
    """Cria e gerencia perfis com padrões efetivos e cancelamento em cada etapa."""
    while True:
        data = config.excel()
        ui.screen("MCP · Excel", "Leitura, edição e cálculo em background")
        ui.render_item("1.", "Criar perfil")
        ui.render_item("2.", "Gerenciar perfil", f"{len(data['profiles'])} configurado(s)")
        ui.render_item("3.", "Limites e retenção", f"{data['defaults']['max_cells']:,} células · {data['defaults']['timeout_seconds']} s".replace(",", "."))
        ui.render_item("4.", "Diagnóstico estático", "Não inicia Excel")
        ui.render_item("5.", "Instalar ou reparar bibliotecas", "Runtime Python compartilhado")
        ui.render_item("X.", "Voltar")
        choice = ui.prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        try:
            if choice == "1":
                name = ui.ask_new_profile_name("Nome do perfil", data["profiles"])
                if not name:
                    continue
                if name in data["profiles"]:
                    raise ValueError("Nome de perfil já utilizado.")
                ui.flow_heading("Criar perfil Excel", "Padrões no workspace externo")
                profile = profile_defaults(config.root)
                for field in ("description", "readable_roots", "writable_roots", "work_directory"):
                    current = ";".join(profile[field]) if isinstance(profile[field], list) else profile[field]
                    value = ui.ask(LABELS[field] + (" separados por ;" if field.endswith("roots") else ""), current, True)
                    if value is None:
                        break
                    profile[field] = [part.strip() for part in value.split(";") if part.strip()] if field.endswith("roots") else value
                else:
                    data["profiles"][name] = profile
                    validate_configuration(config.root, data)
                    _directories(profile)
                    config.save_excel(data)
                    ui.result(True, "Perfil criado. VBA, fontes externas e adaptador de objetos podem ser habilitados em Gerenciar perfil.")
                continue
            if choice == "2":
                name = ui.select("Perfil Excel", sorted(data["profiles"]))
                if name:
                    _edit(config, ui, name)
            elif choice == "3":
                label = ui.flow_choice("Limite Excel para alterar", [LIMIT_LABELS[key] for key in data["defaults"]], LIMIT_LABELS["timeout_seconds"])
                if label is None:
                    continue
                key = next(key for key in data["defaults"] if LIMIT_LABELS[key] == label)
                value = ui.ask(label, str(data["defaults"][key]), True)
                if value is not None:
                    data["defaults"][key] = int(value)
                    config.save_excel(data)
                    ui.result(True, "Limites Excel atualizados.")
            elif choice == "4":
                diagnostics(config, ui)
            elif choice == "5":
                install_components(config, ui)
            else:
                ui.result(False, "Opção inválida.")
        except (ValueError, OSError, RuntimeError) as error:
            ui.result(False, str(error))
