## What and why

## How I checked it

- [ ] `uv run ruff check .` and `uv run ruff format --check .`
- [ ] `uv run pyright`
- [ ] `uv run pytest -m "not live"`
- [ ] Live run, if the change depends on what the API returns (paste the "API calls" line)
