"""End-to-end sanity test (run: python -m unittest discover tests).

Builds a SYNTHETIC "live" feed in SOS media-file format by replaying the 2024
presidential precinct results, labeled as a Governor race, with only half of
precincts marked as reported. Comparing it to 2024 PRESIDENT must give exactly
zero swing and a turnout index of 1.0 on reported precincts. This is test data,
not real 2026 results.
"""
import unittest

import pandas as pd

from mnelect import baselines, compare, sos
from mnelect.common import REFERENCE_DIR, load_config


def make_fake_feed(base: pd.DataFrame) -> str:
    b = base[(base.year == 2024) & (base.office == "PRESIDENT")]
    precincts = b[["county_id", "precinct_code"]].drop_duplicates().reset_index(drop=True)
    precincts["rep"] = (precincts.index % 2 == 0).astype(int)
    b = b.merge(precincts, on=["county_id", "precinct_code"])
    lines = []
    for r in b.itertuples():
        party = {"DFL": "DFL", "R": "R", "OTHER": "LIB"}[r.party]
        lines.append(";".join(["MN", f"{r.county_id:02d}", r.precinct_code, "0331",
                               "Governor & Lt Governor", "", "1", "Candidate", "", "",
                               party, str(r.rep), "1", str(r.votes), "0", "0"]))
    return "\n".join(lines)


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = baselines.load()
        cls.cfg = load_config()
        cls.counties = pd.read_csv(REFERENCE_DIR / "counties.csv")

    def test_zero_swing_against_itself(self):
        live = sos.parse_results(make_fake_feed(self.base))
        self.assertEqual(set(live.office), {"GOVERNOR"})
        out = compare.run(live, None, self.base, self.cfg, self.counties)
        s = out["statewide_summary"].set_index(["baseline_year", "baseline_office"])
        row = s.loc[(2024, "PRESIDENT")]
        self.assertAlmostEqual(row.swing_reported, 0.0, places=9)
        self.assertAlmostEqual(row.turnout_idx_reported, 1.0, places=9)
        self.assertAlmostEqual(row.precincts_reported / row.precincts, 0.5, places=2)
        c = out["county_swing"]
        c = c[(c.baseline_year == 2024) & (c.baseline_office == "PRESIDENT")]
        self.assertEqual(len(c), 87)
        self.assertTrue((c.swing_reported.abs() < 1e-9).all())

    def test_county_mode_zero_swing(self):
        # 2024 presidential totals by county, in SOS county-file format;
        # even-numbered counties fully reported, odd ones half reported.
        b = self.base[(self.base.year == 2024) & (self.base.office == "PRESIDENT")]
        cty = b.groupby(["county_id", "party"], as_index=False)["votes"].sum()
        lines = []
        for r in cty.itertuples():
            party = {"DFL": "DFL", "R": "R", "OTHER": "LIB"}[r.party]
            rep = "10" if r.county_id % 2 == 0 else "5"
            lines.append(";".join(["MN", f"{r.county_id:02d}", "", "0331",
                                   "Governor & Lt Governor", "", "1", "Candidate", "", "",
                                   party, rep, "10", str(r.votes), "0", "0"]))
        live = sos.parse_results("\n".join(lines))
        out = compare.run_county(live, self.base, self.cfg, self.counties)
        s = out["statewide_summary"].set_index(["baseline_year", "baseline_office"])
        row = s.loc[(2024, "PRESIDENT")]
        self.assertAlmostEqual(row.swing_reported, 0.0, places=9)
        self.assertAlmostEqual(row.turnout_idx_reported, 1.0, places=9)
        self.assertEqual(row.counties, 87)
        self.assertEqual(row.counties_reported, 43)
        c = out["county_swing"]
        c = c[(c.baseline_year == 2024) & (c.baseline_office == "PRESIDENT")]
        self.assertEqual(len(c), 87)
        self.assertTrue((c.swing_all.abs() < 1e-9).all())
        self.assertEqual(int(c.swing_reported.notna().sum()), 43)

    def test_legislature_replay(self):
        # Replay 2024 presidential results by district as if they were tonight's
        # House/Senate races: odd-numbered districts fully counted, even ones
        # 0 reported. Swing vs 2024 President must be 0, the shift 0, and
        # counted seats must go to the presidential winner.
        from mnelect import legislature as L
        base = L.load_baselines()
        pres = base[base.baseline_office == "PRESIDENT"]
        lines = []
        for r in pres.itertuples():
            label = ("State Representative District " if r.chamber == "STATE_HOUSE"
                     else "State Senator District ") + r.district
            done = int(L.senate_of(r.district)) % 2 == 1
            rep, tot = ("10", "10") if done else ("0", "10")
            for party, v in (("DFL", r.DFL), ("R", r.R), ("LIB", r.OTHER)):
                lines.append(";".join(["MN", "", "", "0100", label, r.district, "1", f"{party} cand",
                                       "", "", party, rep, tot, str(v if done else 0), "0", "0"]))
        lw = L.district_wide(L.parse("\n".join(lines)))
        lw["reported"] = lw.precincts_reporting >= lw.precincts_total
        self.assertEqual((lw.office == "STATE_HOUSE").sum(), 134)
        self.assertEqual((lw.office == "STATE_SENATE").sum(), 67)
        sw = L.district_swing(lw, base)
        s = sw[(sw.baseline_office == "PRESIDENT") & sw.reported]
        self.assertTrue((s.swing_reported.abs() < 1e-12).all())
        self.assertTrue(((s.turnout_idx_reported - 1).abs() < 1e-12).all())
        dist, ctl = L.project(lw, base, self.cfg)
        self.assertTrue((ctl.shift_vs_prior.abs() < 1e-12).all())
        counted = dist[dist.w >= 1]
        self.assertTrue(((counted.p_dfl == 1) == (counted.margin > 0)).all())
        # uncounted districts: projection falls back to the prior
        open_ = dist[dist.w == 0]
        self.assertTrue(((open_.projected_margin - open_.prior_margin).abs() < 1e-12).all())
        h = ctl.set_index("chamber").loc["STATE_HOUSE"]
        self.assertEqual(h.seats, 134)
        self.assertTrue(0 <= h.p_dfl_majority + h.p_r_majority + h.p_tie <= 1 + 1e-9)

    def test_office_mapping(self):
        from mnelect.common import map_office
        self.assertEqual(map_office("U.S. Senator"), "US_SENATE")
        self.assertEqual(map_office("U.S. Senate", "Unexpired Term"), "US_SENATE_SPECIAL")
        self.assertEqual(map_office("Governor & Lt Governor"), "GOVERNOR")
        self.assertIsNone(map_office("State Senator"))
        self.assertIsNone(map_office("State Senate"))


if __name__ == "__main__":
    unittest.main()
