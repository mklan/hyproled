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


class CliParserTests(unittest.TestCase):
    def run_cli(self, args, *, runtime_dir=None):
        env = os.environ.copy()

        if runtime_dir is not None:
            env["XDG_RUNTIME_DIR"] = str(runtime_dir)

        return subprocess.run(
            [str(HYPROLED), *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )

    def assert_cli_error(self, args, expected_message):
        result = self.run_cli(args)

        self.assertNotEqual(
            result.returncode,
            0,
            f"Expected rejection for {args!r}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}",
        )
        self.assertIn(
            expected_message,
            result.stderr,
            f"Unexpected error for {args!r}\n"
            f"stderr:\n{result.stderr}",
        )

    def test_version_uses_version_file_outside_repository(self):
        expected_version = (
            ROOT / "version.txt"
        ).read_text(encoding="utf-8").strip()

        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [str(HYPROLED), "--version"],
                cwd=directory,
                env=os.environ.copy(),
                text=True,
                capture_output=True,
            )

        self.assertEqual(
            result.returncode,
            0,
            result.stdout + result.stderr,
        )
        self.assertEqual(
            result.stdout,
            f"hyproled {expected_version}\n",
        )

    def test_area_can_only_be_specified_once_across_short_and_long_forms(self):
        cases = (
            ("--area=1:2:3:4", "-a", "5:6:7:8"),
            ("-a", "1:2:3:4", "--area=5:6:7:8"),
            ("--area", "1:2:3:4", "--area=5:6:7:8"),
        )

        for args in cases:
            with self.subTest(args=args):
                self.assert_cli_error(
                    args,
                    "--area may only be specified once",
                )

    def test_monitor_can_only_be_specified_once_across_short_and_long_forms(self):
        cases = (
            ("--monitor=0", "-m", "1"),
            ("-m", "0", "--monitor=1"),
            ("--monitor", "0", "--monitor=1"),
        )

        for args in cases:
            with self.subTest(args=args):
                self.assert_cli_error(
                    args,
                    "--monitor may only be specified once",
                )

    def test_pixel_count_can_only_be_specified_once_across_all_aliases(self):
        cases = (
            ("--off-pixels=1", "-o", "2"),
            ("-o", "1", "--disabled-pixels", "2"),
            ("--disabled-pixels=1", "--off-pixels=2"),
            ("--off-pixels", "1", "--disabled-pixels=2"),
        )

        for args in cases:
            with self.subTest(args=args):
                self.assert_cli_error(
                    args,
                    "pixel count may only be specified once",
                )

    def test_full_screen_conflicts_with_area_in_both_orders(self):
        cases = (
            ("--full-screen", "--area=1:2:3:4"),
            ("--area=1:2:3:4", "--full-screen"),
            ("--full-screen", "-a", "1:2:3:4"),
            ("-a", "1:2:3:4", "--full-screen"),
        )

        for args in cases:
            with self.subTest(args=args):
                self.assert_cli_error(
                    args,
                    "--full-screen cannot be combined with --area",
                )

    def test_full_screen_can_only_be_specified_once(self):
        self.assert_cli_error(
            ("--full-screen", "--full-screen"),
            "--full-screen may only be specified once",
        )

    def test_unknown_long_option_is_rejected(self):
        self.assert_cli_error(
            ("--definitely-unknown-option",),
            "unknown option:",
        )

    def test_status_rejects_additional_actions(self):
        cases = (
            ("--status", "off"),
            ("off", "--status"),
        )

        for args in cases:
            with self.subTest(args=args):
                result = self.run_cli(args)

                self.assertNotEqual(
                    result.returncode,
                    0,
                    result.stdout + result.stderr,
                )
                self.assertTrue(
                    "does not accept additional arguments" in result.stderr,
                    result.stderr,
                )

    def test_status_rejects_effect_options(self):
        result = self.run_cli(("--status", "--mask"))

        self.assertNotEqual(
            result.returncode,
            0,
            result.stdout + result.stderr,
        )
        self.assertIn(
            "does not accept effect options",
            result.stderr,
        )

    @unittest.skipUnless(shutil.which("jq"), "jq is not installed")
    def test_status_is_read_only_and_does_not_require_hyprland(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_dir = root / "runtime"
            mock_bin = root / "bin"
            runtime_dir.mkdir()
            mock_bin.mkdir()

            hyprctl_called = root / "hyprctl-called"
            hyprctl = mock_bin / "hyprctl"
            hyprctl.write_text(
                "#!/bin/sh\n"
                f"touch {json.dumps(str(hyprctl_called))}\n"
                "exit 99\n",
                encoding="utf-8",
            )
            hyprctl.chmod(0o755)

            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = str(runtime_dir)
            env["PATH"] = (
                str(mock_bin)
                + os.pathsep
                + env.get("PATH", "")
            )

            result = subprocess.run(
                [str(HYPROLED), "--status"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stdout + result.stderr,
            )

            status = json.loads(result.stdout)

            self.assertEqual(status["alt"], "disabled")
            self.assertEqual(status["class"], ["disabled"])
            self.assertEqual(status["tooltip"], "Hyproled: disabled")

            self.assertFalse(hyprctl_called.exists())
            self.assertFalse((runtime_dir / "hyproled").exists())


if __name__ == "__main__":
    unittest.main()
