"""Company profile files must satisfy exactly the rules the Settings API enforces (no database needed)."""
import json
import unittest
from pathlib import Path

from app.api.v1.settings import SettingsWrite

BACKEND = Path(__file__).resolve().parents[1]


def load(name: str) -> dict:
    raw = json.loads((BACKEND / name).read_text(encoding="utf-8"))
    return {key: value for key, value in raw.items() if not key.startswith("_")}


class CompanyProfileTests(unittest.TestCase):
    def test_example_lists_exactly_the_settings_fields(self):
        self.assertEqual(set(load("company_profile.example.json")), set(SettingsWrite.model_fields))

    def test_real_profile_passes_the_settings_api_validation(self):
        path = BACKEND / "company_profile.json"
        if not path.is_file():
            self.skipTest("backend/company_profile.json is not present (it is git-ignored)")
        body = SettingsWrite(**load("company_profile.json"))
        if body.gstin and body.pan:
            self.assertEqual(body.pan, body.gstin[2:12], "PAN must equal characters 3-12 of the GSTIN")

    def test_bad_gstin_is_rejected(self):
        data = load("company_profile.example.json")
        data.update({"legal_name": "X", "gstin": "37AAAAA0000A1Z5", "state_code": "37"})
        with self.assertRaises(ValueError):
            SettingsWrite(**data)


if __name__ == "__main__":
    unittest.main()
