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
    def create_environment(self, root, mask_enabled, pixel_shift_enabled):
        runtime_dir = root / "runtime"
        config_dir = root / "config" / "hyproled"
        state_home = root / "state"
        mock_bin = root / "bin"

        for directory in (runtime_dir, config_dir, state_home, mock_bin):
            directory.mkdir(parents=True, exist_ok=True)

        # Keep the test PATH isolated: brightnessctl is deliberately absent.
        commands = (
            "bash", "env", "id", "jq", "flock", "mktemp",
            "mv", "rm", "chmod", "mkdir", "cp", "sha256sum", "awk",
        )
        for name in commands:
            executable = shutil.which(name)
            if executable is None:
                self.skipTest(f"required test utility is missing: {name}")
            (mock_bin / name).symlink_to(executable)

        config = {
            "version": 1,
            "defaults": {
                "disabledPixels": 1,
                "monitor": 0,
                "area": None,
                "invert": False,
                "maskEnabled": mask_enabled,
                "pixelShift": pixel_shift_enabled,
                "pixelShiftMode": "orbit-2x2",
                "pixelShiftIndex": 0,
                "phase": 0,
            },
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
