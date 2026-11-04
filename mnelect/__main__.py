"""Command line entry point.

    python -m mnelect baselines            # download + normalize 2018-2024 history (once)
    python -m mnelect discover URL         # list result files on an SOS index page
    python -m mnelect once                 # fetch live SOS files once and write outputs
    python -m mnelect once --from-dir DIR  # re-run on a saved snapshot (no network)
    python -m mnelect poll                 # fetch every [live].poll_seconds
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from . import baselines, compare, dashboard, legislature, sos
from .common import REFERENCE_DIR, load_config


def run_once(cfg: dict, from_dir: str | None = None) -> None:
    folder = Path(from_dir) if from_dir else sos.snapshot(cfg)
    live, table = sos.load_snapshot(folder, cfg)
    base = baselines.load()
    counties = pd.read_csv(REFERENCE_DIR / "counties.csv")
    if len(live):
        print(f"  precinct mode | rows: {len(live):,}  offices: {sorted(live['office'].unique())}")
        out = compare.run(live, table, base, cfg, counties)
    else:
        live = sos.load_county_snapshot(folder, cfg)
        if not len(live):
            raise FileNotFoundError(f"no precinct or county result files found in {folder}")
        print(f"  county mode (no precinct file yet) | rows: {len(live):,}  "
              f"offices: {sorted(live['office'].unique())}")
        out = compare.run_county(live, base, cfg, counties)
    out["statewide_results"] = compare.statewide_results(live)
    compare.write(out, folder.name)
    s = out["statewide_summary"]
    if len(s):
        view = s.assign(**{c: (s[c] * 100).round(1) for c in
                           ("live_margin", "baseline_margin", "swing_reported")})
        print(view.to_string(index=False))

    leg = legislature.run(folder, cfg)
    if leg:
        compare.write(leg, folder.name)
        for r in leg["chamber_control"].itertuples():
            print(f"  {r.chamber}: DFL {round(r.expected_dfl_seats)} / R {r.seats - round(r.expected_dfl_seats)} expected "
                  f"(DFL 80% range {r.dfl_seats_p10}-{r.dfl_seats_p90}, need {r.majority})  "
                  f"P(DFL maj) {r.p_dfl_majority:.0%}  P(R maj) {r.p_r_majority:.0%}  "
                  f"P(tie) {r.p_tie:.0%}  | {r.districts_fully_reported}/{r.seats} districts fully counted")

    try:
        dashboard.build({**out, **leg}, stamp=folder.name)
    except Exception as e:  # never let the dashboard stop the data pipeline
        print(f"  !! dashboard build failed: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(prog="mnelect")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baselines"); b.add_argument("--force", action="store_true")
    d = sub.add_parser("discover"); d.add_argument("url")
    o = sub.add_parser("once"); o.add_argument("--from-dir")
    sub.add_parser("poll")
    sub.add_parser("dashboard")
    a = ap.parse_args()
    cfg = load_config()

    if a.cmd == "baselines":
        baselines.build(force_download=a.force)
        legislature.build_baselines(force=a.force)
    elif a.cmd == "discover":
        for link in sos.discover(a.url):
            print(link)
    elif a.cmd == "dashboard":
        dashboard.build()
    elif a.cmd == "once":
        run_once(cfg, a.from_dir)
    elif a.cmd == "poll":
        while True:
            try:
                run_once(cfg)
            except Exception as e:  # keep polling through transient errors
                print(f"  !! poll failed: {e}")
            print(f"  sleeping {cfg['live']['poll_seconds']}s ...")
            time.sleep(cfg["live"]["poll_seconds"])


if __name__ == "__main__":
    main()
