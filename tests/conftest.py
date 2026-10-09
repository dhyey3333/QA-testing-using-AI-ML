import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # so tests can import scripted.py
# Tests encrypt secrets with a key of their own, never the installation's (~/.nightshift/secret.key).
os.environ["NIGHTSHIFT_KEY_FILE"] = str(Path(tempfile.mkdtemp(prefix="nightshift-test-key-")) / "secret.key")
os.environ.pop("NIGHTSHIFT_SECRET_KEY", None)

from demo_shop.server import make_server  # noqa: E402
from nightshift.runner import RunOptions, open_browser, run_spec  # noqa: E402
from nightshift.spec import load_spec  # noqa: E402
from scripted import ScriptedModel  # noqa: E402

SPECS = Path(__file__).resolve().parent.parent / "specs"


@pytest.fixture(scope="session")
def shop():
    server = make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="session")
def browser():
    with open_browser() as browser:
        yield browser


@pytest.fixture
def base_url(shop):
    return f"http://127.0.0.1:{shop.server_port}"


@pytest.fixture
def spec_for(base_url):
    def _spec(name):
        return load_spec(SPECS / f"{name}.yaml").with_base_url(base_url)

    return _spec


@pytest.fixture
def run(shop, browser, tmp_path, spec_for):
    """run("checkout", script, bugs=..., evidence=..., recordings=store) -> RunResult"""

    counter = {"n": 0}

    def _run(spec_name, script=(), *, bugs=(), variants=(), evidence=(), model=None, **options):
        shop.bugs = set(bugs)
        shop.variants = set(variants)
        counter["n"] += 1
        model = model or ScriptedModel(script, evidence)
        out_dir = tmp_path / f"{spec_name}-{counter['n']}"
        return run_spec(browser, spec_for(spec_name), model, out_dir=out_dir, options=RunOptions(**options))

    yield _run
    shop.bugs = set()
    shop.variants = set()
