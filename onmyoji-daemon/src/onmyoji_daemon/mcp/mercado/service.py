"""Serviços Mercado Livre, Mercado Pago e Gateway OAuth."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Any

from .client import MercadoClient, decode_cursor, encode_cursor, envelope, observed_at
from .contracts import TOOL_MODULE
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
                result = client.request("POST", f"/users/{user}/bookmarks", body={"item_id": item}, retry=False)
            else:
                result = client.request("DELETE", f"/users/{user}/bookmarks/{item}", retry=False)
            return self._single(client, TOOL_MODULE[tool], result)
        if tool == "mercadolivre.purchases_list":
            query = {"buyer": user, **self._dates_and_filters(args, {"status", "search"})}
            sort = args.get("sort")
            if sort: query["sort"] = "date_asc" if sort == "date_asc" else "date_desc"
            return self._paged(client, tool, "/orders/search", query, args, result_key="results")
        if tool == "mercadolivre.addresses_list":
            return self._paged(client, tool, f"/users/{user}/addresses", {}, args)
        if tool == "mercadolivre.favorites_list":
            return self._paged(client, tool, f"/users/{user}/bookmarks", {}, args)
        if tool == "mercadolivre.purchase_shipments_list":
            return self._paged(client, tool, f"/orders/{self._id(args, 'purchase_id')}/shipments", {}, args)
        billing = {
            "mercadolivre.billing_periods_list": "/billing/integration/periods",
            "mercadolivre.billing_documents_list": "/billing/integration/documents",
            "mercadolivre.billing_summary_get": "/billing/integration/summary",
            "mercadolivre.billing_details_list": "/billing/integration/details",
            "mercadolivre.billing_order_details_list": "/billing/integration/order-details",
            "mercadolivre.billing_payment_details_list": "/billing/integration/payment-details",
            "mercadolivre.billing_payment_charges_list": "/billing/integration/payment-charges",
        }
        if tool in billing:
            query = {key: value for key, value in args.items() if key in {"group", "period"}}
            query["user_id"] = user
            if tool.endswith("summary_get"):
                return self._single(client, TOOL_MODULE[tool], client.request("GET", billing[tool], query=query))
            return self._paged(client, tool, billing[tool], query, args, result_key="results")
        raise MercadoError("invalid_request", "Operação Mercado Livre não registrada.")

    def _mercadopago(self, client: MercadoClient, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        single = {
            "mercadopago.payment_get": ("/v1/payments/{}", ("payment_id",)),
            "mercadopago.order_get": ("/v1/orders/{}", ("order_id",)),
            "mercadopago.refund_get": ("/v1/payments/{}/refunds/{}", ("payment_id", "refund_id")),
            "mercadopago.chargeback_get": ("/v1/chargebacks/{}", ("chargeback_id",)),
        }
        if tool in single:
            pattern, fields = single[tool]
            return self._single(client, TOOL_MODULE[tool], client.request("GET", pattern.format(*(self._id(args, field) for field in fields))))
        paths = {
            "mercadopago.payments_search": ("/v1/payments/search", "results"),
            "mercadopago.orders_search": ("/v1/orders/search", "results"),
            "mercadopago.chargebacks_search": ("/v1/chargebacks/search", "results"),
            "mercadopago.payment_methods_list": ("/v1/payment_methods", None),
        }
        if tool in paths:
            path, key = paths[tool]
            return self._paged(client, tool, path, self._dates_and_filters(args, {"status", "external_reference", "sort", "criteria", "payment_id"}), args, result_key=key)
        if tool == "mercadopago.refunds_list":
            return self._paged(client, tool, f"/v1/payments/{self._id(args, 'payment_id')}/refunds", {}, args)
        if tool.startswith("mercadopago.report_") or tool == "mercadopago.reports_list":
            return self._reports(client, tool, args)
        raise MercadoError("invalid_request", "Operação Mercado Pago não registrada.")

    def _reports(self, client: MercadoClient, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        family = args.get("family")
        base = self._report_base(family)
        if tool == "mercadopago.report_configuration_get":
            return self._single(client, TOOL_MODULE[tool], client.request("GET", base + "/config"))
        if tool == "mercadopago.report_configuration_set":
            body = {key: value for key, value in args.items() if key not in {"family", "confirm"}}
            body["format"] = "CSV"
            if str(family).startswith("activities_"):
                body["notification"] = {"type": "internal_sftp", "data": {}}
            return self._single(client, TOOL_MODULE[tool], client.request("PUT", base + "/config", body=body, retry=False))
        if tool == "mercadopago.report_create":
            self._validate_range(args)
            body = {"begin_date": args["date_from"], "end_date": args["date_to"]}
            return self._single(client, TOOL_MODULE[tool], client.request("POST", base, body=body, retry=False))
        if tool == "mercadopago.reports_list":
            return self._paged(client, tool, base, {}, args, result_key="results")
        report_id = self._id(args, "report_id")
        status = client.request("GET", base + "/" + report_id)
        if tool == "mercadopago.report_status_get":
            return self._single(client, TOOL_MODULE[tool], status)
        state = str(status.get("status", "")).casefold() if isinstance(status, dict) else ""
        if state not in {"available", "ready", "completed"}:
            raise MercadoError("report_not_ready", "Relatório remoto ainda não está disponível.")
        reader = ReportReader(self.root, client.defaults)
        raw = client.request_bytes("GET", base + "/" + report_id + "/file", max_bytes=client.defaults["max_report_bytes"])
        path = reader.cache(client.profile, report_id, raw)
        configuration = client.request("GET", base + "/config")
        separator = configuration.get("separator", ",") if isinstance(configuration, dict) else ","
        if separator not in {",", ";", "|"}:
            raise MercadoError("invalid_report_file", "Separador remoto não suportado.")
        return reader.rows(client.profile, family, report_id, path, int(args.get("limit", client.defaults["page_size"])), args.get("cursor"), separator)

    @staticmethod
    def _report_base(family: Any) -> str:
        mapping = {"released_money": "/v1/account/release_report", "account_money": "/v1/account/settlement_report", "activities_collection": "/v1/reporting/operations/collection", "activities_after_collection": "/v1/reporting/operations/after-collection", "activities_withdraw": "/v1/reporting/operations/withdraw"}
        if family not in mapping:
            raise MercadoError("invalid_request", "Família de relatório inválida.")
        return mapping[family]

    def _paged(self, client: MercadoClient, tool: str, path: str, query: dict[str, Any], args: dict[str, Any], result_key: str | None = None) -> dict[str, Any]:
        limit = int(args.get("limit", client.defaults["page_size"]))
        if not 1 <= limit <= client.defaults["max_page_size"]:
            raise MercadoError("invalid_request", "Limite de paginação inválido.")
        stable = dict(query)
        offset = decode_cursor(args["cursor"], tool, client.profile, stable) if args.get("cursor") else 0
        response = client.request("GET", path, query={**query, "limit": limit, "offset": offset})
        items = response.get(result_key, []) if result_key and isinstance(response, dict) else response
        if not isinstance(items, list):
            items = [items] if isinstance(items, dict) else []
        total = response.get("paging", {}).get("total") if isinstance(response, dict) else None
        has_more = len(items) == limit if not isinstance(total, int) else offset + len(items) < total
        cursor = encode_cursor(tool, client.profile, stable, offset + len(items)) if has_more else None
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
                if datetime.fromisoformat(start.replace("Z", "+00:00")) > datetime.fromisoformat(end.replace("Z", "+00:00")):
                    raise ValueError
            except (AttributeError, TypeError, ValueError) as error:
                raise MercadoError("invalid_request", "Intervalo de datas inválido ou invertido.") from error
