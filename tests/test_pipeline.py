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

    def test_office_mapping(self):
        from mnelect.common import map_office
        self.assertEqual(map_office("U.S. Senator"), "US_SENATE")
        self.assertEqual(map_office("U.S. Senate", "Unexpired Term"), "US_SENATE_SPECIAL")
        self.assertEqual(map_office("Governor & Lt Governor"), "GOVERNOR")
        self.assertIsNone(map_office("State Senator"))
        self.assertIsNone(map_office("State Senate"))


if __name__ == "__main__":
    unittest.main()
