"""Shared helpers: config loading, name normalization, office/party mapping.

All data ends up in one tidy "long" shape so every year is comparable:

    year, county_id, county_name, precinct_code, precinct_name, office, party, votes

Think of it like a Power BI fact table: one row per candidate-party per precinct.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BASELINE_DIR = DATA / "baselines"
OUTPUT_DIR = DATA / "output"
SNAPSHOT_DIR = DATA / "snapshots"
REFERENCE_DIR = DATA / "reference"

LONG_COLUMNS = [
    "year", "county_id", "county_name", "precinct_code", "precinct_name",
    "office", "party", "votes",
]

STATEWIDE_OFFICES = [
    "PRESIDENT", "US_SENATE", "US_SENATE_SPECIAL", "GOVERNOR",
    "ATTORNEY_GENERAL", "SECRETARY_OF_STATE", "STATE_AUDITOR",
]


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def norm_name(s) -> str:
    """Normalize county/precinct names for joining: 'St. Louis' == 'SAINT LOUIS'."""
    if s is None or (isinstance(s, float) and s != s):
        return ""
    s = str(s).upper().strip()
    s = s.replace(".", "").replace(",", "")
    s = re.sub(r"\bSAINT\b", "ST", s)
    s = re.sub(r"\bTOWNSHIP\b", "TWP", s)
    s = re.sub(r"\bPCT\b|\bPRECINCT\b", "P", s)
    s = re.sub(r"\bWARD\b", "W", s)
    s = re.sub(r"\s*-\s*", "-", s)
    s = re.sub(r"\s+", " ", s)
    # 'P-01' vs 'P-1'
    s = re.sub(r"\b([PW])-0+(\d)", r"\1-\2", s)
    return s


def pad_code(c) -> str:
    """Precinct codes are 4-digit strings in SOS files ('0005'); some sources drop zeros."""
    if c is None or (isinstance(c, float) and c != c) or str(c).strip() == "":
        return ""
    c = str(c).strip()
    return c.zfill(4) if c.isdigit() else c


def map_office(office: str, district: str = "", special: bool = False) -> str | None:
    """Map any source's office label to a canonical statewide office, else None."""
    o = (office or "").upper()
    d = (district or "").upper()
    if "PRESIDENT" in o:
        return "PRESIDENT"
    if ("SENATE" in o or "SENATOR" in o) and ("U.S." in o or "US " in o or o.startswith("US") or "UNITED STATES" in o):
        if special or "UNEXPIRED" in o or "UNEXPIRED" in d or "SPECIAL" in o:
            return "US_SENATE_SPECIAL"
        return "US_SENATE"
    if "GOVERNOR" in o:  # SOS label is "Governor & Lt Governor"
        return "GOVERNOR"
    if "ATTORNEY GENERAL" in o:
        return "ATTORNEY_GENERAL"
    if "SECRETARY OF STATE" in o:
        return "SECRETARY_OF_STATE"
    if "STATE AUDITOR" in o:
        return "STATE_AUDITOR"
    return None


def map_party(p: str) -> str:
    p = (p or "").upper().strip()
    if p in ("DFL", "DEM", "DEMOCRAT", "DEMOCRATIC", "DEMOCRATIC-FARMER-LABOR"):
        return "DFL"
    if p in ("R", "REP", "REPUBLICAN", "GOP"):
        return "R"
    return "OTHER"
