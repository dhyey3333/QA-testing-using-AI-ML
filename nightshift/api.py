"""API tests: requests and checks, with no browser and no model.

    name: order-api
    url: http://localhost:5180/
    requests:
      - name: sign in
        post: /api/login
        json: {email: "{{email}}", password: "{{password}}"}
        expect:
          status: 200
          json: {user.name: Test Shopper}
      - name: place an order
        post: /api/order
        json: {items: [{id: coffee, qty: 1}], name: Test, address: x, city: y, pincode: "411001", payment: cod}
        expect:
          status: 201
          json:
            orderId: /^KC-\\d+$/     # a value between slashes is a regular expression
            total: 240
          max_ms: 2000
        save: {order: orderId}      # later requests can use {{order}}

A backend check is deterministic, so a model would only add cost and doubt here.
Results become ordinary RunResults: the same reports, defect analysis, JUnit and
dashboard as browser tests, and an HTTP 5xx fails the test exactly as it does there.
"""

from __future__ import annotations

import json
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .actions import Action
from .result import Check, RunResult, Step

METHODS = ("get", "post", "put", "patch", "delete")
EXISTS, MISSING = "*", "<missing>"
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_PATH_RE = re.compile(r"[^.\[\]]+|\[\d+\]")


class ApiSpecError(ValueError):
    """A request block that can't be run. spec.py turns it into a SpecError with the file name."""


@dataclass(frozen=True)
class ApiRequest:
    name: str
    method: str
    path: str
    json: Any = None
    form: dict | None = None
    headers: dict = field(default_factory=dict)
    expect: dict = field(default_factory=dict)
    save: dict = field(default_factory=dict)

    def describe(self) -> str:
        return f"{self.method} {self.path}" + (f" ({self.name})" if self.name else "")

    def expectations(self) -> list[str]:
        label = self.name or f"{self.method} {self.path}"
        lines = []
        if "status" in self.expect:
            lines.append(f"{label}: status is {self.expect['status']}")
        for path, wanted in (self.expect.get("json") or {}).items():
            lines.append(f"{label}: {path} {_describe_match(wanted)}")
        for name, wanted in (self.expect.get("headers") or {}).items():
            lines.append(f"{label}: header {name} {_describe_match(wanted)}")
        if "contains" in self.expect:
            lines.append(f"{label}: the body contains {self.expect['contains']!r}")
        if "max_ms" in self.expect:
            lines.append(f"{label}: answers within {self.expect['max_ms']} ms")
        return lines


def parse_requests(raw: object) -> tuple[ApiRequest, ...]:
    if not isinstance(raw, list) or not raw:
        raise ApiSpecError("requests must be a non-empty list")
    requests = []
    for number, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ApiSpecError(f"request {number} must be a mapping")
        methods = [m for m in METHODS if m in item]
        if len(methods) != 1:
            raise ApiSpecError(f"request {number} needs exactly one of: {', '.join(METHODS)}")
        path = str(item[methods[0]])
        if not (path.startswith("/") or path.startswith("{{")) or path.startswith("//"):
            raise ApiSpecError(f"request {number}: the path must start with / (a path on the spec's url)")
        expect = item.get("expect") or {}
        if not isinstance(expect, dict):
            raise ApiSpecError(f"request {number}: expect must be a mapping (status, json, headers, contains, max_ms)")
        unknown = set(expect) - {"status", "json", "headers", "contains", "max_ms"}
        if unknown:
            raise ApiSpecError(f"request {number}: unknown expect key(s): {', '.join(sorted(unknown))}")
        requests.append(ApiRequest(
            name=str(item.get("name") or ""), method=methods[0].upper(), path=path,
            json=item.get("json"), form=item.get("form"), headers=dict(item.get("headers") or {}),
            expect=expect, save=dict(item.get("save") or {}),
        ))
    return tuple(requests)


