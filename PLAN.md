# Plan: finishing the MN election tracker

## Where things stand (built in a cloud sandbox, Nov 4 2026)

**Done and tested**
- Historical baselines downloaded and normalized into one table:
  `data/baselines/precinct_results.csv` (172k rows, 2018/2020/2022/2024,
  statewide races only, parties bucketed DFL / R / OTHER).
- Statewide totals checked against known results:
  2018 Gov DFL +11.4, Senate +24.1, Special Senate +10.6 · 2020 Pres +7.1 ·
  2022 Gov +7.7, AG +0.8, SoS +9.2, Auditor +0.3 · 2024 Pres +4.2, Senate +15.7.
- SOS media-file parser, snapshotting, comparison engine (precinct, county,
  statewide) and CLI.
- End-to-end test: a synthetic feed (2024 presidential replayed in SOS
  format, half the precincts "reported") gives exactly 0 swing and 1.0
  turnout index in all 87 counties.

**Not done / unverified (the build machine could not reach sos.mn.gov)**
1. ~~Exact live file names and column layout have **not** been checked
   against the real 2026 feed.~~ **Checked Nov 3 2026 (local):** the 16-field
   layout matches. `allracesbycounty.txt` and `PrctTbl.txt` are live;
   `allracesbyprecinct.txt` is not posted yet (404). Until it is, `once` and
   `poll` run in **county mode** (`compare.run_county`), which writes
   `county_swing.csv` and `statewide_summary.csv` only. The server returns
   403 for folder listings, so `discover` doesn't work: probe file names
   directly.
2. 2022 precinct matching (by name) depends on the live precinct-name table
   and is untested against real names.
3. No Power BI model or visuals yet.
4. ~~No GitHub repo yet.~~ Done: github.com/bschiwal/mn-election-tracker (private).

---

## Step 0: Get the code onto your machine (5 min)

You received `mn-election-tracker.zip` (working copy) and
`mn-election-tracker.bundle` (full git history).

```bash
git clone mn-election-tracker.bundle mn-election-tracker
cd mn-election-tracker
gh repo create mn-election-tracker --private --source . --remote origin --push
```

Without `gh`: create an empty **private** repo on github.com, then
`git remote add origin <url>` and `git push -u origin main`.

Then set up Python:

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover tests
```

## Step 1: Confirm the live SOS files (15–30 min) ← most important

1. Open https://www.sos.state.mn.us/elections-voting/election-results/ and
   find the **"Election Results Media Files"** link for the 2026 general.
   It should list `.txt` files and link a **media-file layout PDF**.
   Also try:
   `python -m mnelect discover https://electionresultsfiles.sos.mn.gov/20261103/`
2. Identify:
   - a file with **statewide races by precinct**. Possible names:
     `allracesbyprecinct.txt`, `stprec.txt`, or one file per office such as
     `ussenate…`, `governor…`. Put every relevant name in
     `[live].precinct_files`.
   - the **precinct table** file (County ID; Precinct ID; Precinct Name …),
     e.g. `PrctTbl.txt`. Put it in `[live].precinct_table_file`.
3. Compare the layout PDF with `SOS_COLS` in `mnelect/sos.py`. The parser
   expects 16 semicolon-separated fields with no header:
   state, county id, precinct id, office id, office name, district,
   candidate order, candidate, suffix, incumbent, party, precincts
   reporting, precincts total, votes, pct, office total. If the order
   differs, edit `SOS_COLS` only.
4. Download one file by hand and run:
   ```python
   from mnelect import sos
   df = sos.parse_results(open("allracesbyprecinct.txt","rb").read())
   df.groupby("office").votes.sum()
   ```
   Check that the office names map correctly (`mnelect/common.py`,
   `map_office`). The SOS labels are likely `U.S. Senator` and
   `Governor & Lt Governor`. Only President, US Senate, Governor, AG,
   SoS and Auditor are kept.
5. Check the party codes (`map_party`). The DFL should be `DFL` and the
   GOP `R`. Everything else becomes OTHER.

## Step 2: First real run (10 min)

```bash
python -m mnelect once
```
- Watch the printed **2022 precinct name match rate**. Aim for >90%. If it's
  low, look at unmatched names and add rules to `norm_name()` (e.g.
  `TWP.` vs `TWP`, `W-1 P-1` vs `W-01 P-01`, `UNORG` spellings).
