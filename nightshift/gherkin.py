"""Cucumber features in and out, for teams whose tests already live in .feature files.

Import: each Scenario becomes a spec. Given and When lines (and the And, But and * after them)
are its steps; Then lines (and theirs) are its expected results. Background steps come first in
every scenario of the feature. A Scenario Outline becomes one spec per Examples row, with its
<placeholders> filled in. A data table or doc string under a step is added to that step as text.
No step definitions (Cucumber's glue code) are needed: the steps are plain English, which is what
Nightshift reads. Imported specs are drafts, like generated ones: someone reads them first.

Export: a spec becomes a Scenario (Given I open the URL, When each step, Then each expected
result), so a team that keeps its tests in Gherkin can keep Nightshift's there too.

English keywords only (Given, When, Then, And, But, Scenario, Scenario Outline, Background, Examples).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .generate import slugify
from .spec import Spec

_KEYWORD_RE = re.compile(r"^(Given|When|Then|And|But|\*)\s+(.*)$")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


class GherkinError(ValueError):
    """A feature file that can't be read as Gherkin."""


@dataclass
class _Scenario:
    title: str
    tags: list[str]
    steps: list[str] = field(default_factory=list)
    expect: list[str] = field(default_factory=list)
    examples: list[dict[str, str]] = field(default_factory=list)
    outline: bool = False


def parse_feature(text: str, url: str = "") -> list[dict]:
    """The scenarios of one feature, as spec dictionaries (name, title, url, steps, expect, data, review)."""
    background: list[str] = []
    scenarios: list[_Scenario] = []
    feature_tags: list[str] = []
    pending_tags: list[str] = []
    current: _Scenario | None = None
    section = ""  # "background" or "scenario"
    phase = "steps"  # where an And or But goes: "steps" or "expect"
    last: list[str] | None = None  # the list the last step went into, for tables and doc strings
    header: list[str] | None = None  # the Examples table's header row
    in_doc, doc = False, []
    saw_feature = False

    for raw in text.splitlines():
        line = raw.strip()
        if in_doc:
            if line.startswith(('"""', "```")):
                in_doc = False
                if last:
                    last[-1] += f' (text: "{" ".join(doc)}")'
                doc = []
            else:
                doc.append(line)
            continue
        if not line or line.startswith("#"):
            continue
        if line.startswith(('"""', "```")):
            in_doc = True
            continue
        if line.startswith("@"):
            pending_tags += line.split()
            continue
        head, _, rest = line.partition(":")
        if head in ("Feature", "Ability", "Business Need"):
            saw_feature, feature_tags, pending_tags = True, pending_tags, []
            continue
        if head in ("Background",):
            section, phase, last = "background", "steps", None
            continue
        if head in ("Scenario", "Example", "Scenario Outline", "Scenario Template"):
            current = _Scenario(rest.strip() or f"scenario {len(scenarios) + 1}", feature_tags + pending_tags,
                                outline=head in ("Scenario Outline", "Scenario Template"))
            scenarios.append(current)
            section, phase, last, header, pending_tags = "scenario", "steps", None, None, []
            continue
        if head in ("Examples", "Scenarios"):
            header = []
            continue
        if head == "Rule":
            continue
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if header is not None and current is not None:
                if not header:
                    header.extend(cells)
                else:
                    current.examples.append(dict(zip(header, cells)))
            elif last:
                last[-1] += f" ({', '.join(cells)})"  # a data table row under a step
            continue
        match = _KEYWORD_RE.match(line)
        if not match:
            continue  # the feature's free-text description
        keyword, step = match.groups()
        if keyword == "Then":
            phase = "expect"
        elif keyword in ("Given", "When"):
            phase = "steps"
        if section == "background":
            background.append(step)
            last = background
        elif current is not None:
            target = current.expect if phase == "expect" else current.steps
            target.append(step)
            last = target
    if not saw_feature and not scenarios:
        raise GherkinError("no Feature or Scenario found: is this a Gherkin .feature file?")

    specs = []
    for scenario in scenarios:
        rows = scenario.examples if scenario.outline and scenario.examples else [{}]
        for number, row in enumerate(rows, 1):
            fill = lambda s: re.sub(r"<([^<>]+)>", lambda m: row.get(m.group(1), m.group(0)), s)  # noqa: E731
            steps = [fill(s) for s in background + scenario.steps]
            expect = [fill(e) for e in scenario.expect]
            review = []
            found = next((m.group(0) for s in steps for m in [_URL_RE.search(s)] if m), "")
            site = url or found
            if not site:
                site = "https://example.com/"
                review.append("set the website: the feature doesn't name one")
            if not expect:
                expect = [f"the page shows this was done: {steps[-1]}" if steps else "the page loads"]
                review.append("the scenario has no Then: say what should happen")
            if not steps:
                review.append("the scenario has no Given or When steps")
            title = scenario.title + (f" ({', '.join(f'{k}={v}' for k, v in row.items())})" if row else "")
            name = slugify(scenario.title)[:50] + (f"-{number}" if len(rows) > 1 else "")
            requirements = [t.lstrip("@") for t in scenario.tags if re.fullmatch(r"@[A-Za-z]+-\d+", t)]
            specs.append({"name": name, "title": title, "url": site, "steps": steps or ["open the page"], "expect": expect,
                          "data": {}, "review": review, **({"requirements": requirements} if requirements else {})})
    return specs


def spec_to_feature(spec: Spec) -> str:
    """One spec as a feature with one scenario."""
    lines = [f"Feature: {spec.title or spec.name}", "", f"  Scenario: {spec.name}", f'    Given I open "{spec.url}"']
    lines += [f"    {'When' if i == 0 else 'And'} {step}" for i, step in enumerate(spec.steps)]
    lines += [f"    {'Then' if i == 0 else 'And'} {item}" for i, item in enumerate(spec.expect)]
    return "\n".join(lines) + "\n"


def write_features(specs: list[Spec], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for spec in specs:
        path = out_dir / f"{spec.name}.feature"
        path.write_text(spec_to_feature(spec), encoding="utf-8")
        written.append(path)
    return written
