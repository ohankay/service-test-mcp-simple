"""The engine: configuration + SOAP/REST adapters + masking + audit. Shared by the MCP server, the CLI runner and tests."""

from __future__ import annotations

import os
import threading
from typing import Any

from .audit import AuditLog
from .config import ConfigError, EnvironmentConfig, ServiceConfig, Settings, load_settings
from .masking import Masker
from .normalize import dumps, json_ready
from .rest import RestAdapter
from .soap import SoapAdapter


class Engine:
    def __init__(self, settings: Settings | None = None) -> None:
        self._lock = threading.Lock()
        self.load_error: str | None = None
        if settings is None:
            try:
                settings = load_settings()
            except ConfigError as exc:
                self.load_error = str(exc)
                settings = Settings()
        self._init(settings)

    def _init(self, settings: Settings) -> None:
        self.settings = settings
        self.soap = SoapAdapter(settings)
        self.rest = RestAdapter(settings)
        self.masker = Masker(settings.masking)
        self.audit = AuditLog(settings.audit_enabled, settings.audit_dir)
        for svc in settings.services.values():
            for auth in [svc.auth, *svc.auth_overrides.values()]:
                for name in auth.secret_env_names():
                    self.masker.add_secret(os.environ.get(name))
        for name, value in os.environ.items():
            if name.startswith("STM_SECRET_"):
                self.masker.add_secret(value)

    def reload(self) -> None:
        with self._lock:
            self.close()
            try:
                settings = load_settings()
                self.load_error = None
            except ConfigError as exc:
                self.load_error = str(exc)
                settings = Settings()
            self._init(settings)

    def close(self) -> None:
        self.soap.close()
        self.rest.close()

    # -- lookup --------------------------------------------------------------------------------------------------
    def _require_config(self) -> None:
        if self.load_error:
            raise ConfigError(self.load_error)
        if not self.settings.services and not self.settings.broken:
            raise ConfigError("No services configured. Create services.yaml (see templates/services.yaml) and point "
                              "STM_CONFIG to it in mcp.json, or put it at %USERPROFILE%\\.service-test-mcp\\services.yaml")

    def resolve(self, service: str, environment: str | None) -> tuple[ServiceConfig, EnvironmentConfig]:
        self._require_config()
        svc = self.settings.service(service)
        env = self.settings.environment(environment)
        if svc.environments and env.name not in svc.environments:
            raise ConfigError(f"Service '{svc.name}' is not available in environment '{env.name}' "
                              f"(only: {', '.join(svc.environments)})")
        return svc, env

    def protocols(self) -> dict[str, str]:
        out = {name: s.protocol for name, s in self.settings.services.items()}
        out.update({name.lower(): p for name, p in list(out.items())})
        return out

    # -- calls (raw results, audited) ------------------------------------------------------------------------------
    def soap_call(self, service: str, operation: str, params: dict[str, Any] | None,
                  environment: str | None = None, origin: str = "tool") -> dict[str, Any]:
        svc, env = self.resolve(service, environment)
        if svc.protocol != "soap":
            raise ConfigError(f"'{svc.name}' is a REST service; use rest_call")
        result = self.soap.call(svc, env, operation, params)
        self._register_secrets(svc, env)
        self._audit(result, origin, request=params)
        return result

    def rest_call(self, service: str, method: str, path: str, query: dict[str, Any] | None = None,
                  headers: dict[str, Any] | None = None, body: Any = None, body_text: str | None = None,
                  form: dict[str, Any] | None = None, environment: str | None = None,
                  origin: str = "tool") -> dict[str, Any]:
        svc, env = self.resolve(service, environment)
        if svc.protocol != "rest":
            raise ConfigError(f"'{svc.name}' is a SOAP service; use soap_call")
        result = self.rest.call(svc, env, method, path, query, headers, body, body_text, form)
        self._register_secrets(svc, env)
        self._audit(result, origin, request={"query": query, "body": body if body is not None else body_text})
        return result

    def _register_secrets(self, svc: ServiceConfig, env: EnvironmentConfig) -> None:
        secrets = []
        if svc.protocol == "soap":
            bound = self.soap._cache.get((svc.name, env.name))
            secrets = bound.secrets if bound else []
        else:
            cached = self.rest._sessions.get((svc.name, env.name))
            secrets = cached[1] if cached else []
        for s in secrets:
            self.masker.add_secret(s)

    def _audit(self, result: dict[str, Any], origin: str, request: Any) -> None:
        self.audit.write(origin=origin, protocol=result.get("protocol"), service=result.get("service"),
                         environment=result.get("environment"),
                         operation=result.get("operation") or f"{result.get('method')} {result.get('path')}",
                         status=result.get("status"), fault=(result.get("fault") or {}).get("code")
                         if result.get("fault") else None, elapsed_ms=result.get("elapsed_ms"),
                         request=self.masker.mask(json_ready(request)),
                         response=self.masker.mask(json_ready(result.get("body"))))

    # -- views for the model -------------------------------------------------------------------------------------
    def view(self, obj: Any, limit: int | None = None, mask: bool = True) -> Any:
        """Masked, JSON-ready copy, with the serialized size capped."""
        masked = self.masker.mask(json_ready(obj)) if mask else json_ready(obj)
        return truncate(masked, limit or self.settings.max_response_chars)

    def result_view(self, result: dict[str, Any], include_raw: bool = False, include_headers: bool = False,
                    limit: int | None = None) -> dict[str, Any]:
        out = {k: v for k, v in result.items() if k not in ("raw", "headers")}
        if include_headers:
            out["headers"] = self.masker.mask_headers(result.get("headers") or {})
        if include_raw and result.get("raw"):
            out["raw"] = {k: self.masker.mask_text(v)[:20000] for k, v in result["raw"].items()}
        view = self.view(out, limit)
        view["success"] = result.get("fault") is None and result.get("error") is None and \
            (result.get("status") is None or int(result.get("status") or 0) < 400)
        return view


def truncate(obj: Any, limit: int) -> Any:
    """Keep the structure but shorten long lists / strings until the JSON fits in ``limit`` characters."""
    text = dumps(obj)
    if len(text) <= limit:
        return obj
    budget = [limit]

    def cut(value: Any, depth: int = 0) -> Any:
        if isinstance(value, str):
            return value if len(value) <= 2000 else value[:2000] + f"...(+{len(value) - 2000} chars)"
        if isinstance(value, list):
            keep = value[:20]
            out = [cut(v, depth + 1) for v in keep]
            if len(value) > 20:
                out.append(f"...(+{len(value) - 20} more items)")
            return out
        if isinstance(value, dict):
            return {k: cut(v, depth + 1) for k, v in value.items()}
        return value

    shortened = cut(obj)
    text = dumps(shortened)
    if len(text) <= budget[0]:
        return {"_truncated": True, **shortened} if isinstance(shortened, dict) else shortened
    return {"_truncated": True, "note": f"response is {len(text)} chars; showing the first {limit}. Use assertions or "
            "extract to check specific fields.", "preview": text[:limit]}
