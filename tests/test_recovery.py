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
                "[===",
                "]==]",
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
        with tempfile.TemporaryDirectory() as directory:
            preferences_path = Path(directory) / "preferences.json"
            preferences_path.write_text(
                json.dumps(preferences),
                encoding="utf-8",
            )

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

    def run_off(self, preferences, *, current_brightness=160):
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
            current_shader_file.write_text(
                str(active_shader), encoding="utf-8"
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


if __name__ == "__main__":
    unittest.main()
