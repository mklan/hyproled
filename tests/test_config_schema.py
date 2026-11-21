from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
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
            (None, 4),  # Missing mode means orbit-2x2.
        )

        for mode, index in invalid_cases:
            with self.subTest(mode=mode, index=index):
                config = self.make_config(mode, index)
                self.assertTrue(
                    list(self.validator.iter_errors(config))
                )

    @unittest.skipUnless(shutil.which("jq"), "jq is not installed")
    def test_production_validator_matches_schema_contract(self):
        def with_value(path, value):
            config = copy.deepcopy(self.example)
            target = config
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            return config

        valid_cases = [
            ("example", copy.deepcopy(self.example)),
            ("unknown properties", with_value(("futureOption",), True)),
            ("omitted mode with boundary index", self.make_config(None, 3)),
            ("omitted mode with default index", self.make_config(None, None)),
            ("fixed mode", self.make_config("fixed", 0)),
            ("horizontal mode", self.make_config("linear-horizontal", 1)),
            ("vertical mode", self.make_config("linear-vertical", 1)),
            ("2x2 orbit", self.make_config("orbit-2x2", 3)),
            ("3x3 orbit", self.make_config("orbit-3x3", 8)),
        ]

        without_schema_hint = copy.deepcopy(self.example)
        without_schema_hint.pop("$schema", None)
        valid_cases.append(("omitted schema hint", without_schema_hint))

        null_mode = copy.deepcopy(self.example)
        null_mode["defaults"]["pixelShiftMode"] = None
        null_mode["defaults"]["pixelShiftIndex"] = 3
        valid_cases.append(("explicit null mode", null_mode))

        invalid_cases = [
            ("non-string schema hint", with_value(("$schema",), 7)),
            ("null schema hint", with_value(("$schema",), None)),
            ("empty schema hint", with_value(("$schema",), "")),
            ("wrong version", with_value(("version",), 2)),
            ("missing required field", {
                **copy.deepcopy(self.example),
                "defaults": {
                    key: value
                    for key, value in self.example["defaults"].items()
                    if key != "invert"
                },
            }),
            ("pixel count below range", with_value(("defaults", "disabledPixels"), 0)),
            ("fractional monitor", with_value(("defaults", "monitor"), 0.5)),
            ("malformed area", with_value(("defaults", "area"), "1:2:3")),
            ("invalid fixed index", self.make_config("fixed", 1)),
            ("invalid horizontal index", self.make_config("linear-horizontal", 2)),
            ("invalid vertical index", self.make_config("linear-vertical", 2)),
            ("invalid 2x2 index", self.make_config("orbit-2x2", 4)),
            ("invalid 3x3 index", self.make_config("orbit-3x3", 9)),
            ("invalid phase", with_value(("defaults", "phase"), 4)),
            ("empty brightness device", with_value(("brightness", "device"), "")),
            (
                "compensation above range",
                with_value(
                    ("brightness", "compensationPercentOfMaxByPixelCount", "2"),
                    1001,
                ),
            ),
        ]

        with self.subTest(case="valid"):
            for label, config in valid_cases:
                with self.subTest(case=label):
                    self.assertTrue(
                        self.validator.is_valid(config),
                        f"JSON Schema rejected {label}",
                    )
                    result = self.run_production_validator(config)
                    self.assertEqual(
                        result.returncode,
                        0,
                        f"Production validator rejected {label}: {result.stderr}",
                    )

        with self.subTest(case="invalid"):
            for label, config in invalid_cases:
                with self.subTest(case=label):
                    self.assertFalse(
                        self.validator.is_valid(config),
                        f"JSON Schema accepted {label}",
                    )
                    result = self.run_production_validator(config)
                    self.assertNotEqual(
                        result.returncode,
                        0,
                        f"Production validator accepted {label}",
                    )

    def run_production_validator(self, config):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            environment = os.environ.copy()
            environment["HYPROLED_CONFIG"] = str(config_path)

            return subprocess.run(
                [str(ROOT / "hyproled"), "--check-config"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
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
