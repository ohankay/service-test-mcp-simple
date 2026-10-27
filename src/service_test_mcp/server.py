"""MCP server exposing SOAP/REST service-testing tools (stdio for VS Code, or streamable HTTP for other agents)."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import ConfigError
from .engine import Engine
from .normalize import json_ready
from .scenario.asserts import evaluate, find
from .scenario.model import ScenarioError
from .scenario.runner import Runner
from .scenario.template import Context, TemplateError

log = logging.getLogger("service_test_mcp")

INSTRUCTIONS = """\
Service testing tools for WCF/SOAP and REST services on TEST environments. Never use a terminal, PowerShell, curl or
Invoke-WebRequest for service calls - use these tools.
- Discover: list_services -> describe_service -> describe_operation (input schema + example_params for SOAP,
  OpenAPI details for REST).
- Explore: soap_call / rest_call. Use extract={"token": "$.Token"} to keep values (tokens, ids) as session variables
  and send them later as {{token}} - masked output values like *** can not be typed back.
  check_last_response evaluates assertions on the last call deterministically.
- Repeatable tests are YAML scenario files (steps: service + operation/params or method/path, assert, extract).
  Write them with your file tools, then validate_scenario, then run_scenario / run_test_suite.
- Assertions: fault, status, responseTimeMs, header, schema, or path (JSONPath) + one operator: equals, notEquals,
  equalsIgnoreCase, contains, notContains, startsWith, endsWith, matches, in, notIn, exists, notEmpty, isNull, type,
  numeric {gt,gte,lt,lte,eq,ne,between,tolerance}, count, length, schema. 'all: true' applies to every match.
- Variables: {{name}}, {{extracted.value}}, {{env.var}}, {{secret.STM_SECRET_X}}, {{$uuid}}, {{$today}}, {{$now}},
  {{$date(+1)}}, {{$random_digits(8)}}.
