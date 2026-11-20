from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "config.schema.json"
EXAMPLE_PATH = ROOT / "config.example.json"


class ConfigSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(
            SCHEMA_PATH.read_text(encoding="utf-8")
        )
        cls.example = json.loads(
            EXAMPLE_PATH.read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema)

    def make_config(self, mode, index):
        config = copy.deepcopy(self.example)

        if mode is None:
            config["defaults"].pop("pixelShiftMode", None)
        else:
            config["defaults"]["pixelShiftMode"] = mode

        if index is None:
            config["defaults"].pop("pixelShiftIndex", None)
        else:
            config["defaults"]["pixelShiftIndex"] = index

        return config

    def test_example_configuration_is_valid(self):
        errors = list(self.validator.iter_errors(self.example))
        self.assertEqual(
            errors,
            [],
            "\n".join(error.message for error in errors),
        )

    def test_schema_accepts_valid_mode_index_boundaries(self):
        valid_cases = (
            ("fixed", 0),
            ("linear-horizontal", 1),
            ("linear-vertical", 1),
            ("orbit-2x2", 3),
            ("orbit-3x3", 8),
            # Missing/null mode and index use the documented defaults.
            (None, 3),
            (None, None),
        )

        for mode, index in valid_cases:
            with self.subTest(mode=mode, index=index):
                config = self.make_config(mode, index)
                errors = list(self.validator.iter_errors(config))
                self.assertEqual(
                    errors,
                    [],
                    "\n".join(error.message for error in errors),
                )

    def test_schema_rejects_invalid_mode_index_combinations(self):
        invalid_cases = (
            ("fixed", 1),
            ("linear-horizontal", 2),
            ("linear-vertical", 2),
            ("orbit-2x2", 4),
            ("orbit-3x3", 9),
            (None, 4),
            (None, 4),  # Missing mode means orbit-2x2.
        )

        for mode, index in invalid_cases:
            with self.subTest(mode=mode, index=index):
                config = self.make_config(mode, index)
                self.assertTrue(
                    list(self.validator.iter_errors(config))
                )

    def test_schema_accepts_explicit_null_mode_as_default_mode(self):
        config = copy.deepcopy(self.example)
        config["defaults"]["pixelShiftMode"] = None
        config["defaults"]["pixelShiftIndex"] = 3

        self.assertEqual(
            list(self.validator.iter_errors(config)),
            [],
        )

        config["defaults"]["pixelShiftIndex"] = 4
        self.assertTrue(list(self.validator.iter_errors(config)))


if __name__ == "__main__":
    unittest.main()