- Sanity checks:
  - `statewide_summary.csv` → `live_margin` should match the SOS website
    headline numbers.
  - In `county_swing.csv`, `precincts` per county should match the SOS
    county precinct counts (statewide ~4,100).
  - In `precinct_swing.csv`, the `matched` rate vs 2024 should be high
    (~95%+). 2018/2020 will be lower (codes renumbered at redistricting).
- Then `python -m mnelect poll` and leave it running.
  `data/snapshots/` keeps every pull, so you can replay the night later
  with `--from-dir`.

## Step 3: Power BI model (1–2 hrs)

1. **Get Data → Text/CSV** for the three output files, plus
   `data/reference/counties.csv` as a dimension.
2. Relationships: `counties[county_id]` 1→* `county_swing[county_id]` and
   `precinct_swing[county_id]`.
3. Slicers: `live_office`, `baseline_year`, `comparison`.
4. Suggested measures (DAX):
   ```DAX
   Swing (pts) = AVERAGE(county_swing[swing_reported]) * 100
   Turnout vs base = DIVIDE(SUM(precinct_swing[total]), SUM(precinct_swing[total_base]))
   DFL margin (pts) = DIVIDE(SUM(precinct_swing[DFL]) - SUM(precinct_swing[R]), SUM(precinct_swing[total])) * 100
   ```
   For statewide or regional swing, compute it from summed votes, not by
   averaging county swings, so large counties weigh more. Use the
   `DFL`/`R`/`total` and `*_base` columns.
5. Visuals: filled map by county colored by `swing_reported` (diverging
   red↔blue, centered on 0); scatter of 2024 margin (x) vs swing (y); table
   of counties sorted by % reported.
6. Precinct maps need shapes. Download the MN precinct shapefile
   (MN Geospatial Commons / LCC-GIS "Election Precincts 2024"), convert
   to TopoJSON (mapshaper.org) for Power BI's Shape Map, and join on
   county + precinct code (`VTDID` is usually `27` + county FIPS + code).

## Step 4: Improvements, in priority order

1. **Better turnout denominator.** The SOS media files usually include
   registered voters and ballots cast per precinct (often a separate
   "precinct table" or "turnout" file). Add them to `sos.py` and compute
   ballots-cast turnout instead of race votes.
2. **Precinct crosswalk across redistricting (2018/2020).** Use the 2020→2022
   VTD relationship files or an area-weighted spatial overlay (geopandas) to
   assign 2018/2020 votes to 2026 precincts. Until then, treat 2018/2020
   precinct swings as approximate and rely on county level for those years.
3. **Projected final turnout per county:** reported-precinct turnout index ×
   full baseline county total.
4. **Change detection between polls:** diff the current snapshot with the
   previous one and print counties with new precincts in.
5. **Write a SQLite/DuckDB file** with history of every snapshot, so Power
   BI can chart how the count moved through the night.
6. **Absentee/early vote:** MN reports some absentee ballots in separate
   precinct-like units. Check whether they appear as special precinct codes
   and decide whether to exclude them from precinct matching (they'll show
   as `matched = False`).

## Known caveats

- **2022 source has precinct names only**, and 2020/2024 have codes only.
  2022 is joined by name via the live precinct table.
- **2018 & 2020 precinct codes predate the 2022 redistricting.** Same code
  ≠ same area. Use county-level results for those years.
- The **2018 Senate special** (`US_SENATE_SPECIAL`) is kept separate from the
  regular race. 2026 has one regular Senate race (open seat).
- MEDSL 2022 was evaluated and rejected: its Governor rows have no
  Democratic party coding.
- Write-ins are lumped into OTHER, which reduces `margin` slightly vs a
  two-party margin. Add `DFL / (DFL + R)` if you prefer two-party share.

## Python notes (for an R / SQL user)

- `pandas.DataFrame` ≈ an R data.frame/tibble. `df.groupby(...).sum()` ≈
  `dplyr::group_by() %>% summarise()` ≈ SQL `GROUP BY`.
- `df.merge(other, on=[...], how="left")` ≈ `left_join()` / SQL `LEFT JOIN`.
- `pivot_table` ≈ `tidyr::pivot_wider`.
- The core math is in `mnelect/compare.py::run`. It's a loop over
  (2026 race, baseline year, baseline race) pairs doing joins and group-bys.
