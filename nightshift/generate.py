"""Writing specs: the part of QA that turns requirements into test cases.

Two ways in:
  - generate_specs: a user story and/or a map of the app from `nightshift explore`
  - design_tests: one requirement from a requirements document, designed into a
    positive case plus negative and boundary cases where the requirement states a rule

Either way the result is YAML specs. They are drafts: a model that misreads the app
writes a wrong test, so the files say so at the top and should be read before they
are trusted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import yaml

from .actions import InvalidAction
from .model import Model
from .prompts import DESIGN_PROMPT, GENERATE_PROMPT

TECHNIQUES = ("positive", "negative", "boundary")
PRIORITIES = ("high", "medium", "low")
_ID_RE = re.compile(r"^((?:REQ|FR|NFR|US|AC|R)[-_ ]?\d+(?:\.\d+)?)\s*[:.)\]\-–—]\s*(.+)$", re.IGNORECASE)
_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$")


@dataclass(frozen=True)
class Requirement:
    id: str
    text: str


def parse_requirements(text: str) -> list[Requirement]:
    """Requirements from a plain document: bullet or numbered items, or lines that start
    with an id like "REQ-12:" or "R3." (kept as the id). Headings and prose are skipped.
    Items without an id are numbered R1, R2... in order."""
    found: list[tuple[str | None, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        item = _ITEM_RE.match(line)
        body = item.group(1).strip() if item else line
        has_id = _ID_RE.match(body)
        if has_id:
            found.append((has_id.group(1).upper().replace(" ", "-").replace("_", "-"), has_id.group(2).strip()))
        elif item:
            found.append((None, body))
    requirements, n, used = [], 0, set()
    for given, body in found:
        if given is None:
            n += 1
            given = f"R{n}"
            while given in used:
                n += 1
                given = f"R{n}"
        used.add(given)
        requirements.append(Requirement(given, body))
    return requirements


def design_tests(model: Model, requirement: Requirement, *, url: str, pages: dict[str, dict] | None = None,
                 max_cases: int = 3, data_keys: tuple[str, ...] = ()) -> list[dict]:
    """Test cases for one requirement: a positive case, and negative or boundary cases where it states a rule.

    The design is reviewed in code before it is accepted (review_design), and the model
    gets one revision with the review's findings: the same idea as the judge, applied
    to test design.
    """
    parts = [f"APP: {url}"]
    if pages:
        parts.append("WHAT THE APP CONTAINS (found by exploring it):\n" + _describe_pages(pages))
    if data_keys:
        parts.append("TEST DATA AVAILABLE (filled in when the test runs; refer to it by name, and use each only "
                     f"for the field it is named for): {', '.join(data_keys)}")
    parts.append(f"REQUIREMENT {requirement.id}: {requirement.text}")
    parts.append(f"Design at most {max_cases} cases. Reply with one JSON object.")
    prompt = "\n\n".join(parts)
    # A case may only start on a page exploring actually found: a guessed /contact/ for
    # /contact-us/ is a 404 the test would blame on the app.
    paths = {urlsplit(page.get("url", "")).path or "/" for page in (pages or {}).values()}
    paths |= set(re.findall(r"(?<![\w.])(/[\w\-./]*)", requirement.text))  # "/admin/" named in the requirement
    designed = _read_cases(model.ask(DESIGN_PROMPT, prompt, max_tokens=2_000), requirement, url, max_cases, paths)

    problems = review_design(requirement, designed)
    if problems:
        revision = prompt + "\n\nYOUR LAST DESIGN HAD PROBLEMS: " + " ".join(problems) + " Design the cases again."
        try:
            revised = _read_cases(model.ask(DESIGN_PROMPT, revision, max_tokens=2_000), requirement, url, max_cases,
                                  paths)
        except InvalidAction:
            revised = []
        if revised and len(review_design(requirement, revised)) <= len(problems):
            designed = revised
    # Whatever the review still finds goes into the spec files, for the person who reads them.
    remaining = review_design(requirement, designed)
    for case in designed:
        case["review"] = remaining
    return designed


def review_design(requirement: Requirement, cases: list[dict]) -> list[str]:
    """What a QA lead would send back: values the requirement names that no case checks,
    and exact wording the cases made up."""
    from .judge import key_terms, normalize

    if not cases:
        return ["there were no usable cases"]
    problems = []
    checked = normalize(" ".join([line for case in cases for line in case["expect"] + case["steps"]]
                                 + [case.get("url", "").replace("-", " ") for case in cases]))
    # The requirement's first word is capitalised because it starts the sentence ("Staff sign in"), not a value.
    opening = requirement.text.split(maxsplit=1)[0] if requirement.text.split() else ""
    missing = [term for term in key_terms(requirement.text) if term != opening and normalize(term) not in checked]
    if missing:
        problems.append("No case checks these values the requirement names: "
                        + ", ".join(f'"{t}"' for t in missing) + ". Test the requirement's own example.")
    source = normalize(requirement.text)
    invented = [quote for case in cases for line in case["expect"] for quote in re.findall(r"['\"“‘]([^'\"”’]{12,})['\"”’]", line)
                if normalize(quote) not in source]
    if invented:
        problems.append("These expected messages are not in the requirement, so they are guesses: "
                        + "; ".join(f'"{q}"' for q in invented[:3]) + ". Describe the result in the requirement's words.")
    return problems


def _read_cases(raw: dict, requirement: Requirement, url: str, max_cases: int,
                known_paths: set[str] | None = None) -> list[dict]:
    cases = raw.get("cases")
    if not isinstance(cases, list):
        raise InvalidAction('the reply had no "cases" list')
    designed = []
    for number, item in enumerate(cases[:max_cases], 1):
        if not isinstance(item, dict):
            continue
        steps = [str(s).strip() for s in item.get("steps") or [] if str(s).strip()]
        expect = [str(e).strip() for e in item.get("expect") or [] if str(e).strip()]
        if not steps or not expect:
            continue
        technique = str(item.get("technique") or "").lower()
        priority = str(item.get("priority") or "").lower()
        title = str(item.get("title") or "").strip() or steps[0]
        data = item.get("data") or {}
        if isinstance(data, list):
            data = {str(k): "" for k in data}
        start = str(item.get("start") or "/").strip()
        if known_paths and start not in known_paths:
            start = "/"
        designed.append({
            "name": slugify(f"{requirement.id}-{number}-{title}")[:60].rstrip("-"),
            "title": title[:120],
            "requirements": [requirement.id],
            "technique": technique if technique in TECHNIQUES else "positive",
            "priority": priority if priority in PRIORITIES else "medium",
            # Only a path on the same site: a model must not point a test at another host.
            "url": urljoin(url, start) if start.startswith("/") and not start.startswith("//") else url,
            "steps": steps,
            "expect": expect,
            "data": {_key(str(k)).lower(): str(v) for k, v in data.items() if _key(str(k))},
        })
    return designed

PAGES_BUDGET = 7_000  # characters of app map to send; small models lose the thread past this


def load_pages(path: Path) -> tuple[str, dict[str, dict]]:
    """(start url, pages) from an explore run's discovered.json."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw.get("url", ""), dict(raw.get("pages", {}))


