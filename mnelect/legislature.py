"""State House + State Senate: district results, swing vs past years, and a
chamber-control estimate.

Baselines (all on the current district lines, drawn in 2022):
  same_office    2024 House, 2022 House, 2022 Senate  (official SOS district totals)
  top_of_ticket  2024 President, 2022 Governor        (SOS precinct results summed
                                                       to each House/Senate district)
2018/2020 are left out: those elections used the old (2012) district lines.

Control estimate, per district:
  w           = share of precincts reported
  prior       = 2024 presidential margin in the district + `shift`, where
                shift = how much better (+) or worse (-) the DFL is running
                tonight vs that margin, vote-weighted across contested districts
  projected   = w * live margin + (1 - w) * prior
  uncertainty = only on the uncounted share: (1 - w) * (statewide error + district error).
                The statewide error is shared by every district in a draw, so
                misses are correlated (if the DFL is underestimated in one
                place it is probably underestimated everywhere).
Seats are simulated `draws` times; control odds = share of draws with a majority.
Uncontested seats (no DFL or no R candidate) go to the party that filed.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import sos
from .common import BASELINE_DIR, map_office

SOS_BASE = "https://electionresultsfiles.sos.mn.gov"
BASELINE_FILE = BASELINE_DIR / "legislative_districts.csv"
RAW_DIR = BASELINE_DIR / "raw"

CHAMBERS = {"STATE_HOUSE": 134, "STATE_SENATE": 67}

# (year, baseline_office, comparison) per chamber
PAIRS = {
    "STATE_HOUSE": [(2024, "STATE_HOUSE", "same_office"), (2022, "STATE_HOUSE", "same_office"),
                    (2024, "PRESIDENT", "top_of_ticket"), (2022, "GOVERNOR", "top_of_ticket")],
    "STATE_SENATE": [(2022, "STATE_SENATE", "same_office"),
                     (2024, "PRESIDENT", "top_of_ticket"), (2022, "GOVERNOR", "top_of_ticket")],
}

DEFAULTS = {"prior_year": 2024, "prior_office": "PRESIDENT", "sigma_statewide": 0.06,
            "sigma_district": 0.08, "draws": 20000, "seed": 0, "recount_margin": 0.005}


def leg_office(name: str) -> str | None:
    o = (name or "").upper()
    if o.startswith("STATE REPRESENTATIVE"):
        return "STATE_HOUSE"
    if o.startswith("STATE SENATOR"):
        return "STATE_SENATE"
    return None


def senate_of(house_district: str) -> str:
    """'10A' -> '10'."""
    return re.sub(r"[A-Z]$", "", str(house_district))


def district_sort_key(s: pd.Series) -> pd.Series:
    """Sort '2A' before '10A'."""
    return s.map(lambda d: (int(re.sub(r"\D", "", d) or 0), d))


# ---------------------------------------------------------------- parsing

def parse(raw: bytes | str) -> pd.DataFrame:
    """District-level State House / Senate rows from an SOS file."""
    df = sos.read_raw(raw)
    df["office"] = df["office_name"].map(leg_office)
    return df[df["office"].notna() & (df["precinct_code"] == "")].copy()


def district_wide(df: pd.DataFrame) -> pd.DataFrame:
    """One row per office x district: votes by party, candidates, reporting."""
    k = ["office", "district"]
    w = df.pivot_table(index=k, columns="party", values="votes", aggfunc="sum",
                       fill_value=0).reset_index()
    for p in ("DFL", "R", "OTHER"):
        if p not in w:
            w[p] = 0
    w["total"] = w["DFL"] + w["R"] + w["OTHER"]
    w["margin"] = (w["DFL"] - w["R"]) / w["total"].where(w["total"] > 0)
    rep = df.groupby(k)[["precincts_reporting", "precincts_total"]].max().reset_index()
    w = w.merge(rep, on=k, how="left")
    for p, col in (("DFL", "dfl_candidate"), ("R", "r_candidate")):
        c = (df[df["party"] == p].sort_values("votes", ascending=False)
             .drop_duplicates(k)[k + ["candidate"]].rename(columns={"candidate": col}))
        w = w.merge(c, on=k, how="left")
    oth = df[(df["party"] == "OTHER") & (df["party_raw"] != "WI")]
    top_oth = oth.groupby(k)["votes"].max().rename("top_other_votes").reset_index()
    w = w.merge(top_oth, on=k, how="left")
    w["top_other_votes"] = w["top_other_votes"].fillna(0).astype(int)
    w["has_dfl"] = w["dfl_candidate"].notna()
    w["has_r"] = w["r_candidate"].notna()
    w["contested"] = w["has_dfl"] & w["has_r"]
    return w


# ---------------------------------------------------------------- baselines

def _get(date: str, name: str, force: bool = False) -> bytes:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    p = RAW_DIR / f"sos_{date}_{name}"
    if force or not p.exists():
        print(f"  downloading {date}/{name}")
        p.write_bytes(sos.fetch(f"{SOS_BASE}/{date}/{name}", timeout=300))
    return p.read_bytes()


def _top_of_ticket(date: str, year: int, office: str, force: bool) -> pd.DataFrame:
    """Sum a statewide race's precinct results to House and Senate districts.
    Each precinct's House district comes from its State Representative rows."""
    df = sos.read_raw(_get(date, "allracesbyprecinct.txt", force))
    df = df[df["precinct_code"] != ""]
    key = ["county_id", "precinct_code"]
    hd = (df[df["office_name"].map(leg_office) == "STATE_HOUSE"][key + ["district"]]
          .drop_duplicates(key))
    top = df[[map_office(o) == office for o in df["office_name"]]].drop(columns="district")
    top = top.merge(hd, on=key, how="inner")
    out = []
    for chamber, dist in (("STATE_HOUSE", top["district"]), ("STATE_SENATE", top["district"].map(senate_of))):
        g = top.assign(district=dist).pivot_table(index="district", columns="party", values="votes",
                                                  aggfunc="sum", fill_value=0).reset_index()
        out.append(g.assign(chamber=chamber))
    g = pd.concat(out, ignore_index=True).assign(year=year, baseline_office=office,
                                                 has_dfl=True, has_r=True)
    return g


