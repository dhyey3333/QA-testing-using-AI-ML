"""Test specs: one user flow in plain English, loaded from YAML.

    name: checkout
    url: http://localhost:5180/
    steps:
      - log in with the test account
      - ...
    expect:
      - a confirmation page shows an order number
    data:
      email: shopper@example.test
      password: ${SHOP_PASSWORD}     # read from the environment, so CI secrets stay out of git

`data` values are typed into the page by placeholder ({{email}}). The model only
ever writes the placeholder; the runner swaps in the value right before typing.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yaml

DEFAULT_MAX_STEPS = 30

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_KEY_RE = re.compile(r"[A-Za-z0-9_]+")


class SpecError(ValueError):
    """A spec that can't be run. The message names the file and the problem."""


@dataclass(frozen=True)
class Spec:
    name: str
    url: str
    steps: tuple[str, ...]
    expect: tuple[str, ...]
    data: dict[str, str] = field(default_factory=dict)
    max_steps: int = DEFAULT_MAX_STEPS
    path: Path | None = None
    raw_data: dict[str, str] = field(default_factory=dict)  # before ${ENV} expansion; export keeps secrets as env reads
    # Test-case metadata, the way a QA team files tests. All optional.
    title: str = ""  # a one-line test case title
    requirements: tuple[str, ...] = ()  # the requirement ids this test covers, for traceability
    technique: str = ""  # positive | negative | boundary | ...: how the case was designed
    priority: str = ""  # high | medium | low
    # "ui": steps in a browser, run by the agent. "api": HTTP requests and checks (api.py), no model.
    kind: str = "ui"
    requests: tuple = ()
    # Where emails to the test address can be read (inbox.py), e.g. a Mailpit URL. Overrides INBOX_URL.
    inbox: str = ""

    def with_base_url(self, base_url: str) -> Spec:
        """Point the spec at another deployment (staging, a CI preview), keeping its path.
        An inbox on the same host as the app moves with it."""
        base = urlsplit(base_url)
        own = urlsplit(self.url)
        url = urlunsplit((base.scheme, base.netloc, own.path or "/", own.query, own.fragment))
        inbox = self.inbox
        if inbox and urlsplit(inbox).netloc == own.netloc:
            parts = urlsplit(inbox)
            inbox = urlunsplit((base.scheme, base.netloc, parts.path, parts.query, parts.fragment))
        return replace(self, url=url, inbox=inbox)


def load_spec(path: Path, expand_env: bool = True) -> Spec:
    """Read and check a spec. With expand_env=False, ${VAR} data is kept as written:
    for showing or checking a spec without needing its secrets set."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SpecError(f"{path}: {exc}") from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark else ""
        raise SpecError(
            f"{path}{where}: this is not valid YAML. The usual cause is a line that starts with a quote, "
            "like  - \"Save\" is shown  . Quote the whole line instead:  - 'the \"Save\" button is shown'"
        ) from exc
    if not isinstance(raw, dict):
        raise SpecError(f"{path}: expected keys like url, steps and expect")

    url = raw.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise SpecError(f"{path}: url must be an http:// or https:// URL")

    data_raw = raw.get("data") or {}
    if not isinstance(data_raw, dict):
        raise SpecError(f"{path}: data must be a mapping of name: value")
    raw_data = {str(key): str(value) for key, value in data_raw.items()}
    data = {key: _expand_env(value, path) if expand_env else value for key, value in raw_data.items()}
    for key in data:
        if not _KEY_RE.fullmatch(key):
            raise SpecError(f"{path}: data key {key!r} may only use letters, digits and _")

    max_steps = raw.get("max_steps", DEFAULT_MAX_STEPS)
    if not isinstance(max_steps, int) or max_steps < 1:
        raise SpecError(f"{path}: max_steps must be a positive whole number")

    requirements = raw.get("requirements", raw.get("requirement")) or []
    if isinstance(requirements, (str, int)):
        requirements = [requirements]
    if not isinstance(requirements, list):
        raise SpecError(f"{path}: requirements must be an id or a list of ids, like [R1, R4]")

    kind = str(raw.get("kind") or ("api" if "requests" in raw else "ui")).lower()
    requests: tuple = ()
    if kind == "api":
        from .api import ApiSpecError, parse_requests  # imported here to avoid an import cycle (api -> prompts -> spec)

        try:
            requests = parse_requests(raw.get("requests"))
        except ApiSpecError as exc:
            raise SpecError(f"{path}: {exc}") from None
        # Steps and expected results are derived, so reports, the test-case document and
        # the traceability matrix read an API test like any other.
        steps = tuple(r.describe() for r in requests)
        expect = tuple(line for r in requests for line in r.expectations()) or ("every request succeeds",)
    elif kind == "ui" and raw.get("goal") and not raw.get("steps"):
        # A URL and a plain-English goal: the agent works out the steps. With no expected
        # results given, the goal itself must be shown done, proven like any other check.
        goal = str(raw["goal"]).strip()
        steps = (goal,)
        expect = _strings(raw["expect"], "expect", path) if raw.get("expect") else (goal_expectation(goal),)
    elif kind == "ui":
        steps, expect = _strings(raw.get("steps"), "steps", path), _strings(raw.get("expect"), "expect", path)
    else:
        raise SpecError(f"{path}: kind must be ui or api")
    inbox = str(raw.get("inbox") or "")
    if inbox and not inbox.startswith(("http://", "https://")):
        raise SpecError(f"{path}: inbox must be the http(s) URL of a Mailpit-style mail API")

    return Spec(
        name=str(raw.get("name") or path.stem),
        url=url,
        steps=steps,
        expect=expect,
        data=data,
        max_steps=max_steps,
        path=path,
        raw_data=raw_data,
        title=str(raw.get("title") or ""),
        requirements=tuple(str(r).strip() for r in requirements if str(r).strip()),
        technique=str(raw.get("technique") or ""),
        priority=str(raw.get("priority") or ""),
        kind=kind,
        requests=requests,
        inbox=inbox,
    )


def load_specs(paths: Iterable[Path]) -> list[Spec]:
    """Load spec files, and every .yaml/.yml file in any folder given."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            found = sorted([*path.glob("*.yaml"), *path.glob("*.yml")])
            if not found:
                raise SpecError(f"{path}: no .yaml specs in this folder")
            files += found
        elif path.is_file():
            files.append(path)
        else:
            raise SpecError(f"{path}: no such file or folder")

    specs = [load_spec(file) for file in files]
    # Each spec's results go in a folder named after it, so names must be unique.
    seen: dict[str, Path | None] = {}
    for spec in specs:
        if spec.name in seen:
            raise SpecError(f"two specs are named {spec.name!r}: {seen[spec.name]} and {spec.path}")
        seen[spec.name] = spec.path
    return specs


def goal_expectation(goal: str) -> str:
    return f"the page shows this was done: {goal}"


def goal_spec_yaml(url: str, goal: str, name: str = "") -> tuple[str, str]:
    """(name, YAML text) for a goal-only spec, as `nightshift run --url --goal` writes it."""
    name = name or re.sub(r"[^a-z0-9]+", "-", goal.lower()).strip("-")[:50] or "goal"
    return name, yaml.safe_dump({"name": name, "url": url, "goal": goal}, sort_keys=False, allow_unicode=True)


def _strings(value: object, key: str, path: Path) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise SpecError(f"{path}: {key} must be a non-empty list of sentences")
    return tuple(v.strip() for v in value)


def _expand_env(value: str, path: Path) -> str:
    def lookup(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise SpecError(f"{path}: environment variable {name} is not set")
        return os.environ[name]

    return _ENV_RE.sub(lookup, value)
