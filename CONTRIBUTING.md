# Contributing

Thanks for helping. Small, focused pull requests with a test are the easiest
to review and merge.

## Setup

```bash
git clone https://github.com/herascrape/instagram-scraper
cd instagram-scraper
uv sync                      # Python 3.10+, installs the dev tools too
uv run instagram-scraper --help
```

CI runs these four checks on Python 3.10 to 3.13. Run them before you push:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -m "not live"
```

Unit tests never touch the network. They drive the real `herascrape` client
through `httpx.MockTransport`: `tests/fakeapi.py` routes requests and
`tests/factories.py` builds payloads shaped like the live API.

Live tests in `tests/live/` call the real API and need a key. They cost about 40
calls ($0.016):

```bash
HERASCRAPE_API_KEY=hs_live_xxxxxxxx uv run pytest tests/live -m live
```

Never commit a key. A test fails CI if anything shaped like one is tracked.

## Layout

```
instagram_scrapers/
  cli.py             typer commands; prompts, progress, errors, exports
  client.py          herascrape client plus the billed-call counter
  instagram.py       typed views over Instagram payloads, pagination, list walks
  signals.py         fake and ghost follower signals and scoring
  fake_followers.py  fake-followers command logic and report
  giveaway.py        giveaway entries, rules, seeded ranking, winner checks
  audit.py           audit metrics and report
  followers.py       follower snapshots (SQLite) and diffs
  export.py          .csv / .json / .md writers
  report.py          Markdown helpers shared by the reports
```

Library code is async, takes a `herascrape.AsyncClient`, returns a pydantic
model and never prints. `cli.py` is the only module that talks to the terminal.

## Recipes

**A new command.** Write the logic as an async function in its own module that
returns a pydantic model with `csv_rows()`, `markdown()` and a
`usage: Usage | None = None` field. Add a typer command in `cli.py` that builds
the job and hands it to `_run(...)`, which takes care of the key, errors,
progress, exports and the cost line. If the run's size is known up front, call
`session.confirm_budget(...)` before the expensive part. Add a section to the
README with a real run and its cost.

**A new export format.** Add a writer to `WRITERS` in `export.py`, keyed by file
extension. It receives the result and a path; use `csv_rows()` or the model
itself. Keep heavy dependencies optional (an extra in `pyproject.toml`).

**A new fake follower signal.** Add it to `Signal`, `SIGNAL_LABELS` and one of the
weight tables in `signals.py`, compute it in `profile_signals`, and update the
scoring table in the README. Explain in the pull request what real accounts it
catches and what it gets wrong.

## Instagram behaviour worth knowing

Measured through the herascrape API:

- Followers and following come 25 per page, posts 9, comments 15.
- Follower and following lists are reordered between requests. One pass sees
  70 to 90% of a list; `walk_until_complete` repeats passes until the list
  stops growing.
- About 1 in 15 list pages comes back as a 404 that succeeds on retry (a
  throttled account on the API side); `_page` in `instagram.py` retries it.
- Some business and verified accounts list only about 50 followers (natgeo:
  45 to 49 of 268 million).
- The profile grid includes pinned posts; `fetch_posts` drops them.
- Some posts hide like counts (`like_count` is null). Leave them out of ratios.
- Comments page with `min_id` until `has_more` is false. Replies are separate.
- Private accounts can't be read. This tool never logs in.

## Style

- pyright strict, ruff (line length 80). Type everything; no `Any` where a real
  type fits.
- Keyword-only arguments for functions with more than two parameters.
- No `from __future__ import annotations`.
- Docstrings short; comments only where the code can't explain itself.
- User-facing text: specific numbers, no hype, no em dashes.

## Pull requests

One change per pull request, with tests. Describe what you changed, why, and how
you checked it, including a live run if you touched what the API returns. Use
[conventional commit](https://www.conventionalcommits.org/) subjects:
`feat: ...`, `fix: ...`, `docs: ...`.
