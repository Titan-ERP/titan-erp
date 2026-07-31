from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
import xmlrpc.client
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_ATTEMPTS = 3
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}


class OdooError(RuntimeError):
    """A sanitized Odoo API failure."""


def load_env_file(path: Path, *, override: bool = False) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing environment file: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required setting: {name}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OdooConfig:
    url: str
    database: str
    api_key: str
    username: str = ""
    api_mode: str = "json2"
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    attempts: int = DEFAULT_ATTEMPTS
    allow_legacy_xmlrpc: bool = False

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "OdooConfig":
        if env_path is not None:
            load_env_file(env_path)
        mode = os.environ.get("ODOO_API_MODE", "json2").strip().casefold()
        if mode not in {"json2", "xmlrpc"}:
            raise RuntimeError("ODOO_API_MODE must be json2 or xmlrpc.")
        allow_legacy = env_bool("ODOO_ALLOW_LEGACY_XMLRPC")
        if mode == "xmlrpc" and not allow_legacy:
            raise RuntimeError(
                "Legacy XML-RPC is disabled. Set ODOO_ALLOW_LEGACY_XMLRPC=true only for an approved migration window."
            )
        return cls(
            url=required_env("ODOO_URL").rstrip("/"),
            database=required_env("ODOO_DB"),
            api_key=required_env("ODOO_API_KEY"),
            username=os.environ.get("ODOO_USERNAME", "").strip(),
            api_mode=mode,
            timeout_seconds=max(1, int(os.environ.get("ODOO_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))),
            attempts=max(1, int(os.environ.get("ODOO_RPC_ATTEMPTS", DEFAULT_ATTEMPTS))),
            allow_legacy_xmlrpc=allow_legacy,
        )


class OdooClient:
    """Odoo 19 JSON-2 client with an explicit, temporary XML-RPC fallback."""

    def __init__(self, config: OdooConfig):
        self.config = config
        self.uid: int | None = None
        self.models: _RetryingLegacyModels | xmlrpc.client.ServerProxy | None = None

    def connect(self) -> "OdooClient":
        if self.config.api_mode == "json2":
            self.call("res.users", "context_get")
            return self
        if not self.config.allow_legacy_xmlrpc:
            raise RuntimeError("Legacy XML-RPC is not allowed.")
        if not self.config.username:
            raise RuntimeError("ODOO_USERNAME is required for legacy XML-RPC.")
        socket.setdefaulttimeout(self.config.timeout_seconds)
        common = xmlrpc.client.ServerProxy(f"{self.config.url}/xmlrpc/2/common", allow_none=True)
        uid = common.authenticate(
            self.config.database,
            self.config.username,
            self.config.api_key,
            {},
        )
        if not uid:
            raise RuntimeError("Odoo authentication failed.")
        self.uid = int(uid)
        self.models = xmlrpc.client.ServerProxy(f"{self.config.url}/xmlrpc/2/object", allow_none=True)
        return self

    def call(self, model: str, method: str, *, ids: Sequence[int] | None = None, **params: Any) -> Any:
        if self.config.api_mode == "xmlrpc":
            return self._legacy_named_call(model, method, ids=ids, **params)
        body = dict(params)
        if ids is not None:
            body["ids"] = list(ids)
        payload = json.dumps(body, separators=(",", ":"), default=str).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.url}/json/2/{model}/{method}",
            data=payload,
            headers={
                "Authorization": f"bearer {self.config.api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "Titan-Odoo-Automation/0.2",
                "X-Odoo-Database": self.config.database,
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(1, self.config.attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = self._safe_http_error(exc)
                if exc.code not in RETRYABLE_HTTP or attempt >= self.config.attempts:
                    raise OdooError(detail) from exc
                last_error = exc
            except (OSError, TimeoutError, urllib.error.URLError) as exc:
                if attempt >= self.config.attempts:
                    raise OdooError(f"Odoo request failed: {type(exc).__name__}") from exc
                last_error = exc
            time.sleep(min(2 ** (attempt - 1), 8))
        raise OdooError(f"Odoo request failed: {type(last_error).__name__ if last_error else 'unknown'}")

    @staticmethod
    def _safe_http_error(exc: urllib.error.HTTPError) -> str:
        try:
            data = json.loads(exc.read().decode("utf-8", errors="replace"))
            name = str(data.get("name") or "OdooError").split(".")[-1]
            message = str(data.get("message") or "request rejected")
            return f"Odoo JSON-2 {exc.code} {name}: {message[:500]}"
        except Exception:
            return f"Odoo JSON-2 request failed with HTTP {exc.code}."

    def _legacy_named_call(
        self, model: str, method: str, *, ids: Sequence[int] | None = None, **params: Any
    ) -> Any:
        if self.uid is None or self.models is None:
            self.connect()
        args: list[Any] = [list(ids)] if ids is not None else []
        return self.models.execute_kw(  # type: ignore[union-attr]
            self.config.database,
            self.uid,
            self.config.api_key,
            model,
            method,
            args,
            params,
        )

    def execute(
        self,
        model: str,
        method: str,
        args: Sequence[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        """Compatibility adapter for common ORM calls.

        New code should use ``call`` with named parameters. Custom positional
        methods are intentionally rejected in JSON-2 mode.
        """
        args = list(args or [])
        kwargs = dict(kwargs or {})
        if self.config.api_mode == "xmlrpc":
            if self.uid is None or self.models is None:
                self.connect()
            return self.models.execute_kw(  # type: ignore[union-attr]
                self.config.database,
                self.uid,
                self.config.api_key,
                model,
                method,
                args,
                kwargs,
            )
        if method == "search_count":
            return self.call(model, method, domain=args[0] if args else [], **kwargs)
        if method in {"search", "search_read"}:
            return self.call(model, method, domain=args[0] if args else [], **kwargs)
        if method == "fields_get":
            fields = args[0] if args else None
            return self.call(model, method, allfields=fields, **kwargs)
        if method == "read":
            return self.call(model, method, ids=args[0] if args else [], **kwargs)
        if method == "create":
            values = args[0] if args else {}
            vals_list = values if isinstance(values, list) else [values]
            return self.call(model, method, vals_list=vals_list, **kwargs)
        if method == "write":
            return self.call(model, method, ids=args[0], vals=args[1], **kwargs)
        if method == "unlink":
            return self.call(model, method, ids=args[0], **kwargs)
        raise OdooError(
            f"JSON-2 requires named parameters for {model}.{method}; use OdooClient.call()."
        )

    def count(self, model: str, domain: list[Any] | None = None, *, context: dict[str, Any] | None = None) -> int:
        params: dict[str, Any] = {"domain": domain or []}
        if context:
            params["context"] = context
        return int(self.call(model, "search_count", **params))

    def fields(self, model: str) -> dict[str, Any]:
        return self.call(
            model,
            "fields_get",
            attributes=["string", "type", "required", "selection"],
        )

    def iter_search_read(
        self,
        model: str,
        domain: list[Any],
        fields: list[str],
        *,
        batch_size: int = 500,
        order: str = "id",
        context: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            params: dict[str, Any] = {
                "domain": domain,
                "fields": fields,
                "limit": batch_size,
                "offset": offset,
                "order": order,
            }
            if context:
                params["context"] = context
            rows = self.call(model, "search_read", **params)
            if not rows:
                return
            yield from rows
            if len(rows) < batch_size:
                return
            offset += len(rows)

    def search_read_all(
        self,
        model: str,
        domain: list[Any],
        fields: list[str],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return list(self.iter_search_read(model, domain, fields, **kwargs))


class _RetryingLegacyModels:
    def __init__(self, client: OdooClient):
        self.client = client

    def execute_kw(
        self,
        database: str,
        uid: int,
        api_key: str,
        model: str,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        if database != self.client.config.database or uid != self.client.uid or api_key != self.client.config.api_key:
            raise RuntimeError("Legacy call credentials do not match the connected shared client.")
        return self.client.execute(model, method, args, kwargs)


def connect_legacy(env_path: Path) -> tuple[str, int, str, _RetryingLegacyModels]:
    config = OdooConfig.from_env(env_path)
    if config.api_mode != "xmlrpc" or not config.allow_legacy_xmlrpc:
        raise RuntimeError(
            "connect_legacy requires ODOO_API_MODE=xmlrpc and ODOO_ALLOW_LEGACY_XMLRPC=true."
        )
    client = OdooClient(config).connect()
    assert client.uid is not None
    return client.config.database, client.uid, client.config.api_key, _RetryingLegacyModels(client)