def build_baselines(force: bool = False) -> pd.DataFrame:
    rows = []
    for date, year, files in (("20241105", 2024, ["LegislativeByDistrict.txt"]),
                              ("20221108", 2022, ["LegislativeByDistrict.txt", "stsenate.txt"])):
        for f in files:
            w = district_wide(parse(_get(date, f, force)))
            w = w.rename(columns={"office": "chamber"}).assign(year=year, baseline_office=w["office"])
            rows.append(w[["year", "chamber", "district", "baseline_office", "DFL", "R", "OTHER",
                           "has_dfl", "has_r"]])
    rows.append(_top_of_ticket("20241105", 2024, "PRESIDENT", force))
    rows.append(_top_of_ticket("20221108", 2022, "GOVERNOR", force))
    b = pd.concat(rows, ignore_index=True)
    b = b[["year", "chamber", "district", "baseline_office", "DFL", "R", "OTHER", "has_dfl", "has_r"]]
    b = b.sort_values(["chamber", "year", "baseline_office"]).reset_index(drop=True)
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    b.to_csv(BASELINE_FILE, index=False)
    print(f"  wrote {BASELINE_FILE.relative_to(BASELINE_DIR.parent.parent)} ({len(b):,} rows)")
    print(b.groupby(["chamber", "year", "baseline_office"]).agg(
        districts=("district", "nunique"), DFL=("DFL", "sum"), R=("R", "sum")).to_string())
    return b


def load_baselines() -> pd.DataFrame:
    if not BASELINE_FILE.exists():
        return build_baselines()
    b = pd.read_csv(BASELINE_FILE, dtype={"district": str})
    b["total"] = b["DFL"] + b["R"] + b["OTHER"]
    b["margin"] = (b["DFL"] - b["R"]) / b["total"].where(b["total"] > 0)
    b["contested"] = b["has_dfl"] & b["has_r"]
    return b


# ---------------------------------------------------------------- comparison

