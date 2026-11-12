"""Despacho de backends e diagnóstico sem iniciar uma instância Excel."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

from ..exception import ExcelExceptions, require


DIRECT_OPERATIONS = frozenset({"inspect", "navigate", "read_range", "search", "analyze_formulas", "validate", "compare"})
WRITING = frozenset({"create", "apply_changes", "recalculate", "refresh", "export", "edit_vba", "run_macro", "set_object", "call_object"})


def get_capabilities(profile: dict | None = None) -> dict:
    """Inspeciona requisitos locais sem carregar COM ou abrir documentos.

    ``available`` não equivale a ``verified``: a ativação e os recursos reais
    são conferidos somente pelo probe nativo explícito e pelos jobs.
    """
    policy = profile or {}
    windows = os.name == "nt"
    pywin32 = importlib.util.find_spec("win32com") is not None if windows else False
    registered = False
    build, architecture = None, None
    if windows:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Excel.Application\CLSID") as key:
                registered = bool(winreg.QueryValue(key, None))
        except OSError:
            pass
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration") as key:
                build = winreg.QueryValueEx(key, "VersionToReport")[0]
                architecture = winreg.QueryValueEx(key, "Platform")[0]
        except OSError:
            pass
    session_id = None
    if windows:
        from ..component.process import process_identity
        try:
            session_id = (process_identity(os.getpid()) or {}).get("session_id")
        except ExcelExceptions:
            pass
    native_available = windows and pywin32 and registered and session_id not in {None, 0}
    reason = (None if native_available else "windows_required" if not windows else "pywin32_missing" if not pywin32
              else "excel_not_installed" if not registered else "excel_session_unavailable")
    features = {}
    for feature in ("inspect", "navigate", "read_range", "search", "analyze_formulas", "validate", "compare"):
        features[feature] = {"supported": True, "available": True, "verified": False,
                             "backend": "direct", "formats": ["xlsx", "xlsm", "xltx", "xltm", "csv", "tsv", "txt"],
                             "limitations": ["Cálculo não é executado; cache salvo não comprova atualização."]}
    native_features = {
        "create": "allow_write", "apply_changes": "allow_write", "recalculate": "allow_write",
        "refresh": "allow_refresh", "export": "allow_write", "tables": "allow_write",
        "filters": "allow_write", "validation": "allow_write", "formulas": "allow_write",
        "pivots": "allow_write", "charts": "allow_write", "slicers": "allow_write",
        "power_query": "allow_write", "data_model": "allow_write", "get_vba_source": "allow_vba_source",
        "edit_vba": "allow_vba_source", "run_macro": "allow_macros", "object_api": "allow_object_api",
        "sparklines": "allow_write", "shapes": "allow_write", "images": "allow_write", "controls": "allow_write",
        "views": "allow_write", "scenarios": "allow_write", "data_tables": "allow_write", "autofill": "allow_write",
        "outline": "allow_write", "subtotals": "allow_write", "connections": "allow_refresh", "copy_sheet": "allow_write",
        "chart_export": "allow_write",
    }
    for feature, permission in native_features.items():
        allowed = policy.get(permission, permission == "allow_write")
        if feature in {"edit_vba", "refresh", "run_macro", "connections"}:
            allowed = allowed and policy.get("allow_write", True)
        if feature in {"run_macro", "connections"}:
            allowed = allowed and policy.get("trusted_content", False)
        features[feature] = {"supported": True, "available": bool(native_available and allowed), "verified": False,
                             "backend": "native", "reason": reason or (None if allowed else "profile_policy"),
                             "prerequisites": ["Windows", "Excel desktop ativado em sessão de usuário", "pywin32", permission],
                             "limitations": ["Suporte depende do build, tipo de objeto e serviços instalados; o job verifica os pré-requisitos."]}
    return {"contract_version": 1, "platform": sys.platform, "python": sys.version.split()[0],
            "direct": {"available": True, "read_only": True, "calculates": False},
            "native": {"supported": windows, "available": native_available, "verified": False,
                       "reason": reason, "registered": registered, "pywin32": pywin32,
                       "installed_build": build, "architecture": architecture, "session_id": session_id, "probe_tool": "excel.native_probe"},
            "features": features,
            "limitations": ["Ocultação não garante ausência absoluta de diálogos de terceiros.",
                            "VBA executa com os privilégios do usuário; o worker não é uma sandbox.",
                            "Recursos sem API pública ou sem evidência de conclusão não recebem suporte presumido."]}


def execute(operation: str, arguments: dict, context) -> dict:
    """Aplica políticas e escolhe backend sem fallback silencioso de escrita.

    Escrita, macros, atualização externa e acesso ao adaptador COM são
    permissões independentes. ``capability_unavailable`` informa ações sem
    backend compatível; políticas negadas usam ``operation_not_allowed``.
    """
    args = {key: value for key, value in arguments.items() if key not in {"_policy_revision", "request_id", "confirm", "profile"}}
    backend = args.pop("backend", "auto")
    requested_backend = backend
    require(backend in {"auto", "direct", "native"}, "invalid_argument", "Backend Excel inválido.")
    profile = context.profile
    if operation in WRITING:
        require(profile.get("allow_write", False), "operation_not_allowed", "A escrita Excel está desabilitada neste perfil.")
    if operation in {"get_vba_source", "edit_vba"}:
        require(profile.get("allow_vba_source", False), "operation_not_allowed", "O acesso ao código VBA está desabilitado neste perfil.")
    if operation == "run_macro" or args.get("calculation", {}).get("enable_vba_udfs", False):
        require(profile.get("allow_macros", False) and profile.get("trusted_content", False),
                "operation_not_allowed", "Execução VBA exige um perfil habilitado para conteúdo confiável.")
    if operation == "refresh" or args.get("calculation", {}).get("refresh_connections", False):
        require(profile.get("allow_refresh", False), "operation_not_allowed", "A atualização de fontes externas está desabilitada neste perfil.")
    if operation in {"describe_object", "get_object", "set_object", "call_object"}:
        require(profile.get("allow_object_api", False), "operation_not_allowed", "O adaptador de objetos COM está desabilitado neste perfil.")
    require(len(args.get("operations", [])) <= context.defaults.get("max_operations", 1000),
            "operation_limit", "O lote excede o limite de operações do perfil.")
    if backend == "direct":
        require(operation in DIRECT_OPERATIONS, "capability_unavailable", "O backend direto é somente de leitura e não executa esta operação.")
    text_input = False
    compound_input = False
    if operation in DIRECT_OPERATIONS and requested_backend != "native":
        source = context.input_path(args["input_path"])
        with source.open("rb") as stream:
            signature = stream.read(8)
        compound_input = signature == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        text_input = source.suffix.lower() in {".csv", ".tsv", ".txt"} and not signature.startswith(
            (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"))
    if backend == "auto":
        backend = "direct" if operation in DIRECT_OPERATIONS else "native"
        if backend == "direct" and not text_input:
            if compound_input and operation != "inspect":
                backend = "native"
            elif operation == "navigate" and args.get("kind") in {"pivots", "charts", "queries", "connections", "vba", "model"}:
                backend = "native"
            elif operation == "read_range" and "display_text" in args.get("fields", []):
                backend = "native"
    context.progress(0, 1, "Inspecionando arquivo" if backend == "direct" else "Preparando Excel oculto")
    if backend == "direct":
        if text_input:
            from .text_inspection import execute_text
            result = execute_text(operation, args, context)
        else:
            from .inspection import execute_direct
            try:
                result = execute_direct(operation, args, context)
            except ExcelExceptions as error:
                if requested_backend != "auto" or error.code != "direct_read_unavailable":
                    raise
                backend = "native"
    if backend == "native":
        require(os.name == "nt", "excel_session_unavailable", "O backend nativo Excel requer Windows e sessão de usuário.")
        from .native import execute_native
        result = execute_native(operation, args, context)
    require(isinstance(result, dict), "invalid_result", "O backend Excel retornou um resultado inválido.")
    result.setdefault("backend", backend)
    result.setdefault("contract_version", 1)
    context.progress(1, 1, "Processamento concluído")
    return result
