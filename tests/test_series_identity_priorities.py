"""ধারা ৪.৬ series identity, all three priorities.

    ১) external show ID          first choice
    ২) normalised title           what the catalogue had
    ৩) first-air-year             only to settle a collision

The hard constraint is the one the plan states twice: a year is never the
identity on its own. Undekhi published four cards because season years split
it; nothing here may bring that back.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scanner import series  # noqa: E402


def _record(name, year=None, **ids):
    record = {"name": name, "category": "English"}
    if year:
        record["year"] = year
    record.update(ids)
    return record


def _groups(records):
    identities = series._disambiguated_identities(records)
    groups = {}
    for record, identity in zip(records, identities):
        groups.setdefault(identity, []).append(record.get("tmdb_id") or record.get("year"))
    return groups


class PriorityOneSplitsShowsThatShareATitle(unittest.TestCase):
    def test_the_office_us_and_uk_are_two_shows(self):
        groups = _groups([_record("The Office", 2005, tmdb_id="2316"),
                          _record("The Office", 2001, tmdb_id="2996")])
        self.assertEqual(len(groups), 2)

    def test_an_id_less_record_joins_the_id_whose_first_air_year_it_shares(self):
        records = [_record("The Office", 2005, tmdb_id="2316"),
                   _record("The Office", 2001, tmdb_id="2996"),
                   _record("The Office", 2001)]
        identities = series._disambiguated_identities(records)
        self.assertEqual(identities[2], identities[1])


class NothingChangesWithoutAConflict(unittest.TestCase):
    def test_years_alone_never_split_a_show(self):
        """The Undekhi case: two season years, no ids, one show."""
        groups = _groups([_record("Undekhi", 2022), _record("Undekhi", 2026)])
        self.assertEqual(len(groups), 1)

    def test_one_id_and_an_id_less_record_stay_one_show(self):
        groups = _groups([_record("Undekhi", 2022, tmdb_id="99"), _record("Undekhi", 2026)])
        self.assertEqual(len(groups), 1)

    def test_the_identity_is_the_title_key_when_unambiguous(self):
        record = _record("Resort")
        self.assertEqual(series._disambiguated_identities([record]),
                         [series._series_identity(record)])

    def test_the_external_id_reader_accepts_the_plan_s_external_ids_map(self):
        self.assertEqual(series._external_show_id({"external_ids": {"tvdb": "81189"}}),
                         "tvdb:81189")


if __name__ == "__main__":
    unittest.main()