def generate_specs(model: Model, *, url: str, pages: dict[str, dict] | None = None, story: str = "",
                   count: int = 5) -> list[dict]:
    """Ask the model for up to `count` specs. Returns [{name, steps, expect, data}] with bad entries dropped."""
    parts = [f"APP: {url}"]
    if pages:
        parts.append("WHAT THE APP CONTAINS (found by exploring it):\n" + _describe_pages(pages))
    if story:
        parts.append(f"REQUIREMENT TO TEST:\n{story.strip()}")
    parts.append(f"Write up to {count} specs. Reply with one JSON object.")
    raw = model.ask(GENERATE_PROMPT, "\n\n".join(parts), max_tokens=2_500)

    specs = raw.get("specs")
    if not isinstance(specs, list):
        raise InvalidAction('the reply had no "specs" list')
    clean = []
    for item in specs[:count]:
        if not isinstance(item, dict):
            continue
        steps = [str(s).strip() for s in item.get("steps") or [] if str(s).strip()]
        expect = [str(e).strip() for e in item.get("expect") or [] if str(e).strip()]
        if not steps or not expect:
            continue
        data = item.get("data") or []
        if isinstance(data, dict):
            data = list(data)
        clean.append({
            "name": slugify(str(item.get("name") or "generated")),
            "steps": steps,
            "expect": expect,
            "data": _data_keys(data, steps),
        })
    return clean


