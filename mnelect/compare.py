"""Compare live 2026 returns with baseline years: turnout + partisan swing.

Definitions (all from the DFL point of view, so + = toward DFL, - = toward R):
  margin       = (DFL - R) / total votes in that race
  swing        = live margin - baseline margin   (percentage points)
  turnout_idx  = live total votes / baseline total votes for the comparison race

Why "reported-only" columns matter on election night: a county that is 40%
counted is usually skewed (e.g. city precincts first). So for each county we
ALSO compute swing/turnout using only precincts that are fully reported in
2026 AND matched to the baseline -- an apples-to-apples read on partial counts.

Precinct matching:
  2024, 2020, 2018 -> by (county_id, precinct_code)
  2022             -> by (county_id, normalized precinct name), using the live
                      precinct table for names (2022 source has no codes)
  2018/2020 matches cross the 2022 redistricting, so treat them as approximate.
"""
from __future__ import annotations

import pandas as pd

from .common import OUTPUT_DIR, norm_name

KEY = ["county_id", "precinct_code"]


def _wide(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    w = df.pivot_table(index=keys + ["office"], columns="party", values="votes",
                       aggfunc="sum", fill_value=0).reset_index()
    for p in ("DFL", "R", "OTHER"):
        if p not in w:
            w[p] = 0
    w["total"] = w["DFL"] + w["R"] + w["OTHER"]
    w["margin"] = (w["DFL"] - w["R"]) / w["total"].where(w["total"] > 0)
    return w


def live_precinct_frame(live: pd.DataFrame, table: pd.DataFrame | None) -> pd.DataFrame:
    """One row per precinct x office x party, plus a 'reported' flag."""
    rep = (live.groupby(KEY)[["precincts_reporting", "precincts_total"]].max().reset_index())
    rep["reported"] = (rep["precincts_total"] > 0) & (rep["precincts_reporting"] >= rep["precincts_total"])
    agg = live.groupby(KEY + ["office", "party"], as_index=False)["votes"].sum()
    agg = agg.merge(rep[KEY + ["reported"]], on=KEY, how="left")
    if table is not None:
        agg = agg.merge(table, on=KEY, how="left")
    else:
        agg["precinct_name"] = ""
    agg["precinct_name"] = agg["precinct_name"].fillna("")
    return agg


def attach_codes_to_2022(base: pd.DataFrame, table: pd.DataFrame | None) -> pd.DataFrame:
    """Give 2022 rows precinct codes by name-matching to the live precinct table."""
    b22 = base["year"] == 2022
    if table is None or not b22.any():
        return base
    t = table.assign(nkey=table["precinct_name"].map(norm_name))
    t = t.drop_duplicates(["county_id", "nkey"])
    sub = base[b22].assign(nkey=base.loc[b22, "precinct_name"].map(norm_name))
    sub = sub.drop(columns="precinct_code").merge(
        t[["county_id", "nkey", "precinct_code"]], on=["county_id", "nkey"], how="left")
    sub["precinct_code"] = sub["precinct_code"].fillna("")
    matched = (sub.drop_duplicates(["county_id", "nkey"])["precinct_code"] != "").mean()
    print(f"  2022 precinct name match rate: {matched:.1%}")
    return pd.concat([base[~b22], sub.drop(columns="nkey")], ignore_index=True)


def comparison_pairs(live_offices, base: pd.DataFrame, top_of_ticket: dict) -> list[tuple]:
    """(live_office, year, baseline_office, comparison_type) combos to compute."""
    have = set(zip(base["year"], base["office"]))
    pairs = []
    for lo in sorted(live_offices):
        for y in sorted(base["year"].unique(), reverse=True):
            if (y, lo) in have:
                pairs.append((lo, int(y), lo, "same_office"))
            top = top_of_ticket.get(str(y))
            if top and top != lo and (y, top) in have:
                pairs.append((lo, int(y), top, "top_of_ticket"))
    return pairs


def run(live: pd.DataFrame, table: pd.DataFrame | None, base: pd.DataFrame,
        cfg: dict, counties: pd.DataFrame) -> dict[str, pd.DataFrame]:
    base = attach_codes_to_2022(base, table)
    lp = live_precinct_frame(live, table)
    lw = _wide(lp, KEY)
    lw = lw.merge(lp[KEY + ["reported", "precinct_name"]].drop_duplicates(KEY), on=KEY, how="left")
    bw = _wide(base[base["precinct_code"] != ""], ["year"] + KEY)

    pairs = comparison_pairs(lw["office"].unique(), base, cfg["baselines"]["top_of_ticket"])
    prec_rows, cty_rows, state_rows = [], [], []
    for lo, y, bo, kind in pairs:
        L = lw[lw["office"] == lo]
        B = bw[(bw["year"] == y) & (bw["office"] == bo)].drop(columns=["year", "office"])
        m = L.merge(B, on=KEY, how="left", suffixes=("", "_base"))
        m["matched"] = m["total_base"].notna()
        m = m.assign(live_office=lo, baseline_year=y, baseline_office=bo, comparison=kind)
        m["swing"] = m["margin"] - m["margin_base"]
        m["turnout_idx"] = m["total"] / m["total_base"]
        prec_rows.append(m)

        # county level: (a) everything counted so far vs full baseline county,
        #               (b) only reported+matched precincts on both sides
        Bc = (base[(base["year"] == y) & (base["office"] == bo)]
              .pipe(_wide, ["county_id"]).drop(columns="office"))
        Lc = L.groupby("county_id")[["DFL", "R", "OTHER", "total"]].sum()
        Lc["precincts"] = L.groupby("county_id").size()
        Lc["precincts_reported"] = L.groupby("county_id")["reported"].sum()
        Lc = Lc.reset_index()
        Lc["margin"] = (Lc["DFL"] - Lc["R"]) / Lc["total"].where(Lc["total"] > 0)
        c = Lc.merge(Bc, on="county_id", how="left", suffixes=("", "_base"))
        c["swing_all"] = c["margin"] - c["margin_base"]
        c["turnout_idx_all"] = c["total"] / c["total_base"]
        rm = m[m["reported"] & m["matched"]]
        r = rm.groupby("county_id")[["DFL", "R", "total", "DFL_base", "R_base", "total_base"]].sum()
        r["swing_reported"] = ((r["DFL"] - r["R"]) / r["total"]) - ((r["DFL_base"] - r["R_base"]) / r["total_base"])
        r["turnout_idx_reported"] = r["total"] / r["total_base"]
        r["matched_reported_precincts"] = rm.groupby("county_id").size()
        c = c.merge(r[["swing_reported", "turnout_idx_reported", "matched_reported_precincts"]]
                    .reset_index(), on="county_id", how="left")
        c = c.assign(live_office=lo, baseline_year=y, baseline_office=bo, comparison=kind)
        cty_rows.append(c)

        s = {"live_office": lo, "baseline_year": y, "baseline_office": bo, "comparison": kind,
             "precincts_reported": int(L["reported"].sum()), "precincts": len(L),
             "live_margin": (L["DFL"].sum() - L["R"].sum()) / max(L["total"].sum(), 1),
             "baseline_margin": (B["DFL"].sum() - B["R"].sum()) / max(B["total"].sum(), 1),
             "swing_reported": (r["DFL"].sum() - r["R"].sum()) / max(r["total"].sum(), 1)
                               - (r["DFL_base"].sum() - r["R_base"].sum()) / max(r["total_base"].sum(), 1),
             "turnout_idx_reported": r["total"].sum() / max(r["total_base"].sum(), 1)}
        state_rows.append(s)

    names = counties.set_index("county_id")["county_name"]
    precinct = pd.concat(prec_rows, ignore_index=True) if prec_rows else pd.DataFrame()
    county = pd.concat(cty_rows, ignore_index=True) if cty_rows else pd.DataFrame()
    for df in (precinct, county):
        if len(df):
            df.insert(0, "county_name", df["county_id"].map(names))
    return {"precinct_swing": precinct, "county_swing": county,
            "statewide_summary": pd.DataFrame(state_rows)}


def write(outputs: dict[str, pd.DataFrame], stamp: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in outputs.items():
        df = df.assign(snapshot_utc=stamp)
        df.to_csv(OUTPUT_DIR / f"{name}.csv", index=False)
        print(f"  wrote data/output/{name}.csv ({len(df):,} rows)")
