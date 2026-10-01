# CLAUDE.md

## Project
Nightshift (working name): an AI QA tester. Plain-English specs (`specs/*.yaml`) run by an agent in a real browser (Playwright); a grounded judge must prove every expected result from the page before a pass counts. Also: exploratory testing, spec generation, self-healing replay, Playwright export, bug reports, CI integration.

The user's personal startup/CV project, separate from PrivAgent (their SIH repo). Design reasoning: `docs/DECISIONS.md`.

## Layout
- `nightshift/`: the product. `runner.py` (replay, agent loop, judge gate, retries), `judge.py`, `observe.py`, `locators.py`, `recording.py`, `checks.py`, `explore.py`, `generate.py`, `export.py`, `report.py`, `notify.py`, `prompts.py` (every prompt), `cli.py`.
- `demo_shop/`: Kulhad & Co., the development app. 20 planted bugs in `server.py` -> `BUGS`; the `redesign` variant tests self-healing.
- `holdout/`: Sehat Clinic, the holdout app. 8 planted bugs, specs in `holdout/specs/`.
- `benchmark/`: `python -m benchmark [--app shop|clinic]`.
- `realworld/bakerydemo/`: Wagtail's real bakerydemo site: specs, 8 bug patches for its real source, and `eval.py`. Needs a checkout at `../realapps/bakerydemo` (or `BAKERYDEMO_DIR`) set up per its README (Python 3.12 venv in `.venv`, migrate, load_initial_data). The harness resets the checkout and database before every run and refuses to run over uncommitted edits.
- `tests/`: unit tests plus end-to-end tests with a scripted model (`tests/scripted.py`), no LLM needed.
- `action.yml`, `examples/github-workflow.yml`, `Dockerfile`: CI.

## Commands (keep current)
- `uv sync`, `uv run pytest` (about 2 minutes)
- `uv run python -m demo_shop [--bugs NAME,... | all] [--variant redesign] [--list-bugs]` (port 5180); `uv run python -m holdout.server [--bugs ...]` (port 5190)
- `uv run nightshift run specs/ [--headed] [--no-vision] [--base-url URL] [--retries N] [--device NAME] [--video] [--junit PATH]`
- `uv run nightshift explore URL [--steps N] [--focus TEXT] [--data k=v]`
- `uv run nightshift generate --story TEXT [--from discovered.json] [--url URL]`
- `uv run nightshift export specs/`, `uv run nightshift report`, `uv run nightshift init`
- `uv run nightshift validate reqs.md --url URL [--data k=v] [--design-only] [--redesign R5,R6] [--from discovered.json | --explore-steps N]`: requirements -> designed test cases (specs/requirements/) -> run -> traceability.html, defects.html, test-cases.csv
- `uv run nightshift cases specs/ [--run runs/<run>]` (test-case document), `uv run nightshift triage runs/<run> [--file-github owner/repo]` (defect analysis)
- `uv run python -m realworld.bakerydemo.validate_demo`: requirement validation on the real app, clean then with 3 bugs (about 20 min)
- `uv run python -m benchmark [--app clinic] [--only bug1,bug2] [--no-judge] [--no-vision] [--clean-only]` writes `runs/bench-*/bench.md`
- `uv run python -m realworld.bakerydemo.eval [--only bug,...] [--clean-only] [--skip-clean]` (real app; about 12 min)
- A new planted bug must be checked to actually show on the site before scoring anything against it.
- Model: any OpenAI-compatible endpoint via `MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY`; judge override `JUDGE_NAME` etc. Default: local Ollama `qwen3-vl:4b-instruct`.

## Rules
- **Holdout rule:** never tune a prompt, threshold, rule or spec against `holdout/`. Don't open its bug list while changing the agent. Run it only to report a number, and report that number as is.
- Verdicts stay honest: `fail` = the app has a bug, `error` = the tester couldn't finish, `flaky` = failed then passed. The benchmark counts a `fail` on a clean app as a false alarm.
- A pass needs proof: never weaken the judge's quote check to make a benchmark number better.
- A new planted bug goes in `BUGS` with the spec that should catch it. Re-run the benchmark before and after any prompt change and report both.
- Test data reaches the model only as `{{placeholders}}`. Demo data is fake.
