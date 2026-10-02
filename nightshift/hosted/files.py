"""A project's files: its specs, saved paths, runs and secrets, in its own folder.

    <data>/projects/<slug>/specs/*.yaml       the client's tests
    <data>/projects/<slug>/recordings/        saved paths: nightly runs replay these with no model
    <data>/projects/<slug>/runs/<run id>/     output.log, and the `nightshift run` folder with its reports
    <data>/projects/<slug>/secrets.env        test passwords and keys, for ${NAME} in specs

Secrets are write-only through the app: their names are listed, their values never leave
the server except as environment variables of the run that needs them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..spec import SpecError, load_spec
from .store import StoreError

MAX_SPEC_BYTES = 100_000
_SPEC_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,59}")
_SECRET_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")


class ProjectFiles:
    def __init__(self, data: Path, slug: str) -> None:
        self.root = data / "projects" / slug
        self.specs = self.root / "specs"
        self.recordings = self.root / "recordings"
        self.runs = self.root / "runs"
        self.secrets_file = self.root / "secrets.env"
        for folder in (self.specs, self.recordings, self.runs):
            folder.mkdir(parents=True, exist_ok=True)

    # --- specs --------------------------------------------------------------------

    def spec_names(self) -> list[str]:
        return sorted(path.stem for path in self.specs.glob("*.yaml"))

    def read_spec(self, name: str) -> str:
        return self._spec_path(name).read_text(encoding="utf-8")

    def save_spec(self, name: str, text: str) -> None:
        """Check the spec loads (and that its name is unique in the project) before it replaces anything."""
        path = self._spec_path(name)
        if len(text.encode("utf-8")) > MAX_SPEC_BYTES:
            raise StoreError("that spec is too long")
        draft = path.with_suffix(".draft")
        draft.write_text(text, encoding="utf-8")
        try:
            spec = load_spec(draft, expand_env=False)  # ${SECRETS} are checked when it runs, not here
            others = {load_spec(p, expand_env=False).name: p.stem for p in self.specs.glob("*.yaml") if p != path}
        except SpecError as exc:
            draft.unlink(missing_ok=True)
            raise StoreError(str(exc).replace(str(draft), f"{name}.yaml")) from None
        if spec.name in others:
            draft.unlink(missing_ok=True)
            raise StoreError(f'{others[spec.name]}.yaml already has the name "{spec.name}"; give this spec another')
        draft.replace(path)

    def delete_spec(self, name: str) -> None:
        self._spec_path(name).unlink(missing_ok=True)

    def _spec_path(self, name: str) -> Path:
        name = name.removesuffix(".yaml")
        if not _SPEC_NAME_RE.fullmatch(name):
            raise StoreError("a spec file name uses lowercase letters, digits and dashes")
        return self.specs / f"{name}.yaml"

    # --- secrets ------------------------------------------------------------------

    def secrets(self) -> dict[str, str]:
        if not self.secrets_file.exists():
            return {}
        found = {}
        for line in self.secrets_file.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and _SECRET_NAME_RE.fullmatch(name):
                found[name] = value
        return found

    def set_secret(self, name: str, value: str) -> None:
        if not _SECRET_NAME_RE.fullmatch(name):
            raise StoreError("a secret's name uses capital letters, digits and _, like SHOP_PASSWORD")
        if "\n" in value or "\r" in value:
            raise StoreError("a secret is one line")
        self._write_secrets({**self.secrets(), name: value})

    def delete_secret(self, name: str) -> None:
        values = self.secrets()
        values.pop(name, None)
        self._write_secrets(values)

    def _write_secrets(self, values: dict[str, str]) -> None:
        # Created readable by its owner only, before anything is written into it.
        fd = os.open(self.secrets_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write("".join(f"{name}={value}\n" for name, value in sorted(values.items())))

    # --- runs ---------------------------------------------------------------------

    def run_folder(self, run_id: int) -> Path:
        return self.runs / str(int(run_id))

    def run_file(self, run_id: int, relative: str) -> Path:
        """A file of one run, refusing any path that leads outside that run's folder."""
        base = self.run_folder(run_id).resolve()
        target = (base / relative).resolve()
        if base not in target.parents or not target.is_file():
            raise StoreError("no such file")
        return target
