from __future__ import annotations

import fcntl
import json
import os
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HYPROLED = ROOT / "hyproled"
SOURCE = HYPROLED.read_text(encoding="utf-8")


@unittest.skipUnless(
    shutil.which("jq") and shutil.which("flock"),
    "jq and flock are required",
)
class FilesystemFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.functions = {}

        for name in ("save_preferences", "save_state", "on_exit"):
            pattern = (
                r"(?ms)^"
                + name
                + r"\(\) \{\n.*?^\}"
            )
            matches = list(__import__("re").finditer(pattern, SOURCE))

            if len(matches) != 1:
                raise AssertionError(
                    f"Expected one {name}() function, found {len(matches)}"
                )

            cls.functions[name] = matches[0].group(0)

        lock_start = (
            '[[ -d "$RUNTIME_DIR" ]] || '
            'die "XDG_RUNTIME_DIR does not exist: $RUNTIME_DIR"'
        )
        if SOURCE.count(lock_start) != 1:
            raise AssertionError("Runtime lock setup anchor is ambiguous")

        start = SOURCE.index(lock_start)
        lock_end = SOURCE.index("\nflock 9", start) + len("\nflock 9")
        cls.lock_block = SOURCE[start:lock_end]

    def persistence_script(self, root, functions, action, hooks=""):
        state_dir = root / "runtime" / "hyproled"
        preferences_dir = root / "state" / "hyproled"

        state_dir.mkdir(parents=True, exist_ok=True)

        def quote(value):
            return shlex.quote(os.fspath(value))
        variables = "\n".join((
            f"STATE_DIR={quote(state_dir)}",
            f"STATE_FILE={quote(state_dir / 'state.json')}",
            f"PREFERENCES_DIR={quote(preferences_dir)}",
            f"PREFERENCES_FILE={quote(preferences_dir / 'preferences.json')}",
            'TEMP_STATE_FILE=""',
            'TEMP_PREFERENCES_FILE=""',
            'STATE_BACKUP=""',
            'NEW_SHADER=""',
            'KEEP_NEW_SHADER=false',
            'ROLLBACK_ENABLED=false',
            'OP_COMMITTED=false',
            'ROLLBACK_SHADER=""',
            'ROLLBACK_BRIGHTNESS=0',
            'BRIGHTNESS_WRITE_ATTEMPTED=false',
            'DEFAULTS_HASH="test-defaults-hash"',
            'PIXEL_SHIFT_MODE="orbit-2x2"',
            'AREA=""',
            'MASK_ENABLED=true',
            'PIXEL_SHIFT=false',
            'PIXEL_SHIFT_INDEX=0',
            'PHASE=0',
            'OFF_PIXELS=1',
            'MONITOR=0',
            'INVERT=false',
            'BRIGHTNESS_COMPENSATION_ENABLED=false',
            'STATUS="active"',
            'BRIGHTNESS_DEVICE="test_backlight"',
            'PREVIOUS_SHADER=""',
            'ACTIVE_SHADER="/tmp/hyproled-test.glsl"',
            'RESTORE_TARGET=""',
            'BRIGHTNESS_ORIGINAL=0',
            'BRIGHTNESS_MAX=1',
            'BRIGHTNESS_APPLIED=0',
            'COMPENSATION_PERCENT=0',
            'BRIGHTNESS_ENABLED=false',
            'COMPENSATION_MAP=\'{"1":10,"2":20,"3":30}\'',
        ))

        function_text = "\n\n".join(
            self.functions[name] for name in functions
        )

        return "\n".join((
            "set -Eeuo pipefail",
            "umask 077",
            variables,
            function_text,
            hooks,
            "trap on_exit EXIT",
            "trap 'exit 143' TERM",
            action,
            "",
        ))

    def run_shell(self, script, env=None):
        return subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )

    def temporary_preferences(self, directory):
        target = directory / "preferences.json"
        return [
            path for path in directory.glob("preferences.*")
            if path != target
        ]

    def temporary_state(self, directory):
        target = directory / "state.json"
        return [
            path for path in directory.glob("state.*")
            if path != target
        ]

    def test_preferences_file_and_directory_have_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preferences_dir = root / "state" / "hyproled"
            preferences_file = preferences_dir / "preferences.json"

            script = self.persistence_script(
                root,
                ("save_preferences", "on_exit"),
                "save_preferences",
            )
            result = self.run_shell(script)

            self.assertEqual(
                result.returncode, 0, result.stdout + result.stderr
            )
            self.assertTrue(preferences_file.is_file())
            self.assertEqual(
                stat.S_IMODE(preferences_dir.stat().st_mode), 0o700
            )
            self.assertEqual(
                stat.S_IMODE(preferences_file.stat().st_mode), 0o600
            )
            self.assertEqual(
                json.loads(preferences_file.read_text(encoding="utf-8"))["version"],
                1,
            )
            self.assertEqual(self.temporary_preferences(preferences_dir), [])

    def test_failed_preferences_rename_preserves_old_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preferences_dir = root / "state" / "hyproled"
            preferences_dir.mkdir(parents=True)
            preferences_file = preferences_dir / "preferences.json"
            preferences_file.write_text(
                "previous-preferences", encoding="utf-8"
            )

            script = self.persistence_script(
                root,
                ("save_preferences", "on_exit"),
                "save_preferences || exit 17",
                hooks="mv() { return 1; }",
            )
            result = self.run_shell(script)

            self.assertEqual(
                result.returncode, 17, result.stdout + result.stderr
            )
            self.assertEqual(
                preferences_file.read_text(encoding="utf-8"),
                "previous-preferences",
            )
            self.assertEqual(self.temporary_preferences(preferences_dir), [])

    def test_preferences_temporary_file_is_removed_on_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preferences_dir = root / "state" / "hyproled"
            started = root / "jq-started"
            release = root / "jq-release"

            script = self.persistence_script(
                root,
                ("save_preferences", "on_exit"),
                "save_preferences",
                hooks=(
                    "jq() {\n"
                    f"  : > {shlex.quote(str(started))}\n"
                    f"  while [[ ! -e {shlex.quote(str(release))} ]]; do\n"
                    "    sleep 0.02\n"
                    "  done\n"
                    "  printf '{}'\n"
                    "}\n"
                ),
            )

            process = subprocess.Popen(
                ["bash", "-c", script],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            try:
                deadline = time.monotonic() + 5
                while (
                    not started.exists()
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)

                self.assertTrue(
                    started.exists(),
                    "The preferences write did not reach the jq stage",
                )
                self.assertTrue(self.temporary_preferences(preferences_dir))

                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)

                self.assertEqual(
                    process.returncode,
                    143,
                    stdout + stderr,
                )
                self.assertEqual(self.temporary_preferences(preferences_dir), [])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)

    def test_failed_state_rename_preserves_old_file_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "runtime" / "hyproled"
            state_dir.mkdir(parents=True, exist_ok=True)
            state_file = state_dir / "state.json"
            state_file.write_text("previous-state", encoding="utf-8")

            hooks = (
                "save_preferences() { return 0; }\n"
                "mv() { return 1; }"
            )
            script = self.persistence_script(
                root,
                ("save_state", "on_exit"),
                "save_state || exit 17",
                hooks=hooks,
            )
            result = self.run_shell(script)

            self.assertEqual(
                result.returncode, 17, result.stdout + result.stderr
            )
            self.assertEqual(
                state_file.read_text(encoding="utf-8"),
                "previous-state",
            )
            self.assertEqual(self.temporary_state(state_dir), [])

    def test_successful_state_replacement_is_private_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "runtime" / "hyproled"
            state_file = state_dir / "state.json"

            script = self.persistence_script(
                root,
                ("save_state", "on_exit"),
                "save_state",
                hooks="save_preferences() { return 0; }",
            )
            result = self.run_shell(script)

            self.assertEqual(
                result.returncode, 0, result.stdout + result.stderr
            )
            state = json.loads(state_file.read_text(encoding="utf-8"))

            self.assertEqual(state["version"], 5)
            self.assertEqual(
                stat.S_IMODE(state_file.stat().st_mode), 0o600
            )
            self.assertEqual(self.temporary_state(state_dir), [])

    def test_runtime_lock_waits_until_previous_operation_releases_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_dir = root / "runtime"
            state_dir = runtime_dir / "hyproled"
            mock_bin = root / "bin"

            state_dir.mkdir(parents=True)
            mock_bin.mkdir()

            lock_file = state_dir / "lock"
            lock_file.touch()

            lock_fd = os.open(lock_file, os.O_RDWR)
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            real_flock = shutil.which("flock")
            self.assertIsNotNone(real_flock)

            attempted = root / "flock-attempted"
            acquired = root / "lock-acquired"

            wrapper = mock_bin / "flock"
            wrapper.write_text(
                "#!/usr/bin/env bash\n"
                ': > "$FLOCK_ATTEMPTED"\n'
                'exec "$REAL_FLOCK" "$@"\n',
                encoding="utf-8",
            )
            wrapper.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "PATH": str(mock_bin) + os.pathsep + env.get("PATH", ""),
                "REAL_FLOCK": real_flock,
                "FLOCK_ATTEMPTED": str(attempted),
                "ACQUIRED_FILE": str(acquired),
            })

            script = "\n".join((
                "set -Eeuo pipefail",
                "umask 077",
                f"RUNTIME_DIR={shlex.quote(str(runtime_dir))}",
                f"STATE_DIR={shlex.quote(str(state_dir))}",
                f"LOCK_FILE={shlex.quote(str(lock_file))}",
                'die() { printf "%s\\n" "$*" >&2; exit 1; }',
                self.lock_block,
                ': > "$ACQUIRED_FILE"',
                "",
            ))

            process = subprocess.Popen(
                ["bash", "-c", script],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            try:
                deadline = time.monotonic() + 5
                while (
                    not attempted.exists()
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)

                self.assertTrue(
                    attempted.exists(),
                    "The process did not reach the flock command",
                )

                time.sleep(0.05)
                self.assertIsNone(
                    process.poll(),
                    "The second operation bypassed the held runtime lock",
                )
                self.assertFalse(acquired.exists())
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(
                process.returncode, 0, stdout + stderr
            )
            self.assertTrue(acquired.exists())


if __name__ == "__main__":
    unittest.main()
