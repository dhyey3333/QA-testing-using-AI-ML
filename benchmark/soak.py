"""The soak test: how the hosted app holds up over weeks of nightly runs nobody watches.

    uv run python -m benchmark.soak --data hosted-data [--project soak-test-practice-sites] [--out FILE]

Reads the app's own records (it changes nothing): which nights ran and which didn't, how late,
how long, every test's verdict night by night, how many runs replayed with no model, failures to
check by hand, server restarts in the middle of a run, the data folder's size and the backups.
A missed night means the app (or the laptop it runs on) was off or stuck at the nightly time.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from nightshift.cli import configure_stdout
from nightshift.hosted.backup import PREFIX as BACKUP_PREFIX
from nightshift.hosted.store import Store

MARK = {"pass": "P", "fail": "F", "flaky": "~", "error": "E"}


def folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def report(data: Path, slug: str, backups: Path | None = None, log: Path | None = None, today: date | None = None) -> str:
    today = today or date.today()
    store = Store(data / "nightshift.db")
    try:
        project = store.project(slug)
        if project is None:
            return f"no project {slug} in {data}"
        runs = sorted(store.runs(project["id"], limit=1_000_000), key=lambda r: r["queued"])
    finally:
        store.close()
    nightly = [r for r in runs if r["trigger"] == "nightly"]
    first = date.fromisoformat(project["created"][:10]) + timedelta(days=1)
    expected = [first + timedelta(days=n) for n in range((today - first).days + 1)] if today >= first else []
    ran_on = {datetime.fromtimestamp(r["queued"]).date() for r in nightly}
    missed = [d for d in expected if d not in ran_on and d < today]

    lines = [f"# Soak test: {project['client']} ({slug})", "",
             f"Nightly at {project['nightly'] or '(none)'}; {len(expected)} nights since {first}, "
             f"{len(nightly)} nightly runs, {len(missed)} missed.", ""]
    if missed:
        lines += ["**Missed nights** (the app or the computer was off or stuck): "
                  + ", ".join(d.isoformat() for d in missed), ""]

    lines += ["| Night | Queued | Waited (s) | Took (min) | Status | Pass | Fail | Flaky | Error | Replayed | Note |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    per_spec: dict[str, list[str]] = defaultdict(list)
    failures, took, replay_share = [], [], []
    for run in nightly:
        rows = _summary(data, slug, run)
        replayed = sum(row.get("mode") == "replay" for row in rows)
        if rows:
            replay_share.append(replayed / len(rows))
        waited = (run["started"] - run["queued"]) if run["started"] else None
        minutes = (run["finished"] - run["started"]) / 60 if run["finished"] and run["started"] else None
        if minutes is not None:
            took.append(minutes)
        queued = datetime.fromtimestamp(run["queued"])
        lines.append(f"| {queued:%Y-%m-%d} | {queued:%H:%M} | {waited:.0f} | {minutes:.1f} | {run['status']} | "
                     f"{run['passed']} | {run['failed']} | {run['flaky']} | {run['errors']} | "
                     f"{replayed}/{len(rows)} | {run['message'][:60]} |"
                     if waited is not None and minutes is not None else
                     f"| {queued:%Y-%m-%d} | {queued:%H:%M} | - | - | {run['status']} | {run['passed']} | {run['failed']} | "
                     f"{run['flaky']} | {run['errors']} | {replayed}/{len(rows)} | {run['message'][:60]} |")
        for row in rows:
            per_spec[row["spec"]].append(MARK.get(row.get("verdict"), "?"))
            if row.get("verdict") in ("fail", "error", "flaky"):
                failures.append((queued.date(), row["spec"], row.get("verdict"), row.get("category", ""),
                                 row.get("reason", "")[:140]))

    if took:
        lines += ["", f"Run time: median {statistics.median(took):.1f} min, longest {max(took):.1f} min. "
                      f"Replayed with no model: median {statistics.median(replay_share or [0]):.0%} of tests."]
    restarted = [r for r in runs if "restarted" in (r["message"] or "")]
    lines += ["", f"Runs cut short by a server restart: {len(restarted)}."]

    if per_spec:
        lines += ["", "## Each test, night by night (P pass, F fail, ~ flaky, E error)", "",
                  "| Test | Nights | Pass rate |", "|---|---|---|"]
        for spec, marks in sorted(per_spec.items()):
            lines.append(f"| {spec} | {' '.join(marks)} | {marks.count('P')}/{len(marks)} |")
    if failures:
        lines += ["", "## Failures to check by hand (a site bug, the site down, or a false alarm?)", "",
                  "| Night | Test | Verdict | Kind | Reason | Checked |", "|---|---|---|---|---|---|"]
        for night, spec, verdict, kind, reason in failures:
            lines.append(f"| {night} | {spec} | {verdict} | {kind} | {reason.replace('|', '/')} | |")

    lines += ["", "## Housekeeping", "",
              f"- Data folder: {folder_size(data) / 1e6:.1f} MB (this project: "
              f"{folder_size(data / 'projects' / slug) / 1e6:.1f} MB)."]
    if backups is not None:
        found = sorted(backups.glob(f"{BACKUP_PREFIX}*.zip")) if backups.exists() else []
        lines.append(f"- Backups in {backups}: {len(found)}" + (f", newest {found[-1].name}" if found else ""))
    if log is not None and log.exists():
        text = log.read_text(encoding="utf-8", errors="replace")
        lines.append(f"- Server log: {text.count('Traceback')} tracebacks, "
                     f"{text.count('Nightshift hosted on')} starts.")
    return "\n".join(lines) + "\n"


def _summary(data: Path, slug: str, run: dict) -> list[dict]:
    if not run["run_dir"]:
        return []
    path = data / "projects" / slug / "runs" / str(run["id"]) / run["run_dir"] / "summary.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = argparse.ArgumentParser(prog="python -m benchmark.soak", description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=Path("hosted-data"))
    parser.add_argument("--project", default="soak-test-practice-sites")
    parser.add_argument("--backups", type=Path, help="(default: hosted-backups/ beside the data folder)")
    parser.add_argument("--log", type=Path, default=Path("runs/nightshift.log"))
    parser.add_argument("--out", type=Path, help="also write the report here")
    args = parser.parse_args(argv)
    text = report(args.data, args.project, args.backups or args.data.resolve().parent / "hosted-backups", args.log)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
