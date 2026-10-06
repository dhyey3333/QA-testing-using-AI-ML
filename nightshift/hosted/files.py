"""A project's files: its specs, saved paths, runs and secrets, in its own folder.

    <data>/projects/<slug>/specs/*.yaml       the client's tests
    <data>/projects/<slug>/drafts/*.yaml      tests the AI wrote, waiting for a person to accept them
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

import yaml

from ..spec import SpecError, load_spec
from .store import StoreError

MAX_SPEC_BYTES = 100_000
_SPEC_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,59}")
_SECRET_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")
_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DATA_LINE_RE = re.compile(r"\s*([A-Za-z0-9_]+)\s*[=:]\s*(.*?)\s*$")
# What the form edits. Anything else in a spec (an inbox, a session, API requests) is kept as it was.
_FORM_KEYS = ("name", "url", "steps", "goal", "expect", "data", "max_steps", "js_errors")


class ProjectFiles:
    def __init__(self, data: Path, slug: str) -> None:
        self.root = data / "projects" / slug
        self.specs = self.root / "specs"
        self.recordings = self.root / "recordings"
        self.runs = self.root / "runs"
        self.secrets_file = self.root / "secrets.env"
        self.drafts = self.root / "drafts"
        for folder in (self.specs, self.recordings, self.runs, self.drafts):
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

    # --- the test form: no YAML for the people writing tests ---------------------------

    def form_of(self, text: str) -> dict:
        """A spec as the form shows it: one step and one expected result per line."""
        raw = yaml.safe_load(text) or {}
        if not isinstance(raw, dict) or raw.get("kind") == "api" or "requests" in raw:
            return {"advanced": True}  # an API test: only the YAML editor fits it
        steps = raw.get("steps") or ([raw["goal"]] if raw.get("goal") else [])
        return {
            "url": str(raw.get("url") or ""),
            "steps": "\n".join(str(step) for step in steps),
            "expect": "\n".join(str(item) for item in (raw.get("expect") or [])),
            "data": "\n".join(f"{key} = {value}" for key, value in (raw.get("data") or {}).items()),
            "max_steps": raw.get("max_steps") or 30,
            "js_errors_warn": raw.get("js_errors") == "warn",
        }

    def yaml_of(self, name: str, form: dict, existing: str = "") -> str:
        """The form's answers as a spec. Keys the form doesn't edit are kept from the existing spec."""
        steps = _lines(form.get("steps"))
        expect = _lines(form.get("expect"))
        if not steps:
            raise StoreError("write at least one step, one per line")
        doc: dict = {"name": name.removesuffix(".yaml"), "url": str(form.get("url") or "").strip()}
        if expect:
            doc["steps"], doc["expect"] = steps, expect
        elif len(steps) == 1:
            doc["goal"] = steps[0]  # one sentence and no expected results: the agent proves the goal was done
        else:
            raise StoreError("say what should happen at the end: one expected result per line")
        data = {}
        for line in _lines(form.get("data")):
            match = _DATA_LINE_RE.fullmatch(line)
            if not match:
                raise StoreError(f'test data goes one per line as name = value, like  email = qa@example.com  (not "{line}")')
            data[match[1]] = match[2]
        if data:
            doc["data"] = data
        try:
            max_steps = int(form.get("max_steps") or 30)
        except (TypeError, ValueError):
            raise StoreError("the step limit must be a number") from None
        doc["max_steps"] = max(1, min(max_steps, 60))
        if form.get("js_errors_warn"):
            doc["js_errors"] = "warn"
        kept = yaml.safe_load(existing) if existing else {}
        if isinstance(kept, dict):
            doc.update({key: value for key, value in kept.items() if key not in _FORM_KEYS})
        return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)

    # --- drafts the AI wrote ------------------------------------------------------------

    def drafts_list(self) -> list[dict]:
        found = []
        secrets = self.secrets()
        for path in sorted(self.drafts.glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            reviews = [line.removeprefix("# Review:").strip() for line in text.splitlines() if line.startswith("# Review:")]
            needs = sorted({name for name in _ENV_RE.findall(text) if name not in secrets and name not in os.environ})
            found.append({"name": path.stem, "review": reviews, "needs": needs})
        return found

    def read_draft(self, name: str) -> str:
        return self._draft_path(name).read_text(encoding="utf-8")

    def save_draft(self, name: str, text: str) -> None:
        self._draft_path(name).write_text(text, encoding="utf-8")

    def accept_draft(self, name: str) -> None:
        """A reviewed draft becomes a test. It must load, and must not replace a test with the same name."""
        if name in self.spec_names():
            raise StoreError(f'there is already a test called "{name}"; delete one of them first')
        self.save_spec(name, self.read_draft(name))
        self._draft_path(name).unlink(missing_ok=True)

    def delete_draft(self, name: str) -> None:
        self._draft_path(name).unlink(missing_ok=True)

    def _draft_path(self, name: str) -> Path:
        path = self._spec_path(name)
        return self.drafts / path.name

    def missing_secrets(self) -> list[tuple[str, str]]:
        """(test, secret) for every ${SECRET} a test uses that isn't set: the run would stop on it."""
        secrets = self.secrets()
        missing = []
        for path in sorted(self.specs.glob("*.yaml")):
            for name in sorted(set(_ENV_RE.findall(path.read_text(encoding="utf-8")))):
                if name not in secrets and name not in os.environ:
                    missing.append((path.stem, name))
        return missing

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


def _lines(value) -> list[str]:
    return [line.strip().lstrip("-•*").strip() for line in str(value or "").splitlines() if line.strip()]
