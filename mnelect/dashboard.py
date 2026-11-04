"""Build a self-contained HTML dashboard from the output tables.

    python -m mnelect dashboard        # rebuild from data/output/*.csv

`once` and `poll` also rebuild it after every pull, so an open browser tab
(which reloads itself every 60 s) always shows the latest numbers.

Everything (data, county shapes, JS) is inlined into data/output/dashboard.html,
so the file opens straight from disk with no server and no internet.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from .common import OUTPUT_DIR, REFERENCE_DIR

COUNTY_SHAPES = REFERENCE_DIR / "mn_county_paths.json"
TEMPLATE = Path(__file__).with_name("dashboard_template.html")
OUT = OUTPUT_DIR / "dashboard.html"


# ---------------------------------------------------------------- county shapes

def build_county_shapes(geojson_path: str | Path, width: float = 520.0) -> dict:
    """US county GeoJSON (FIPS ids) -> {fips: svg path} for Minnesota, projected
    with a simple cos(latitude) equirectangular projection. Run once."""
    gj = json.loads(Path(geojson_path).read_text(encoding="utf-8"))
    feats = [f for f in gj["features"] if str(f["id"]).startswith("27")]
    k = math.cos(math.radians(46.3))

    def rings(geom):
        if geom["type"] == "Polygon":
            return geom["coordinates"]
        return [r for poly in geom["coordinates"] for r in poly]

    pts = [(lon * k, lat) for f in feats for r in rings(f["geometry"]) for lon, lat in r]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    s = width / (x1 - x0)
    height = (y1 - y0) * s

    paths = {}
    for f in feats:
        d = []
        for r in rings(f["geometry"]):
            seg, last = [], None
            for lon, lat in r:
                p = (round((lon * k - x0) * s, 1), round((y1 - lat) * s, 1))
                if p != last:
                    seg.append(f"{p[0]},{p[1]}")
                    last = p
            if len(seg) >= 3:
                d.append("M" + "L".join(seg) + "Z")
        paths[str(f["id"])] = "".join(d)
    out = {"width": width, "height": round(height, 1), "paths": paths}
    COUNTY_SHAPES.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"  wrote {COUNTY_SHAPES.name}: {len(paths)} counties")
    return out


# ---------------------------------------------------------------- data

def _records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    cols = [c for c in cols if c in df.columns]
    d = df[cols].copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].round(5)
    return json.loads(d.to_json(orient="records"))


def _read(name: str) -> pd.DataFrame:
    p = OUTPUT_DIR / f"{name}.csv"
    return pd.read_csv(p, dtype={"district": str}) if p.exists() else pd.DataFrame()


def build(outputs: dict[str, pd.DataFrame] | None = None, stamp: str | None = None) -> Path:
    """Write data/output/dashboard.html. `outputs` defaults to the CSVs on disk."""
    o = dict(outputs or {})
    for name in ("statewide_summary", "statewide_results", "county_swing", "district_swing",
                 "district_projection", "chamber_control"):
        if name not in o:
            o[name] = _read(name)
    if stamp is None:
        s = o["statewide_summary"]
        stamp = str(s["snapshot_utc"].iloc[0]) if "snapshot_utc" in s and len(s) else ""

    ds = o["district_swing"]
    # swing vs 2024 President for every district (a common yardstick for both chambers)
    vs_pres = pd.DataFrame(columns=["chamber", "district", "swing_pres24"])
    if len(ds):
        vs_pres = (ds[(ds["baseline_year"] == 2024) & (ds["baseline_office"] == "PRESIDENT")]
                   [["chamber", "district", "margin_base"]]
                   .rename(columns={"margin_base": "pres24_margin"}))
    dp = o["district_projection"]
    if len(dp):
        dp = dp.merge(vs_pres, on=["chamber", "district"], how="left")

    data = {
        "stamp": stamp,
        "summary": _records(o["statewide_summary"], [
            "live_office", "baseline_year", "baseline_office", "comparison", "precincts_reported",
            "precincts", "counties_reported", "counties", "live_margin", "baseline_margin",
            "swing_reported", "turnout_idx_reported"]),
        "results": _records(o["statewide_results"], [
            "office", "candidate", "party", "votes", "pct", "precincts_reporting", "precincts_total"]),
        "county": _records(o["county_swing"], [
            "county_id", "county_name", "live_office", "baseline_year", "baseline_office",
            "comparison", "DFL", "R", "OTHER", "total", "margin", "DFL_base", "R_base", "total_base",
            "margin_base", "swing_all", "swing_reported", "turnout_idx_all", "turnout_idx_reported",
            "precincts", "precincts_reported", "reported"]),
        "districts": _records(dp, [
            "chamber", "district", "status", "p_dfl", "projected_margin", "w", "margin",
            "prior_margin", "pres24_margin", "dfl_candidate", "r_candidate", "DFL", "R", "OTHER",
            "total", "precincts_reporting", "precincts_total", "contested", "recount_range",
            "other_leading"]),
        "dswing": _records(ds, [
            "chamber", "district", "baseline_year", "baseline_office", "comparison", "margin",
            "margin_base", "swing_all", "swing_reported", "turnout_idx_all", "reported"]),
        "control": _records(o["chamber_control"], list(o["chamber_control"].columns)),
        "fips": json.loads(pd.read_csv(REFERENCE_DIR / "counties.csv")
                           .set_index("county_id")["county_fips"].astype(str).to_json()),
        "shapes": json.loads(COUNTY_SHAPES.read_text(encoding="utf-8")) if COUNTY_SHAPES.exists() else None,
    }
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", blob)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"  wrote data/output/{OUT.name}")
    return OUT
