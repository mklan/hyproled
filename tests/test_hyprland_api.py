from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HYPROLED = ROOT / "hyproled"
SOURCE = HYPROLED.read_text(encoding="utf-8")


@unittest.skipUnless(shutil.which("jq"), "jq is required")
class HyprlandApiCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.functions = {}

        for name in (
            "setup_hyprland",
            "get_current_shader",
            "lua_long_string",
            "set_shader",
        ):
            pattern = (
                r"(?ms)^"
                + re.escape(name)
                + r"\(\) \{\n.*?^\}"
            )
            match = re.search(pattern, SOURCE)

            if match is None:
                raise AssertionError(f"{name}() was not found")

            cls.functions[name] = match.group(0)

    MOCK_HYPRCTL = r"""
hyprctl() {
  printf '%s\n' "${1:-}" >> "$COMMAND_LOG"

  case "${1:-}" in
    version)
      printf '%s\n' "$TEST_VERSION"
      ;;

    eval)
      if [[ -z "${2:-}" ]]; then
        printf '%s' "$TEST_EVAL_PROBE"
        return "$TEST_EVAL_STATUS"
      fi

      if [[ "$TEST_PROVIDER" != "lua" ]]; then
        printf 'eval is only supported with the lua config manager'
        return 1
      fi

      if [[ "$TEST_FAIL_EVAL_SET" == true ]]; then
        printf 'injected eval setter failure'
        return 1
      fi

      printf '%s' "$2" > "$EVAL_EXPRESSION_FILE"

      if [[ "$TEST_SKIP_EVAL_WRITE" != true ]]; then
        printf '%s' "$EXPECTED_SHADER" > "$SHADER_FILE"
      fi

      printf 'ok'
      ;;

    keyword)
      if [[ "$TEST_PROVIDER" != "legacy" ||
            "${2:-}" != "decoration:screen_shader" ]]; then
        printf 'keyword API unavailable'
        return 1
      fi

      if [[ "$TEST_FAIL_KEYWORD_SET" == true ]]; then
        printf 'injected keyword setter failure'
        return 1
      fi

      printf '%s' "${3:-}" > "$SHADER_FILE"
      printf 'ok'
      ;;

    -j)
      [[ "${2:-}" == "getoption" ]] || {
        printf 'unsupported query'
        return 1
      }

      option="${3:-}"
      printf '%s\n' "$option" >> "$OPTION_LOG"
      accepted=false

      case "$TEST_OPTION_SUPPORT" in
        legacy)
          [[ "$option" == "decoration:screen_shader" ]] && accepted=true
          ;;
        lua-alias)
          if [[ "$option" == "decoration:screen_shader" ||
                "$option" == "decoration.screen_shader" ]]; then
            accepted=true
          fi
          ;;
        dot-only)
          [[ "$option" == "decoration.screen_shader" ]] && accepted=true
          ;;
      esac

      if [[ "$accepted" != true ]]; then
        printf 'no such option'
        return 0
      fi

      jq -cn \
        --arg value "$(cat "$SHADER_FILE")" \
        --argjson set "$TEST_SHADER_SET" \
        '{str: $value, set: $set}'
      ;;

    *)
      printf 'unsupported hyprctl command'
      return 1
      ;;
  esac
}
"""

    def run_case(
        self,
        *,
        version,
        probe,
        provider,
        option_support,
        probe_status=0,
        fail_eval_set=False,
        fail_keyword_set=False,
        skip_eval_write=False,
        initial_shader="",
        shader_set=True,
        read_only=False,
    ):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            runtime_dir = temp / "runtime"
            runtime_dir.mkdir()

            shader_file = temp / "current-shader"
            shader_file.write_text(initial_shader, encoding="utf-8")

            command_log = temp / "commands.log"
            option_log = temp / "options.log"
            eval_file = temp / "eval-expression"
            expected_shader = temp / "shader with spaces.glsl"

            env = os.environ.copy()
            env.update({
                "TEST_RUNTIME_DIR": str(runtime_dir),
                "HYPRLAND_INSTANCE_SIGNATURE": "hyproled-test-instance",
                "TEST_VERSION": version,
                "TEST_EVAL_PROBE": probe,
                "TEST_EVAL_STATUS": str(probe_status),
                "TEST_PROVIDER": provider,
                "TEST_OPTION_SUPPORT": option_support,
                "TEST_FAIL_EVAL_SET": str(fail_eval_set).lower(),
                "TEST_FAIL_KEYWORD_SET": str(fail_keyword_set).lower(),
                "TEST_SKIP_EVAL_WRITE": str(skip_eval_write).lower(),
                "TEST_SHADER_SET": str(shader_set).lower(),
                "EXPECTED_SHADER": str(expected_shader),
                "SHADER_FILE": str(shader_file),
                "COMMAND_LOG": str(command_log),
                "OPTION_LOG": str(option_log),
                "EVAL_EXPRESSION_FILE": str(eval_file),
            })

            parts = [
                "set -Eeuo pipefail",
                'RUNTIME_DIR="$TEST_RUNTIME_DIR"',
                'die() { printf "hyproled: %s\\n" "$*" >&2; exit 1; }',
                self.MOCK_HYPRCTL,
                self.functions["setup_hyprland"],
                self.functions["get_current_shader"],
                self.functions["lua_long_string"],
                self.functions["set_shader"],
                "setup_hyprland",
            ]

            if read_only:
                parts.extend((
                    'current_shader=$(get_current_shader)',
                    'printf "USE_EVAL=%s\\n" "$USE_EVAL"',
                    'printf "READBACK=<%s>\\n" "$current_shader"',
                ))
            else:
                parts.extend((
                    'if ! set_shader "$EXPECTED_SHADER"; then exit 42; fi',
                    'printf "USE_EVAL=%s\\n" "$USE_EVAL"',
                ))

            result = subprocess.run(
                ["bash", "-c", "\n\n".join(parts) + "\n"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )

            def read_lines(path):
                return (
                    path.read_text(encoding="utf-8").splitlines()
                    if path.exists() else []
                )

            return {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "commands": read_lines(command_log),
                "option_queries": read_lines(option_log),
                "eval_expression": (
                    eval_file.read_text(encoding="utf-8")
                    if eval_file.exists() else ""
                ),
                "shader": shader_file.read_text(encoding="utf-8"),
                "expected_shader": str(expected_shader),
            }

    def assert_success(self, result, use_eval):
        self.assertEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        self.assertIn(
            f"USE_EVAL={'true' if use_eval else 'false'}",
            result["stdout"],
        )
        self.assertEqual(result["shader"], result["expected_shader"])

    def test_versions_before_lua_transition_use_legacy_keyword(self):
        for version in ("Hyprland 0.53.3", "Hyprland 0.54.3"):
            with self.subTest(version=version):
                result = self.run_case(
                    version=version,
                    probe="unused",
                    provider="legacy",
                    option_support="legacy",
                )

                self.assert_success(result, use_eval=False)
                self.assertNotIn("eval", result["commands"])
                self.assertIn("keyword", result["commands"])

    def test_transitional_versions_can_use_legacy_provider(self):
        legacy_error = (
            "eval is only supported with the lua config manager"
        )

        for version in ("Hyprland 0.55.3", "Hyprland 0.56.2"):
            with self.subTest(version=version):
                result = self.run_case(
                    version=version,
                    probe=legacy_error,
                    provider="legacy",
                    option_support="legacy",
                )

                self.assert_success(result, use_eval=False)
                self.assertEqual(result["commands"].count("eval"), 1)
                self.assertIn("keyword", result["commands"])

    def test_transitional_versions_can_use_lua_provider(self):
        for version in ("Hyprland 0.55.3", "Hyprland 0.56.2"):
            with self.subTest(version=version):
                result = self.run_case(
                    version=version,
                    probe="ok",
                    provider="lua",
                    option_support="lua-alias",
                )

                self.assert_success(result, use_eval=True)
                self.assertIn("hl.config(", result["eval_expression"])
                self.assertNotIn("keyword", result["commands"])

    def test_0572_lua_provider_and_legacy_option_alias(self):
        result = self.run_case(
            version="Hyprland 0.57.2",
            probe="ok",
            provider="lua",
            option_support="lua-alias",
        )

        self.assert_success(result, use_eval=True)
        # 0.57.2 accepts the legacy colon spelling and normalizes it.
        self.assertEqual(
            result["option_queries"],
            ["decoration:screen_shader"],
        )

    def test_dot_option_fallback_is_preserved(self):
        result = self.run_case(
            version="Hyprland 0.57.2",
            probe="ok",
            provider="lua",
            option_support="dot-only",
        )

        self.assert_success(result, use_eval=True)
        self.assertEqual(
            result["option_queries"],
            [
                "decoration:screen_shader",
                "decoration.screen_shader",
            ],
        )

    def test_unknown_eval_response_fails_closed(self):
        result = self.run_case(
            version="Hyprland 0.57.2",
            probe="unexpected compositor response",
            provider="lua",
            option_support="lua-alias",
        )

        self.assertEqual(result["returncode"], 1, result["stderr"])
        self.assertIn(
            "cannot determine the active Hyprland config API",
            result["stderr"],
        )
        self.assertEqual(result["commands"], ["version", "eval"])

    def test_ok_probe_with_nonzero_exit_status_fails_closed(self):
        result = self.run_case(
            version="Hyprland 0.57.2",
            probe="ok",
            probe_status=1,
            provider="lua",
            option_support="lua-alias",
        )

        self.assertEqual(result["returncode"], 1, result["stderr"])
        self.assertIn(
            "cannot determine the active Hyprland config API",
            result["stderr"],
        )
        self.assertEqual(result["commands"], ["version", "eval"])

    def test_unparseable_version_fails_closed(self):
        result = self.run_case(
            version="Hyprland development build",
            probe="ok",
            provider="lua",
            option_support="lua-alias",
        )

        self.assertEqual(result["returncode"], 1, result["stderr"])
        self.assertIn(
            "cannot parse the running Hyprland version",
            result["stderr"],
        )
        self.assertEqual(result["commands"], ["version"])

    def test_lua_setter_failure_is_reported(self):
        result = self.run_case(
            version="Hyprland 0.57.2",
            probe="ok",
            provider="lua",
            option_support="lua-alias",
            fail_eval_set=True,
        )

        self.assertEqual(result["returncode"], 42, result["stderr"])
        self.assertIn("hyprctl eval failed", result["stderr"])
        self.assertIn("injected eval setter failure", result["stderr"])

    def test_legacy_setter_failure_is_reported(self):
        result = self.run_case(
            version="Hyprland 0.53.3",
            probe="unused",
            provider="legacy",
            option_support="legacy",
            fail_keyword_set=True,
        )

        self.assertEqual(result["returncode"], 42, result["stderr"])
        self.assertIn("hyprctl keyword failed", result["stderr"])
        self.assertIn("injected keyword setter failure", result["stderr"])

    def test_success_response_is_verified_by_readback(self):
        result = self.run_case(
            version="Hyprland 0.57.2",
            probe="ok",
            provider="lua",
            option_support="lua-alias",
            skip_eval_write=True,
            initial_shader="/tmp/previous-shader.glsl",
        )

        self.assertEqual(result["returncode"], 42, result["stderr"])
        self.assertIn(
            "Hyprland did not apply the requested shader",
            result["stderr"],
        )
        self.assertIn("/tmp/previous-shader.glsl", result["stderr"])

    def test_unset_shader_option_is_read_as_empty(self):
        result = self.run_case(
            version="Hyprland 0.57.2",
            probe="ok",
            provider="lua",
            option_support="lua-alias",
            shader_set=False,
            read_only=True,
        )

        self.assertEqual(
            result["returncode"],
            0,
            result["stdout"] + result["stderr"],
        )
        self.assertIn("READBACK=<>", result["stdout"])


if __name__ == "__main__":
    unittest.main()
