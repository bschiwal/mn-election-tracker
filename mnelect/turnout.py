"""Statewide turnout: ballots cast and turnout % for each election.

Source: the SOS precinct statistics media file (pctstats.txt), posted for every
election. Semicolon-delimited, no header. Fields used (checked against official
statewide totals: 2018 2,611,365 · 2020 3,292,997 · 2022 2,525,873 ballots):

  0 State   1 County ID   2 Precinct ID   3 Precinct name   4 Reported (0/1)
  5 Registered voters at 7am   6 Same-day (election day) registrations
  11 Total voters (ballots cast)

Turnout % = ballots cast / (registered at 7am + same-day registrations).
This is turnout of REGISTERED voters. SOS's headline figure uses ELIGIBLE
voters (a state demographer estimate that is not in these files), so it runs
lower than the numbers here.

Election night: SOS updates pctstats.txt more slowly than the race files. While
it is incomplete, ballots cast = the larger of its total and the biggest
statewide race's vote total (a slight undercount: some voters skip that race),
and same-day registrations in precincts not yet in the file are estimated at
the rate seen in precincts that are.
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

from .common import BASELINE_DIR

TURNOUT_CSV = BASELINE_DIR / "turnout.csv"
COLS = ["year", "precincts", "precincts_reported", "reg_7am", "reg_eday", "registered",
        "ballots", "ballots_source", "turnout"]


def parse_stats(raw: bytes | str) -> pd.DataFrame:
    text = raw.decode("latin-1") if isinstance(raw, bytes) else raw
    df = pd.read_csv(io.StringIO(text), sep=";", header=None, dtype=str,
                     keep_default_na=False, quoting=3)
    df = df.iloc[:, [1, 2, 4, 5, 6, 11]]
    df.columns = ["county_id", "precinct_code", "reported", "reg_7am", "reg_eday", "ballots"]
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    return df[df["county_id"] > 0]


def summarize(stats: pd.DataFrame, year: int, top_race_votes: int = 0) -> dict:
    """One statewide row. `top_race_votes` is only used while the stats file is incomplete."""
    rep = stats[stats["reported"] == 1]
    n, n_rep = len(stats), len(rep)
    reg_7am = int(stats["reg_7am"].sum())
    reg_eday = int(rep["reg_eday"].sum())
    ballots, source = int(rep["ballots"].sum()), "SOS precinct stats"
    if n_rep < n:
        rate = rep["reg_eday"].sum() / max(rep["reg_7am"].sum(), 1)
        reg_eday += round(rate * stats.loc[stats["reported"] != 1, "reg_7am"].sum())
        if top_race_votes > ballots:
            ballots, source = int(top_race_votes), "largest statewide race (SOS stats file behind)"
    registered = reg_7am + reg_eday
    return {"year": year, "precincts": n, "precincts_reported": n_rep, "reg_7am": reg_7am,
            "reg_eday": reg_eday, "registered": registered, "ballots": ballots,
            "ballots_source": source, "turnout": ballots / registered if registered else None}


def build_baselines(cfg: dict, force: bool = False) -> pd.DataFrame:
    """Download pctstats.txt for each baseline year -> data/baselines/turnout.csv."""
    if TURNOUT_CSV.exists() and not force:
        return pd.read_csv(TURNOUT_CSV)
    rows = []
    for year, url in sorted(cfg["baselines"]["stats_sources"].items()):
        p = BASELINE_DIR / "raw" / f"sos_{url.rstrip('/').split('/')[-2]}_pctstats.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        if force or not p.exists():
            print(f"  downloading {year} precinct stats: {url}")
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            p.write_bytes(r.content)
        rows.append(summarize(parse_stats(p.read_bytes()), int(year)))
    t = pd.DataFrame(rows, columns=COLS)
    t.to_csv(TURNOUT_CSV, index=False)
    print(f"  wrote {TURNOUT_CSV.name}")
    print(t[["year", "ballots", "registered", "turnout"]].to_string(index=False))
    return t


def run(folder: Path, cfg: dict, results: pd.DataFrame, year: int = 2026) -> pd.DataFrame:
    """All years: baselines + the live election (if its stats file was fetched)."""
    try:
        t = build_baselines(cfg)
    except (requests.RequestException, OSError) as e:
        print(f"  !! turnout baselines unavailable: {e}")
        t = pd.DataFrame(columns=COLS)
    name = cfg["live"].get("stats_file")
    p = Path(folder) / name if name else None
    if p and p.exists():
        top = int(results.groupby("office")["votes"].sum().max()) if len(results) else 0
        t = pd.concat([pd.DataFrame([summarize(parse_stats(p.read_bytes()), year, top)]), t],
                      ignore_index=True)
    return t.sort_values("year", ascending=False).reset_index(drop=True)[COLS]
