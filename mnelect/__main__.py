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

from . import baselines, compare, sos
from .common import REFERENCE_DIR, load_config


def run_once(cfg: dict, from_dir: str | None = None) -> None:
    folder = Path(from_dir) if from_dir else sos.snapshot(cfg)
    live, table = sos.load_snapshot(folder, cfg)
    print(f"  live rows: {len(live):,}  offices: {sorted(live['office'].unique())}")
    base = baselines.load()
    counties = pd.read_csv(REFERENCE_DIR / "counties.csv")
    out = compare.run(live, table, base, cfg, counties)
    compare.write(out, folder.name)
    s = out["statewide_summary"]
    if len(s):
        view = s.assign(**{c: (s[c] * 100).round(1) for c in
                           ("live_margin", "baseline_margin", "swing_reported")})
        print(view.to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(prog="mnelect")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baselines"); b.add_argument("--force", action="store_true")
    d = sub.add_parser("discover"); d.add_argument("url")
    o = sub.add_parser("once"); o.add_argument("--from-dir")
    sub.add_parser("poll")
    a = ap.parse_args()
    cfg = load_config()

    if a.cmd == "baselines":
        baselines.build(force_download=a.force)
    elif a.cmd == "discover":
        for link in sos.discover(a.url):
            print(link)
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
