"""Starting a test already logged in.

A spec with `session_from: login` starts with the browser session (cookies and local
storage) the `login` spec ended with, instead of logging in again. On a real site that
saves a minute a test, and it means an OTP or authenticator login is done once per run
rather than once per test, which is also what keeps a site's login rate limit quiet.

Sessions live in memory for one run and are never written to disk: a session is a live
credential, and runs/ is uploaded as a CI artifact. The spec a session comes from runs
first; when it isn't part of the run, it is found next to the spec that needs it.

A test that logs out ends the session on the server too, so it should log in itself
rather than use session_from.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .spec import Spec, SpecError, load_spec


class Sessions:
    def __init__(self, specs: Iterable[Spec], base_url: str = "") -> None:
        specs = list(specs)
        self.specs = {spec.name: spec for spec in specs}
        self.needed = {spec.session_from for spec in specs if spec.session_from}
        self.failures: dict[str, str] = {}  # spec name -> why it didn't pass
        self.starting: set[str] = set()  # specs being run for their session right now (a cycle guard)
        self._states: dict[str, dict] = {}
        for spec in specs:
            if spec.session_from and spec.session_from not in self.specs and spec.path is not None:
                if (found := _find_spec(spec.path.parent, spec.session_from)) is not None:
                    self.specs[found.name] = found.with_base_url(base_url) if base_url else found

    def wants(self, name: str) -> bool:
        return name in self.needed

    def save(self, name: str, state: dict) -> None:
        self._states[name] = state
        self.failures.pop(name, None)

    def state(self, name: str) -> dict | None:
        return self._states.get(name)

    def provider(self, name: str) -> Spec | None:
        return self.specs.get(name)

    def first(self, specs: list[Spec]) -> list[Spec]:
        """The specs in run order: the ones others start from go first, the rest keep their order."""
        return sorted(specs, key=lambda spec: spec.name not in self.needed)


def _find_spec(folder: Path, name: str) -> Spec | None:
    for path in sorted([*folder.glob("*.yaml"), *folder.glob("*.yml")]):
        try:
            spec = load_spec(path)
        except SpecError:
            continue
        if spec.name == name:
            return spec
    return None
