# MN Election Tracker

Polls the Minnesota Secretary of State (SOS) election-night results files for
the **Nov 3, 2026 general election** and compares every statewide race,
precinct by precinct and county by county, against:

| Baseline | Why | Source |
|---|---|---|
| 2024 general | last presidential | MEDSL `2024-elections-official` (precinct) |
| 2022 general | last midterm | OpenElections `openelections-data-mn` (precinct) |
| 2018 general | midterm before that | OpenElections (precinct) |
| 2020 general | bonus presidential | OpenElections (precinct) |

Outputs are flat CSVs in `data/output/`, built for loading into **Power BI**
(Get Data → Folder or Text/CSV, then refresh while it polls).

## Quick start (on your own machine)

```bash
git clone <your-repo-url> && cd mn-election-tracker
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m mnelect baselines        # already built; re-run only with --force
python -m unittest discover tests  # sanity check
python -m mnelect discover https://electionresultsfiles.sos.mn.gov/20261103/   # confirm file names
python -m mnelect once             # one live pull
python -m mnelect poll             # keep pulling every 5 min
```

Before the first live pull, **read PLAN.md step 1**: the SOS file names in
`config.toml` couldn't be verified from the build machine.

## Output files (`data/output/`)

| File | Grain | Key columns |
|---|---|---|
| `statewide_summary.csv` | race × baseline | `live_margin`, `baseline_margin`, `swing_reported`, `turnout_idx_reported` |
| `county_swing.csv` | county × race × baseline | `swing_all`, `swing_reported`, `turnout_idx_all`, `turnout_idx_reported`, `precincts_reported` |
| `precinct_swing.csv` | precinct × race × baseline | `margin`, `margin_base`, `swing`, `turnout_idx`, `reported`, `matched` (only once SOS posts the precinct file) |
| `district_swing.csv` | House/Senate district × baseline | same columns as `county_swing`, plus `contested`, `contested_base`, candidates |
| `district_projection.csv` | House/Senate district | `status`, `p_dfl`, `projected_margin`, `w` (share of precincts in), `margin`, `prior_margin`, `recount_range` |
| `chamber_control.csv` | chamber | `expected_dfl_seats`, `dfl_seats_p10`/`p90`, `p_dfl_majority`, `p_r_majority`, `p_tie`, `shift_vs_prior` |

### State House and Senate

District baselines use the current lines (drawn 2022): 2024 House, 2022
House, 2022 Senate (`same_office`), and 2024 President / 2022 Governor summed
to each district (`top_of_ticket`). 2018 and 2020 are left out because they
used the old district lines. Swing is blank when either year was uncontested.

**Control estimate** (`mnelect/legislature.py`): for each district,
projected margin = counted share × live margin + uncounted share × prior.
The prior is the district's 2024 presidential margin plus `shift_vs_prior`,
which is how much better (+) or worse (−) the DFL is running tonight than
Harris did in the districts counted so far. The uncounted share is simulated
20,000 times with a shared statewide error (6 pts) and a per-district error
(8 pts). Tune these in `config.toml` `[legislature]`. Uncontested seats go to
the party that filed. Independent candidates are not modeled (flagged in
`other_leading`). Fully counted districts are "Counted", not certified.

**Sign convention:** margins are (DFL − R) / total votes. Positive swing is
toward the DFL, negative is toward the GOP. Values are fractions (0.05 = 5 pts).

**`*_reported` columns** use only precincts that are fully counted in 2026
*and* matched to the baseline. Use these on election night. The `*_all`
columns compare a partial count with a full one and will be misleading until
counting finishes.

**Comparisons:** each 2026 race is compared with the same office in each
baseline year where it was on the ballot (`comparison = same_office`) and
with that year's top race (`top_of_ticket`: 2024/2020 President, 2022/2018
Governor).

**Turnout** has no registered-voter or ballots-cast count in the baseline
sources, so `turnout_idx` = 2026 votes in the race ÷ baseline votes in the
comparison race. A value of 0.85 means 85% of the baseline vote.

## Layout

```
config.toml            URLs, file names, poll interval, baseline mapping
mnelect/common.py      name normalization, office/party mapping
mnelect/baselines.py   download + normalize 2018–2024 history
mnelect/sos.py         fetch / snapshot / parse SOS media files
mnelect/compare.py     swing + turnout math
mnelect/legislature.py State House/Senate swing + chamber-control estimate
data/baselines/legislative_districts.csv  2022/2024 results by district (committed)
mnelect/__main__.py    CLI
data/baselines/precinct_results.csv   normalized history (committed)
data/reference/counties.csv           SOS county id ↔ name ↔ FIPS
data/snapshots/        every live pull, timestamped (git-ignored)
tests/                 end-to-end test with a synthetic feed
```
