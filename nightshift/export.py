"""Exporting a saved run as a plain Playwright test (TypeScript).

No lock-in: once the agent has found a path, a team can keep it as an ordinary
@playwright/test file with role-based locators, in their own repo and CI, with
no model at all. The judge's evidence quotes become the assertions.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .recording import Recording
from .spec import Spec

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
_ENV_ONLY_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_LONG_NUMBER_RE = re.compile(r"\d{4,}")
_JS_REGEX_SPECIAL = re.compile(r"[\\^$.*+?()\[\]{}|/]")


def js(text: str) -> str:
    """A JavaScript string literal."""
    return json.dumps(text, ensure_ascii=False)


def locator_ts(candidate: dict) -> str:
    by, value = candidate["by"], candidate.get("value", "")
    match by:
        case "test_id":
            return f"page.getByTestId({js(value)})"
        case "role":
            return f"page.getByRole({js(candidate['role'])}, {{ name: {js(candidate['name'])}, exact: true }})"
        case "label":
            return f"page.getByLabel({js(value)}, {{ exact: true }})"
        case "placeholder":
            return f"page.getByPlaceholder({js(value)}, {{ exact: true }})"
        case "text":
            return f"page.getByText({js(value)}, {{ exact: true }})"
        case _:
            return f"page.locator({js(value)})"


def value_ts(text: str) -> str:
    """Typed text, with {{placeholders}} read from the `data` object."""
    if not _PLACEHOLDER_RE.search(text):
        return js(text)
    whole = _PLACEHOLDER_RE.fullmatch(text.strip())
    if whole:
        return f"data.{whole.group(1)}"
    escaped = text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    return "`" + _PLACEHOLDER_RE.sub(lambda m: "${data." + m.group(1) + "}", escaped) + "`"


def evidence_assertion(quote: str) -> str:
    """Assert the quote is on the page. Long numbers (order ids, dates) become \\d+ so the test survives the next order."""
    if not _LONG_NUMBER_RE.search(quote):
        return f'await expect(page.locator("body")).toContainText({js(quote)});'
    pattern = r"\d+".join(_JS_REGEX_SPECIAL.sub(r"\\\g<0>", piece) for piece in _LONG_NUMBER_RE.split(quote))
    return f'await expect(page.locator("body")).toContainText(/{pattern}/);'


def export_playwright(spec: Spec, recording: Recording) -> str:
    lines = [
        'import { test, expect } from "@playwright/test";',
        "",
        f"// Exported by Nightshift on {date.today()} from {spec.path or spec.name}.",
        f"// Recorded {recording.recorded_at} by {recording.model}. Re-export after re-recording.",
    ]
    if spec.data:
        raw_data = spec.raw_data or spec.data
        if any(_ENV_ONLY_RE.match(raw) for raw in raw_data.values()):
            lines += ["", "function required(name: string): string {",
                      "  const value = process.env[name];",
                      "  if (!value) throw new Error(`Set the ${name} environment variable`);",
                      "  return value;", "}"]
        lines += ["", "const data = {"]
        for key, raw in raw_data.items():
            env = _ENV_ONLY_RE.match(raw)
            lines.append(f"  {key}: required({js(env.group(1))})," if env else f"  {key}: {js(raw)},")
        lines.append("};")

    lines += ["", f"test({js(spec.name)}, async ({{ page }}) => {{"]
    for step_text in spec.steps:
        lines.append(f"  // {step_text}")
    lines.append(f"  await page.goto({js(spec.url)});")
    for saved in recording.steps:
        action = saved.action
        target = locator_ts(saved.locators[0]) if saved.locators else None
        match action.kind:
            case "click":
                lines.append(f"  await {target}.click();")
            case "type":
                lines.append(f"  await {target}.fill({value_ts(action.text or '')});")
            case "select":
                lines.append(f"  await {target}.selectOption({{ label: {js(action.value or '')} }});")
            case "press":
                lines.append(f"  await page.keyboard.press({js(action.key or '')});")
            case "scroll":
                lines.append(f"  await page.mouse.wheel(0, {600 if action.direction == 'down' else -600});")
            case "back":
                lines.append("  await page.goBack();")
            case "goto":
                lines.append(f"  await page.goto(new URL({js(action.value or '/')}, page.url).toString());")
    if recording.evidence or recording.absent:
        lines.append("")
        lines += [f"  // expected: {e}" for e in spec.expect]
        lines += [f"  {evidence_assertion(q)}" for q in dict.fromkeys(recording.evidence)]
        lines += [f'  await expect(page.locator("body")).not.toContainText({js(q)});'
                  for q in dict.fromkeys(recording.absent)]
    lines += ["});", ""]
    return "\n".join(lines)


def write_export(spec: Spec, recording: Recording, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{spec.name}.spec.ts"
    path.write_text(export_playwright(spec, recording), encoding="utf-8")
    return path