def district_swing(lw: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    """District x baseline comparison (the legislative version of county_swing)."""
    out = []
    keep = ["DFL", "R", "OTHER", "total", "margin", "contested"]
    for chamber, pairs in PAIRS.items():
        L = lw[lw["office"] == chamber]
        for y, bo, kind in pairs:
            B = base[(base["chamber"] == chamber) & (base["year"] == y) & (base["baseline_office"] == bo)]
            m = L.merge(B[["district"] + keep], on="district", how="left", suffixes=("", "_base"))
            ok = m["contested"] & m["contested_base"].fillna(False).astype(bool)
            m["swing_all"] = (m["margin"] - m["margin_base"]).where(ok)
            m["turnout_idx_all"] = m["total"] / m["total_base"]
            m["swing_reported"] = m["swing_all"].where(m["reported"])
            m["turnout_idx_reported"] = m["turnout_idx_all"].where(m["reported"])
            out.append(m.assign(chamber=chamber, baseline_year=y, baseline_office=bo, comparison=kind))
    d = pd.concat(out, ignore_index=True).drop(columns="office")
    lead = ["chamber", "district", "baseline_year", "baseline_office", "comparison"]
    return d[lead + [c for c in d.columns if c not in lead]]


def project(lw: pd.DataFrame, base: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-district projection + chamber-control summary."""
    p = {**DEFAULTS, **cfg.get("legislature", {})}
    rng = np.random.default_rng(p["seed"])
    dists, chambers = [], []
    for chamber, seats in CHAMBERS.items():
        d = lw[lw["office"] == chamber].copy()
        pr = base[(base["chamber"] == chamber) & (base["year"] == p["prior_year"])
                  & (base["baseline_office"] == p["prior_office"])][["district", "margin"]]
        d = d.merge(pr.rename(columns={"margin": "prior_base_margin"}), on="district", how="left")
        d["w"] = (d["precincts_reporting"] / d["precincts_total"].where(d["precincts_total"] > 0)).fillna(0).clip(0, 1)

        c = d[d["contested"] & (d["total"] > 0) & d["prior_base_margin"].notna()]
        shift = float(np.average(c["margin"] - c["prior_base_margin"], weights=c["total"])) if len(c) else 0.0
        d["shift"] = shift
        d["prior_margin"] = d["prior_base_margin"].fillna(0) + shift
        live = d["margin"].fillna(d["prior_margin"])
        d["projected_margin"] = d["w"] * live + (1 - d["w"]) * d["prior_margin"]
        sig = np.hypot(p["sigma_statewide"], p["sigma_district"])
        d["projected_sigma"] = (1 - d["w"]) * sig

        n = len(d)
        e = (rng.normal(0, p["sigma_statewide"], (p["draws"], 1))
             + rng.normal(0, p["sigma_district"], (p["draws"], n)))
        final = d["projected_margin"].to_numpy() + (1 - d["w"].to_numpy()) * e
        dfl = final > 0
        dfl[:, (~d["has_r"]).to_numpy()] = True          # no R on the ballot
        dfl[:, (~d["has_dfl"]).to_numpy()] = False       # no DFL on the ballot
        d["p_dfl"] = dfl.mean(axis=0)
        d["p_r"] = 1 - d["p_dfl"]

        done = d["w"] >= 1
        unc = ~d["contested"]
        lead_dfl = d["projected_margin"] > 0
        d["status"] = np.select(
            [unc & d["has_dfl"], unc, done & lead_dfl, done,
             d["p_dfl"] >= .95, d["p_dfl"] >= .75, d["p_dfl"] > .25, d["p_dfl"] > .05],
            ["Uncontested DFL", "Uncontested R", "Counted DFL", "Counted R",
             "Likely DFL", "Lean DFL", "Tossup", "Lean R"], default="Likely R")
        d["recount_range"] = d["contested"] & done & (d["margin"].abs() <= p["recount_margin"])
        # an independent / third-party candidate ahead of both majors is not modeled
        d["other_leading"] = d["top_other_votes"] > d[["DFL", "R"]].max(axis=1)
        dists.append(d.assign(chamber=chamber))

        maj = seats // 2 + 1
        ds = dfl.sum(axis=1)
        rs = n - ds
        cc = d[d["contested"]]
        chambers.append({
            "chamber": chamber, "seats": seats, "majority": maj,
            "districts_fully_reported": int(done.sum()),
            "precincts_reported": int(d["precincts_reporting"].sum()),
            "precincts": int(d["precincts_total"].sum()),
            "uncontested_dfl": int((unc & d["has_dfl"]).sum()),
            "uncontested_r": int((unc & ~d["has_dfl"]).sum()),
            "counted_dfl": int((done & d["contested"] & lead_dfl).sum()),
            "counted_r": int((done & d["contested"] & ~lead_dfl).sum()),
            "leading_dfl": int((d["projected_margin"] > 0).where(d["contested"], d["has_dfl"]).sum()),
            "leading_r": int(n - (d["projected_margin"] > 0).where(d["contested"], d["has_dfl"]).sum()),
            "tossups": int((d["status"] == "Tossup").sum()),
            "expected_dfl_seats": round(float(ds.mean()), 1),
            "expected_r_seats": round(float(rs.mean()), 1),
            "dfl_seats_p10": int(np.percentile(ds, 10)), "dfl_seats_p50": int(np.percentile(ds, 50)),
            "dfl_seats_p90": int(np.percentile(ds, 90)),
            "p_dfl_majority": float((ds >= maj).mean()),
            "p_r_majority": float((rs >= maj).mean()),
            "p_tie": float((ds * 2 == seats).mean()),
            "live_margin_contested": (cc["DFL"].sum() - cc["R"].sum()) / max(cc["total"].sum(), 1),
            "shift_vs_prior": shift,
            "prior": f"{p['prior_year']} {p['prior_office']}",
        })
    dist = pd.concat(dists, ignore_index=True).drop(columns="office")
    dist = dist.sort_values(["chamber", "district"], key=lambda s: district_sort_key(s)
                            if s.name == "district" else s).reset_index(drop=True)
    lead = ["chamber", "district", "status", "p_dfl", "p_r", "projected_margin", "w",
            "margin", "prior_margin", "dfl_candidate", "r_candidate"]
    dist = dist[lead + [c for c in dist.columns if c not in lead]]
    return dist, pd.DataFrame(chambers)


def run(folder: Path, cfg: dict) -> dict[str, pd.DataFrame]:
    """Legislative outputs for one snapshot folder ({} if no legislative files)."""
    frames = [parse((Path(folder) / f).read_bytes())
              for f in cfg["live"].get("legislative_files", []) if (Path(folder) / f).exists()]
    if not frames:
        return {}
    live = pd.concat(frames, ignore_index=True).drop_duplicates(["office", "district", "cand_order"])
    lw = district_wide(live)
    lw["reported"] = (lw["precincts_total"] > 0) & (lw["precincts_reporting"] >= lw["precincts_total"])
    base = load_baselines()
    dist, control = project(lw, base, cfg)
    return {"district_swing": district_swing(lw, base), "district_projection": dist,
            "chamber_control": control}
