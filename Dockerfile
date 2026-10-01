# Nightshift anywhere: Jenkins, GitLab CI, a cron box.
#
#   docker build -t nightshift .
#   docker run --rm -v "$PWD:/work" -e MODEL_BASE_URL -e MODEL_NAME -e MODEL_API_KEY nightshift run specs/
#
# The Playwright image ships the browsers for exactly this Playwright version,
# which is why the tag matches the version pinned in pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY nightshift ./nightshift
COPY demo_shop ./demo_shop
COPY holdout ./holdout
COPY benchmark ./benchmark
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /work
ENTRYPOINT ["nightshift"]