_VALUE_LIKE = re.compile(r"[@.\s/:]|\d{3,}")  # "test@example.com", "password123": values, not names
_COMMON_KEYS = ("email", "password", "username", "mobile", "phone", "pincode")


def _data_keys(raw: list, steps: list[str]) -> list[str]:
    """Names of test data. Small models sometimes list example *values* here; those are dropped,
    and the usual account fields are inferred from what the steps mention."""
    keys = [_key(str(k)).lower() for k in raw if not _VALUE_LIKE.search(str(k).strip())]
    # Only steps that talk about test data count: "click forgot password" needs no password.
    text = " ".join(step for step in steps if "test" in step.lower()).lower()
    keys += [k for k in _COMMON_KEYS if k in text]
    return list(dict.fromkeys(k for k in keys if k))


def write_specs(specs: list[dict], out_dir: Path, *, url: str, known_data: dict[str, str] | None = None,
                source: str = "") -> list[Path]:
    """One YAML file per spec. Data with no known value becomes ${NS_KEY}, read from the environment."""
    known_data = known_data or {}
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    taken = {p.stem for p in out_dir.glob("*.yaml")}
    for spec in specs:
        name = _unique(spec["name"], taken)
        taken.add(name)
        doc: dict = {"name": name}
        for meta in ("title", "requirements", "technique", "priority"):
            if spec.get(meta):
                doc[meta] = spec[meta]
        doc.update({"url": spec.get("url") or url, "steps": spec["steps"], "expect": spec["expect"]})
        needed_env = []
        # data is a list of names (generate_specs) or name: value (design_tests). A known value
        # wins; a blank one is read from the environment, so credentials never land in the file.
        data = spec["data"] if isinstance(spec["data"], dict) else {key: "" for key in spec["data"]}
        if data:
            doc["data"] = {}
            for key, value in data.items():
                if key in known_data:
                    doc["data"][key] = known_data[key]
                elif value:
                    doc["data"][key] = value
                else:
                    env = env_name(key)
                    needed_env.append(env)
                    doc["data"][key] = "${" + env + "}"
        header = [f"# Draft written by `nightshift generate`{f' from {source}' if source else ''} on {date.today()}.",
                  "# Read it before trusting it: a wrong spec makes a wrong test."]
        if needed_env:
            header.append(f"# Needs environment variables: {', '.join(needed_env)}")
        for problem in spec.get("review") or []:
            header.append(f"# Review: {problem}")
        body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
        path = out_dir / f"{name}.yaml"
        path.write_text("\n".join(header) + "\n" + body, encoding="utf-8")
        written.append(path)
    return written


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "generated"


def env_name(key: str) -> str:
    return "NS_" + re.sub(r"[^A-Z0-9]", "_", key.upper())


def _key(text: str) -> str:
    return re.sub(r"\W+", "_", text.strip()).strip("_")


def _unique(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    n = 2
    while f"{name}-{n}" in taken:
        n += 1
    return f"{name}-{n}"


def _describe_pages(pages: dict[str, dict]) -> str:
    chunks = []
    for key, page in pages.items():
        elements = "\n".join(f"  {e}" for e in page.get("elements", [])[:25])
        text = page.get("text", "")[:400].replace("\n", " | ")
        chunks.append(f'PAGE {key} "{page.get("title", "")}"\n  text: {text}\n{elements}')
    described = "\n\n".join(chunks)
    return described[:PAGES_BUDGET] + ("\n...(more pages not shown)" if len(described) > PAGES_BUDGET else "")
