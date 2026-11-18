# Official NSE corporate actions

A beginner's guide to what we download from NSE, where it is stored, and how to use it.

## What is a corporate action, and why do we care?

When a company splits its shares (one Rs 10 share becomes two Rs 5 shares) or gives bonus
shares, the price drops overnight even though nobody lost money. On a chart this looks like a
crash. To compare prices across time we **adjust** the old prices: multiply every price before
that date by a number called the **price factor**.

Until now we *guessed* these factors from the size of the price drop. That works for clean
cases but is wrong when two actions happen on one day or the market also moved. A check of our
applied events against the official data (18 Nov 2026) found 321 correct, **19 with the wrong
factor** and 23 with no official event at all. NSE publishes the exact numbers, so we now
download them.

## The three reports

| Report | Where it comes from | What we use it for |
|---|---|---|
| **Corporate actions** | NSE website API `/api/corporates-corporateActions` (the same call the "Corporate Filings > Corporate Actions" page makes; there is no ready-made CSV file, the page builds its CSV in your browser) | Exact split / bonus / consolidation factors, plus rights, mergers, demergers, dividends |
| **Symbol changes** | `nsearchives.nseindia.com/content/equities/symbolchange.csv` (a static file, like our Bhavcopy files) | Official evidence for renames such as ICICIPRULI to ICICILIFE. The API cannot give this |
| **Listing master** | `nsearchives.nseindia.com/content/equities/EQUITY_L.csv` | ISIN, face value, listing date for every listed symbol |

All three use one HTTP session and one retry method (`NSEReportsDownloader` in
`src/ingestion/corporate_actions.py`), built the same way as the Bhavcopy downloader.

NSE's website shows a "1 year" limit in its date dropdown, but the API itself returned the
full 10 years in one call (checked 18 Nov 2026). We still ask one calendar year at a time so a
failed call only repeats a small piece.

## How to run it

```bash
source .venv/bin/activate
bash scripts/backup_db.sh                       # backup first (it writes to the database)
alembic upgrade head                            # creates the two new tables (migration 008)
python run_pipeline.py --download-corporate-actions
```

Optional window (default is 10 years back to 90 days ahead, so announced future actions are
included):

```bash
python run_pipeline.py --download-corporate-actions --start 2024-01-01 --end 2026-12-31
```

It is safe to run again and again: rows are updated in place, never duplicated.
`--dry-run` is refused for this command because it writes.

Expected last lines (numbers from the real run on 18 Nov 2026):

```text
Corporate actions: 22545 API rows -> 22537 stored (724 exact-factor events, 413 to review).
Symbol changes stored: 1080 (361 touch our stock_master). Listing master rows: 2632.
```

## What is stored

Files in `data/corporate_actions/` (git-ignored, never committed):

| File | Content |
|---|---|
| `corporate_actions_raw.json` | The untouched API response. Kept so a format change never loses data and the text can be re-read later |
| `symbol_changes_raw.csv` | The untouched `symbolchange.csv` |
| `equity_listing.parquet` | Parsed `EQUITY_L.csv` (listing date is a real date) |

MySQL tables (created by migration 008):

* **`corporate_actions`**: one row per action. A line like
  `Bonus 1:1/Face Value Split ... From Rs 10 To Re 1` holds two actions, so it becomes two rows
  (`part_no` 0 and 1). Important columns: `symbol`, `ex_date`, `action_type`
  (`SPLIT`, `BONUS`, `CONSOLIDATION`, `RIGHTS`, `DEMERGER`, `MERGER`, `ARRANGEMENT`,
  `CAPITAL_REDUCTION`, `BUYBACK`, `DIVIDEND`, `MEETING`, `OTHER`), `subject` (NSE's original
  text), `price_factor` and `parse_status`.
* **`symbol_changes`**: `old_symbol`, `new_symbol`, `effective_date`, `company`. This is
  evidence only; it does not merge any price history (renames still go through
  `--apply-symbol-aliases` and its price-continuity check).

### `parse_status`: how much to trust a row

| Status | Meaning | Used for adjustment? |
|---|---|---|
| `exact` | A split, bonus or consolidation whose ratio was read exactly | Yes (`price_factor` is set) |
| `needs_review` | May move the price but needs a person: rights, mergers, demergers, unreadable text | **Never automatically** |
| `info` | Dividends, meetings, buybacks: no price adjustment | No |

