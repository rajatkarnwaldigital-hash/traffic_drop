# traffic-drop-detection

Identifies SaaS companies with significant organic traffic drops and generates personalised outreach hooks. Part of the EthicalSEO outbound engine.

---

## What it does

Takes a raw Apollo export of SaaS companies and runs them through a 3-stage pipeline:

1. **Qualify** — filters the raw list down to companies worth contacting (live site, English, has a blog, 500+ monthly visits, Authority Score ≥10)
2. **Detect drops** — compares each qualified company's current organic traffic against 6 months ago using SEMrush history; flags any company that dropped ≥20%
3. **Generate hooks** — for each flagged company, pulls their top organic keywords and writes a personalised 2–3 sentence cold email opening via Claude Sonnet

The end result is a CSV of flagged companies with ready-to-use outreach hooks mapped to real traffic data.

---

## Scripts

### 1. `qualify.py`

Filters a raw Apollo export down to qualified prospects.

**Input:** `apollo_export.csv`

The script expects a CSV exported directly from Apollo. The following columns must be present with these exact header names:

| Column | Required | Notes |
|--------|----------|-------|
| `Company Name` | Yes | Used in hook generation and output |
| `Website` | Yes | Primary filter field — rows with no website are dropped |
| `Industry` | Yes | Used for sector blocklist and physical service check |
| `Company City` | No | Passed through to output |
| `# Employees` | No | Passed through to output |
| `Country` | No | Passed through to output |

**How to export from Apollo:** Companies tab → select your filtered list → Export → CSV → choose "All fields" or at minimum the columns above. The script handles both full Apollo exports and trimmed versions as long as the required columns are present.

The script automatically cleans the `Website` column — it strips `https://`, `www.`, and trailing slashes, and deduplicates by domain. You do not need to pre-clean the URLs.

**Example input row:**
```
Company Name,Website,Industry,Company City,# Employees,Country
Acme Analytics,https://www.acmeanalytics.com,SaaS,London,51-200,United Kingdom
```

**Output:** `qualified_companies.csv`

Output columns:
| Column | Description |
|--------|-------------|
| `company_name` | Company name |
| `domain` | Cleaned domain (no https, no www, no trailing slash) |
| `industry` | Industry label |
| `city`, `country`, `employees` | Passed through from input |
| `site_alive` | `True` if site responded |
| `is_english` | `True` if `<html lang>` is English |
| `sector_ok` | `True` if not in blocked sector list |
| `is_digital` | `True` if Claude Haiku determined no physical visit required |
| `has_blog` | `True` if a blog path returned 200 with content |
| `blog_url` | The exact blog path that resolved |
| `authority_score` | SEMrush Authority Score |
| `monthly_traffic` | SEMrush organic traffic (US database) |
| `semrush_pending` | `True` if SEMrush key was missing at run time |
| `qualified` | `True` if all filters passed |
| `fail_reason` | Why the company was filtered out (if applicable) |

**Filters applied (in order):**
1. Sector blocklist check (healthcare, government, education, food, construction, etc.)
2. Physical service check — Claude Haiku YES/NO prompt
3. Site alive — tries 4 URL variants (https/http × www/non-www), HEAD then GET fallback
4. Language detection — reads `<html lang>` attribute
5. Blog detection — checks 8 paths: `/blog`, `/resources`, `/insights`, `/articles`, `/news`, `/learn`, `/content`, `/updates`
6. Organic traffic ≥ 500/month (SEMrush `domain_rank`, US database)
7. Authority Score ≥ 10 (SEMrush `backlinks_overview`)

**SEMrush cost:** ~2 API calls per company that reaches filters 6–7 (~10 units each)

**Performance:** Runs 10 parallel workers via `ThreadPoolExecutor`. Checkpoints every 100 rows to `qualified_companies.csv.checkpoint` — safe to interrupt and resume.

---

### 2. `traffic_drop.py`

Reads the qualified companies and flags those with a significant organic traffic drop.

**Input:** `qualified_companies.csv` — reads only rows where `qualified == True`

**Output:** `traffic_drop_results.csv`

Output columns:
| Column | Description |
|--------|-------------|
| `company_name` | Company name |
| `domain` | Domain |
| `industry`, `country` | Passed through from qualify output |
| `authority_score` | Passed through from qualify output |
| `traffic_6mo_ago` | Organic traffic at baseline month |
| `traffic_now` | Organic traffic at current month |
| `traffic_drop_pct` | Percentage change (negative = drop) |
| `drop_flagged` | `True` if drop ≥ 20% |
| `comparison_month` | The exact month used as baseline (format: `YYYYMMDD`) |
| `used_fallback` | `True` if company had <6 months of history; oldest available month used instead |

**Drop logic:**
- Pulls up to 8 months of traffic history via SEMrush `domain_rank_history` (US database)
- Compares traffic now (1 month lag) vs 7 months ago
- If no data from 7 months ago, falls back to the oldest available data point
- Flags drop if `(traffic_now - traffic_then) / traffic_then ≤ -0.20`
- Skips companies where either data point is missing (`no data` in terminal)

**SEMrush cost:** 80 units per domain (8 months × 10 units/line). Previously 240 units — reduced by 67%.

**Checkpointing:** Saves to `traffic_drop_checkpoint.json` after every domain. Resume by re-running — already-processed domains are skipped automatically.

---

### 3. `hook_generator.py`

Generates personalised cold email hooks for every flagged company.

**Input:** `traffic_drop_results.csv` — reads only rows where `drop_flagged == True`

**Output:** `outreach_hooks.csv`

