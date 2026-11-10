"""Serviços Mercado Livre, Mercado Pago e Gateway OAuth."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Any

from .client import MercadoClient, decode_cursor, encode_cursor, envelope, observed_at
from .contracts import REPORT_COLUMNS, TOOL_MODULE
from .exception import MercadoError
from .oauth import MercadoOAuth
from .reports import ReportReader


ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
WRITE_TOOLS = {"mercadolivre.account_update", "mercadolivre.favorite_add", "mercadolivre.favorite_remove", "mercadopago.report_configuration_set", "mercadopago.report_create"}


class MercadoService:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def invoke(self, profile: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise MercadoError("invalid_request", "Argumentos Mercado devem ser objeto.")
        if tool in WRITE_TOOLS and arguments.get("confirm") is not True:
            raise MercadoError("confirmation_required", "Obtenha autorização explícita e informe confirm=true.")
        provider = tool.split(".", 1)[0]
        operation = tool.split(".", 1)[1]
        if operation in {"get_auth_status", "get_oauth_callback", "start_authorization", "get_authorization_status", "cancel_authorization"}:
            return self._auth(profile, provider, operation, arguments)
        client = MercadoClient(self.root, provider, profile)
        if provider == "mercadolivre":
            return self._mercadolivre(client, tool, arguments)
        return self._mercadopago(client, tool, arguments)

    def _auth(self, profile: str, provider: str, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        oauth = MercadoOAuth(self.root, provider, profile)
        if operation == "get_auth_status":
            return oauth.status()
        if oauth.configured.get("auth_mode", "authorization_code") != "authorization_code" and operation in {"start_authorization", "cancel_authorization"}:
            raise MercadoError("profile_auth_mode_unavailable", "Este perfil usa client_credentials e não possui autorização interativa.", profile=profile)
        from ...rpc import RpcError, call
        from ...supervisor import endpoint
        integration = provider
        action = {"get_oauth_callback": "callback-url", "start_authorization": "start", "get_authorization_status": "status", "cancel_authorization": "cancel"}[operation]
        params: dict[str, Any] = {"integration": integration, "profile": profile}
        if action in {"start", "cancel"}:
            params["confirm"] = arguments.get("confirm") is True
        session = arguments.get("session_id")
        try:
            host, port, token = endpoint(self.root)
            if action in {"status", "cancel"} and not session:
                active = call(host, port, token, "mini-apps.oauth.active", params, timeout=10)
                session = active.get("id") if isinstance(active, dict) else None
                if not session:
                    if action == "status":
                        return oauth.status()
                    raise MercadoError("remote_not_found", "Não há autorização pendente para cancelar.")
            if session:
                params["session_id"] = session
            return call(host, port, token, "mini-apps.oauth." + action, params, timeout=200)
        except MercadoError:
            raise
        except (OSError, ValueError, KeyError, RuntimeError, RpcError) as error:
            raise MercadoError("network_error", "Gateway OAuth Mercado indisponível.") from error

    def _mercadolivre(self, client: MercadoClient, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        user = client.user_id()
        single: dict[str, tuple[str, str, str | None]] = {
            "mercadolivre.account_get": ("GET", f"/users/{user}", None),
            "mercadolivre.accepted_payment_methods_list": ("GET", f"/sites/{client.oauth.configured['site_id']}/payment_methods", None),
            "mercadolivre.purchase_get": ("GET", "/orders/{}", "purchase_id"),
            "mercadolivre.pack_get": ("GET", "/packs/{}", "pack_id"),
            "mercadolivre.purchase_discounts_get": ("GET", "/orders/{}/discounts", "purchase_id"),
            "mercadolivre.shipment_get": ("GET", "/shipments/{}", "shipment_id"),
        }
        if tool in single:
            method, path, field = single[tool]
            if field:
                path = path.format(self._id(args, field))
            return self._single(client, TOOL_MODULE[tool], client.request(method, path))
        if tool == "mercadolivre.account_update":
            body = {key: value for key, value in args.items() if key != "confirm"}
            if not body:
                raise MercadoError("invalid_request", "Informe ao menos um campo permitido para atualização.")
            return self._single(client, TOOL_MODULE[tool], client.request("PUT", f"/users/{user}", body=body, retry=False))
        if tool in {"mercadolivre.favorite_add", "mercadolivre.favorite_remove"}:
            item = self._id(args, "item_id")
            if tool.endswith("favorite_add"):
                result = client.request("POST", "/users/me/bookmarks", body={"item_id": item}, retry=False)
            else:
                result = client.request("DELETE", f"/users/me/bookmarks/{item}", retry=False)
            return self._single(client, TOOL_MODULE[tool], result)
        if tool == "mercadolivre.purchases_list":
            query = {"buyer": user, **self._dates_and_filters(args, {"status", "search"})}
            sort = args.get("sort")
            if sort: query["sort"] = "date_asc" if sort == "date_asc" else "date_desc"
            return self._paged(client, tool, "/orders/search", query, args, result_key="results")
        if tool == "mercadolivre.addresses_list":
            return self._paged(client, tool, f"/users/{user}/addresses", {}, args)
        if tool == "mercadolivre.favorites_list":
            return self._paged(client, tool, "/users/me/bookmarks", {}, args)
        if tool == "mercadolivre.purchase_shipments_list":
            return self._paged(client, tool, f"/orders/{self._id(args, 'purchase_id')}/shipments", {}, args)
        if tool == "mercadolivre.billing_periods_list":
            query = {key: args.get(key) for key in ("group", "document_type")}
            return self._paged(client, tool, "/billing/integration/monthly/periods", query, args, result_key="results")
        if tool == "mercadolivre.billing_documents_list":
            period = self._period(args)
            query = {key: args.get(key) for key in ("group", "document_type")}
            return self._paged(client, tool, f"/billing/integration/periods/key/{period}/documents", query, args, result_key="results")
        if tool == "mercadolivre.billing_summary_get":
            period = self._period(args)
            return self._single(client, TOOL_MODULE[tool], client.request("GET", f"/billing/integration/periods/key/{period}/summary/details", query={"group": args.get("group")}))
        if tool == "mercadolivre.billing_details_list":
            period, group = self._period(args), self._group(args)
            return self._paged(client, tool, f"/billing/integration/periods/key/{period}/group/{group}/details", {"document_type": args.get("document_type")}, args, result_key="results")
        if tool == "mercadolivre.billing_order_details_list":
            group = self._group(args, {"ML"})
            return self._paged(client, tool, f"/billing/integration/group/{group}/order/details", {"order_ids": ",".join(args.get("order_ids", []))}, args, result_key="results")
        if tool == "mercadolivre.billing_payment_details_list":
            period, group = self._period(args), self._group(args, {"ML"})
            return self._paged(client, tool, f"/billing/integration/periods/key/{period}/group/{group}/payment/details", {}, args, result_key="results")
        if tool == "mercadolivre.billing_payment_charges_list":
            payment = self._id(args, "payment_id")
            return self._paged(client, tool, f"/billing/integration/payment/{payment}/charges", {}, args, result_key="payment_details")
        raise MercadoError("invalid_request", "Operação Mercado Livre não registrada.")

    def _mercadopago(self, client: MercadoClient, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        single = {
            "mercadopago.payment_get": ("/v1/payments/{}", ("payment_id",), False),
            "mercadopago.order_get": ("/v1/orders/{}", ("order_id",), False),
            "mercadopago.refund_get": ("/v1/payments/{}/refunds/{}", ("payment_id", "refund_id"), False),
            "mercadopago.chargeback_get": ("/v1/chargebacks/{}", ("chargeback_id",), True),
        }
        if tool in single:
            pattern, fields, caller_id = single[tool]
            return self._single(client, TOOL_MODULE[tool], client.request("GET", pattern.format(*(self._id(args, field) for field in fields)), caller_id=caller_id))
        paths = {
            "mercadopago.payments_search": ("/v1/payments/search", "results"),
            "mercadopago.payment_methods_list": ("/v1/payment_methods", None),
        }
        if tool in paths:
            path, key = paths[tool]
            query = self._dates_and_filters(args, {"status", "external_reference", "sort", "criteria"})
            if tool == "mercadopago.payments_search":
                query.setdefault("sort", "date_created"); query.setdefault("criteria", "desc"); query["range"] = query["sort"]
            return self._paged(client, tool, path, query, args, result_key=key)
        if tool == "mercadopago.orders_search":
            self._validate_range(args)
            query = {"begin_date": args["date_from"], "end_date": args["date_to"], "sort_by": "created_date", "sort_order": "desc"}
            for field in ("status", "external_reference"):
                if args.get(field) is not None: query[field] = args[field]
            return self._page_numbered(client, tool, "/v1/orders/search", query, args)
        if tool == "mercadopago.chargebacks_search":
            return self._paged(client, tool, "/v1/chargebacks/search", {"payment_id": self._id(args, "payment_id")}, args, result_key="results", caller_id=True)
        if tool == "mercadopago.refunds_list":
            return self._paged(client, tool, f"/v1/payments/{self._id(args, 'payment_id')}/refunds", {}, args)
        if tool.startswith("mercadopago.report_") or tool == "mercadopago.reports_list":
            return self._reports(client, tool, args)
        raise MercadoError("invalid_request", "Operação Mercado Pago não registrada.")

    def _reports(self, client: MercadoClient, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        family = args.get("family")
        base = self._report_base(family)
        activities = str(family).startswith("activities_")
        if tool == "mercadopago.report_configuration_get":
            return self._single(client, TOOL_MODULE[tool], client.request("GET", base + "/config"))
        if tool == "mercadopago.report_configuration_set":
            body = self._report_configuration_body(str(family), args)
            try:
                current = client.request("GET", base + "/config")
                method = "PUT"
            except MercadoError as error:
                if error.code != "remote_not_found": raise
                method = "POST"
                current = None
            path = base + "/config"
            if activities and method == "PUT":
                structure_id = current.get("structure", {}).get("id") if isinstance(current, dict) else None
                if not isinstance(structure_id, str) or not ID.fullmatch(structure_id):
                    raise MercadoError("invalid_request", "Configuração remota de atividade não informou structureId válido.")
                path += "/" + structure_id
                existing = next((item for item in current.get("notifiers", []) if isinstance(item, dict) and item.get("type") == "internal_sftp" and isinstance(item.get("id"), str)), None) if isinstance(current, dict) else None
                if existing:
                    body["notifiers"][0]["id"] = existing["id"]
            return self._single(client, TOOL_MODULE[tool], client.request(method, path, body=body, retry=False))
        if tool == "mercadopago.report_create":
            self._validate_range(args)
            body = {"filters": {"creation_date": {"range": {"gte": args["date_from"], "lte": args["date_to"]}}}, "file_format": "application/csv"} if activities else {"begin_date": args["date_from"], "end_date": args["date_to"]}
            path = base + "/statements" if activities else base
            return self._single(client, TOOL_MODULE[tool], client.request("POST", path, body=body, retry=False))
        if tool == "mercadopago.reports_list":
            path = base + "/statements" if activities else base + "/list"
            return self._paged(client, tool, path, {}, args, result_key="results" if activities else None)
        report_id = self._id(args, "report_id")
        status_path = base + "/statements/" + report_id if activities else base + "/task/" + report_id
        status = client.request("GET", status_path)
        if tool == "mercadopago.report_status_get":
            return self._single(client, TOOL_MODULE[tool], status)
        state = str(status.get("status", "")).casefold() if isinstance(status, dict) else ""
        if state not in {"available", "ready", "completed", "processed"}:
            raise MercadoError("report_not_ready", "Relatório remoto ainda não está disponível.")
        reader = ReportReader(self.root, client.defaults)
        if activities:
            download_path = base + "/statements/" + report_id + "/download"
        else:
            file_name = status.get("file_name") if isinstance(status, dict) else None
            if not isinstance(file_name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,240}", file_name):
                raise MercadoError("invalid_report_file", "O provedor não informou um nome de arquivo seguro.")
            download_path = base + "/" + file_name
        path = reader.cached(client.profile, report_id)
        if path is None:
            raw = client.request_bytes("GET", download_path, max_bytes=client.defaults["max_report_bytes"])
            path = reader.cache(client.profile, report_id, raw)
        configuration = client.request("GET", base + "/config")
        separator = configuration.get("separator", ",") if isinstance(configuration, dict) else ","
        if separator not in {",", ";", "|"}:
            raise MercadoError("invalid_report_file", "Separador remoto não suportado.")
        return reader.rows(client.profile, family, report_id, path, int(args.get("limit", client.defaults["page_size"])), args.get("cursor"), separator)

    @staticmethod
    def _report_base(family: Any) -> str:
        mapping = {"released_money": "/v1/account/release_report", "account_money": "/v1/account/settlement_report", "activities_collection": "/v1/reporting/operations/activities_collection", "activities_after_collection": "/v1/reporting/operations/activities_after_collection", "activities_withdraw": "/v1/reporting/operations/activities_withdraw"}
        if family not in mapping:
            raise MercadoError("invalid_request", "Família de relatório inválida.")
        return mapping[family]

    @staticmethod
    def _report_configuration_body(family: str, args: dict[str, Any]) -> dict[str, Any]:
        columns = args.get("columns")
        if not isinstance(columns, list) or not columns or len(columns) != len(set(columns)) or family not in REPORT_COLUMNS or not all(isinstance(value, str) and value in REPORT_COLUMNS[family] for value in columns):
            raise MercadoError("invalid_request", "Informe colunas válidas para a família de relatório.")
        if family.startswith("activities_"):
            structure: dict[str, Any] = {"name": "onmyoji-" + family, "columns": [{"key": value} for value in columns], "file_format": {"column_separator": args.get("separator", ","), "decimal_separator": ".", "date_format": "yyyy-MM-dd", "name": family, "prefix": args.get("file_prefix", family)}, "display_timezone": args.get("timezone", "GMT-03")}
            return {"structure": structure, "notifiers": [{"type": "internal_sftp", "data": {}}]}
        body: dict[str, Any] = {"columns": [{"key": value} for value in columns], "file_name_prefix": args.get("file_prefix", "onmyoji-" + family), "separator": args.get("separator", ","), "display_timezone": args.get("timezone", "GMT-03"), "format": "CSV"}
        boolean_fields = {
            "released_money": {"include_withdrawal_at_end", "execute_after_withdrawal", "check_available_balance", "compensate_detail"},
            "account_money": {"coupon_detailed", "include_withdraw", "refund_detailed", "shipping_detail", "show_chargeback_cancel", "show_fee_prevision"},
        }[family]
        unsupported = {field for field in args if field in {"include_withdrawal_at_end", "execute_after_withdrawal", "check_available_balance", "compensate_detail", "coupon_detailed", "include_withdraw", "refund_detailed", "shipping_detail", "show_chargeback_cancel", "show_fee_prevision"} and field not in boolean_fields}
        if unsupported:
            raise MercadoError("invalid_request", "Opção booleana não é suportada por esta família de relatório.")
        for field in boolean_fields:
            if field in args: body[field] = args[field]
        return body

    def _paged(self, client: MercadoClient, tool: str, path: str, query: dict[str, Any], args: dict[str, Any], result_key: str | None = None, caller_id: bool = False) -> dict[str, Any]:
        limit = int(args.get("limit", client.defaults["page_size"]))
        if not 1 <= limit <= client.defaults["max_page_size"]:
            raise MercadoError("invalid_request", "Limite de paginação inválido.")
        stable = dict(query)
        offset = decode_cursor(args["cursor"], tool, client.profile, stable) if args.get("cursor") else 0
        response = client.request("GET", path, query={**query, "limit": limit, "offset": offset}, caller_id=caller_id)
        items = response.get(result_key, []) if result_key and isinstance(response, dict) else response
        if not isinstance(items, list):
            items = [items] if isinstance(items, dict) else []
        total = response.get("paging", {}).get("total") if isinstance(response, dict) else None
        has_more = len(items) == limit if not isinstance(total, int) else offset + len(items) < total
        cursor = encode_cursor(tool, client.profile, stable, offset + len(items)) if has_more else None
        return envelope(client.profile, client.provider, TOOL_MODULE[tool], items, limit, cursor)

    def _page_numbered(self, client: MercadoClient, tool: str, path: str, query: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit", client.defaults["page_size"]))
        if not 1 <= limit <= client.defaults["max_page_size"]:
            raise MercadoError("invalid_request", "Limite de paginação inválido.")
        offset = decode_cursor(args["cursor"], tool, client.profile, query) if args.get("cursor") else 0
        response = client.request("GET", path, query={**query, "page": offset // limit + 1, "page_size": limit})
        items = response.get("results", response.get("data", [])) if isinstance(response, dict) else []
        if not isinstance(items, list): items = []
        total = response.get("paging", {}).get("total") if isinstance(response, dict) else None
        has_more = len(items) == limit if not isinstance(total, int) else offset + len(items) < total
        cursor = encode_cursor(tool, client.profile, query, offset + len(items)) if has_more else None
        return envelope(client.profile, client.provider, TOOL_MODULE[tool], items, limit, cursor)

    @staticmethod
    def _single(client: MercadoClient, module: str, response: Any) -> dict[str, Any]:
        return {"profile": client.profile, "provider": client.provider, "module": module, "source": "remote", "observedAt": observed_at(), "item": response}

    @staticmethod
    def _id(args: dict[str, Any], name: str) -> str:
        value = args.get(name)
        if not isinstance(value, str) or not ID.fullmatch(value):
            raise MercadoError("invalid_request", f"{name} inválido.")
        return value

    @staticmethod
    def _period(args: dict[str, Any]) -> str:
        value = args.get("period")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-01", value):
            raise MercadoError("invalid_request", "Período de faturamento inválido; use YYYY-MM-01.")
        return value

    @staticmethod
    def _group(args: dict[str, Any], allowed: set[str] | None = None) -> str:
        value = args.get("group")
        if value not in (allowed or {"ML", "MP"}):
            raise MercadoError("invalid_request", "Grupo de faturamento inválido.")
        return value

    def _dates_and_filters(self, args: dict[str, Any], fields: set[str]) -> dict[str, Any]:
        self._validate_range(args)
        result = {key: args[key] for key in fields if args.get(key) is not None}
        if args.get("date_from") is not None: result["begin_date"] = args["date_from"]
        if args.get("date_to") is not None: result["end_date"] = args["date_to"]
        return result

    @staticmethod
    def _validate_range(args: dict[str, Any]) -> None:
        start, end = args.get("date_from"), args.get("date_to")
        if (start is None) != (end is None):
            raise MercadoError("invalid_request", "Informe início e fim do intervalo.")
        if start is not None:
            try:
                start_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
                end_at = datetime.fromisoformat(end.replace("Z", "+00:00"))
                if start_at.tzinfo is None or end_at.tzinfo is None or start_at > end_at or (end_at - start_at).days > 366:
                    raise ValueError
            except (AttributeError, TypeError, ValueError) as error:
                raise MercadoError("invalid_request", "Intervalo de datas inválido ou invertido.") from error
