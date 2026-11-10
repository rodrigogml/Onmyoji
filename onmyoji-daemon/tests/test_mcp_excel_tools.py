"""Contratos MCP Excel e configuração sem dependência do Excel instalado."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.excel.config.configuration import POLICIES, empty_configuration, load_configuration, profile_defaults, render_configuration
from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.tools import ExcelToolRegistry
from onmyoji_daemon.mcp.workflows import WorkflowRegistry


def configured(tmp_path):
    instance, workspace = tmp_path / "instance", tmp_path / "workspace"
    (instance / "configs").mkdir(parents=True)
    workspace.mkdir()
    (instance / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory = "' + workspace.as_posix() + '"\n', encoding="utf-8")
    config = McpConfiguration(instance)
    data = empty_configuration()
    data["profiles"]["default"] = profile_defaults(instance)
    config.save_excel(data)
    return config, data, workspace


class FakeJobs:
    def __init__(self, root):
        self.root = root
        self.calls = []
    def register(self, *args): self.calls.append(("register", args))
    def submit(self, *args):
        self.calls.append(("submit", args))
        return {"job_id": "a" * 32, "status": "queued"}
    def get(self, *args): self.calls.append(("get", args)); return {"status": "running"}
    def result(self, *args): self.calls.append(("result", args)); return {"result": {}}
    def cancel(self, *args): self.calls.append(("cancel", args)); return {"status": "cancelled"}
    def list(self, *args): self.calls.append(("list", args)); return {"jobs": []}
    def close(self): pass


@pytest.fixture
def fake_registry(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.excel import jobs
    monkeypatch.setattr(jobs, "ExcelJobs", FakeJobs)
    config, data, work = configured(tmp_path)
    return ExcelToolRegistry(config), config, data, work


def test_missing_configuration_is_inactive_and_has_no_side_effects(tmp_path):
    assert load_configuration(tmp_path) == empty_configuration()
    assert not (tmp_path / "configs").exists()
    registry = ExcelToolRegistry(McpConfiguration(tmp_path))
    try:
        assert registry.definitions() == []
    finally:
        registry.close()


def test_configuration_roundtrip_and_default_execution_policies(tmp_path):
    config, data, _ = configured(tmp_path)
    assert config.excel() == data
    assert config.enabled_excel_profiles()[0]["name"] == "default"
    profile = data["profiles"]["default"]
    assert profile["allow_write"] is True
    assert not any(profile[key] for key in POLICIES if key != "allow_write")
    assert "password" not in render_configuration(data)
    assert not (config.root / "venv").exists()


@pytest.mark.parametrize("field,value", [("max_cells", 0), ("max_queue", True), ("timeout_seconds", None), ("retention_hours", 8761), ("max_input_bytes", -1)])
def test_configuration_rejects_invalid_limits(tmp_path, field, value):
    config, data, _ = configured(tmp_path)
    data["defaults"][field] = value
    with pytest.raises(ValueError, match="Limite"):
        config.save_excel(data)


@pytest.mark.parametrize("field", ["readable_roots", "writable_roots", "work_directory"])
def test_configuration_rejects_outside_workspace(tmp_path, field):
    config, data, _ = configured(tmp_path)
    data["profiles"]["default"][field] = str(tmp_path) if field == "work_directory" else [str(tmp_path)]
    with pytest.raises(ValueError):
        config.save_excel(data)


def test_configuration_rejects_unknown_attributes_and_untrusted_macro_execution(tmp_path):
    config, data, _ = configured(tmp_path)
    bad = copy.deepcopy(data)
    bad["profiles"]["default"]["password"] = "not-allowed"
    with pytest.raises(ValueError, match="Atributos"):
        config.save_excel(bad)
    bad = copy.deepcopy(data)
    bad["defaults"]["unknown"] = 1
    with pytest.raises(ValueError, match="Limites"):
        config.save_excel(bad)
    data["profiles"]["default"]["allow_macros"] = True
    with pytest.raises(ValueError, match="confiável"):
        config.save_excel(data)
    data["profiles"]["default"]["trusted_content"] = True
    config.save_excel(data)
    assert config.excel()["profiles"]["default"]["allow_macros"] is True


def test_all_advertised_schemas_are_valid_and_nested_operations_are_closed(fake_registry):
    registry, _, _, _ = fake_registry
    tools = {tool["name"]: tool for tool in registry.definitions()}
    for definition in tools.values():
        Draft202012Validator.check_schema(definition["input_schema"])
        assert definition["input_schema"]["additionalProperties"] is False
    schema = tools["excel.apply_changes"]["input_schema"]
    validator = Draft202012Validator(schema)
    valid = {"input_path": "C:/Work/a.xlsx", "request_id": "a", "confirm": True,
             "operations": [{"type": "set_values", "sheet": "Vendas", "range": "A1", "values": [[None, "=literal", 0, False]]}]}
    assert validator.is_valid(valid)
    invalid = copy.deepcopy(valid)
    invalid["operations"][0]["unknown"] = True
    assert not validator.is_valid(invalid)
    invalid = copy.deepcopy(valid)
    invalid["operations"] = [{"type": "table", "action": "create", "sheet": "Vendas", "name": "Vendas"}]
    assert not validator.is_valid(invalid)
    assert not validator.is_valid({**valid, "confirm": False})
    assert "profile" not in schema["properties"]


@pytest.mark.parametrize("preserve", [True, False])
def test_table_append_accepts_explicit_calculated_column_policy(fake_registry, preserve):
    registry, _, _, _ = fake_registry
    schema = next(tool["input_schema"] for tool in registry.definitions() if tool["name"] == "excel.apply_changes")
    operation = {"type": "table", "action": "append", "sheet": "Dados", "name": "Itens", "values": [[4, 7, None]],
                 "preserve_calculated_columns": preserve}
    payload = {"input_path": "C:/Work/a.xlsx", "request_id": "append", "confirm": True, "operations": [operation]}
    validator = Draft202012Validator(schema)
    assert validator.is_valid(payload)
    operation["preserve_calculated_columns"] = "yes"
    assert not validator.is_valid(payload)


def test_advanced_pivot_contracts_require_complete_filters_and_ranges(fake_registry):
    registry, _, _, _ = fake_registry
    definition = next(item for item in registry.definitions() if item["name"] == "excel.apply_changes")
    validator = Draft202012Validator(definition["input_schema"])
    def valid(operation):
        return validator.is_valid({"input_path": "C:/Work/a.xlsx", "request_id": "pivot", "confirm": True, "operations": [operation]})
    value_filter = {"type": "pivot_field", "sheet": "Dados", "name": "Resumo", "field": "Produto", "action": "value", "operator": "top_count", "criteria1": 3}
    assert not valid(value_filter)
    assert valid({**value_filter, "data_field": "Soma de Valor"})
    dates = {"type": "pivot_field", "sheet": "Dados", "name": "Resumo", "field": "Data", "action": "date", "operator": "between", "criteria1": "2026-01-01"}
    assert not valid(dates)
    assert valid({**dates, "criteria2": "2026-12-31"})
    grouping = {"type": "pivot_group", "sheet": "Dados", "name": "Resumo", "field": "Data", "action": "group", "periods": [False] * 6}
    assert not valid(grouping)
    assert valid({**grouping, "periods": [False, False, False, False, True, False, True]})
    detail = {"type": "pivot_drilldown", "sheet": "Dados", "name": "Resumo", "range": "B3"}
    assert not valid(detail)
    assert valid({**detail, "destination_sheet": "Detalhes"})
    advanced = {"type": "advanced_filter", "sheet": "Dados", "range": "A1:B10", "action": "copy", "unique": True}
    assert not valid(advanced)
    assert valid({**advanced, "copy_sheet": "Dados", "copy_range": "D1:E1"})


def test_validation_schema_requires_either_typed_list_or_formula_and_bounds_formula(fake_registry):
    registry, _, _, _ = fake_registry
    schema = next(item["input_schema"] for item in registry.definitions() if item["name"] == "excel.apply_changes")
    validator = Draft202012Validator(schema)
    def valid(operation):
        return validator.is_valid({"input_path": "C:/Work/a.xlsx", "request_id": "validation", "confirm": True, "operations": [operation]})
    base = {"type": "validation", "sheet": "Dados", "range": "A1", "action": "set", "validation_type": "list"}
    assert valid({**base, "list_values": ["Sim", "Nao"]})
    assert valid({**base, "formula1": "=$D$1:$D$3"})
    assert not valid(base)
    assert not valid({**base, "list_values": ["Sim"], "formula1": "Sim"})
    assert not valid({**base, "list_values": ["Sim"], "validation_type": "custom"})
    assert not valid({**base, "formula1": "A" * 256})
    assert not valid({**base, "validation_type": "custom"})
    assert valid({**base, "validation_type": "custom", "formula1": "=A1>0"})


@pytest.mark.parametrize("arguments", [None, {}, {"input_path": "x", "request_id": "a", "unknown": True}])
def test_registry_rejects_invalid_inputs_before_jobs(fake_registry, arguments):
    registry, _, _, _ = fake_registry
    with pytest.raises(ExcelExceptions, match="Parâmetros"):
        registry.call("excel.inspect", arguments)
    assert registry.jobs.calls == []


def test_registry_sanitizes_file_access_failure_during_real_job_fingerprinting(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.excel import jobs
    config, _, work = configured(tmp_path)
    source = work / "private-source.csv"
    source.write_text("A,B\n1,2\n", encoding="utf-8")
    def locked_file(path):
        raise PermissionError("private-token-in-OS-error: " + str(path))
    monkeypatch.setattr(jobs, "file_digest", locked_file)
    registry = ExcelToolRegistry(config)
    try:
        with pytest.raises(ExcelExceptions) as failure:
            registry.call("excel.inspect", {"input_path": str(source), "request_id": "locked-source"})
        assert failure.value.code == "file_access_failed"
        assert str(failure.value) == "Não foi possível acessar os arquivos necessários à operação Excel."
        assert "private" not in str(failure.value) and str(source) not in str(failure.value)
    finally:
        registry.close()


def test_registry_preserves_domain_errors_and_configuration_value_errors(fake_registry, monkeypatch):
    from onmyoji_daemon.mcp.document_processing.contracts import DocumentError
    registry, config, _, _ = fake_registry
    for expected in [DocumentError("path_not_allowed", "Fonte fora das raízes permitidas."),
                     ExcelExceptions("deadline_exceeded", "O prazo terminou.")]:
        def reject(*arguments):
            raise expected
        monkeypatch.setattr(registry.jobs, "submit", reject)
        with pytest.raises(type(expected)) as failure:
            registry.call("excel.inspect", {"input_path": "C:/Work/a.csv", "request_id": "domain"})
        assert failure.value is expected
    invalid_configuration = ValueError("Configuração Excel inválida.")
    def reject_configuration():
        raise invalid_configuration
    monkeypatch.setattr(config, "enabled_excel_profiles", reject_configuration)
    with pytest.raises(ValueError) as failure:
        registry.call("excel.inspect", {"input_path": "C:/Work/a.csv", "request_id": "configuration"})
    assert failure.value is invalid_configuration


def test_registry_derives_output_and_enqueues_closed_copy(fake_registry):
    registry, _, _, work = fake_registry
    args = {"input_path": str(work / "source.xlsm"), "request_id": "relatório 1", "confirm": True,
            "operations": [{"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[42]]}]}
    result = registry.call("excel.apply_changes", args)
    assert result["status"] == "queued"
    action, submitted = registry.jobs.calls[-1]
    assert action == "submit" and submitted[3] == "apply_changes"
    assert Path(submitted[4]["output_path"]).suffix == ".xlsm"
    assert Path(submitted[4]["output_path"]).is_relative_to(work)
    assert "output_path" not in args


def test_default_destinations_avoid_windows_reserved_names_and_normalization_collisions(fake_registry):
    registry, _, _, _ = fake_registry
    first = registry._default_output("create", {"request_id": "CON"})["output_path"]
    assert Path(first).name.upper().startswith("WORKBOOK-CON-")
    left = registry._default_output("create", {"request_id": "á"})["output_path"]
    right = registry._default_output("create", {"request_id": "é"})["output_path"]
    assert left != right
    template = registry._default_output("create", {"request_id": "template", "template_path": "C:/Work/base.xltm"})["output_path"]
    assert Path(template).suffix == ".xlsm"


@pytest.mark.parametrize("tool,args,policy", [
    ("run_macro", {"procedure": "Atualizar", "confirm": True}, "allow_macros"),
    ("get_vba_source", {}, "allow_vba_source"),
    ("refresh", {"confirm": True}, "allow_refresh"),
    ("get_object", {"member": "Name"}, "allow_object_api"),
])
def test_registry_enforces_independent_policies(fake_registry, tool, args, policy):
    registry, config, data, work = fake_registry
    arguments = {"input_path": str(work / "source.xlsm"), "request_id": "a", **args}
    with pytest.raises(ExcelExceptions):
        registry.call("excel." + tool, arguments)
    assert not any(call[0] == "submit" for call in registry.jobs.calls)
    data["profiles"]["default"][policy] = True
    if policy == "allow_macros":
        data["profiles"]["default"]["trusted_content"] = True
    config.save_excel(data)
    assert registry.call("excel." + tool, arguments)["status"] == "queued"


def test_registry_rejects_nonfinite_numbers_and_excessive_operations(fake_registry):
    registry, config, data, work = fake_registry
    args = {"input_path": str(work / "a.xlsx"), "request_id": "a", "confirm": True,
            "operations": [{"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[float("inf")]]}]}
    with pytest.raises(ExcelExceptions, match="finitos"):
        registry.call("excel.apply_changes", args)
    data["defaults"]["max_operations"] = 1
    config.save_excel(data)
    args["operations"] = [{"type": "merge", "sheet": "Dados", "range": "A1:B1"}] * 2
    with pytest.raises(ExcelExceptions, match="limite"):
        registry.call("excel.apply_changes", args)


def test_vba_export_requires_destination_of_correct_component_kind_in_schema(fake_registry):
    registry, _, _, work = fake_registry
    definition = next(item for item in registry.definitions() if item["name"] == "excel.edit_vba")
    validator = Draft202012Validator(definition["input_schema"])
    args = {"input_path": str(work / "a.xlsm"), "request_id": "vba", "confirm": True, "action": "export", "module": "ThisWorkbook"}
    assert not validator.is_valid(args)
    assert validator.is_valid({**args, "output_module_path": str(work / "ThisWorkbook.cls")})
    assert not validator.is_valid({**args, "action": "import"})
    assert not validator.is_valid({**args, "action": "set"})


def test_udf_calculation_requires_macro_policy_even_without_run_macro_tool(fake_registry):
    registry, config, data, work = fake_registry
    args = {"input_path": str(work / "a.xlsm"), "request_id": "udf", "confirm": True,
            "calculation": {"mode": "full_rebuild", "enable_vba_udfs": True}}
    with pytest.raises(ExcelExceptions, match="confiável"):
        registry.call("excel.recalculate", args)
    data["profiles"]["default"].update(allow_macros=True, trusted_content=True)
    config.save_excel(data)
    assert registry.call("excel.recalculate", args)["status"] == "queued"


@pytest.mark.parametrize("operation,extra", [
    ("export", {"format": "xlsb", "allow_feature_loss": True}),
    ("edit_vba", {"action": "set", "module": "Module1", "source": "Option Explicit\n"}),
    ("set_object", {"selector": [{"collection": "Worksheets", "name": "Dados"}, {"collection": "Range", "name": "A1"}], "member": "NumberFormat", "value": "0.00"}),
    ("call_object", {"selector": [{"collection": "Worksheets", "name": "Dados"}, {"collection": "Range", "name": "A1"}], "member": "ClearContents"}),
])
def test_binary_writing_tools_accept_and_forward_explicit_calculation_and_preservation_policies(fake_registry, operation, extra):
    registry, config, data, work = fake_registry
    data["profiles"]["default"].update(allow_vba_source=True, allow_object_api=True, trusted_content=True,
                                       allow_unverified_active_content=True, allow_signature_changes=True)
    config.save_excel(data)
    payload = {"input_path": str(work / "source.xlsb"), "output_path": str(work / (operation + ".xlsb")),
               "request_id": "binary-" + operation, "confirm": True, "allow_signature_invalidation": True,
               "calculation": {"mode": "none", "output_mode": "manual"},
               "validation": {"require_preservation": False}, **extra}
    definition = next(item for item in registry.definitions() if item["name"] == "excel." + operation)
    assert Draft202012Validator(definition["input_schema"]).is_valid(payload)
    assert registry.call("excel." + operation, payload)["status"] == "queued"
    submitted = registry.jobs.calls[-1][1][4]
    assert submitted["calculation"] == payload["calculation"]
    assert submitted["validation"] == payload["validation"]
    assert submitted["allow_signature_invalidation"] is True


def test_every_writing_tool_exposes_closed_calculation_and_validation_objects(fake_registry):
    from onmyoji_daemon.mcp.excel.schemas import WRITING
    registry, _, _, _ = fake_registry
    definitions = {item["name"]: item for item in registry.definitions()}
    for operation in WRITING:
        fields = definitions["excel." + operation]["input_schema"]["properties"]
        assert fields["calculation"]["properties"]["output_mode"]["enum"] == ["preserve", "manual", "automatic"]
        assert fields["calculation"]["additionalProperties"] is False
        assert fields["validation"]["properties"]["require_preservation"]["type"] == "boolean"
        assert fields["validation"]["additionalProperties"] is False


def test_embedded_refresh_exposes_verification_and_pivot_policy_with_normal_refresh_permission(fake_registry):
    registry, config, data, work = fake_registry
    payload = {"input_path": str(work / "source.xlsx"), "request_id": "embedded-refresh", "confirm": True,
               "operations": [{"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[1]]}],
               "calculation": {"refresh_connections": True, "refresh_require_verified": False, "refresh_pivots": False}}
    with pytest.raises(ExcelExceptions, match="fontes externas"):
        registry.call("excel.apply_changes", payload)
    data["profiles"]["default"]["allow_refresh"] = True
    config.save_excel(data)
    assert registry.call("excel.apply_changes", payload)["status"] == "queued"
    assert registry.jobs.calls[-1][1][4]["calculation"] == payload["calculation"]


def test_write_policy_and_unavailable_tool_are_reported_before_submission(fake_registry):
    registry, config, data, _ = fake_registry
    data["profiles"]["default"]["allow_write"] = False
    config.save_excel(data)
    with pytest.raises(ExcelExceptions, match="publicar"):
        registry.call("excel.create", {"request_id": "create", "confirm": True})
    with pytest.raises(ExcelExceptions, match="indisponível"):
        registry.call("excel.nonexistent", {})
    assert not any(call[0] == "submit" for call in registry.jobs.calls)


def test_multiple_profiles_require_selection_and_support_listing(fake_registry):
    registry, config, data, work = fake_registry
    data["profiles"]["second"] = copy.deepcopy(data["profiles"]["default"])
    config.save_excel(data)
    profiles = registry.call("excel.list_profiles", {})
    assert [item["name"] for item in profiles["profiles"]] == ["default", "second"]
    with pytest.raises(ExcelExceptions):
        registry.call("excel.inspect", {"input_path": str(work / "a.xlsx"), "request_id": "a"})
    registry.call("excel.inspect", {"profile": "second", "input_path": str(work / "a.xlsx"), "request_id": "a"})
    assert registry.jobs.calls[-1][1][0] == "second"


def test_diagnostics_and_description_do_not_register_jobs(fake_registry):
    registry, _, _, _ = fake_registry
    caps = registry.call("excel.get_capabilities", {})
    feature = registry.call("excel.describe_feature", {"feature": "read_range"})
    assert caps["direct"]["read_only"] is True
    assert feature["feature"] == "read_range"
    assert registry.jobs.calls == []
    with pytest.raises(ExcelExceptions, match="catálogo"):
        registry.call("excel.describe_feature", {"feature": "nonexistent"})


@pytest.mark.parametrize("operation,args,expected", [("get_job", {"job_id": "a" * 32}, "get"),
    ("get_job_result", {"job_id": "a" * 32, "offset": 3, "limit": 2}, "result"),
    ("cancel_job", {"job_id": "a" * 32}, "cancel"), ("list_jobs", {}, "list")])
def test_job_controls_dispatch_to_selected_profile(fake_registry, operation, args, expected):
    registry, _, _, _ = fake_registry
    registry.call("excel." + operation, args)
    assert registry.jobs.calls[-1][0] == expected
    assert registry.jobs.calls[-1][1][0] == "default"


def test_excel_workflow_is_only_published_for_enabled_profiles(tmp_path):
    config, data, _ = configured(tmp_path)
    registry = WorkflowRegistry(config)
    workflow = registry.call("workflows.read", {"id": "excel-operations"})
    assert "snapshot_id" in workflow["content"] and "confirm=true" in workflow["content"]
    data["profiles"]["default"]["enabled"] = False
    config.save_excel(data)
    assert "excel-operations" not in {item.identifier for item in registry.available()}