Output columns:
| Column | Description |
|--------|-------------|
| `company_name` | Company name |
| `domain` | Domain |
| `industry`, `country` | Passed through |
| `authority_score` | Passed through |
| `traffic_6mo_ago` | Baseline traffic |
| `traffic_now` | Current traffic |
| `traffic_drop_pct` | Drop percentage |
| `top_keywords` | Pipe-separated list of top keywords with positions (e.g. `project management software (#4) | task tracking (#11)`) |
| `outreach_hook` | 2–3 sentence personalised cold email opening |

**Hook generation logic (single Claude Sonnet call per company):**
1. Fetches top 10 organic keywords from SEMrush (`domain_organic`, sorted by search volume)
2. Passes keywords + traffic data to Claude Sonnet with instructions to:
   - Silently filter out irrelevant keywords
   - Write a 2–3 sentence hook referencing a specific keyword or traffic stat
   - Sound human, not AI
   - Not mention the agency or pitch any service
   - Use `Hi [First Name]` as placeholder

**API cost:** 1 SEMrush call (10 units) + 1 Claude Sonnet call per flagged company

**Checkpointing:** Saves to `hook_generator_checkpoint.json` after every company. Resume by re-running.

---

## API Keys Required

| Key | Where to set it | Used by |
|-----|----------------|---------|
| SEMrush API key | `SEMRUSH_API_KEY` in each script | `qualify.py`, `traffic_drop.py`, `hook_generator.py` |
| Anthropic API key | `ANTHROPIC_API_KEY` in `qualify.py` and `hook_generator.py` | Physical service filter + hook generation |

> **Note:** The SEMrush key is IP-whitelisted. Run locally or via a consistent IP. Do not run from cloud VMs with dynamic IPs.

---

## Installation

```bash
pip install pandas requests beautifulsoup4 python-dateutil
```

Python 3.9+ required.

---

## Usage

### Step 1 — Prepare your input and run qualify.py

Export your company list from Apollo as a CSV and save it as `apollo_export.csv` in the same folder as the scripts. The file must have these column headers: `Company Name`, `Website`, `Industry`. See the Input spec above for full details.

Then run:

```bash
python qualify.py
```

This produces `qualified_companies.csv`. On a 5,000-company list expect ~30–45 minutes with 10 threads.

### Step 2 — Run traffic_drop.py

```bash
python traffic_drop.py
```

Reads `qualified_companies.csv` automatically (only `qualified == True` rows). Produces `traffic_drop_results.csv`. On 2,000 qualified companies expect ~35 minutes (1 second delay per domain) and ~160k SEMrush units.

### Step 3 — Run hook_generator.py

```bash
python hook_generator.py
```

Reads `traffic_drop_results.csv` automatically (only `drop_flagged == True` rows). Produces `outreach_hooks.csv`. On 200 flagged companies expect ~10 minutes.

---

## Checkpointing and Resume

All three scripts are safe to interrupt mid-run.

| Script | Checkpoint file | How to resume |
|--------|----------------|---------------|
| `qualify.py` | `qualified_companies.csv.checkpoint` | Re-run — already-processed domains skipped automatically |
| `traffic_drop.py` | `traffic_drop_checkpoint.json` | Re-run — already-processed domains skipped automatically |
| `hook_generator.py` | `hook_generator_checkpoint.json` | Re-run — already-processed domains skipped automatically |

Checkpoint files are deleted automatically on successful completion.

---

## SEMrush Unit Consumption (per full run)

| Stage | Units per domain | On 5,000 companies |
|-------|-----------------|-------------------|
| qualify.py | ~20 units (traffic + AS) | ~50,000 units |
| traffic_drop.py | 80 units (8 months history) | ~160,000 units (on ~2,000 qualified) |
| hook_generator.py | 10 units (top keywords) | ~2,000 units (on ~200 flagged) |
| **Total** | | **~212,000 units** |

---

## Known Limitations

- `traffic_now` uses a 1-month lag (SEMrush data is not real-time). A company that dropped last week won't show up yet.
- Companies with fewer than 3 months of history may produce unreliable drop signals. Check `used_fallback == True` rows manually before including them in outreach.
- The physical service filter uses Claude Haiku — it's accurate for clear-cut cases but may misclassify niche B2B services with unusual industry labels.
- Bot-blocked domains (`site_alive = bot_blocked`) are excluded from qualification. Manual spot-check recommended on large runs to see the false-positive rate.
- Hook quality degrades if SEMrush returns no keyword data for a domain — the hook will still be generated but will rely only on traffic numbers.

---

## File Structure

```
traffic-drop-detection/
├── qualify.py                   # Stage 1: filter Apollo export
├── traffic_drop.py              # Stage 2: detect traffic drops
├── hook_generator.py            # Stage 3: generate outreach hooks
├── apollo_export.csv            # Your input file (gitignored)
├── qualified_companies.csv      # Output of Stage 1 (gitignored)
├── traffic_drop_results.csv     # Output of Stage 2 (gitignored)
└── outreach_hooks.csv           # Output of Stage 3 — load into Plusvibe
```

---

## Plusvibe Sequence (Reference)

Load `outreach_hooks.csv` into Plusvibe. Map the `outreach_hook` column to `{{hook}}` in the email copy.

| Step | Day | Purpose |
|------|-----|---------|
| Email 1 | Day 1 | Hook-led opener — `{{hook}}` as the first paragraph |
| Email 2 | Day 4 | Follow-up — reference the drop signal again, add social proof |
| Email 3 | Day 9 | Short nudge — one line + CTA |
| Email 4 | Day 14 | Break-up email |

Proof points to use in copy: Wallester (800% organic growth in 12 months), Vespia (10x traffic, acquired by Veriff), Remofirst (136 backlinks, 478% overall growth, $170K/year traffic value).