def run_api_spec(spec, out_dir: Path, log=None) -> RunResult:
    """Send the requests in order, checking each. Stops at the first request that fails:
    later ones usually depend on it."""
    from .prompts import mask
    from .report import write_spec_report

    log = log or (lambda line: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = RunResult(spec=spec.name, url=spec.url, model="none (API test)", out_dir=str(out_dir),
                       started_at=datetime.now().isoformat(timespec="seconds"), browser="none", viewport="")
    values = dict(spec.data)
    started = time.perf_counter()
    origin = urlsplit(spec.url)
    with httpx.Client(timeout=30, follow_redirects=False, transport=_transport_for(spec.url)) as client:
        for index, request in enumerate(spec.requests, 1):
            url = urljoin(spec.url, _fill(request.path, values))
            if urlsplit(url).netloc != origin.netloc:
                result.verdict, result.reason = "error", f"{request.describe()} leaves the spec's site"
                break
            body = _fill(request.json, values) if request.json is not None else None
            form = _fill(request.form, values) if request.form is not None else None
            sent = time.perf_counter()
            try:
                response = client.request(request.method, url, json=body, data=form,
                                          headers=_fill(request.headers, values))
            except httpx.HTTPError as exc:
                result.verdict, result.reason = "error", f"{request.describe()}: could not connect ({type(exc).__name__})"
                break
            took = round((time.perf_counter() - sent) * 1000)
            shown = mask(json.dumps(request.json, ensure_ascii=False), spec.data) if request.json is not None else ""
            step = Step(index, Action("request", value=f"{request.method} {request.path}"),
                        f"{request.method} {request.path} -> {response.status_code} ({took} ms)",
                        thought=shown[:300], outcome="changed", action_ms=took, target_label=request.name)
            result.steps.append(step)
            result.final_url, result.final_text = url, mask(response.text, spec.data)[:6_000]
            log(f"{index:>2}. {step.description}")

            if response.status_code >= 500:
                result.app_errors.append(f"HTTP {response.status_code} on {request.method} {urlsplit(url).path}")
            checks = _check(request, response, took, values)
            result.checks.extend(checks)
            for check in checks:
                log(f"   [{'ok' if check.holds else 'NO'}] {check.expected}" + ("" if check.holds else f" ({check.why})"))
            if result.app_errors:
                result.verdict, result.reason = "fail", result.app_errors[0]
                break
            failing = [c for c in checks if not c.holds]
            if failing:
                step.outcome = "failed: expectations not met"
                result.verdict = "fail"
                result.reason = f"not returned: {failing[0].expected} ({failing[0].why})"
                break
            for name, path in request.save.items():
                found, value = _lookup(_json(response), str(path))
                if found:
                    values[name] = str(value)
        else:
            result.verdict, result.reason = "pass", "every expected result was returned"

    result.duration_s = round(time.perf_counter() - started, 2)
    (out_dir / "result.json").write_text(json.dumps(result.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
    write_spec_report(result, spec)
    return result


def _transport_for(url: str) -> httpx.HTTPTransport | None:
    """Connect to "localhost" over IPv4 when an IPv4 server is listening there.

    Found in a live run: Windows tries IPv6 (::1) first for "localhost" and waits about
    two seconds before falling back to 127.0.0.1, so a max_ms check blamed the app for
    the operating system's delay.
    """
    parts = urlsplit(url)
    if parts.hostname != "localhost":
        return None
    port = parts.port or (443 if parts.scheme == "https" else 80)
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            return None
    return httpx.HTTPTransport(local_address="0.0.0.0")


def _check(request: ApiRequest, response: httpx.Response, took: int, values: dict) -> list[Check]:
    label = request.name or f"{request.method} {request.path}"
    checks = []
    expect = request.expect
    if "status" in expect:
        wanted = expect["status"]
        allowed = [int(s) for s in (wanted if isinstance(wanted, list) else [wanted])]
        checks.append(Check(f"{label}: status is {wanted}", [f"status {response.status_code}"],
                            "" if response.status_code in allowed else f"got {response.status_code}",
                            response.status_code in allowed))
    data = _json(response)
    for path, wanted in (expect.get("json") or {}).items():
        found, actual = _lookup(data, str(path))
        holds, why = _matches(found, actual, _fill(wanted, values))
        evidence = [f"{path} = {json.dumps(actual, ensure_ascii=False)}"] if found else []
        checks.append(Check(f"{label}: {path} {_describe_match(wanted)}", evidence, why, holds))
    for name, wanted in (expect.get("headers") or {}).items():
        actual = response.headers.get(str(name))
        holds, why = _matches(actual is not None, actual, _fill(wanted, values))
        checks.append(Check(f"{label}: header {name} {_describe_match(wanted)}",
                            [f"{name}: {actual}"] if actual is not None else [], why, holds))
    if "contains" in expect:
        needle = str(_fill(expect["contains"], values))
        holds = needle in response.text
        checks.append(Check(f"{label}: the body contains {needle!r}", [needle] if holds else [],
                            "" if holds else "not in the response body", holds))
    if "max_ms" in expect:
        limit = int(expect["max_ms"])
        checks.append(Check(f"{label}: answers within {limit} ms", [f"{took} ms"],
                            "" if took <= limit else f"took {took} ms", took <= limit))
    return checks


def _matches(found: bool, actual: Any, wanted: Any) -> tuple[bool, str]:
    if wanted == MISSING:
        return (not found, "" if not found else f"it is there: {json.dumps(actual, ensure_ascii=False)}")
    if not found:
        return False, "missing from the response"
    if wanted == EXISTS:
        return True, ""
    if isinstance(wanted, str) and len(wanted) > 1 and wanted.startswith("/") and wanted.endswith("/"):
        ok = re.search(wanted[1:-1], str(actual)) is not None
        return ok, "" if ok else f"got {json.dumps(actual, ensure_ascii=False)}"
    if isinstance(wanted, (int, float)) and not isinstance(wanted, bool) and isinstance(actual, (int, float)):
        ok = float(actual) == float(wanted)
    else:
        ok = actual == wanted
    return ok, "" if ok else f"got {json.dumps(actual, ensure_ascii=False)}"


def _describe_match(wanted: Any) -> str:
    if wanted == EXISTS:
        return "is present"
    if wanted == MISSING:
        return "is absent"
    if isinstance(wanted, str) and len(wanted) > 1 and wanted.startswith("/") and wanted.endswith("/"):
        return f"matches {wanted}"
    return f"is {json.dumps(wanted, ensure_ascii=False)}"


def _lookup(data: Any, path: str) -> tuple[bool, Any]:
    """`user.name`, `items[0].id`, `[0].name`, `items.length`."""
    current = data
    for part in _PATH_RE.findall(path):
        if part.startswith("["):
            index = int(part[1:-1])
            if not isinstance(current, list) or index >= len(current):
                return False, None
            current = current[index]
        elif part == "length" and isinstance(current, (list, str, dict)):
            current = len(current)
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _fill(value: Any, values: dict) -> Any:
    """{{name}} in any string, at any depth. A string that is only a placeholder keeps the value's text."""
    if isinstance(value, str):
        return _PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [_fill(v, values) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, values) for k, v in value.items()}
    return value