### How the price factor is worked out

The factor is what you multiply every price **before** the ex-date by.

| NSE text | Calculation | Factor |
|---|---|---|
| Split, face value Rs 10 to Rs 5 | 5 / 10 | 0.5 |
| Bonus 1:1 (1 new share per 1 held) | 1 / (1 + 1) | 0.5 |
| Bonus 1:2 (1 new share per 2 held) | 2 / (1 + 2) | 0.6667 |
| Consolidation, face value Re 1 to Rs 10 | 10 / 1 | 10 |

Two actions on one day are multiplied: split 10 to 2 (0.2) and bonus 1:1 (0.5) on the same
day give **0.1**. (AHCL, 24 Apr 2026.)

## Reading it from code

```python
from src.db.session import get_db
from src.ingestion.corporate_actions_store import get_price_adjustment_events

with get_db() as session:
    events, review = get_price_adjustment_events(session)

# events: symbol, date, factor, kinds, n_parts   (one row per symbol and ex-date)
# review: symbol, date, action_type, subject     (needs a person, never applied automatically)
print(events[events.symbol == "HARDWYN"])
```

Expected: HARDWYN on 2023-06-05 has `factor` 0.075 (split 10 to 1 = 0.1, bonus 1:3 = 0.75) and
`kinds` `BONUS+SPLIT`.

## How the adjustment uses it

`python run_pipeline.py --rebuild-adjusted` (`src/etl/adjust.py`) works in four steps:

1. `read_official_events` reads the exact events above. An empty table stops the command
   ("run --download-corporate-actions").
2. `build_events` keeps an event only if the stock has prices **before** it and a price **on or
   after** it. This drops future ex-dates (the table runs about 3 months ahead) and actions
   from before the stock's first stored price.
3. The owner's `config/adjustment_overrides.csv` is applied last and always wins
   (`apply` an exact factor, `ignore` an official event).
4. `cumulative_factors` multiplies every older price by the factors after it.

Prices are never used to guess a ratio. `find_unexplained_jumps` only writes a review list
(`reports/adj_events_uncertain.csv`): big overnight moves that no applied event explains, with
a `reason` and an `implied_factor` hint. If a real split is on that list, NSE's data lacks it:
add an `apply` row to the overrides file and rebuild.

Run `--rebuild-adjusted` after every `--download-corporate-actions`.

## NSE changes formats: how we stay safe

Run the live health check when something looks wrong, or about once a month. It writes nothing:

```bash
python scripts/check_nse_reports.py
```

It prints PASS or FAIL lines (shape, fields, readable wording, three known factors, known
rename and ISIN) and exits with code 1 on any failure. It talks to the real NSE site, so
pytest and CI never run it.

The download itself also protects the data. It stops **before writing to the database**, with a
clear message, when:

* the API answers with something that is not a list of rows (NSE answers "no data" with HTTP
  200 and a small dictionary, not an error);
* a row lacks a required field, or a CSV lacks a required column (CSV columns are matched by
  name, never by position);
* a date is not in `18-Nov-2026` form;
* a long past window comes back empty;
* more than 5 percent of splits, bonuses and consolidations cannot be read (it is 0.13 percent
  today). The limit is `CORP_ACTIONS_MAX_UNPARSED_SHARE` in `config/settings.py`.

The raw response is saved before it is interpreted, so nothing is lost. Stored rows that NSE
stops listing (for example a moved ex-date) are counted in the log and kept, not deleted.

If NSE moves the site to another host, set the environment variable `NSE_API_BASE`
(NSE also mirrors the site on `nseindia.in`).

## Known limits

* The `/api/` path is listed in NSE's robots.txt, so keep the polite pace (one session, one
  call per year, a short pause) and do not run it in a tight loop.
* It has not been tested from a data-centre IP address (NSE sometimes blocks those); test it
  first when the nightly server is set up.
* Rights issues, mergers and demergers need share prices to compute a factor, so they are
  listed for review only.
* At least one official bonus shows no price jump in our data (DVL, 2021-08-05, bonus 1:2);
  such cases need a look before adjusting.
