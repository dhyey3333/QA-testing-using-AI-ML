"""Saved runs: the path a passing agent took, so later runs can replay it for free.

A recording lives at `<store>/<spec name>.json` (default `.nightshift/recordings/`).
Commit that folder: CI then replays the saved path with no model calls except
the final judge, and only pays for the agent when a step breaks.

A recording is thrown away when the spec's steps, expected results or data keys
change, since the saved path may no longer do what the spec says.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .actions import Action
from .result import RunResult
from .spec import Spec

REPLAYABLE_OUTCOMES = ("changed", "no change", "replayed", "done")


@dataclass
class RecordedStep:
    action: Action
    description: str
    target_label: str
    locators: list[dict]


@dataclass
class Recording:
    spec: str
    fingerprint: str
    model: str
    recorded_at: str
    steps: list[RecordedStep]
    evidence: list[str]  # the judge's quotes from the passing run; export turns them into assertions
    absent: list[str] = field(default_factory=list)  # text the judge proved was NOT on the page


def fingerprint(spec: Spec) -> str:
    """What the recording depends on. The host is left out, so staging replays a path recorded locally."""
    parts = [urlsplit(spec.url).path or "/", *spec.steps, "--", *spec.expect, "--", *sorted(spec.data)]
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]


class RecordingStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, spec: Spec) -> Path:
        return self.root / f"{spec.name}.json"

    def load(self, spec: Spec) -> Recording | None:
        path = self.path(spec)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            recording = Recording(
                spec=raw["spec"],
                fingerprint=raw["fingerprint"],
                model=raw.get("model", ""),
                recorded_at=raw.get("recorded_at", ""),
                steps=[RecordedStep(Action(**s["action"]), s["description"], s.get("target_label", ""), s["locators"])
                       for s in raw["steps"]],
                evidence=list(raw.get("evidence", [])),
                absent=list(raw.get("absent", [])),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None  # an unreadable recording is just a missing one; the agent re-records it
        return recording if recording.fingerprint == fingerprint(spec) else None

    def save(self, spec: Spec, result: RunResult) -> Path | None:
        """Save a passing run's path. Returns None if some step can't be found again reliably."""
        steps: list[RecordedStep] = []
        for step in result.steps:
            if step.action is None or step.action.kind in ("pass", "fail"):
                continue
            if step.outcome not in REPLAYABLE_OUTCOMES:
                continue  # refused or invalid actions did nothing; replaying them would only break
            if step.action.kind == "wait":
                continue  # replay settles after every step anyway
            if step.action.id is not None and not step.locators:
                return None
            steps.append(RecordedStep(step.action, step.description, step.target_label, step.locators))

        recording = Recording(
            spec=spec.name,
            fingerprint=fingerprint(spec),
            model=result.model,
            recorded_at=datetime.now().isoformat(timespec="seconds"),
            steps=steps,
            evidence=[quote for check in result.checks for quote in check.evidence],
            absent=[quote for check in result.checks for quote in check.absent],
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(spec)
        path.write_text(json.dumps(asdict(recording), indent=2, ensure_ascii=False), encoding="utf-8")
        return path
