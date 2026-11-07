"""Publicação condicional de ferramentas Excel e despacho para jobs isolados."""
from __future__ import annotations

import atexit
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .config.configuration import load_configuration
from .exception import ExcelExceptions, require
from .schemas import BOOL, CALCULATION, IMMEDIATE, JOB_SPECS, NO_INPUT, PASSWORD_REF, PATH, SPECS, VALIDATION, WRITING, enum, obj, text


class ExcelToolRegistry:
    """Valida contratos e permissões antes de enfileirar processamento Excel.

    Um perfil é implícito; vários exigem seleção explícita. Nenhum diagnóstico
    estático inicia a aplicação. Processos nativos pertencem ao supervisor de jobs.
    """

    def __init__(self, configuration):
        from .jobs import ExcelJobs
        self.configuration = configuration
        self.jobs = ExcelJobs(configuration.root)
        atexit.register(self.close)

    def close(self):
        self.jobs.close()

    def definitions(self) -> list[dict]:
        profiles = self.configuration.enabled_excel_profiles()
        if not profiles:
            return []
        result = []
        if len(profiles) > 1:
            result.append({"name": "excel.list_profiles", "description": "Lista perfis Excel habilitados e seus contextos.", "input_schema": obj({})})
        for operation, (description, fields, mandatory) in {**SPECS, **JOB_SPECS}.items():
            properties, required = deepcopy(fields), list(mandatory)
            if operation not in IMMEDIATE:
                properties["request_id"] = text(200, description="Chave estável: repetir os mesmos argumentos e fontes consulta o mesmo job.")
                required.append("request_id")
                if operation not in NO_INPUT:
                    properties.update(input_path=PATH, backend=enum("auto", "direct", "native"), password_ref=PASSWORD_REF)
                    required.append("input_path")
                if operation in {"inspect", "navigate", "read_range", "search", "analyze_formulas", "validate", "compare"}:
                    properties.update(encoding=text(100, description="Encoding de entrada CSV/TSV/texto; padrão utf-8-sig."),
                                      delimiter=text(1, description="Delimitador CSV/texto; .txt exige definição explícita."))
                if operation in {"validate", "compare"}:
                    properties.update(other_encoding=text(100), other_delimiter=text(1))
                if operation in WRITING:
                    properties.setdefault("calculation", deepcopy(CALCULATION))
                    properties.setdefault("validation", deepcopy(VALIDATION))
                    properties["confirm"] = {**BOOL, "const": True}
                    properties["allow_signature_invalidation"] = BOOL
                    required.append("confirm")
                description += " Retorna job_id; consulte excel.get_job e excel.get_job_result."
            if len(profiles) > 1:
                properties["profile"] = enum(*(profile["name"] for profile in profiles))
                required.append("profile")
            description += " Perfil: " + "; ".join(profile["name"] + " — " + profile["description"] for profile in profiles)
            schema = obj(properties, required)
            if operation == "edit_vba":
                schema["allOf"] = [{"if": {"properties": {"action": {"const": action}}, "required": ["action"]},
                                    "then": {"required": fields}}
                                   for action, fields in {"set": ["source"], "import": ["input_module_path"], "export": ["output_module_path"]}.items()]
            if operation == "export":
                schema["allOf"] = [{"if": {"properties": {"format": {"enum": ["png", "jpeg"]}}, "required": ["format"]},
                                    "then": {"required": ["sheet", "chart_name"]}},
                                   {"if": {"properties": {"format": {"const": "json"}}, "required": ["format"]}, "then": {"required": ["sheet"]}}]
            result.append({"name": "excel." + operation, "description": description, "input_schema": schema})
        return result

    def _default_output(self, operation: str, args: dict) -> dict:
        if operation not in WRITING:
            return args
        from ..document_processing.configuration import workspace_for
        base = workspace_for(self.configuration.root) / ".onmyoji" / "excel" / "output"
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", args["request_id"]).strip(".-") or "workbook"
        reserved = {"CON", "PRN", "AUX", "NUL", *("COM" + str(index) for index in range(1, 10)), *("LPT" + str(index) for index in range(1, 10))}
        if name.split(".", 1)[0].upper() in reserved:
            name = "workbook-" + name
        name = name[:110] + "-" + hashlib.sha256(args["request_id"].encode("utf-8")).hexdigest()[:8]
        if operation == "edit_vba" and args["action"] == "export":
            return args
        if "output_path" in args:
            return args
        suffix = "." + args["format"] if operation == "export" else Path(args.get("template_path", args.get("input_path", "workbook.xlsx"))).suffix
        if operation == "create":
            suffix = {".xltx": ".xlsx", ".xltm": ".xlsm", ".xlt": ".xls"}.get(suffix.casefold(), suffix)
        return {**args, "output_path": str(base / (name + (suffix or ".xlsx")))}

    def call(self, name: str, arguments: dict) -> dict:
        """Despacha ferramentas com código público estável para falhas de arquivo.

        Erros de domínio e configuração mantêm seus contratos. Falhas inesperadas
        de I/O são convertidas em ``file_access_failed`` sem expor caminhos ou
        a mensagem potencialmente privada fornecida pelo sistema operacional.
        """
        try:
            return self._call(name, arguments)
        except OSError as error:
            raise ExcelExceptions("file_access_failed", "Não foi possível acessar os arquivos necessários à operação Excel.") from error

    def _call(self, name: str, arguments: dict) -> dict:
        from jsonschema import Draft202012Validator
        definition = next((item for item in self.definitions() if item["name"] == name), None)
        require(definition is not None, "tool_unavailable", "Ferramenta Excel indisponível.")
        require(isinstance(arguments, dict) and Draft202012Validator(definition["input_schema"]).is_valid(arguments),
                "invalid_argument", "Parâmetros Excel inválidos; consulte o contrato da ferramenta.")
        try:
            json.dumps(arguments, allow_nan=False)
        except (ValueError, TypeError) as error:
            raise ExcelExceptions("invalid_argument", "Os argumentos Excel precisam ser dados JSON finitos.") from error
        profiles = self.configuration.enabled_excel_profiles()
        operation = name.split(".", 1)[1]
        if operation == "list_profiles":
            return {"profiles": [{"name": item["name"], "description": item["description"]} for item in profiles]}
        selected = profiles[0]["name"] if len(profiles) == 1 else arguments["profile"]
        document = load_configuration(self.configuration.root)
        profile = document["profiles"][selected]
        if operation in {"get_capabilities", "describe_feature"}:
            from .service.execution import get_capabilities
            result = get_capabilities(profile)
            if operation == "describe_feature":
                features = result.get("features", {})
                if isinstance(features, list):
                    features = {item.get("id", item.get("name")): item for item in features}
                require(arguments["feature"] in features, "feature_not_found", "A capacidade Excel informada não está no catálogo.")
                return {"feature": arguments["feature"], **features[arguments["feature"]]}
            return result
        self.jobs.register(selected, profile, document["defaults"])
        if operation == "get_job":
            return self.jobs.get(selected, arguments["job_id"])
        if operation == "get_job_result":
            return self.jobs.result(selected, arguments["job_id"], arguments.get("offset", 0), arguments.get("limit", 100))
        if operation == "cancel_job":
            return self.jobs.cancel(selected, arguments["job_id"])
        if operation == "list_jobs":
            return self.jobs.list(selected)
        if operation in WRITING:
            require(profile["allow_write"], "write_not_allowed", "O perfil Excel não permite publicar alterações.")
        if operation in {"get_vba_source", "edit_vba"}:
            require(profile["allow_vba_source"], "vba_access_denied", "Habilite leitura/edição de fonte VBA neste perfil.")
        if operation == "run_macro" or arguments.get("calculation", {}).get("enable_vba_udfs"):
            require(profile["allow_macros"] and profile["trusted_content"], "macro_execution_disabled", "A execução VBA exige perfil habilitado para conteúdo confiável.")
        if operation == "refresh" or arguments.get("calculation", {}).get("refresh_connections"):
            require(profile["allow_refresh"], "refresh_not_allowed", "A atualização de fontes externas não está habilitada neste perfil.")
        if operation in {"describe_object", "get_object", "set_object", "call_object"}:
            require(profile["allow_object_api"], "object_api_disabled", "O adaptador de objetos não está habilitado neste perfil.")
        if "operations" in arguments:
            require(len(arguments["operations"]) <= document["defaults"]["max_operations"], "operation_limit_exceeded", "O lote excede o limite de operações configurado.")
        payload = self._default_output(operation, deepcopy(arguments))
        return self.jobs.submit(selected, profile, document["defaults"], operation, payload, arguments["request_id"])
