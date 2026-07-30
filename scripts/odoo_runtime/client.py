from __future__ import annotations

import os
import socket
import time
import xmlrpc.client
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence


DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_ATTEMPTS = 3
RETRYABLE = (OSError, TimeoutError, xmlrpc.client.ProtocolError)


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


@dataclass(frozen=True)
class OdooConfig:
    url: str
    database: str
    username: str
    api_key: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    attempts: int = DEFAULT_ATTEMPTS

    @classmethod
    def from_env(cls, env_path: Path | None = None) -> "OdooConfig":
        if env_path is not None:
            load_env_file(env_path)
        return cls(
            url=required_env("ODOO_URL").rstrip("/"),
            database=required_env("ODOO_DB"),
            username=required_env("ODOO_USERNAME"),
            api_key=required_env("ODOO_API_KEY"),
            timeout_seconds=int(os.environ.get("ODOO_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)),
            attempts=max(1, int(os.environ.get("ODOO_RPC_ATTEMPTS", DEFAULT_ATTEMPTS))),
        )


class OdooClient:
    def __init__(self, config: OdooConfig):
        self.config = config
        socket.setdefaulttimeout(config.timeout_seconds)
        self.common = xmlrpc.client.ServerProxy(f"{config.url}/xmlrpc/2/common", allow_none=True)
        self.models = xmlrpc.client.ServerProxy(f"{config.url}/xmlrpc/2/object", allow_none=True)
        self.uid: int | None = None

    def connect(self) -> "OdooClient":
        uid = self.common.authenticate(
            self.config.database,
            self.config.username,
            self.config.api_key,
            {},
        )
        if not uid:
            raise RuntimeError("Odoo authentication failed.")
        self.uid = int(uid)
        return self

    def execute(
        self,
        model: str,
        method: str,
        args: Sequence[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        if self.uid is None:
            self.connect()
        last_error: Exception | None = None
        for attempt in range(1, self.config.attempts + 1):
            try:
                return self.models.execute_kw(
                    self.config.database,
                    self.uid,
                    self.config.api_key,
                    model,
                    method,
                    list(args or []),
                    kwargs or {},
                )
            except RETRYABLE as exc:
                last_error = exc
                if attempt >= self.config.attempts:
                    raise
                time.sleep(min(2 ** (attempt - 1), 8))
        raise last_error or RuntimeError("Unknown Odoo RPC failure")

    def count(self, model: str, domain: list[Any] | None = None, *, context: dict[str, Any] | None = None) -> int:
        kwargs = {"context": context} if context else {}
        return int(self.execute(model, "search_count", [domain or []], kwargs))

    def fields(self, model: str) -> dict[str, Any]:
        return self.execute(model, "fields_get", [], {"attributes": ["string", "type", "required"]})

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
            kwargs: dict[str, Any] = {
                "fields": fields,
                "limit": batch_size,
                "offset": offset,
                "order": order,
            }
            if context:
                kwargs["context"] = context
            rows = self.execute(model, "search_read", [domain], kwargs)
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
    client = OdooClient(OdooConfig.from_env(env_path)).connect()
    assert client.uid is not None
    return client.config.database, client.uid, client.config.api_key, _RetryingLegacyModels(client)
