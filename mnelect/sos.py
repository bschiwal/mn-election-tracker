"""Fetch + parse the MN Secretary of State election-night media files.

SOS media files are semicolon-delimited text with NO header row. The documented
layout (verify against the SOS "media file" spec PDF -- see PLAN.md step 1):

  0 State            1 County ID (01-87)   2 Precinct ID (4-digit; blank in county files)
  3 Office ID        4 Office Name         5 District
  6 Candidate Order  7 Candidate Name      8 Suffix
  9 Incumbent Code  10 Party Abbreviation 11 Precincts Reporting
 12 Precincts Total 13 Votes for Candidate 14 Pct of votes
 15 Total votes for office in area

Every fetch is saved under data/snapshots/<UTC timestamp>/ so you can replay
the night later (`python -m mnelect once --from-dir data/snapshots/...`).
"""
from __future__ import annotations

import datetime as dt
import io
import re
from pathlib import Path

import pandas as pd
import requests

from .common import SNAPSHOT_DIR, map_office, map_party, pad_code

SOS_COLS = [
    "state", "county_id", "precinct_code", "office_id", "office_name", "district",
    "cand_order", "candidate", "suffix", "incumbent", "party",
    "precincts_reporting", "precincts_total", "votes", "pct", "office_total",
]

HEADERS = {"User-Agent": "mn-election-tracker (personal research script)"}


def fetch(url: str, timeout: int = 60) -> bytes:
    r = requests.get(url, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.content


def snapshot(cfg: dict) -> Path:
    """Download every configured live file into a new timestamped folder."""
    live = cfg["live"]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = SNAPSHOT_DIR / stamp
    folder.mkdir(parents=True, exist_ok=True)
    names = (list(live.get("precinct_files", [])) + list(live.get("county_files", []))
             + list(live.get("legislative_files", [])))
    for key in ("precinct_table_file", "stats_file"):
        if live.get(key):
            names.append(live[key])
    for name in names:
        url = live["base_url"].rstrip("/") + "/" + name
        try:
            (folder / name).write_bytes(fetch(url))
            print(f"  fetched {name}")
        except requests.RequestException as e:
            print(f"  !! could not fetch {url}: {e}")
    return folder


def read_raw(raw: bytes | str) -> pd.DataFrame:
    """Parse any SOS media file (all offices). Party is bucketed DFL/R/OTHER;
    the original code is kept in `party_raw`."""
    text = raw.decode("latin-1") if isinstance(raw, bytes) else raw
    df = pd.read_csv(io.StringIO(text), sep=";", header=None, dtype=str,
                     keep_default_na=False, quoting=3, on_bad_lines="warn")
    df = df.iloc[:, : len(SOS_COLS)]
    df.columns = SOS_COLS[: df.shape[1]]
    for c in ("county_id", "precincts_reporting", "precincts_total", "votes", "office_total"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
    df["precinct_code"] = df["precinct_code"].map(pad_code)
    df["district"] = df["district"].str.strip().str.upper()
    df["party_raw"] = df["party"]
    df["party"] = df["party"].map(map_party)
    return df


def parse_results(raw: bytes | str) -> pd.DataFrame:
    """Parse one SOS media results file into a tidy frame (statewide races only)."""
    df = read_raw(raw)
    df["office"] = [map_office(o, d) for o, d in zip(df["office_name"], df["district"])]
    return df[df["office"].notna()].copy()


def parse_precinct_table(raw: bytes | str) -> pd.DataFrame:
    """County ID;Precinct ID;Precinct Name;... -> county_id, precinct_code, precinct_name."""
    text = raw.decode("latin-1") if isinstance(raw, bytes) else raw
    df = pd.read_csv(io.StringIO(text), sep=";", header=None, dtype=str,
                     keep_default_na=False, quoting=3)
    df = df.iloc[:, :3]
    df.columns = ["county_id", "precinct_code", "precinct_name"]
    df["county_id"] = pd.to_numeric(df["county_id"], errors="coerce")
    df = df.dropna(subset=["county_id"])
    df["county_id"] = df["county_id"].astype(int)
    df["precinct_code"] = df["precinct_code"].map(pad_code)
    return df


def load_snapshot(folder: Path, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Return (precinct-level live results, precinct name table or None)."""
    live = cfg["live"]
    frames = []
    for name in live.get("precinct_files", []):
        p = Path(folder) / name
        if p.exists():
            frames.append(parse_results(p.read_bytes()))
    if not frames:
        return pd.DataFrame(columns=SOS_COLS + ["office"]), None
    res = pd.concat(frames, ignore_index=True)
    res = res[res["precinct_code"] != ""]          # keep precinct rows only
    res = res.drop_duplicates(["county_id", "precinct_code", "office", "cand_order"])
    table = None
    tname = live.get("precinct_table_file")
    if tname and (Path(folder) / tname).exists():
        table = parse_precinct_table((Path(folder) / tname).read_bytes())
    return res, table


def load_county_snapshot(folder: Path, cfg: dict) -> pd.DataFrame:
    """County-level live results (statewide races), or an empty frame."""
    frames = []
    for name in cfg["live"].get("county_files", []):
        p = Path(folder) / name
        if p.exists():
            frames.append(parse_results(p.read_bytes()))
    if not frames:
        return pd.DataFrame(columns=SOS_COLS + ["office"])
    res = pd.concat(frames, ignore_index=True)
    res = res[(res["county_id"] > 0) & (res["precinct_code"] == "")]
    return res.drop_duplicates(["county_id", "office", "cand_order"])


def discover(index_url: str) -> list[str]:
    """List .txt/.csv links on an SOS index page (to confirm file names on election night)."""
    html = fetch(index_url).decode("utf-8", "replace")
    links = sorted(set(re.findall(r'href="([^"]+\.(?:txt|csv|zip))"', html, re.I)))
    return links
