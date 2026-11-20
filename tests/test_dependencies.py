from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HYPROLED = ROOT / "hyproled"


@unittest.skipUnless(shutil.which("jq"), "jq is not installed")
class LazyBrightnessDependencyTests(unittest.TestCase):
    def create_environment(
        self,
        root,
        mask_enabled,
        pixel_shift_enabled,
        pixel_shift_mode="orbit-2x2",
        pixel_shift_index=0,
    ):
        runtime_dir = root / "runtime"
        config_dir = root / "config" / "hyproled"
        state_home = root / "state"
        mock_bin = root / "bin"

        for directory in (runtime_dir, config_dir, state_home, mock_bin):
            directory.mkdir(parents=True, exist_ok=True)

        # Keep the test PATH isolated: brightnessctl is deliberately absent.
        commands = (
            "bash", "cat", "env", "id", "jq", "flock", "mktemp",
            "mv", "rm", "chmod", "mkdir", "cp", "sha256sum", "awk",
        )
        for name in commands:
            executable = shutil.which(name)
            if executable is None:
                self.skipTest(f"required test utility is missing: {name}")
            (mock_bin / name).symlink_to(executable)

        defaults = {
            "disabledPixels": 1,
            "monitor": 0,
            "area": None,
            "invert": False,
            "maskEnabled": mask_enabled,
            "pixelShift": pixel_shift_enabled,
            "pixelShiftIndex": pixel_shift_index,
            "phase": 0,
        }
        if pixel_shift_mode is not None:
            defaults["pixelShiftMode"] = pixel_shift_mode

        config = {
            "version": 1,
            "defaults": defaults,
            "brightness": {
                "enabled": True,
                "device": "test_backlight",
                "compensationPercentOfMaxByPixelCount": {
                    "1": 10,
                    "2": 20,
                    "3": 30,
                },
            },
        }
        (config_dir / "config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )

        shader_file = root / "current-shader"
        shader_file.write_text("", encoding="utf-8")

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
      jq -cn --arg str "$current_shader" '{str: $str, set: true}'
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

        env = os.environ.copy()
        env.update({
            "PATH": str(mock_bin),
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(state_home),
            "HYPROLED_TEST_SHADER_FILE": str(shader_file),
        })

        return env, runtime_dir / "hyproled" / "state.json"

    def run_hyproled(self, args, env):
        return subprocess.run(
            [str(HYPROLED), *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )

    def test_config_rejects_pixel_shift_index_outside_selected_mode(self):
        cases = (
            ("fixed", 1),
            ("linear-horizontal", 2),
            ("linear-vertical", 2),
            ("orbit-2x2", 4),
            # An omitted mode uses the effective default: orbit-2x2.
            (None, 4),
        )

        for mode, index in cases:
            with self.subTest(mode=mode, index=index):
                with tempfile.TemporaryDirectory() as directory:
                    env, state_file = self.create_environment(
                        Path(directory),
                        mask_enabled=False,
                        pixel_shift_enabled=True,
                        pixel_shift_mode=mode,
                        pixel_shift_index=index,
                    )

                    result = self.run_hyproled(["--pixel-shift"], env)

                    self.assertNotEqual(
                        result.returncode, 0, result.stdout + result.stderr
                    )
                    self.assertIn("defaults.pixelShiftIndex", result.stderr)
                    self.assertFalse(state_file.exists())

                    preferences_file = (
                        Path(env["XDG_STATE_HOME"])
                        / "hyproled"
                        / "preferences.json"
                    )
                    self.assertFalse(preferences_file.exists())

    def test_preferences_reject_pixel_shift_index_outside_selected_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            env, state_file = self.create_environment(
                Path(directory),
                mask_enabled=False,
                pixel_shift_enabled=True,
            )

            preferences_file = (
                Path(env["XDG_STATE_HOME"])
                / "hyproled"
                / "preferences.json"
            )
            preferences_file.parent.mkdir(parents=True, exist_ok=True)

            invalid_preferences = {
                "version": 1,
                "defaultsHash": "test-defaults-hash",
                "maskEnabled": False,
                "pixelShift": True,
                "pixelShiftMode": "fixed",
                "pixelShiftIndex": 1,
                "phase": 0,
                "offPixels": 1,
                "monitor": 0,
                "area": None,
                "invert": False,
                "brightnessCompensationEnabled": False,
            }
            original_preferences = json.dumps(invalid_preferences)
            preferences_file.write_text(
                original_preferences, encoding="utf-8"
            )

            result = self.run_hyproled(["--pixel-shift"], env)

            self.assertNotEqual(
                result.returncode, 0, result.stdout + result.stderr
            )
            self.assertIn("preferences file is invalid", result.stderr)
            self.assertFalse(state_file.exists())
            self.assertEqual(
                preferences_file.read_text(encoding="utf-8"),
                original_preferences,
            )

    def test_check_config_does_not_require_hyprland_or_write_runtime_state(self):
        with tempfile.TemporaryDirectory() as directory:
            env, state_file = self.create_environment(
                Path(directory),
                mask_enabled=True,
                pixel_shift_enabled=True,
            )

            hyprctl = Path(env["PATH"]) / "hyprctl"
            hyprctl.unlink()

            result = self.run_hyproled(["--check-config"], env)

            self.assertEqual(
                result.returncode, 0, result.stdout + result.stderr
            )
            self.assertIn("configuration is valid", result.stdout)
            self.assertIn(env["XDG_CONFIG_HOME"], result.stdout)

            preferences_file = (
                Path(env["XDG_STATE_HOME"])
                / "hyproled"
                / "preferences.json"
            )
            runtime_state_dir = Path(env["XDG_RUNTIME_DIR"]) / "hyproled"

            self.assertFalse(state_file.exists())
            self.assertFalse(preferences_file.exists())
            self.assertFalse(runtime_state_dir.exists())

    def test_check_config_reports_cross_field_errors_without_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            env, state_file = self.create_environment(
                Path(directory),
                mask_enabled=False,
                pixel_shift_enabled=True,
                pixel_shift_mode="fixed",
                pixel_shift_index=1,
            )

            hyprctl = Path(env["PATH"]) / "hyprctl"
            hyprctl.unlink()

            result = self.run_hyproled(["--check-config"], env)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("defaults.pixelShiftIndex", result.stderr)

            preferences_file = (
                Path(env["XDG_STATE_HOME"])
                / "hyproled"
                / "preferences.json"
            )
            runtime_state_dir = Path(env["XDG_RUNTIME_DIR"]) / "hyproled"

            self.assertFalse(state_file.exists())
            self.assertFalse(preferences_file.exists())
            self.assertFalse(runtime_state_dir.exists())

    def test_long_area_and_inversion_options_can_clear_saved_values(self):
        with tempfile.TemporaryDirectory() as directory:
            env, state_file = self.create_environment(
                Path(directory),
                mask_enabled=True,
                pixel_shift_enabled=False,
            )

            start = self.run_hyproled(
                [
                    "--area=10:20:300:40",
                    "--invert",
                    "--no-brightness-compensation",
                ],
                env,
            )
            self.assertEqual(
                start.returncode, 0, start.stdout + start.stderr
            )

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["area"], "10:20:300:40")
            self.assertTrue(state["invert"])

            update = self.run_hyproled(
                ["--full-screen", "--no-invert"],
                env,
            )
            self.assertEqual(
                update.returncode, 0, update.stdout + update.stderr
            )

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertIsNone(state["area"])
            self.assertFalse(state["invert"])

            preferences_file = (
                Path(env["XDG_STATE_HOME"])
                / "hyproled"
                / "preferences.json"
            )
            preferences = json.loads(
                preferences_file.read_text(encoding="utf-8")
            )
            self.assertIsNone(preferences["area"])
            self.assertFalse(preferences["invert"])

    def test_pixel_shift_only_can_start_and_advance_without_brightnessctl(self):
        with tempfile.TemporaryDirectory() as directory:
            env, state_file = self.create_environment(
                Path(directory),
                mask_enabled=False,
                pixel_shift_enabled=True,
            )

            start = self.run_hyproled(["--pixel-shift"], env)
            self.assertEqual(
                start.returncode, 0, start.stdout + start.stderr
            )

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertFalse(state["maskEnabled"])
            self.assertTrue(state["pixelShift"])
            self.assertFalse(state["brightnessEnabled"])

            advance = self.run_hyproled(["--shift-step"], env)
            self.assertEqual(
                advance.returncode, 0, advance.stdout + advance.stderr
            )

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["pixelShiftIndex"], 1)

    def test_mask_without_compensation_can_start_and_advance_without_brightnessctl(self):
        with tempfile.TemporaryDirectory() as directory:
            env, state_file = self.create_environment(
                Path(directory),
                mask_enabled=True,
                pixel_shift_enabled=False,
            )

            start = self.run_hyproled(
                ["--no-brightness-compensation"], env
            )
            self.assertEqual(
                start.returncode, 0, start.stdout + start.stderr
            )

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertTrue(state["maskEnabled"])
            self.assertFalse(state["brightnessEnabled"])

            advance = self.run_hyproled(["--mask-step"], env)
            self.assertEqual(
                advance.returncode, 0, advance.stdout + advance.stderr
            )

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["phase"], 1)

    def test_enabling_compensation_still_requires_brightnessctl(self):
        with tempfile.TemporaryDirectory() as directory:
            env, state_file = self.create_environment(
                Path(directory),
                mask_enabled=True,
                pixel_shift_enabled=False,
            )

            start = self.run_hyproled(
                ["--no-brightness-compensation"], env
            )
            self.assertEqual(
                start.returncode, 0, start.stdout + start.stderr
            )

            enable = self.run_hyproled(
                ["--brightness-compensation"], env
            )
            self.assertNotEqual(enable.returncode, 0)
            self.assertIn(
                "required command not found: brightnessctl",
                enable.stderr,
            )

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertFalse(state["brightnessEnabled"])


if __name__ == "__main__":
    unittest.main()
