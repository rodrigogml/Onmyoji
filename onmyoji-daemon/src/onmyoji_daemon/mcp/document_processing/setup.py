"""Roteiros administrativos dos módulos documentais no Setup MCP."""
from __future__ import annotations

import copy
from pathlib import Path

from .configuration import profile_defaults, validate_configuration


LABELS = {"description": "Descrição", "readable_roots": "Diretórios de entrada", "writable_roots": "Diretórios de saída",
          "work_directory": "Diretório de trabalho", "ocr_profile": "Perfil OCR padrão", "engine": "Motor OCR",
          "languages": "Idiomas OCR", "dpi": "Resolução OCR", "tesseract_path": "Executável Tesseract",
          "tessdata_dir": "Diretório de idiomas Tesseract", "advanced_model_directory": "Modelos OCR avançado",
          "ghostscript_path": "Executável Ghostscript", "verapdf_path": "Executável veraPDF", "icc_profile_path": "Perfil ICC para PDF/A"}


def _configured_item(ui, key, label, values):
    render = ui.render_item
    for index, (field, value) in enumerate(values):
        display = "; ".join(map(str, value)) if isinstance(value, list) else str(value)
        text = f"{LABELS.get(field, field)}: {display or '(vazio)'}"
        if index == 0:
            render(key, label, text)
        else:
            print(" " * max(41, 11 + len(label)) + ui.Ui.text(text, ui.Ui.slate))


def _create_directories(profile):
    for value in [*profile["readable_roots"], *profile["writable_roots"], profile["work_directory"]]:
        Path(value).mkdir(parents=True, exist_ok=True)


def _diagnostics(config, ui, profile=None):
    from ..ocr.runtime import get_capabilities, native_installation_guidance
    capabilities = get_capabilities(config.root, profile)
    ui.flow_heading("Diagnóstico PDF e OCR", "Componentes disponíveis nesta máquina")
    for package, status in capabilities["packages"].items():
        ui.render_item("·", package, status["installed"] or "não instalado")
    tesseract = capabilities["tesseract"]
    ui.render_item("·", "Tesseract", "disponível" if tesseract["available"] else "não instalado")
    ui.render_item("·", "Idiomas OCR", "; ".join(tesseract["languages"]) or "nenhum instalado")
    ui.render_item("·", "OCR avançado", "preparado" if capabilities["advanced"]["available"] else "modelos/ambiente não preparados")
    for name, available in capabilities["pdfa"].items():
        ui.render_item("·", name, "disponível" if available else "não instalado")
    if not tesseract["available"] or not all(capabilities["pdfa"].values()):
        guidance = native_installation_guidance()
        ui.note("Componentes nativos pendentes: siga a instalação da sua plataforma e repita o diagnóstico.")
        for line in [*guidance["commands"], *guidance["steps"]]:
            ui.note(line)
    return capabilities


def _edit(config, ui, namespace, name):
    while True:
        data = getattr(config, namespace)(); profile = data["profiles"].get(name)
        if profile is None:
            return
        ui.screen(f"MCP · Perfil {namespace.upper()}", name)
        groups = [["description"], ["readable_roots"], ["writable_roots"], ["work_directory"]]
        groups += [["ocr_profile"], ["ghostscript_path", "verapdf_path", "icc_profile_path"]] if namespace == "pdf" else [["engine", "languages", "dpi"], ["tesseract_path", "tessdata_dir"], ["advanced_model_directory"]]
        for index, fields in enumerate(groups, 1):
            _configured_item(ui, f"{index}.", "Alterar " + LABELS[fields[0]].lower(), [(field, profile.get(field, "")) for field in fields])
        ui.render_item("H.", "Desabilitar" if profile["enabled"] else "Habilitar")
        ui.render_item("D.", "Diagnóstico e teste", "Motores, versões, idiomas e modelos")
        ui.render_item("I.", "Instalar ou reparar componentes")
        ui.render_item("R.", "Remover perfil")
        ui.render_item("X.", "Voltar")
        action = ui.prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}:
            return
        try:
            if action == "d":
                _diagnostics(config, ui, profile); continue
            if action == "i":
                _install(config, ui); continue
            if action == "r":
                if (ui.ask("Digite REMOVER para confirmar", required=True) or "").casefold() != "remover":
                    continue
                del data["profiles"][name]; getattr(config, "save_" + namespace)(data)
                ui.result(True, "Perfil removido; documentos e resultados preservados."); return
            if action == "h":
                profile["enabled"] = not profile["enabled"]
            elif action.isdigit() and 1 <= int(action) <= len(groups):
                updated = copy.deepcopy(profile)
                for field in groups[int(action) - 1]:
                    if field == "ocr_profile":
                        names = ["Nenhum", *[p["name"] for p in config.enabled_document_profiles("ocr")]]
                        value = ui.flow_choice("Perfil OCR padrão", names, profile.get(field) or "Nenhum")
                        if value is None: break
                        updated[field] = "" if value == "Nenhum" else value
                    elif field == "engine":
                        value = ui.flow_choice("Motor OCR", ["tesseract", "paddleocr"], str(profile.get(field, "tesseract")))
                        if value is None: break
                        updated[field] = value
                    else:
                        current = profile.get(field, "")
                        current = ";".join(current) if isinstance(current, list) else str(current)
                        value = ui.ask(LABELS[field] + (" separados por ;" if isinstance(profile.get(field), list) else ""), current, field not in {"tessdata_dir", "icc_profile_path"})
                        if value is None: break
                        updated[field] = [part.strip() for part in value.split(";") if part.strip()] if isinstance(profile.get(field), list) else int(value) if field == "dpi" else value
                else:
                    data["profiles"][name] = updated
                    validate_configuration(config.root, namespace, data); _create_directories(updated)
                    getattr(config, "save_" + namespace)(data); ui.result(True, "Perfil atualizado.")
                continue
            else:
                ui.result(False, "Opção inválida."); continue
            getattr(config, "save_" + namespace)(data); ui.result(True, "Perfil atualizado.")
        except (ValueError, OSError) as error:
            ui.result(False, str(error))


