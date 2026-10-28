# AGENTS.md

Guidance for AI agents (and humans) working in this repo. Read
[CONTRIBUTING.md](CONTRIBUTING.md) first: it has the layout, the recipes and the
Instagram behaviour the code is built around.

## Rules

- Run the four checks before you finish: `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run pyright`,
  `uv run pytest -m "not live"`.
- Unit tests stay offline: use `tests/fakeapi.py` and `tests/factories.py`.
- Library modules never print; only `cli.py` talks to the terminal.
- Instagram payloads are dicts at the edge; `instagram.py` turns the fields we
  use into pydantic models. Don't spread raw dict access across modules.
- Every claim in a report or the README must come from a real run. No invented
  numbers, benchmarks or grades.
- Never commit an API key. Use the placeholder `hs_live_xxxxxxxx`.
