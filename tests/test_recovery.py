from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "hyproled").read_text(encoding="utf-8")


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


if __name__ == "__main__":
    unittest.main()
