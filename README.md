# Instagram Scraper: fake follower checker, giveaway picker, audit and follower tracker

[![PyPI](https://img.shields.io/pypi/v/instagram-scrapers?label=pypi)](https://pypi.org/project/instagram-scrapers/)
[![Python](https://img.shields.io/pypi/pyversions/instagram-scrapers)](https://pypi.org/project/instagram-scrapers/)
[![CI](https://github.com/herascrape/instagram-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/herascrape/instagram-scraper/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Four Instagram jobs, one command each:

- **`fake-followers`**: what share of an account's followers look fake or inactive.
- **`giveaway`**: pick winners from a post's comments, with filters, in a draw
  anyone can re-run.
- **`audit`**: engagement rate, posting cadence, best days and times, content
  mix and top posts.
- **`followers`**: who followed and who unfollowed since the last run.

You never log in with your own Instagram account, so there is no account to get
banned. The scraping runs on the [herascrape](https://herascrape.com) Instagram
API, which is maintained for you when Instagram changes something.

```bash
pipx install instagram-scrapers
export HERASCRAPE_API_KEY=hs_live_xxxxxxxx
instagram-scraper audit natgeo
```

## Install

```bash
pipx install instagram-scrapers     # or: uv tool install instagram-scrapers
instagram-scraper --help
```

Python 3.10 or newer. `pip install instagram-scrapers` works too if you want
the library in a project.

## API key and cost

Create a key at [herascrape.com/api-keys](https://herascrape.com/api-keys) and
put it in `HERASCRAPE_API_KEY` (or pass `--api-key`). New accounts get $1 of free
balance, about 2,500 calls, once Telegram is connected. That covers 23 fake
follower checks or 400 audits.

A call costs $0.0004 and failed calls are not billed. Every run ends with the
calls it made and their cost:

```
106 API calls · $0.0424
```

Commands that know their size up front ask before spending more than 1,250 calls
($0.50). Pass `--yes` to skip the question in scripts.

| Command | Calls for a typical run | Cost |
| --- | --- | --- |
| `fake-followers` (100 sampled) | about 106 | $0.04 |
| `giveaway` (1,282 comments) | 87, plus up to 1,000 for winner checks | $0.035 |
| `audit` (24 posts, 50 followers) | 6 | $0.0024 |
| `followers` (3,295 followers) | 661 first run, 1,057 next | $0.26, $0.42 |

## Fake follower checker

```bash
instagram-scraper fake-followers youkneadsourdough -o followers.csv
```

It takes a sample of followers (100 by default, `--sample` up to 1,000), reads
each one's profile and scores it on the signals in
[how the score works](#how-the-fake-follower-score-works). Real output, abridged,
with follower handles masked:

```
Fake follower check: @youkneadsourdough
Sample: 100 of 51,487 followers, as Instagram lists them, checked 2026-10-28.

                     Followers   Share
 Real                       79     79%
 Ghost (inactive)           14     14%
 Fake (bot-like)             7      7%

Flagged accounts
 Account      Category  Followers  Following  Posts  Why
 @e•••1973    fake             75      2,471      1  no profile picture, fewer than 10 posts, follows 10x more ...
 @l•••1963    fake             45        575      0  no posts, follows 10x more accounts than follow it (500+), ...
 @k•••2808    fake             52      2,495      5  fewer than 10 posts, follows 10x more accounts than follow ...
```

The CSV has one row per sampled follower with the score and the reasons. Two
things limit what a sample can say, and the report states both:

- Instagram shows only about 50 followers of some business and verified
  accounts (natgeo returns 45 to 49 of 268 million), so for those the check
  covers what Instagram lists.
- The sample is the first accounts Instagram returns, not a random draw from
  the whole audience. Treat the share as an estimate, not a verdict on any one
  account.

## Giveaway comment picker

```bash
instagram-scraper giveaway https://www.instagram.com/p/DQG9-twEzlf/ \
  --winners 2 --min-mentions 1 --seed carrs-demo -o entries.csv
```

It fetches every top-level comment, applies the rules, ranks the entries with a
seed and prints a result you can post. Real output from a closed
[@carrs_flour](https://www.instagram.com/carrs_flour/) giveaway, abridged, with
entrant handles masked:

```
Giveaway draw: https://www.instagram.com/p/DQG9-twEzlf/
Post by @carrs_flour. Drawn from 1,266 top-level comments (Instagram counts 1,282 including replies).

Winners
 Rank  Account      Comment      Posted
    1  @r•••egas    @k•••on30    2025-10-27 09:53 UTC
    2  @l•••llyn    @f•••arah    2025-10-22 15:53 UTC

Entries: 904 eligible entries from 904 accounts.
 Not counted                Comments
 entry limit (1) reached         355
 0 of 1 mentions                   6
 host                              1

Verify this draw
 Seed: carrs-demo
 Seed SHA-256: 6151702aca6d70a1cfaeeb0c1d5f8eaf19507285eb8e59888492f7dc907ca4b4
 Entries digest (SHA-256): 1fad1185daff34fbfb279e2bc738cc64ceade9cb8efc18550834520ed7d9fb6b
```

| Rule | Option | Cost |
| --- | --- | --- |
| Tag at least N other accounts | `--min-mentions N` | free |
| Include a word or hashtag (repeatable, all required) | `--keyword "#giveaway"` | free |
| Ignore comments after the close time | `--until 2026-11-01T18:00` | free |
| Entries per account (default 1) | `--max-entries-per-user N` | free |
| Exclude accounts (the host and co-authors always are) | `--exclude name` | free |
| Winner has N followers, N posts, a profile picture | `--min-followers` `--min-posts` `--require-avatar` | 1 call per drawn account |
| Winner follows an account | `--must-follow host` | see below |

Profile checks run only on drawn accounts, in rank order, until enough pass, so
their cost depends on the number of winners, not the number of entries.
`--must-follow` reads the drawn account's following list: 1 call per 25
accounts it follows, read up to 5 times because Instagram reorders the list
between requests. Accounts that follow more than `--follow-scan-limit` accounts
(2,000 by default) are skipped, and the published rules say so. On busy
giveaways many entrants follow thousands of accounts, so expect skips. Winner
checks stop after `--max-check-calls` (1,000 by default) and the cost prompt
counts that limit up front.

### Verify a draw

The ranking needs no randomness at draw time: each entry's rank is
`sha256(seed + ":" + comment_pk)`, ascending. With the seed and the entries CSV,
anyone can check the digest and the winners:

```python
import csv
import hashlib

seed = "carrs-demo"
with open("entries.csv", encoding="utf-8-sig") as f:
    entries = [r for r in csv.DictReader(f) if r["eligible"] == "True"]

entries.sort(key=lambda r: int(r["comment_pk"]))
lines = "".join(f"{r['comment_pk']},{r['user_pk']}\n" for r in entries)
print(hashlib.sha256(lines.encode()).hexdigest())

def rank(r):
    return hashlib.sha256(f"{seed}:{r['comment_pk']}".encode()).hexdigest()

print([r["username"] for r in sorted(entries, key=rank)[:4]])
```

The winners are the first ranked entries that pass the winner checks; the
`result` column of the CSV marks winners, backups and rejected entries with the
reason. Verify from the CSV as downloaded: opening and re-saving it in Excel
rounds the long ids and changes the digest.

Without `--seed` the seed is 128 random bits, printed with the result. For a
public draw, commit to the seed before the giveaway closes: pick one, publish
its SHA-256 (`printf %s 'your seed' | shasum -a 256`), and draw with `--seed`.
The result prints the seed and its hash, so anyone can check they match.
Don't publish the seed itself early: with it, an entrant could re-post a
comment until it ranks first. A host who knows the seed in advance can still
favor a friend, so for high-stakes draws use a seed nobody knows before the
close, such as a public randomness beacon round.

## Account audit

```bash
instagram-scraper audit youkneadsourdough --tz Australia/Adelaide -o audit.md
```

Real output, abridged:

```
Instagram audit: @youkneadsourdough
You Knead Sourdough · business account · 51,487 followers · 357 following · 858 posts

Engagement: last 24 posts, 2026-09-29 to 2026-10-28
 Engagement rate, mean          0.10%
 Engagement rate, median        0.03%
 Reel plays, mean               2,158
 Posts per week                   5.6
 Longest gap                 3.1 days

Content mix
 Type        Posts   Avg engagement
 Reels          18            0.06%
 Carousels       2            0.04%
 Photos          4            0.34%
```

The full report adds engagement by weekday and by 3-hour block in your time
zone, the top 5 posts, the hashtags and accounts mentioned in captions, and a
light check of 50 followers (no picture, private, generated-looking names).
Engagement rate is (likes + comments) / followers per post. Pinned posts are
left out. Posts that hide their like counts are left out of engagement, and the
report says how many. Add `--deep-audience` for the full real, ghost and fake
split, at one call per sampled follower.

## Follower tracker: who unfollowed me

```bash
instagram-scraper followers your_account
```

The first run saves a snapshot of the whole follower list. Each later run saves
another and lists who followed, who unfollowed and who changed their username.
Snapshots stay on your machine in an SQLite file (`--db` to choose where).

Real output from two runs an hour apart on
[@microbakery00](https://www.instagram.com/microbakery00/), abridged, follower
handles masked:

```
Follower changes: @microbakery00
Snapshot 2026-10-28 04:34 UTC: 3,292 of 3,295 followers, read in 8 passes.
Since 2026-10-28 03:34 UTC: 16 new, 2 unfollowed, 0 renamed.

New followers (16)
 - @l•••atte
 - @p•••_b_k
 - @j•••nnan
 ...
The 2026-10-28 03:34 UTC snapshot listed 3,278 of 3,295 followers, so up to
17 of these may have followed before it.

Unfollowed (2)
 - @l•••nilo
 - @l•••air1
```

Instagram reorders follower lists between requests, so a single pass over the
list misses 10 to 30% of it. The tracker reads the list again until a pass
adds nobody new (up to 5 passes), and before it reports anyone as unfollowed
it looks for them again (up to 3 more). That is 5 to 8 calls per 25 followers:
661 calls for the first run on 3,295 followers, 1,057 for the next. It compares
only complete snapshots. New followers are an upper bound: when the previous
snapshot missed a few accounts, the report says how many of the "new" ones may
have followed earlier. When Instagram
lists only part of an account's followers (it does this for some business and
verified accounts), it says so and reports no changes rather than guessed
ones. Private accounts can't be read without logging in, and this tool never
logs in.

Run it daily with cron:

```
0 9 * * * HERASCRAPE_API_KEY=hs_live_xxxxxxxx $HOME/.local/bin/instagram-scraper followers your_account --yes -o $HOME/followers-$(date +\%F).csv
```

## Output formats

Every command prints a report and can write it to files at the same time:

```bash
instagram-scraper audit natgeo -o audit.md -o audit.json -o posts.csv
instagram-scraper fake-followers natgeo --json | jq .counts
```

| Extension | Contents |
| --- | --- |
| `.md` | The report as Markdown, ready for GitHub, Notion or a doc |
| `.json` | Every field of the result |
| `.csv` | One row per item: sampled follower, comment entry, post or follower change. UTF-8 with BOM so Excel shows names correctly |

`--json` prints the JSON to stdout instead of the report. Progress and cost go
to stderr, so pipes stay clean.

## How the fake follower score works

Each sampled follower's profile is checked for these signals:

| Signal | Rule | Points |
| --- | --- | --- |
| follow ratio | follows 500+ accounts and 10x more than follow it | 3 bot |
| mass following | follows 5,000+ accounts | 1 bot |
| generated username | 4+ trailing digits, or 40%+ digits | 1 bot |
| no display name | empty name | 1 bot |
| no profile picture | Instagram's default picture | 2 inactive |
| no posts | 0 posts | 2 inactive |
| few posts | 1 to 9 posts | 1 inactive |
| empty bio | no bio and no link | 1 inactive |

- **Fake** (bot-like): 3+ bot points, 5+ points in total, and no picture or
  fewer than 10 posts.
- **Ghost** (inactive): 3+ inactive points and fewer than 10 posts.
- **Real**: everything else, and every verified account.

The rules live in [`signals.py`](instagram_scrapers/signals.py). They catch
accounts that follow far more than they post. They do not catch bought
followers made to look active, and one signal on its own means little.

## Use it as a library

Each command is an async function that returns a pydantic model:

```python
import asyncio

from instagram_scrapers import audit_account, get_public_profile, open_client


async def main() -> None:
    client, counter = open_client(api_key="hs_live_xxxxxxxx")
    async with client:
        profile = await get_public_profile(client, username="natgeo")
        report = await audit_account(client, profile=profile, posts=24)
    print(report.markdown())
    print(f"{counter.usage().calls} calls, ${counter.usage().cost_usd}")


asyncio.run(main())
```

`check_fake_followers`, `run_giveaway` and `track_followers` work the same way.
For any other Instagram data, use the [`herascrape`](https://github.com/herascrape/herascrape)
client this tool is built on: 37 endpoints for profiles, posts, reels, stories,
comments, hashtags, locations and search.

## Contributing

Issues and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) covers
setup, the checks CI runs and how to add a command, an export format or a
signal. Issues labeled `good first issue` are a good place to start.

## Related

- [herascrape](https://github.com/herascrape/herascrape): the Instagram API
  client for Python and TypeScript this tool uses.
- [herascrape-mcp](https://github.com/herascrape/herascrape-mcp): Instagram
  data for Claude, Cursor and other AI agents.
- [instagram-video-downloader](https://github.com/herascrape/instagram-video-downloader):
  download public reels, videos, photos and stories.

## License

MIT. Not affiliated with Instagram or Meta. It reads public data only; follow
Instagram's terms and your local laws when you use it.
