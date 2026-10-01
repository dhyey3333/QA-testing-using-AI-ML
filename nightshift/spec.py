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

    def with_base_url(self, base_url: str) -> Spec:
        """Point the spec at another deployment (staging, a CI preview), keeping its path."""
        base = urlsplit(base_url)
        own = urlsplit(self.url)
        url = urlunsplit((base.scheme, base.netloc, own.path or "/", own.query, own.fragment))
        return replace(self, url=url)


def load_spec(path: Path) -> Spec:
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
    data = {key: _expand_env(value, path) for key, value in raw_data.items()}
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

    return Spec(
        name=str(raw.get("name") or path.stem),
        url=url,
        steps=_strings(raw.get("steps"), "steps", path),
        expect=_strings(raw.get("expect"), "expect", path),
        data=data,
        max_steps=max_steps,
        path=path,
        raw_data=raw_data,
        title=str(raw.get("title") or ""),
        requirements=tuple(str(r).strip() for r in requirements if str(r).strip()),
        technique=str(raw.get("technique") or ""),
        priority=str(raw.get("priority") or ""),
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
