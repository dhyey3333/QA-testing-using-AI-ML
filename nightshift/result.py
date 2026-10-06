"""What a run produces: one Step per action, one RunResult per spec."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .actions import Action

PENDING = "pending"  # the action ran; whether it changed the page is known next turn

# pass:  every expected result was proven on the page.
# fail:  the app has a bug (and, with retries on, it happened every time).
# flaky: it failed, then passed on a retry. Worth a look, not a red build.
# error: the tester could not finish (model down, step budget spent).
# Keep fail and error apart: the benchmark counts a fail on a bug-free app as a false alarm.
VERDICTS = ("pass", "fail", "flaky", "error")


@dataclass
class Check:
    """The judge's ruling on one expected result."""

    expected: str
    evidence: list[str]  # snippets copied from the page, verified to be there
    why: str
    holds: bool
    absent: list[str] = field(default_factory=list)  # text claimed missing, verified NOT to be on the page


@dataclass
class Step:
    index: int
    action: Action | None  # None when the model's reply could not be used
    description: str  # readable; test data stays as {{placeholders}}
    thought: str = ""
    # "changed" / "no change" once the next page is read, "failed: ..." when the
    # browser refused, "invalid: ..." for an unusable reply, "verdict" for
    # pass/fail, "replayed" when it came from a saved run.
    outcome: str = PENDING
    model_ms: int = 0
    action_ms: int = 0
    screenshot: str | None = None  # file name inside the run folder
    target_label: str = ""  # the element's label when it was acted on
    locators: list[dict] = field(default_factory=list)  # how to find that element again (see locators.py)

    def history_line(self) -> str:
        return f"{self.index}. {self.description} -> {self.outcome}"


@dataclass
class RunResult:
    spec: str
    url: str
    model: str
    verdict: str = "error"
    reason: str = ""
    category: str = ""  # BUG | FLAKY | TEST_OUTDATED | ENV_ISSUE for anything but a pass (outcome.py)
    mode: str = "agent"  # agent | replay (a saved run, no model steps) | healed (replay broke, agent finished)
    healed_at: int | None = None  # the replay step that broke
    steps: list[Step] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    app_errors: list[str] = field(default_factory=list)  # uncaught JS errors, HTTP 5xx: these fail a run
    warnings: list[str] = field(default_factory=list)  # console errors, 404s, slow calls, a11y, dialogs
    duration_s: float = 0.0
    judge_ms: int = 0
    out_dir: str = ""
    started_at: str = ""
    browser: str = "chromium"
    viewport: str = ""
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    attempts: list[dict] = field(default_factory=list)  # every try, when the run was retried
    visual: dict = field(default_factory=dict)  # the visual check of the final screen (visual.py), when one ran
    cause: str = ""  # why it failed, in one sentence, from what the browser recorded (cause.py)
    # The page the run ended on (test data masked). Defect analysis diffs it against the
    # last run of the same spec that passed: "Total: ₹600" became "Total: ₹180".
    final_url: str = ""
    final_text: str = ""

    @property
    def model_s(self) -> float:
        return (sum(step.model_ms for step in self.steps) + self.judge_ms) / 1000

    def to_json(self) -> dict:
        data = asdict(self)
        data["model_s"] = round(self.model_s, 2)
        return data

    def summary(self) -> dict:
        return {
            "spec": self.spec,
            "verdict": self.verdict,
            "reason": self.reason,
            "category": self.category,
            "mode": self.mode,
            "steps": len(self.steps),
            "duration_s": self.duration_s,
            "model_s": round(self.model_s, 2),
            "model_calls": self.model_calls,
            "tokens": self.prompt_tokens + self.completion_tokens,
            "out_dir": self.out_dir,
        }
