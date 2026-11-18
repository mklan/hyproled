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

    def run_off(self, preferences):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_dir = root / "runtime"
            state_dir = runtime_dir / "hyproled"
            preferences_dir = root / "state" / "hyproled"
            mock_bin = root / "bin"

            state_dir.mkdir(parents=True)
            preferences_dir.mkdir(parents=True)
            mock_bin.mkdir()

            active_shader = state_dir / "shader.test.glsl"
            active_shader.write_text("test shader", encoding="utf-8")

            current_shader_file = root / "current-shader"
            current_shader_file.write_text(
                str(active_shader), encoding="utf-8"
            )

            brightness_file = root / "brightness"
            brightness_file.write_text("160", encoding="utf-8")

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
                """#!/usr/bin/env bash
set -eu

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
                """#!/usr/bin/env bash
set -eu

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


if __name__ == "__main__":
    unittest.main()
