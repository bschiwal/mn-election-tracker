"""Download and normalize historical precinct results (2018, 2020, 2022, 2024).

Sources (public, GitHub-hosted):
  2018/2020/2022  OpenElections  (openelections/openelections-data-mn)
  2024            MIT Election Data + Science Lab (MEDSL/2024-elections-official)

Each source has a different schema; this module flattens them all into
common.LONG_COLUMNS and writes data/baselines/precinct_results.csv.

Quirks handled here (found while profiling the files):
  * 2018 has county_code + precinct_code + precinct name  (pre-2022 redistricting)
  * 2020 has county name + numeric precinct code ('5' == '0005'), no names
  * 2022 has county name + precinct NAME only (no code) -> matched by name later
  * 2024 (MEDSL) has county FIPS + numeric precinct code, no names
  * 2018 had two U.S. Senate races (regular + 'Unexpired Term' special)
"""
from __future__ import annotations

import io
import zipfile

import pandas as pd
import requests

from .common import (BASELINE_DIR, LONG_COLUMNS, REFERENCE_DIR, load_config,
                     map_office, map_party, norm_name, pad_code)


def _download(url: str) -> bytes:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content


def _raw_path(year: int, url: str):
    return BASELINE_DIR / "raw" / f"{year}_{url.rsplit('/', 1)[-1]}"


def fetch_raw(cfg: dict, force: bool = False) -> dict[int, "Path"]:
    out = {}
    for year, url in cfg["baselines"]["sources"].items():
        year = int(year)
        p = _raw_path(year, url)
        p.parent.mkdir(parents=True, exist_ok=True)
        if force or not p.exists():
            print(f"  downloading {year}: {url}")
            p.write_bytes(_download(url))
        out[year] = p
    return out


def _read_any(path) -> pd.DataFrame:
    if str(path).endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = [n for n in z.namelist() if n.endswith(".csv")][0]
            return pd.read_csv(io.BytesIO(z.read(name)), dtype=str, low_memory=False)
    return pd.read_csv(path, dtype=str, low_memory=False)


def build_county_reference(df2018: pd.DataFrame) -> pd.DataFrame:
    """SOS county IDs are 1..87 alphabetical; 2018 OpenElections carries them."""
    ref = (df2018[["county_code", "county"]].drop_duplicates()
           .rename(columns={"county_code": "county_id", "county": "county_name"}))
    ref["county_id"] = ref["county_id"].astype(int)
    ref["county_key"] = ref["county_name"].map(norm_name)
    ref["county_fips"] = (27000 + 2 * ref["county_id"] - 1).astype(str)
    ref = ref.sort_values("county_id").reset_index(drop=True)
    assert len(ref) == 87, f"expected 87 MN counties, got {len(ref)}"
    return ref


def _finish(df: pd.DataFrame, year: int, ref: pd.DataFrame) -> pd.DataFrame:
    df = df[df["office"].notna()].copy()
    df["year"] = year
    df["votes"] = pd.to_numeric(df["votes"], errors="coerce").fillna(0).astype(int)
    if "county_id" not in df:
        key = df["county_raw"].map(norm_name)
        df["county_id"] = key.map(dict(zip(ref.county_key, ref.county_id)))
        missing = df["county_id"].isna()
        if missing.any():
            raise ValueError(f"{year}: unmatched counties {df.loc[missing,'county_raw'].unique()[:5]}")
        df["county_id"] = df["county_id"].astype(int)
    df["county_name"] = df["county_id"].map(dict(zip(ref.county_id, ref.county_name)))
    for c in ("precinct_code", "precinct_name"):
        if c not in df:
            df[c] = ""
    df["precinct_code"] = df["precinct_code"].map(pad_code)
    df["precinct_name"] = df["precinct_name"].fillna("")
    # collapse multiple 'OTHER' candidates into one row per party bucket
    return (df.groupby([c for c in LONG_COLUMNS if c != "votes"], as_index=False)["votes"].sum()
            [LONG_COLUMNS])


def normalize_openelections(raw: pd.DataFrame, year: int, ref: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({
        "county_raw": raw["county"],
        "precinct_name": raw["precinct"] if year != 2020 else "",
        "precinct_code": raw["precinct_code"] if "precinct_code" in raw else
                         (raw["precinct"] if year == 2020 else ""),
        "office": [map_office(o, d) for o, d in zip(raw["office"], raw["district"].fillna(""))],
        "party": raw["party"].map(map_party),
        "votes": raw["votes"],
    })
    if "county_code" in raw:
        df["county_id"] = raw["county_code"].astype(int)
    return _finish(df, year, ref)


def normalize_medsl(raw: pd.DataFrame, year: int, ref: pd.DataFrame) -> pd.DataFrame:
    raw = raw[(raw["stage"] == "GEN") & (raw["mode"].fillna("TOTAL") == "TOTAL")]
    fips_to_id = dict(zip(ref.county_fips, ref.county_id))
    df = pd.DataFrame({
        "county_id": raw["county_fips"].map(fips_to_id),
        "precinct_code": raw["precinct"],
        "office": [map_office(o, d, s == "TRUE")
                   for o, d, s in zip(raw["office"], raw["district"].fillna(""), raw["special"])],
        "party": raw["party_simplified"].map(map_party),
        "votes": raw["votes"],
    })
    if df["county_id"].isna().any():
        raise ValueError(f"{year}: unmatched county FIPS in MEDSL file")
    df["county_id"] = df["county_id"].astype(int)
    return _finish(df, year, ref)


def build(force_download: bool = False) -> pd.DataFrame:
    cfg = load_config()
    paths = fetch_raw(cfg, force=force_download)
    raw2018 = _read_any(paths[2018])
    ref = build_county_reference(raw2018)
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    ref.to_csv(REFERENCE_DIR / "counties.csv", index=False)

    frames = []
    for year in cfg["baselines"]["years"]:
        raw = raw2018 if year == 2018 else _read_any(paths[year])
        print(f"  normalizing {year} ({len(raw):,} raw rows)")
        if "party_simplified" in raw.columns:
            frames.append(normalize_medsl(raw, year, ref))
        else:
            frames.append(normalize_openelections(raw, year, ref))
    long = pd.concat(frames, ignore_index=True)
    out = BASELINE_DIR / "precinct_results.csv"
    long.to_csv(out, index=False)
    print(f"  wrote {out} ({len(long):,} rows)")
    print(statewide_check(long).to_string(index=False))
    return long


def statewide_check(long: pd.DataFrame) -> pd.DataFrame:
    """Statewide totals per race -- eyeball these against official canvass numbers."""
    t = long.pivot_table(index=["year", "office"], columns="party", values="votes",
                         aggfunc="sum", fill_value=0).reset_index()
    t["total"] = t[["DFL", "R", "OTHER"]].sum(axis=1)
    t["dfl_margin_pct"] = ((t["DFL"] - t["R"]) / t["total"] * 100).round(2)
    return t


def load() -> pd.DataFrame:
    return pd.read_csv(BASELINE_DIR / "precinct_results.csv",
                       dtype={"precinct_code": str, "precinct_name": str}, keep_default_na=False)