def _install(config, ui):
    from ..ocr.runtime import install_runtime
    tier = ui.flow_choice("Componentes documentais", ["Básico PDF e OCR", "OCR avançado CPU", "OCR avançado GPU NVIDIA", "PDF/A"], "Básico PDF e OCR")
    if tier is None:
        return
    ui.note("Dependências e modelos serão preparados agora; o processamento posterior usa os arquivos locais.")
    install_runtime(config.root, "advanced" if "avançado" in tier else "pdfa" if tier == "PDF/A" else "base", gpu="GPU" in tier)
    ui.result(True, "Bibliotecas Python preparadas.")
    _diagnostics(config, ui)


def document_menu(config, ui, namespace):
    """Cria perfis com caminhos efetivos em Enter e opções de edição associadas."""
    while True:
        data = getattr(config, namespace)()
        ui.screen("MCP · " + namespace.upper(), "Documentos locais e processamento isolado")
        ui.render_item("1.", "Criar perfil")
        ui.render_item("2.", "Gerenciar perfil", f"{len(data['profiles'])} configurado(s)")
        ui.render_item("3.", "Instalar ou reparar componentes")
        ui.render_item("4.", "Limites e retenção", str(data["defaults"]))
        ui.render_item("X.", "Voltar")
        choice = ui.prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        try:
            if choice == "1":
                name = ui.ask_new_profile_name("Nome do perfil", data["profiles"])
                if not name: continue
                if name in data["profiles"]:
                    ui.result(False, "Nome já utilizado."); continue
                profile = profile_defaults(config.root, namespace)
                fields = ["description", "readable_roots", "writable_roots", "work_directory"]
                cancelled = False
                for field in fields:
                    current = profile[field]; current = ";".join(current) if isinstance(current, list) else current
                    value = ui.ask(LABELS[field] + (" separados por ;" if field.endswith("roots") else ""), current, True)
                    if value is None: cancelled = True; break
                    profile[field] = [part.strip() for part in value.split(";") if part.strip()] if field.endswith("roots") else value
                if cancelled: continue
                if namespace == "pdf":
                    names = ["Nenhum", *[p["name"] for p in config.enabled_document_profiles("ocr")]]
                    selected = ui.flow_choice("Perfil OCR padrão", names, names[1] if len(names) > 1 else "Nenhum")
                    if selected is None: continue
                    profile["ocr_profile"] = "" if selected == "Nenhum" else selected
                else:
                    languages = ui.ask("Idiomas OCR separados por ;", "por;eng", True)
                    if languages is None: continue
                    profile["languages"] = [part.strip() for part in languages.split(";") if part.strip()]
                data["profiles"][name] = profile
                validate_configuration(config.root, namespace, data); _create_directories(profile)
                getattr(config, "save_" + namespace)(data); ui.result(True, "Perfil criado. Use diagnóstico para conferir os componentes instalados.")
                if ui.prompt("Preparar componentes básicos agora? [S/n]: ").strip().casefold() in {"", "s", "sim"}:
                    from ..ocr.runtime import install_runtime
                    install_runtime(config.root, "base")
                    ui.result(True, "Bibliotecas Python preparadas.")
                    _diagnostics(config, ui, profile)
            elif choice == "2":
                name = ui.select("Perfil " + namespace.upper(), sorted(data["profiles"]))
                if name: _edit(config, ui, namespace, name)
            elif choice == "3":
                _install(config, ui)
            elif choice == "4":
                key = ui.flow_choice("Limite para alterar", list(data["defaults"]), "timeout_seconds")
                if key is None: continue
                value = ui.ask(key, str(data["defaults"][key]), True)
                if value is None: continue
                data["defaults"][key] = int(value)
                getattr(config, "save_" + namespace)(data); ui.result(True, "Limites atualizados.")
            else:
                ui.result(False, "Opção inválida.")
        except (ValueError, OSError, RuntimeError) as error:
            ui.result(False, str(error))