- Sensitive values in responses are masked (***1234); assertions and extract use the real values.
- Mutating operations are blocked unless the environment allows them. Production environments are refused.
"""

mcp = FastMCP("service-test-mcp", instructions=INSTRUCTIONS)
_engine: Engine | None = None
_engine_lock = threading.Lock()
_last: dict[str, Any] = {}
_vars: dict[str, Any] = {}   # session variables saved by 'extract' on soap_call / rest_call
_EXPECTED = (ConfigError, ScenarioError, TemplateError, ValueError, FileNotFoundError, PermissionError)
READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
CALL = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)


def engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = Engine()
        return _engine


def set_engine(value: Engine | None) -> None:
    global _engine
    with _engine_lock:
        _engine = value
    _last.clear()
    _vars.clear()


def _context(e: Engine, environment: str | None) -> Context:
    e._require_config()
    env = e.settings.environment(environment)
    return Context(variables=_vars, env_variables=env.variables, on_secret=e.masker.add_secret)


def _extract(result: dict[str, Any], extract: dict[str, str] | None) -> dict[str, Any]:
    saved: dict[str, Any] = {}
    errors = []
    for name, path in (extract or {}).items():
        values = find(result.get("body"), str(path))
        if not values:
            errors.append(f"{name}: {path} not found")
            continue
        saved[name] = values[0] if len(values) == 1 else values
    _vars.update(saved)
    out: dict[str, Any] = {"saved_variables": saved} if saved else {}
    if errors:
        out["extract_errors"] = errors
    return out


def _error(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, ScenarioError):
        return {"success": False, "error": {"type": "ScenarioError", "problems": exc.problems}}
    if isinstance(exc, _EXPECTED):
        return {"success": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
    log.debug("tool error", exc_info=exc)
    message = str(exc)
    hint = None
    if "SSL" in message or "CERTIFICATE" in message.upper():
        hint = "TLS verification failed: set verify_tls to the CA bundle (.pem) path of the environment, or false on test"
    elif "Max retries" in message or "Failed to establish" in message or "NameResolution" in message:
        hint = "Host not reachable: check the URL, VPN/proxy (environment 'proxy') and firewall"
    elif "401" in message:
        hint = "Unauthorized: check the service's auth type (windows / ntlm / basic / bearer) and credentials"
    out: dict[str, Any] = {"success": False, "error": {"type": type(exc).__name__, "message": message[:3000]}}
    if hint:
        out["error"]["hint"] = hint
    return out


async def _call(fn: Callable[[], Any]) -> str:
    def guarded() -> Any:
        try:
            result = fn()
            if isinstance(result, dict) and "success" not in result:
                result = {"success": True, **result}
            return result
        except Exception as exc:  # noqa: BLE001 - reported to the model as a structured error
            return engine().view(_error(exc))

    result = await anyio.to_thread.run_sync(guarded)
    return json.dumps(json_ready(result), ensure_ascii=False, default=str, separators=(",", ":"))


# -- discovery -------------------------------------------------------------------------------------------------
@mcp.tool(annotations=READ)
async def list_services(environment: str | None = None) -> str:
    """List configured services (SOAP/REST), environments and the default environment. Start here."""
    def run() -> dict[str, Any]:
        e = engine()
        s = e.settings
        out: dict[str, Any] = {"config_file": s.path, "default_environment": s.default_environment,
                               "environments": {n: {"description": v.description, "allow_mutating": v.allow_mutating,
                                                    "variables": sorted(v.variables)}
                                                for n, v in s.environments.items()},
                               "services": [svc.public(environment) for svc in s.services.values()]}
        if s.broken:
            out["broken_services"] = s.broken
        if e.load_error:
            out["config_error"] = e.load_error
        if not s.services and not e.load_error:
            out["note"] = ("No services configured. Set STM_CONFIG in mcp.json to a services.yaml "
                           "(template: templates/services.yaml).")
        return e.view(out)
    return await _call(run)


@mcp.tool(annotations=READ)
async def describe_service(service: str, environment: str | None = None) -> str:
    """SOAP: WSDL ports, selected address and operation names. REST: base_url and OpenAPI operations (if configured)."""
    def run() -> dict[str, Any]:
        e = engine()
        svc, env = e.resolve(service, environment)
        details = e.soap.describe_service(svc, env) if svc.protocol == "soap" else e.rest.describe_service(svc, env)
        return e.view({"service": svc.public(env.name), "environment": env.name, **details}, mask=False)
    return await _call(run)


@mcp.tool(annotations=READ)
async def describe_operation(service: str, operation: str, environment: str | None = None) -> str:
    """Input/output schema of an operation. SOAP: operation name -> input_schema, example_params, output_schema.
    REST: 'METHOD /path' or operationId from the OpenAPI document."""
    def run() -> dict[str, Any]:
        e = engine()
        svc, env = e.resolve(service, environment)
        if svc.protocol == "soap":
            return e.view(e.soap.describe_operation(svc, env, operation), 60000, mask=False)
        return e.view(e.rest.describe_operation(svc, env, operation), 60000, mask=False)
    return await _call(run)


@mcp.tool(annotations=READ)
async def check_service(service: str | None = None, environment: str | None = None) -> str:
    """Connectivity check without calling an operation: SOAP loads the WSDL and binds the port; REST requests base_url.
    Without 'service' every service is checked."""
    def run() -> dict[str, Any]:
        e = engine()
        e._require_config()
        names = [service] if service else list(e.settings.services)
        report: dict[str, Any] = {}
        for name in names:
            try:
                svc, env = e.resolve(name, environment)
                if svc.protocol == "soap":
                    info = e.soap.describe_service(svc, env)
                    report[svc.name] = {"ok": True, "environment": env.name, "address": info.get("address"),
                                        "operations": len(info.get("operations") or [])}
                else:
                    res = e.rest.call(svc, env, "GET", "")
                    ok = res.get("error") is None
                    report[svc.name] = {"ok": ok, "environment": env.name, "http_status": res.get("status"),
                                        **({"error": res["error"]} if not ok else {})}
            except Exception as exc:  # noqa: BLE001
                report[name] = {"ok": False, **_error(exc)["error"]}
        for name, why in e.settings.broken.items():
            if not service or service == name:
                report[name] = {"ok": False, "type": "ConfigError", "message": why}
        return e.view({"services": report, "all_ok": all(v.get("ok") for v in report.values())})
    return await _call(run)


# -- calls -----------------------------------------------------------------------------------------------------
@mcp.tool(annotations=CALL)
async def soap_call(service: str, operation: str, params: dict[str, Any] | None = None,
                    environment: str | None = None, extract: dict[str, str] | None = None,
                    include_raw: bool = False) -> str:
    """Call a SOAP/WCF operation. params = JSON object of the operation's input elements (see describe_operation).
    Values may use {{variables}}. extract = {name: JSONPath} saves values from the response as session variables
    (e.g. {"token": "$.Token"}) so later calls can send {{token}} even though it is masked in the output.
    Returns status, fault (code/message/detail) or body (JSON), elapsed_ms. include_raw adds the SOAP envelopes."""
    def run() -> dict[str, Any]:
        e = engine()
        rendered = _context(e, environment).render(params or {})
        result = e.soap_call(service, operation, rendered, environment)
        _last.clear()
        _last.update(result)
        return {**e.result_view(result, include_raw=include_raw), **e.view(_extract(result, extract))}
    return await _call(run)


@mcp.tool(annotations=CALL)
async def rest_call(service: str, method: str, path: str, query: dict[str, Any] | None = None,
                    headers: dict[str, Any] | None = None, body: Any = None, body_text: str | None = None,
                    environment: str | None = None, extract: dict[str, str] | None = None,
                    include_headers: bool = False) -> str:
    """Call a REST endpoint. path is relative to the service base_url (e.g. /hesaplar/123). body = JSON value,
    body_text = raw text (XML, form). path, query, headers and body may use {{variables}}
    (e.g. headers {"Authorization": "Bearer {{token}}"}). extract = {name: JSONPath} saves session variables.
    Returns status, body (parsed JSON or text), elapsed_ms."""
    def run() -> dict[str, Any]:
        e = engine()
        ctx = _context(e, environment)
        result = e.rest_call(service, method, ctx.render(path), ctx.render(query), ctx.render(headers),
                             ctx.render(body), ctx.render(body_text), None, environment)
        _last.clear()
        _last.update(result)
        return {**e.result_view(result, include_headers=include_headers), **e.view(_extract(result, extract))}
    return await _call(run)


@mcp.tool(annotations=READ)
async def session_variables(clear: bool = False) -> str:
    """Show (masked) or clear the session variables saved by 'extract' on soap_call / rest_call."""
    def run() -> dict[str, Any]:
        e = engine()
        if clear:
            _vars.clear()
        return e.view({"variables": dict(_vars)})
    return await _call(run)


@mcp.tool(annotations=READ)
async def check_last_response(assertions: list[dict[str, Any]]) -> str:
    """Evaluate assertions against the last soap_call / rest_call response (real values, deterministic).
    Use it to try assertions before writing them into a scenario file."""
    def run() -> dict[str, Any]:
        e = engine()
        if not _last:
            raise ValueError("No call yet: run soap_call or rest_call first")
        checks = evaluate(assertions, _last)
        return e.view({"service": _last.get("service"), "operation": _last.get("operation") or
                       f"{_last.get('method')} {_last.get('path')}", "passed": all(c["passed"] for c in checks),
                       "assertions": checks})
    return await _call(run)


# -- scenarios -------------------------------------------------------------------------------------------------
@mcp.tool(annotations=READ)
async def validate_scenario(path: str | None = None, content: str | None = None) -> str:
    """Validate scenario YAML (a file, a folder, or inline 'content'): structure, services, assertion syntax,
    JSONPath, and variables used before they are defined. Does not call any service."""
    def run() -> dict[str, Any]:
        e = engine()
        return e.view(Runner(e).validate(path, content))
    return await _call(run)


@mcp.tool(annotations=CALL)
async def run_scenario(path: str | None = None, content: str | None = None, environment: str | None = None,
                       variables: dict[str, Any] | None = None, verbosity: str = "failures") -> str:
    """Run one scenario file (or inline YAML 'content'). Steps run in order; extract passes values to later steps.
    verbosity: summary | failures (default: details only for failed steps) | full."""
    def run() -> dict[str, Any]:
        e = engine()
        return e.view(Runner(e).run(path, content, environment, variables, verbosity=verbosity), 60000)
    return await _call(run)


@mcp.tool(annotations=CALL)
async def run_test_suite(path: str, environment: str | None = None, tags: list[str] | None = None,
                         verbosity: str = "summary", junit_path: str | None = None) -> str:
    """Run every scenario under a folder (recursively), optionally only those with one of 'tags'. Returns a summary
    and failed assertions; junit_path writes a JUnit XML report."""
    def run() -> dict[str, Any]:
        e = engine()
        out = Runner(e).run(path, None, environment, None, tags, verbosity, junit_path)
        if verbosity == "summary":
            for r in out["results"]:
                r["steps"] = [s for s in r["steps"] if s.get("status") in ("failed", "error")]
        return e.view(out, 60000)
    return await _call(run)


@mcp.tool(annotations=READ)
async def reload_config() -> str:
    """Re-read services.yaml after it was edited (no server restart needed)."""
    def run() -> dict[str, Any]:
        e = engine()
        e.reload()
        _last.clear()
        _vars.clear()
        out = {"config_file": e.settings.path, "services": sorted(e.settings.services),
               "environments": sorted(e.settings.environments)}
        if e.settings.broken:
            out["broken_services"] = e.settings.broken
        if e.load_error:
            out["config_error"] = e.load_error
        return out
    return await _call(run)


TOOL_NAMES = ("list_services", "describe_service", "describe_operation", "check_service", "soap_call", "rest_call",
              "session_variables", "check_last_response", "validate_scenario", "run_scenario", "run_test_suite",
              "reload_config")


def run_stdio() -> None:
    mcp.run("stdio")


def run_http(host: str, port: int, token: str | None) -> None:
    """Streamable HTTP transport (endpoint /mcp) for agents outside VS Code. Bearer token required if set."""
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    local = host in ("127.0.0.1", "localhost", "::1")
    if not local:
        if not token:
            raise SystemExit("Listening on a non-local address needs a token: set STM_HTTP_TOKEN (clients send "
                             "'Authorization: Bearer <token>').")
        # Remote clients use the machine's name in the Host header; the bearer token protects the endpoint.
        mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp.settings.host, mcp.settings.port = host, port
    app = mcp.streamable_http_app()
    if token:
        inner = app

        async def guarded(scope, receive, send):  # type: ignore[no-untyped-def]
            if scope["type"] == "http":
                headers = dict(scope.get("headers") or [])
                if headers.get(b"authorization", b"").decode() != f"Bearer {token}":
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                    return
            await inner(scope, receive, send)

        app = guarded  # type: ignore[assignment]
    uvicorn.run(app, host=host, port=port, log_level="warning")
