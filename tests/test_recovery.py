from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HYPROLED = ROOT / "hyproled"
SOURCE = HYPROLED.read_text(encoding="utf-8")


class RuntimeRollbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        match = re.search(
            r"(?ms)^on_exit\(\) \{\n.*?^\}",
            SOURCE,
        )
        if match is None:
            raise AssertionError("on_exit() was not found")
        cls.on_exit_function = match.group(0)

    def run_exit(
        self,
        *,
        shader_status=0,
        brightness_status=0,
        brightness_write_attempted=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "state.json"
            backup_file = root / "state-backup"
            new_shader = root / "shader.new.glsl"

            state_file.write_text("new-state", encoding="utf-8")
            backup_file.write_text("previous-state", encoding="utf-8")
            new_shader.write_text("new-shader", encoding="utf-8")

            script = "\n".join([
                f"STATE_FILE={shlex.quote(str(state_file))}",
                f"STATE_BACKUP={shlex.quote(str(backup_file))}",
                f"NEW_SHADER={shlex.quote(str(new_shader))}",
                'TEMP_STATE_FILE=""',
                'ROLLBACK_SHADER="/previous/shader.glsl"',
                "ROLLBACK_BRIGHTNESS=100",
                "ROLLBACK_ENABLED=true",
                "OP_COMMITTED=false",
                f"BRIGHTNESS_WRITE_ATTEMPTED={'true' if brightness_write_attempted else 'false'}",
                "KEEP_NEW_SHADER=false",
                f"ROLLBACK_SHADER_RESULT={shader_status}",
                f"ROLLBACK_BRIGHTNESS_RESULT={brightness_status}",
                'warn() { printf "%s\\n" "$*" >&2; }',
                'set_shader() { return "$ROLLBACK_SHADER_RESULT"; }',
                'set_brightness() { return "$ROLLBACK_BRIGHTNESS_RESULT"; }',
                self.on_exit_function,
                "trap on_exit EXIT",
                "exit 42",
            ]) + "\n"

            result = subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            return {
                "returncode": result.returncode,
                "stderr": result.stderr,
                "backup_exists": backup_file.exists(),
                "state_content": (
                    state_file.read_text(encoding="utf-8")
                    if state_file.exists() else None
                ),
                "new_shader_exists": new_shader.exists(),
            }

    def test_backup_is_preserved_when_shader_rollback_fails(self):
        result = self.run_exit(shader_status=1)

        self.assertEqual(result["returncode"], 42, result["stderr"])
        self.assertTrue(result["backup_exists"])
        self.assertEqual(result["state_content"], "new-state")
        self.assertTrue(result["new_shader_exists"])

    def test_backup_is_preserved_when_brightness_rollback_fails(self):
        result = self.run_exit(
            brightness_write_attempted=True,
            brightness_status=1,
        )

        self.assertEqual(result["returncode"], 42, result["stderr"])
        self.assertTrue(result["backup_exists"])
        self.assertEqual(result["state_content"], "new-state")
        self.assertTrue(result["new_shader_exists"])

    def test_successful_rollback_restores_backup_and_cleans_new_shader(self):
        result = self.run_exit(
            brightness_write_attempted=True,
            brightness_status=0,
        )

        self.assertEqual(result["returncode"], 42, result["stderr"])
        self.assertFalse(result["backup_exists"])
        self.assertEqual(result["state_content"], "previous-state")
        self.assertFalse(result["new_shader_exists"])


class LuaShaderPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.functions = []

        for name in ("lua_long_string", "set_shader"):
            pattern = (
                r"(?ms)^"
                + re.escape(name)
                + r"\(\) \{\n.*?^\}"
            )
            match = re.search(pattern, SOURCE)

            if match is None:
                raise AssertionError(f"{name}() was not found")

            cls.functions.append(match.group(0))

    def run_set_shader(self, path):
        with tempfile.TemporaryDirectory() as directory:
            capture_file = Path(directory) / "eval-expression"

            script = "\n".join((
                f"CAPTURE_FILE={shlex.quote(str(capture_file))}",
                'EXPECTED_PATH="$1"',
                "USE_EVAL=true",
                'hyprctl() { [[ "${1:-}" == "eval" ]] || return 1; printf "%s" "${2:-}" > "$CAPTURE_FILE"; }',
                'get_current_shader() { printf "%s\\n" "$EXPECTED_PATH"; }',
                *self.functions,
                'set_shader "$EXPECTED_PATH"',
            )) + "\n"

            result = subprocess.run(
                ["bash", "-c", script, "hyproled-test", path],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stdout + result.stderr,
            )

            return capture_file.read_text(encoding="utf-8")

    def test_eval_path_uses_a_non_terminating_lua_delimiter(self):
        cases = (
            ("/tmp/shader.glsl", "[[", "]]"),
            ("/tmp/runtime]]/shader.glsl", "[=[", "]=]"),
            ("/tmp/runtime]]/and]=]/shader.glsl", "[==[", "]==]"),
            (
                "/tmp/runtime]]/and]=]/and]==]/shader.glsl",
                "[===[",
                "]===]",
            ),
            (
                '/tmp/runtime]]; os.execute("unexpected") --/shader.glsl',
                "[=[",
                "]=]",
            ),
        )

        for path, opening, closing in cases:
            with self.subTest(path=path):
                expected = (
                    "hl.config({ decoration = { screen_shader = "
                    f"{opening}{path}{closing}"
                    " } })"
                )
                self.assertEqual(self.run_set_shader(path), expected)


class ShaderPathOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        match = re.search(
            r"(?ms)^is_our_shader_path\(\) \{\n.*?^\}",
            SOURCE,
        )
        if match is None:
            raise AssertionError("is_our_shader_path() was not found")
        cls.function = match.group(0)

    def test_only_direct_shader_files_in_state_dir_are_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "runtime" / "hyproled"
            state_dir.mkdir(parents=True)

            valid_path = str(state_dir / "shader.A1b2C3.glsl")
            nested_path = str(state_dir / "nested" / "shader.A1b2C3.glsl")
            traversal_path = (
                f"{state_dir}/shader.fake/../../important.glsl"
            )
            outside_path = str(Path(directory) / "shader.A1b2C3.glsl")
            unrelated_path = str(state_dir / "state.json")

            script = (
                f"STATE_DIR={shlex.quote(str(state_dir))}\n"
                + self.function
                + '\nis_our_shader_path "$1"\n'
            )

            cases = (
                (valid_path, True),
                (nested_path, False),
                (traversal_path, False),
                (outside_path, False),
                (unrelated_path, False),
            )

            for path, expected in cases:
                with self.subTest(path=path):
                    result = subprocess.run(
                        ["bash", "-c", script, "hyproled-test", path],
                        cwd=ROOT,
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(
                        result.returncode == 0,
                        expected,
                        result.stdout + result.stderr,
                    )


@unittest.skipUnless(shutil.which("jq"), "jq is not installed")
class PreferencesValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        match = re.search(
            r"(?ms)^pixel_shift_index_valid\(\) \{\n.*?^\}",
            SOURCE,
        )
        if match is None:
            raise AssertionError("pixel_shift_index_valid() was not found")
        cls.index_function = match.group(0)

        match = re.search(
            r"(?ms)^validate_preferences\(\) \{\n.*?^\}",
            SOURCE,
        )
        if match is None:
            raise AssertionError("validate_preferences() was not found")
        cls.validation_function = match.group(0)

    def valid_preferences(self):
        return {
            "version": 1,
            "defaultsHash": "original-defaults-hash",
            "maskEnabled": True,
            "pixelShift": False,
            "pixelShiftMode": "orbit-2x2",
            "pixelShiftIndex": 0,
            "phase": 2,
            "offPixels": 1,
            "monitor": 0,
            "area": None,
            "invert": False,
            "brightnessCompensationEnabled": True,
        }

    def run_validation(self, preferences):
        return self.run_validation_document(json.dumps(preferences))

    def run_validation_document(self, document):
        with tempfile.TemporaryDirectory() as directory:
            preferences_path = Path(directory) / "preferences.json"
            preferences_path.write_text(document, encoding="utf-8")

            script = "\n".join((
                'PREFERENCES_FILE="$1"',
                self.index_function,
                self.validation_function,
                "validate_preferences",
            )) + "\n"

            result = subprocess.run(
                ["bash", "-c", script, "hyproled-test", str(preferences_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

        return result.returncode

    def test_null_valid_and_missing_area_are_accepted(self):
        preferences = self.valid_preferences()
        self.assertEqual(self.run_validation(preferences), 0)

        preferences["area"] = "10:20:300:400"
        self.assertEqual(self.run_validation(preferences), 0)

        # Older preferences may omit this optional field; it means full screen.
        preferences.pop("area")
        self.assertEqual(self.run_validation(preferences), 0)

    def test_malformed_area_is_rejected(self):
        for area in (
            "",
            "10:20:300",
            "10:20:300:400;invalid",
            "10:20:300:400x",
            42,
            False,
        ):
            with self.subTest(area=area):
                preferences = self.valid_preferences()
                preferences["area"] = area
                self.assertNotEqual(self.run_validation(preferences), 0)

    def test_pixel_shift_mode_index_boundaries_are_enforced(self):
        valid_modes = (
            ("fixed", (0,)),
            ("linear-horizontal", (0, 1)),
            ("linear-vertical", (0, 1)),
            ("orbit-2x2", (0, 1, 2, 3)),
            ("orbit-3x3", tuple(range(9))),
        )

        for mode, indices in valid_modes:
            for index in indices:
                with self.subTest(mode=mode, index=index):
                    preferences = self.valid_preferences()
                    preferences["pixelShiftMode"] = mode
                    preferences["pixelShiftIndex"] = index
                    self.assertEqual(self.run_validation(preferences), 0)

        invalid_modes = (
            ("fixed", 1),
            ("linear-horizontal", 2),
            ("linear-vertical", 2),
            ("orbit-2x2", 4),
            ("orbit-3x3", 9),
        )

        for mode, index in invalid_modes:
            with self.subTest(mode=mode, index=index):
                preferences = self.valid_preferences()
                preferences["pixelShiftMode"] = mode
                preferences["pixelShiftIndex"] = index
                self.assertNotEqual(self.run_validation(preferences), 0)

    def test_required_fields_and_value_ranges_are_enforced(self):
        required_fields = (
            "version",
            "defaultsHash",
            "maskEnabled",
            "pixelShift",
            "pixelShiftMode",
            "pixelShiftIndex",
            "phase",
            "offPixels",
            "monitor",
            "invert",
            "brightnessCompensationEnabled",
        )
        for field in required_fields:
            with self.subTest(missing=field):
                preferences = self.valid_preferences()
                del preferences[field]
                self.assertNotEqual(self.run_validation(preferences), 0)

        invalid_cases = (
            ("version", 2),
            ("defaultsHash", 42),
            ("maskEnabled", 1),
            ("pixelShift", "true"),
            ("pixelShiftMode", "unsupported"),
            ("pixelShiftIndex", -1),
            ("pixelShiftIndex", 9),
            ("pixelShiftIndex", 1.5),
            ("phase", -1),
            ("phase", 4),
            ("offPixels", 0),
            ("offPixels", 4),
            ("monitor", -1),
            ("monitor", 0.5),
            ("invert", 0),
            ("brightnessCompensationEnabled", "true"),
        )
        for field, value in invalid_cases:
            with self.subTest(field=field, value=value):
                preferences = self.valid_preferences()
                preferences[field] = value
                self.assertNotEqual(self.run_validation(preferences), 0)

    def test_all_preference_fields_follow_their_value_contract(self):
        valid_cases = (
            ("empty defaults hash", "defaultsHash", ""),
            ("mask disabled", "maskEnabled", False),
            ("pixel shift enabled", "pixelShift", True),
            ("phase minimum", "phase", 0),
            ("phase maximum", "phase", 3),
            ("pixel count minimum", "offPixels", 1),
            ("pixel count maximum", "offPixels", 3),
            ("monitor minimum", "monitor", 0),
            ("large monitor ID", "monitor", 2147483647),
            ("invert enabled", "invert", True),
            ("brightness compensation disabled",
             "brightnessCompensationEnabled", False),
        )

        for label, field, value in valid_cases:
            with self.subTest(case=label):
                preferences = self.valid_preferences()
                preferences[field] = value
                self.assertEqual(self.run_validation(preferences), 0)

        invalid_cases = (
            ("fractional version", "version", 1.5),
            ("string version", "version", "1"),
            ("null version", "version", None),
            ("null defaults hash", "defaultsHash", None),
            ("numeric defaults hash", "defaultsHash", 123),
            ("boolean defaults hash", "defaultsHash", True),
            ("null mask flag", "maskEnabled", None),
            ("integer mask flag", "maskEnabled", 1),
            ("string mask flag", "maskEnabled", "true"),
            ("null shift flag", "pixelShift", None),
            ("integer shift flag", "pixelShift", 0),
            ("null shift mode", "pixelShiftMode", None),
            ("non-string shift mode", "pixelShiftMode", 1),
            ("unknown shift mode", "pixelShiftMode", "unknown"),
            ("string shift index", "pixelShiftIndex", "1"),
            ("null shift index", "pixelShiftIndex", None),
            ("boolean shift index", "pixelShiftIndex", True),
            ("fractional shift index", "pixelShiftIndex", 1.5),
            ("negative shift index", "pixelShiftIndex", -1),
            ("shift index overflow", "pixelShiftIndex", 9),
            ("string phase", "phase", "1"),
            ("null phase", "phase", None),
            ("fractional phase", "phase", 1.5),
            ("negative phase", "phase", -1),
            ("phase overflow", "phase", 4),
            ("string pixel count", "offPixels", "1"),
            ("null pixel count", "offPixels", None),
            ("fractional pixel count", "offPixels", 1.5),
            ("negative monitor", "monitor", -1),
            ("fractional monitor", "monitor", 0.5),
            ("string monitor", "monitor", "0"),
            ("null monitor", "monitor", None),
            ("integer invert", "invert", 0),
            ("string invert", "invert", "false"),
            ("null invert", "invert", None),
            ("integer brightness flag",
             "brightnessCompensationEnabled", 0),
            ("null brightness flag",
             "brightnessCompensationEnabled", None),
        )

        for label, field, value in invalid_cases:
            with self.subTest(case=label):
                preferences = self.valid_preferences()
                preferences[field] = value
                self.assertNotEqual(self.run_validation(preferences), 0)

        # Unknown fields remain permitted for forward compatibility.
        preferences = self.valid_preferences()
        preferences["futurePreference"] = {"enabled": True}
        self.assertEqual(self.run_validation(preferences), 0)

    def test_invalid_preference_documents_are_rejected(self):
        invalid_documents = (
            "",
            "{",
            "null",
            "[]",
            "42",
            '"preferences"',
        )

        for document in invalid_documents:
            with self.subTest(document=document):
                self.assertNotEqual(
                    self.run_validation_document(document),
                    0,
                )


@unittest.skipUnless(shutil.which("jq"), "jq is not installed")
class RuntimeOffRecoveryTests(unittest.TestCase):
    def valid_preferences(self):
        return {
            "version": 1,
            "defaultsHash": "original-defaults-hash",
            "maskEnabled": True,
            "pixelShift": False,
            "pixelShiftMode": "orbit-2x2",
            "pixelShiftIndex": 0,
            "phase": 2,
            "offPixels": 1,
            "monitor": 0,
            "area": None,
            "invert": False,
            "brightnessCompensationEnabled": True,
        }

    def run_off(
        self,
        preferences,
        *,
        current_brightness=160,
        state_overrides=None,
        current_shader_is_previous=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_dir = root / "runtime"
            state_dir = runtime_dir / "hyproled"
            preferences_dir = root / "state" / "hyproled"
            mock_bin = root / "bin"

            state_dir.mkdir(parents=True)
            preferences_dir.mkdir(parents=True)
            mock_bin.mkdir()

            bash_path = shutil.which("bash")
            if bash_path is None:
                self.skipTest("bash is required for recovery mocks")
            (mock_bin / "bash").symlink_to(bash_path)

            active_shader = state_dir / "shader.test.glsl"
            active_shader.write_text("test shader", encoding="utf-8")

            current_shader_file = root / "current-shader"
            if current_shader_is_previous:
                previous_shader = state_dir / "shader.previous.glsl"
                previous_shader.write_text(
                    "previous shader", encoding="utf-8"
                )
                current_shader = previous_shader
            else:
                current_shader = active_shader

            current_shader_file.write_text(
                str(current_shader), encoding="utf-8"
            )

            brightness_file = root / "brightness"
            brightness_file.write_text(
                str(current_brightness), encoding="utf-8"
            )

            state = {
                "version": 5,
                "status": "active",
                "phase": 2,
                "offPixels": 1,
                "monitor": 0,
                "area": None,
                "invert": False,
                "maskEnabled": True,
                "pixelShift": False,
                "pixelShiftMode": "orbit-2x2",
                "pixelShiftIndex": 0,
                "brightnessDevice": "test_backlight",
                "brightnessOriginal": 100,
                "brightnessMax": 500,
                "brightnessApplied": 150,
                "compensationPercent": 10,
                "brightnessEnabled": True,
                "brightnessCompensationEnabled": True,
                "compensationPercentOfMaxByPixelCount": {
                    "1": 10,
                    "2": 20,
                    "3": 30,
                },
                "previousShader": "",
                "activeShader": str(active_shader),
                "restoreTarget": None,
            }

            if state_overrides is not None:
                state.update(state_overrides)

            (state_dir / "state.json").write_text(
                json.dumps(state), encoding="utf-8"
            )

            preferences_file = preferences_dir / "preferences.json"
            if isinstance(preferences, dict):
                preferences_file.write_text(
                    json.dumps(preferences), encoding="utf-8"
                )
            elif preferences is not None:
                preferences_file.write_text(
                    preferences, encoding="utf-8"
                )

            hyprctl = mock_bin / "hyprctl"
            hyprctl.write_text(
                f"#!{mock_bin / 'bash'}\n"
                + """set -eu

case "${1:-}" in
  version)
    printf 'Hyprland 0.53.3\\n'
    ;;
  -j)
    [[ "${2:-}" == "getoption" ]] || exit 2
    current_shader=$(<"$HYPROLED_TEST_SHADER_FILE")
    if [[ -n "$current_shader" ]]; then
      jq -cn --arg value "$current_shader" '{str: $value, set: true}'
    else
      jq -cn '{str: "", set: false}'
    fi
    ;;
  keyword)
    [[ "${2:-}" == "decoration:screen_shader" ]] || exit 3
    printf '%s' "${3:-}" > "$HYPROLED_TEST_SHADER_FILE"
    ;;
  *)
    exit 4
    ;;
esac
""",
                encoding="utf-8",
            )
            hyprctl.chmod(0o755)

            brightnessctl = mock_bin / "brightnessctl"
            brightnessctl.write_text(
                f"#!{mock_bin / 'bash'}\n"
                + """set -eu

[[ "${1:-}" == "-d" ]] || exit 1
shift 2
command="${1:-}"
shift || true

case "$command" in
  get)
    cat "$HYPROLED_TEST_BRIGHTNESS_FILE"
    ;;
  max)
    printf '500\\n'
    ;;
  set)
    printf '%s\\n' "$1" > "$HYPROLED_TEST_BRIGHTNESS_FILE"
    ;;
  *)
    exit 2
    ;;
esac
""",
                encoding="utf-8",
            )
            brightnessctl.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "XDG_RUNTIME_DIR": str(runtime_dir),
                "XDG_STATE_HOME": str(root / "state"),
                "HYPROLED_CONFIG": str(root / "missing-config.json"),
                "HYPROLED_TEST_SHADER_FILE": str(current_shader_file),
                "HYPROLED_TEST_BRIGHTNESS_FILE": str(brightness_file),
                "PATH": str(mock_bin) + os.pathsep + env.get("PATH", ""),
            })

            result = subprocess.run(
                [str(HYPROLED), "off"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )

            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "state_exists": (state_dir / "state.json").exists(),
                "shader": current_shader_file.read_text(
                    encoding="utf-8"
                ),
                "brightness": brightness_file.read_text(
                    encoding="utf-8"
                ).strip(),
                "active_shader_exists": active_shader.exists(),
                "preferences_exists": preferences_file.exists(),
                "preferences": (
                    preferences_file.read_text(encoding="utf-8")
                    if preferences_file.exists() else None
                ),
            }

    def assert_recovered(self, result):
        self.assertEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        self.assertFalse(result["state_exists"])
        self.assertEqual(result["shader"], "")
        self.assertEqual(result["brightness"], "110")
        self.assertFalse(result["active_shader_exists"])

    def test_off_recovers_with_missing_config_and_corrupt_preferences(self):
        corrupt_preferences = '{"broken":'

        result = self.run_off(corrupt_preferences)

        self.assert_recovered(result)
        self.assertEqual(result["preferences"], corrupt_preferences)
        self.assertIn("preferences", result["stderr"])

    def test_off_recovers_when_preferences_are_missing(self):
        result = self.run_off(None)

        self.assert_recovered(result)
        self.assertFalse(result["preferences_exists"])

    def test_off_updates_valid_preferences_without_loading_config(self):
        result = self.run_off(self.valid_preferences())

        self.assert_recovered(result)
        preferences = json.loads(result["preferences"])

        self.assertEqual(
            preferences["defaultsHash"],
            "original-defaults-hash",
        )
        self.assertFalse(preferences["maskEnabled"])
        self.assertFalse(preferences["pixelShift"])

    def test_off_preserves_preferences_with_invalid_area(self):
        preferences = self.valid_preferences()
        preferences["area"] = "0:0:10:10;invalid"
        original_preferences = json.dumps(preferences)

        result = self.run_off(preferences)

        self.assert_recovered(result)
        self.assertEqual(result["preferences"], original_preferences)
        self.assertIn("preferences", result["stderr"])

    def test_off_recovers_brightness_restore_before_shader_switch(self):
        result = self.run_off(
            self.valid_preferences(),
            current_brightness=150,
            state_overrides={
                "status": "applying",
                "maskEnabled": False,
                "pixelShift": True,
                "restoreTarget": 110,
            },
            current_shader_is_previous=True,
        )

        self.assert_recovered(result)
        self.assertEqual(result["brightness"], "110")

    def test_off_recovers_brightness_restore_after_shader_switch(self):
        result = self.run_off(
            self.valid_preferences(),
            current_brightness=110,
            state_overrides={
                "status": "applying",
                "maskEnabled": False,
                "pixelShift": True,
                "restoreTarget": 110,
            },
        )

        self.assert_recovered(result)
        self.assertEqual(result["brightness"], "110")

    def test_off_rejects_applying_restore_target_without_brightness_ownership(self):
        result = self.run_off(
            self.valid_preferences(),
            current_brightness=160,
            state_overrides={
                "status": "applying",
                "maskEnabled": False,
                "pixelShift": True,
                "brightnessEnabled": False,
                "restoreTarget": 110,
            },
        )

        self.assertNotEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        self.assertIn("state file is invalid or unsupported", result["stderr"])
        self.assertTrue(result["state_exists"])
        self.assertEqual(result["brightness"], "160")
        self.assertTrue(result["active_shader_exists"])

    def test_off_preserves_manual_brightness_adjustment(self):
        result = self.run_off(
            self.valid_preferences(),
            current_brightness=180,
        )

        self.assertEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        self.assertFalse(result["state_exists"])
        self.assertEqual(result["shader"], "")
        self.assertEqual(result["brightness"], "130")
        self.assertFalse(result["active_shader_exists"])



@unittest.skipUnless(shutil.which("jq"), "jq is not installed")
class BrightnessLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

        self.runtime_dir = self.root / "runtime"
        self.state_home = self.root / "state"
        self.home = self.root / "home"
        self.mock_bin = self.root / "bin"

        for directory in (
            self.runtime_dir,
            self.state_home,
            self.home,
            self.mock_bin,
        ):
            directory.mkdir(parents=True)

        self.state_dir = self.runtime_dir / "hyproled"
        self.state_file = self.state_dir / "state.json"
        self.preferences_file = (
            self.state_home / "hyproled" / "preferences.json"
        )
        self.config_file = self.root / "config.json"
        self.brightness_file = self.root / "brightness"
        self.shader_file = self.root / "current-shader"
        self.brightness_log = self.root / "brightness.log"
        self.fail_next_set_file = self.root / "fail-next-set"

        self.brightness_file.write_text("100", encoding="utf-8")
        self.shader_file.write_text("", encoding="utf-8")

        config = {
            "version": 1,
            "defaults": {
                "disabledPixels": 1,
                "monitor": 0,
                "area": None,
                "invert": False,
                "maskEnabled": False,
                "pixelShift": False,
                "pixelShiftMode": "orbit-2x2",
                "pixelShiftIndex": 0,
                "phase": 0,
            },
            "brightness": {
                "enabled": False,
                "device": "test_backlight",
                "compensationPercentOfMaxByPixelCount": {
                    "1": 10,
                    "2": 20,
                    "3": 30,
                },
            },
        }
        self.config_file.write_text(
            json.dumps(config),
            encoding="utf-8",
        )

        hyprctl = self.mock_bin / "hyprctl"
        hyprctl.write_text(
            """#!/usr/bin/env bash
set -eu

case "${1:-}" in
  version)
    printf 'Hyprland 0.53.3\\n'
    ;;
  -j)
    [[ "${2:-}" == "getoption" ]] || exit 2
    current=$(<"$HYPROLED_TEST_SHADER_FILE")
    jq -cn --arg value "$current" '{str: $value, set: ($value != "")}'
    ;;
  keyword)
    [[ "${2:-}" == "decoration:screen_shader" ]] || exit 3
    printf '%s' "${3:-}" > "$HYPROLED_TEST_SHADER_FILE"
    ;;
  *)
    exit 4
    ;;
esac
""",
            encoding="utf-8",
        )
        hyprctl.chmod(0o755)

        brightnessctl = self.mock_bin / "brightnessctl"
        brightnessctl.write_text(
            """#!/usr/bin/env bash
set -eu

[[ "${1:-}" == "-d" ]] || exit 1
shift 2
command="${1:-}"
shift || true

printf '%s' "$command" >> "$HYPROLED_TEST_BRIGHTNESS_LOG"
for argument in "$@"; do
  printf ' %s' "$argument" >> "$HYPROLED_TEST_BRIGHTNESS_LOG"
done
printf '\\n' >> "$HYPROLED_TEST_BRIGHTNESS_LOG"

case "$command" in
  get)
    cat "$HYPROLED_TEST_BRIGHTNESS_FILE"
    ;;
  max)
    printf '500\\n'
    ;;
  set)
    if [[ -e "$HYPROLED_TEST_FAIL_NEXT_SET" ]]; then
      rm -f -- "$HYPROLED_TEST_FAIL_NEXT_SET"
      exit 1
    fi
    printf '%s\\n' "$1" > "$HYPROLED_TEST_BRIGHTNESS_FILE"
    ;;
  *)
    exit 2
    ;;
esac
""",
            encoding="utf-8",
        )
        brightnessctl.chmod(0o755)

        self.environment = os.environ.copy()
        self.environment.update({
            "HOME": str(self.home),
            "XDG_RUNTIME_DIR": str(self.runtime_dir),
            "XDG_STATE_HOME": str(self.state_home),
            "HYPROLED_CONFIG": str(self.config_file),
            "HYPRLAND_INSTANCE_SIGNATURE": "hyproled-test",
            "HYPROLED_TEST_SHADER_FILE": str(self.shader_file),
            "HYPROLED_TEST_BRIGHTNESS_FILE": str(self.brightness_file),
            "HYPROLED_TEST_BRIGHTNESS_LOG": str(self.brightness_log),
            "HYPROLED_TEST_FAIL_NEXT_SET": str(self.fail_next_set_file),
            "PATH": (
                str(self.mock_bin)
                + os.pathsep
                + os.environ.get("PATH", "")
            ),
        })

    def invoke(self, *arguments):
        result = subprocess.run(
            [str(HYPROLED), *arguments],
            cwd=ROOT,
            env=self.environment,
            text=True,
            capture_output=True,
        )

        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "state": (
                json.loads(self.state_file.read_text(encoding="utf-8"))
                if self.state_file.exists() else None
            ),
            "preferences": (
                json.loads(self.preferences_file.read_text(encoding="utf-8"))
                if self.preferences_file.exists() else None
            ),
            "brightness": self.brightness_file.read_text(
                encoding="utf-8"
            ).strip(),
            "shader": self.shader_file.read_text(encoding="utf-8"),
            "brightness_log": (
                self.brightness_log.read_text(encoding="utf-8")
                if self.brightness_log.exists() else ""
            ),
        }

    def enable_compensation(self):
        result = self.invoke(
            "--mask",
            "--pixel-shift",
            "--brightness-compensation",
        )
        self.assertEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        return result

    def test_initial_compensation_captures_baseline_before_writing(self):
        result = self.enable_compensation()

        self.assertEqual(result["brightness"], "150")
        self.assertEqual(result["state"]["brightnessOriginal"], 100)
        self.assertEqual(result["state"]["brightnessApplied"], 150)
        self.assertTrue(result["state"]["brightnessEnabled"])
        self.assertTrue(
            result["preferences"]["brightnessCompensationEnabled"]
        )
        self.assertIn("get\n", result["brightness_log"])
        self.assertIn("max\n", result["brightness_log"])
        self.assertIn("set 150\n", result["brightness_log"])

    def test_compensation_is_clamped_to_device_maximum(self):
        self.brightness_file.write_text("480", encoding="utf-8")

        result = self.enable_compensation()

        self.assertEqual(result["brightness"], "500")
        self.assertEqual(result["state"]["brightnessOriginal"], 480)
        self.assertEqual(result["state"]["brightnessApplied"], 500)

    def test_reconfiguration_preserves_manual_brightness_delta(self):
        self.enable_compensation()
        self.brightness_file.write_text("180", encoding="utf-8")

        result = self.invoke("--off-pixels", "2")

        self.assertEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        self.assertEqual(result["brightness"], "230")
        self.assertEqual(result["state"]["brightnessOriginal"], 130)
        self.assertEqual(result["state"]["brightnessApplied"], 230)
        self.assertEqual(result["state"]["compensationPercent"], 20)

    def test_disabling_compensation_preserves_manual_brightness_delta(self):
        self.enable_compensation()
        self.brightness_file.write_text("180", encoding="utf-8")

        result = self.invoke("--no-brightness-compensation")

        self.assertEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        self.assertEqual(result["brightness"], "130")
        self.assertEqual(result["state"]["brightnessOriginal"], 130)
        self.assertEqual(result["state"]["brightnessApplied"], 130)
        self.assertFalse(result["state"]["brightnessEnabled"])
        self.assertFalse(
            result["preferences"]["brightnessCompensationEnabled"]
        )
        self.assertTrue(result["state"]["maskEnabled"])

    def test_reenabling_compensation_uses_current_uncompensated_brightness(self):
        self.enable_compensation()
        self.brightness_file.write_text("180", encoding="utf-8")

        disabled = self.invoke("--no-brightness-compensation")
        self.assertEqual(
            disabled["returncode"],
            0,
            disabled["stdout"] + disabled["stderr"],
        )
        self.assertEqual(disabled["brightness"], "130")

        result = self.invoke("--brightness-compensation")

        self.assertEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        self.assertEqual(result["brightness"], "180")
        self.assertEqual(result["state"]["brightnessOriginal"], 130)
        self.assertEqual(result["state"]["brightnessApplied"], 180)
        self.assertTrue(result["state"]["brightnessEnabled"])

    def test_disabling_mask_restores_brightness_while_pixel_shift_remains_active(self):
        self.enable_compensation()
        self.brightness_file.write_text("180", encoding="utf-8")

        result = self.invoke("--no-mask")

        self.assertEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        self.assertEqual(result["brightness"], "130")
        self.assertFalse(result["state"]["maskEnabled"])
        self.assertTrue(result["state"]["pixelShift"])
        self.assertFalse(result["state"]["brightnessEnabled"])
        self.assertFalse(result["preferences"]["maskEnabled"])

    def test_mask_without_compensation_never_accesses_brightness(self):
        result = self.invoke(
            "--mask",
            "--pixel-shift",
            "--no-brightness-compensation",
        )

        self.assertEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        self.assertEqual(result["brightness_log"], "")
        self.assertEqual(result["brightness"], "100")
        self.assertFalse(result["state"]["brightnessEnabled"])

    def test_pixel_shift_only_session_never_accesses_brightness(self):
        result = self.invoke("--pixel-shift")

        self.assertEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        self.assertEqual(result["brightness_log"], "")
        self.assertEqual(result["brightness"], "100")
        self.assertFalse(result["state"]["brightnessEnabled"])

    def test_failed_initial_compensation_rolls_back_shader_brightness_and_state(self):
        self.fail_next_set_file.touch()

        result = self.invoke(
            "--mask",
            "--pixel-shift",
            "--brightness-compensation",
        )

        self.assertNotEqual(result["returncode"], 0)
        self.assertIsNone(result["state"])
        self.assertEqual(result["brightness"], "100")
        self.assertEqual(result["shader"], "")
        self.assertFalse(list(self.state_dir.glob("shader.*")))
        self.assertIn("set 150\n", result["brightness_log"])
        self.assertIn("set 100\n", result["brightness_log"])

    def test_failed_off_restore_persists_target_for_retry(self):
        self.enable_compensation()
        self.fail_next_set_file.touch()

        failed = self.invoke("off")

        self.assertNotEqual(failed["returncode"], 0)
        self.assertIsNotNone(failed["state"])
        self.assertEqual(failed["state"]["status"], "restoring")
        self.assertEqual(failed["state"]["restoreTarget"], 100)
        self.assertEqual(failed["brightness"], "150")
        self.assertEqual(failed["shader"], "")

        recovered = self.invoke("off")

        self.assertEqual(
            recovered["returncode"],
            0,
            recovered["stdout"] + recovered["stderr"],
        )
        self.assertIsNone(recovered["state"])
        self.assertEqual(recovered["brightness"], "100")
        self.assertEqual(recovered["shader"], "")




if __name__ == "__main__":
    unittest.main()
