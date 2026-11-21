from __future__ import annotations

import json
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


class CompositeShaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        match = re.search(
            r"(?ms)^make_shader\(\) \{\n.*?^\}",
            SOURCE,
        )
        if match is None:
            raise AssertionError("make_shader() was not found")
        cls.function = match.group(0)

    def generate(self, mask, shift, path, dx=1, dy=0, phase=0, off_pixels=1):
        script = (
            self.function
            + "\nMONITOR=0\n"
            + f"PHASE={phase}\n"
            + f"OFF_PIXELS={off_pixels}\n"
            + f"MASK_ENABLED={'true' if mask else 'false'}\n"
            + f"PIXEL_SHIFT={'true' if shift else 'false'}\n"
            + f"PIXEL_SHIFT_DX={dx}\n"
            + f"PIXEL_SHIFT_DY={dy}\n"
            + 'AREA=""\n'
            + "INVERT=false\n"
            + 'make_shader "$1"\n'
        )

        result = subprocess.run(
            ["bash", "-c", script, "hyproled-test", str(path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return path.read_text(encoding="utf-8")

    def test_mask_only(self):
        with tempfile.TemporaryDirectory() as directory:
            shader = self.generate(
                True, False, Path(directory) / "mask.frag"
            )

        self.assertIn("vec4 orig = texture(tex, v_texcoord);", shader)
        self.assertIn("bool off =", shader)
        self.assertNotIn("texelFetch(tex, sourceTexel, 0)", shader)

    def test_pixel_shift_only(self):
        with tempfile.TemporaryDirectory() as directory:
            shader = self.generate(
                False, True, Path(directory) / "shift.frag"
            )

        self.assertIn("texelFetch(tex, sourceTexel, 0)", shader)
        self.assertIn("sourceTexel.x < 0", shader)
        self.assertIn("sourceTexel.y < 0", shader)
        self.assertIn("orig = vec4(0.0, 0.0, 0.0, 1.0);", shader)
        self.assertNotIn("bool off =", shader)
        self.assertIn("fragColor = orig;", shader)

    def test_combined_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            shader = self.generate(
                True, True, Path(directory) / "combined.frag"
            )

        self.assertIn("texelFetch(tex, sourceTexel, 0)", shader)
        self.assertIn("bool off =", shader)

    def test_all_mask_phases_and_pixel_shift_positions_generate(self):
        modes = {
            "fixed": [(1, 0)],
            "linear-horizontal": [(0, 0), (1, 0)],
            "linear-vertical": [(0, 0), (0, 1)],
            "orbit-2x2": [(0, 0), (1, 0), (1, 1), (0, 1)],
            "orbit-3x3": [
                (0, 0), (1, 0), (1, 1), (0, 1), (-1, 1),
                (-1, 0), (-1, -1), (0, -1), (1, -1),
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            for phase in range(4):
                for off_pixels in range(1, 4):
                    for mode, offsets in modes.items():
                        for index, (dx, dy) in enumerate(offsets):
                            with self.subTest(
                                phase=phase,
                                off_pixels=off_pixels,
                                mode=mode,
                                index=index,
                            ):
                                path = Path(directory) / (
                                    f"{mode}-{index}-phase-{phase}-pixels-{off_pixels}.frag"
                                )
                                shader = self.generate(
                                    True,
                                    True,
                                    path,
                                    dx=dx,
                                    dy=dy,
                                    phase=phase,
                                    off_pixels=off_pixels,
                                )

                                self.assertIn(
                                    f"mod(pos - float({phase}), 4.0) < float({off_pixels});",
                                    shader,
                                )
                                if dx == 0 and dy == 0:
                                    self.assertIn(
                                        "vec4 orig = texture(tex, v_texcoord);",
                                        shader,
                                    )
                                    self.assertNotIn("texelFetch", shader)
                                else:
                                    self.assertIn(
                                        f"ivec2 sourceTexel = texel - ivec2({dx}, {dy});",
                                        shader,
                                    )

    def test_mask_uses_output_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            shader = self.generate(
                True, True, Path(directory) / "combined.frag"
            )

        self.assertIn("vec2 fc = gl_FragCoord.xy;", shader)
        self.assertIn(
            "float pos = mod(fc.x, 2.0) + 2.0 * mod(fc.y, 2.0);",
            shader,
        )

    def test_fixed_shift_uses_one_physical_texel(self):
        with tempfile.TemporaryDirectory() as directory:
            shader = self.generate(
                False, True, Path(directory) / "shift.frag", dx=1, dy=0
            )

        self.assertIn("textureSize(tex, 0)", shader)
        self.assertIn(
            "ivec2 sourceTexel = texel - ivec2(1, 0);",
            shader,
        )

    def test_pixel_shift_remains_full_monitor_when_mask_area_is_restricted(self):
        for invert in (False, True):
            with self.subTest(invert=invert), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"area-invert-{invert}.frag"
                script = (
                    self.function
                    + "\nMONITOR=2\n"
                    + "PHASE=0\n"
                    + "OFF_PIXELS=1\n"
                    + "MASK_ENABLED=true\n"
                    + "PIXEL_SHIFT=true\n"
                    + "PIXEL_SHIFT_DX=1\n"
                    + "PIXEL_SHIFT_DY=-1\n"
                    + 'AREA="10:20:300:40"\n'
                    + f"INVERT={'true' if invert else 'false'}\n"
                    + 'make_shader "$1"\n'
                )

                result = subprocess.run(
                    ["bash", "-c", script, "hyproled-test", str(path)],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(
                    result.returncode, 0, result.stdout + result.stderr
                )

                shader = path.read_text(encoding="utf-8")
                self.assertIn("if(wl_output != 2) {", shader)

                pass_through = shader.index(
                    "fragColor = texture(tex, v_texcoord);"
                )
                pass_return = shader.index("return;", pass_through)
                shift_sampling = shader.index(
                    "ivec2 sourceTexel = texel - ivec2(1, -1);"
                )
                mask_region = shader.index("bool inRegion = ")

                self.assertLess(pass_through, pass_return)
                self.assertLess(pass_return, shift_sampling)
                self.assertLess(shift_sampling, mask_region)

                expected_output = (
                    "fragColor = inRegion ? orig : pixel;"
                    if invert
                    else "fragColor = inRegion ? pixel : orig;"
                )
                self.assertIn(expected_output, shader)
                self.assertIn("bool off =", shader)

    def test_zero_offset_keeps_the_original_sampling_path(self):
        with tempfile.TemporaryDirectory() as directory:
            shader = self.generate(
                False, True, Path(directory) / "zero.frag", dx=0, dy=0
            )

        self.assertIn("vec4 orig = texture(tex, v_texcoord);", shader)
        self.assertNotIn("texelFetch", shader)

    def test_cli_help_version_aliases_and_action_conflicts(self):
        for option in ("-h", "--help"):
            result = subprocess.run(
                ["bash", str(HYPROLED), option],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for documented in (
                "--area",
                "--full-screen",
                "--invert",
                "--no-invert",
                "--off-pixels",
                "--step",
                "--monitor",
                "--pixel-shift",
                "--no-pixel-shift",
                "--pixel-shift-mode",
                "--mask-step",
                "--shift-step",
                "--check-config",
                "--status",
                "--version",
            ):
                self.assertIn(documented, result.stdout)

        version = subprocess.run(
            ["bash", str(HYPROLED), "--version", "--unknown-option"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertIn(f"hyproled {self.version_from_file()}", version.stdout)

        # A consumed value must not hide a later short option.
        parser_regression = subprocess.run(
            ["bash", str(HYPROLED), "--pixel-shift-mode", "orbit-3x3", "-z"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertNotEqual(parser_regression.returncode, 0)
        self.assertIn("unknown option: -z", parser_regression.stderr)

        # The option terminator must prevent action and option parsing.
        for arguments in (["--", "off"], ["--", "--status"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    ["bash", str(HYPROLED), *arguments],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "unexpected argument after --",
                    result.stderr,
                )

        invalid_options = [
            (["--pixel-shift-mode", "unknown"], "invalid Pixel Shift mode"),
            (["--area=invalid"], "invalid area"),
            (["--off-pixels=4"], "invalid pixel count"),
            (["--monitor=invalid"], "invalid monitor ID"),
            (["--unknown-option"], "unknown option: --unknown-option"),
        ]
        for arguments, expected_error in invalid_options:
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    ["bash", str(HYPROLED), *arguments],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)

        repeated_value_options = [
            (["--area=0:0:10:10", "--area=1:1:20:20"],
             "--area may only be specified once"),
            (["--area", "0:0:10:10", "-a", "1:1:20:20"],
             "--area may only be specified once"),
            (["-a", "0:0:10:10", "--area=1:1:20:20"],
             "--area may only be specified once"),
            (["--monitor=0", "--monitor=1"],
             "--monitor may only be specified once"),
            (["--monitor", "0", "-m", "1"],
             "--monitor may only be specified once"),
            (["-m", "0", "--monitor=1"],
             "--monitor may only be specified once"),
            (["--off-pixels=1", "--off-pixels=2"],
             "pixel count may only be specified once"),
            (["-o", "1", "-o", "2"],
             "pixel count may only be specified once"),
            (["--off-pixels=1", "--disabled-pixels=2"],
             "pixel count may only be specified once"),
            (["--disabled-pixels=2", "--off-pixels=1"],
             "pixel count may only be specified once"),
            (["-o", "1", "--disabled-pixels=2"],
             "pixel count may only be specified once"),
            (["--disabled-pixels=2", "-o", "1"],
             "pixel count may only be specified once"),
        ]
        for arguments, expected_error in repeated_value_options:
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    ["bash", str(HYPROLED), *arguments],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_error, result.stderr)

        conflicting_options = [
            ["-s", "--shift-step"],
            ["-s", "--mask-step"],
            ["--mask-step", "--shift-step"],
            ["--mask-step", "--pixel-shift"],
            ["--step", "--shift-step"],
            ["--status", "--mask-step"],
            ["--status", "--monitor=0"],
            ["--status", "--invert"],
            ["off", "--area=0:0:10:10"],
            ["off", "--no-invert"],
            ["--invert", "--no-invert"],
            ["--no-invert", "-i"],
            ["--area=0:0:10:10", "--full-screen"],
            ["--full-screen", "--area=0:0:10:10"],
            ["--check-config", "--no-invert"],
            ["--check-config", "--area=0:0:10:10"],
        ]
        for arguments in conflicting_options:
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    ["bash", str(HYPROLED), *arguments],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)

        status_conflict = subprocess.run(
            ["bash", str(HYPROLED), "--status", "--invert"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(status_conflict.returncode, 0)
        self.assertIn(
            "'status' does not accept effect options",
            status_conflict.stderr,
        )

    @staticmethod
    def version_from_file():
        version = (ROOT / "version.txt").read_text(encoding="utf-8").strip()
        if not version:
            raise AssertionError("version.txt is empty")
        return version

    def test_all_pixel_shift_positions_compile(self):
        validator = shutil.which("glslangValidator")
        if validator is None:
            self.skipTest("glslangValidator is not installed or not on PATH")
        modes = {
            "fixed": [(1, 0)],
            "linear-horizontal": [(0, 0), (1, 0)],
            "linear-vertical": [(0, 0), (0, 1)],
            "orbit-2x2": [(0, 0), (1, 0), (1, 1), (0, 1)],
            "orbit-3x3": [
                (0, 0), (1, 0), (1, 1), (0, 1), (-1, 1),
                (-1, 0), (-1, -1), (0, -1), (1, -1),
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            mask_path = Path(directory) / "mask-only.frag"
            self.generate(True, False, mask_path, dx=0, dy=0)
            cases = [(True, False, 0, 0, "mask-only")]
            for mode, offsets in modes.items():
                for index, (dx, dy) in enumerate(offsets):
                    cases.append((False, True, dx, dy, f"{mode}-{index}-shift"))
                    cases.append((True, True, dx, dy, f"{mode}-{index}-combined"))

            for mask, shift, dx, dy, name in cases:
                path = Path(directory) / f"{name}.frag"
                if name == "mask-only":
                    path = mask_path
                else:
                    self.generate(mask, shift, path, dx=dx, dy=dy)
                result = subprocess.run(
                    [validator, "-S", "frag", str(path)],
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    f"{name}: {result.stdout}{result.stderr}",
                )


class PixelShiftModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        functions = []
        for name in ("pixel_shift_mode_count", "pixel_shift_position",
                     "pixel_shift_direction", "select_pixel_shift_mode", "advance_pixel_shift",
                     "advance_active_effects"):
            match = re.search(rf"(?ms)^{name}\(\) \{{\n.*?^\}}", SOURCE)
            if match is None:
                raise AssertionError(f"{name}() was not found")
            functions.append(match.group(0))
        cls.functions = "\n\n".join(functions)

    def run_bash(self, command):
        result = subprocess.run(
            ["bash", "-c", self.functions + "\n" + command],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def test_mode_lengths_and_positions(self):
        expected = {
            "fixed": [(1, 0)],
            "linear-horizontal": [(0, 0), (1, 0)],
            "linear-vertical": [(0, 0), (0, 1)],
            "orbit-2x2": [(0, 0), (1, 0), (1, 1), (0, 1)],
            "orbit-3x3": [
                (0, 0), (1, 0), (1, 1), (0, 1), (-1, 1),
                (-1, 0), (-1, -1), (0, -1), (1, -1),
            ],
        }
        for mode, offsets in expected.items():
            count = int(self.run_bash(f'pixel_shift_mode_count "{mode}"'))
            self.assertEqual(count, len(offsets), mode)
            for index, offset in enumerate(offsets):
                actual = tuple(map(int, self.run_bash(
                    f'pixel_shift_position "{mode}" "{index}"'
                ).split()))
                self.assertEqual(actual, offset, f"{mode}[{index}]")

    def test_direction_mapping_uses_the_actual_physical_vector(self):
        expected = {
            "fixed": ["east"],
            "linear-horizontal": ["center", "east"],
            "linear-vertical": ["center", "south"],
            "orbit-2x2": ["center", "east", "south-east", "south"],
            "orbit-3x3": [
                "center", "east", "south-east", "south", "south-west",
                "west", "north-west", "north", "north-east",
            ],
        }

        for mode, directions in expected.items():
            for index, direction in enumerate(directions):
                actual = self.run_bash(
                    f'pixel_shift_direction "{mode}" "{index}"'
                )
                self.assertEqual(actual, direction, f"{mode}[{index}]")

    def test_new_sessions_default_to_orbit_2x2(self):
        self.assertRegex(SOURCE, r'(?m)^PIXEL_SHIFT_MODE="orbit-2x2"$')

    def test_mode_selection_preserves_or_resets_position(self):
        script = (
            self.functions
            + '\nPIXEL_SHIFT=true PIXEL_SHIFT_MODE=orbit-2x2 PIXEL_SHIFT_INDEX=2\n'
            + 'select_pixel_shift_mode orbit-2x2\nprintf "%s %s\\n" "$PIXEL_SHIFT_MODE" "$PIXEL_SHIFT_INDEX"\n'
            + 'select_pixel_shift_mode orbit-3x3\nprintf "%s %s\\n" "$PIXEL_SHIFT_MODE" "$PIXEL_SHIFT_INDEX"\n'
        )
        result = subprocess.run(["bash", "-c", script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["orbit-2x2 2", "orbit-3x3 0"])

    def test_shift_step_wraps_and_fixed_mode_has_no_next_position(self):
        script = (
            self.functions
            + '\nPIXEL_SHIFT_MODE=orbit-3x3 PIXEL_SHIFT_INDEX=8\n'
            + 'advance_pixel_shift\nprintf "%s\\n" "$PIXEL_SHIFT_INDEX"\n'
        )
        result = subprocess.run(["bash", "-c", script], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "0")

        fixed = subprocess.run(
            ["bash", "-c", self.functions + '\nPIXEL_SHIFT_MODE=fixed PIXEL_SHIFT_INDEX=0\nadvance_pixel_shift'],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(fixed.returncode, 2)

    def test_advance_active_effects(self):
        cases = [
            (
                "MASK_ENABLED=true PIXEL_SHIFT=false PHASE=3 PIXEL_SHIFT_MODE=orbit-2x2 PIXEL_SHIFT_INDEX=0",
                "0 0 0",
                "mask only",
            ),
            (
                "MASK_ENABLED=false PIXEL_SHIFT=true PHASE=2 PIXEL_SHIFT_MODE=orbit-3x3 PIXEL_SHIFT_INDEX=8",
                "2 0 0",
                "Pixel Shift only",
            ),
            (
                "MASK_ENABLED=true PIXEL_SHIFT=true PHASE=3 PIXEL_SHIFT_MODE=orbit-2x2 PIXEL_SHIFT_INDEX=3",
                "1 0 0",
                "combined effects",
            ),
            (
                "MASK_ENABLED=true PIXEL_SHIFT=true PHASE=2 PIXEL_SHIFT_MODE=fixed PIXEL_SHIFT_INDEX=0",
                "3 0 0",
                "fixed plus mask",
            ),
            (
                "MASK_ENABLED=false PIXEL_SHIFT=true PHASE=2 PIXEL_SHIFT_MODE=fixed PIXEL_SHIFT_INDEX=0",
                "2 0 2",
                "fixed only",
            ),
        ]

        for initial_state, expected, label in cases:
            with self.subTest(case=label):
                script = (
                    self.functions
                    + "\n" + initial_state + "\n"
                    + "status=0\n"
                    + "advance_active_effects || status=$?\n"
                    + 'printf "%s %s %s\\n" "$PHASE" "$PIXEL_SHIFT_INDEX" "$status"\n'
                )
                result = subprocess.run(
                    ["bash", "-c", script],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def run_combined_sequence(self, mode, phase=0, index=0):
        count = int(self.run_bash(f'pixel_shift_mode_count "{mode}"'))
        steps = 4 * count
        script = (
            self.functions
            + f"\nMASK_ENABLED=true PIXEL_SHIFT=true PHASE={phase} "
              f'PIXEL_SHIFT_MODE="{mode}" PIXEL_SHIFT_INDEX={index}\n'
            + f"for ((step=0; step<{steps}; step++)); do\n"
            + '  printf "%s %s\\n" "$PHASE" "$PIXEL_SHIFT_INDEX"\n'
            + "  previous_phase=$PHASE\n"
            + "  previous_index=$PIXEL_SHIFT_INDEX\n"
            + "  advance_active_effects || exit $?\n"
            + '  [[ "$PHASE" != "$previous_phase" ]] || exit 20\n'
            + '  [[ "$PIXEL_SHIFT_INDEX" != "$previous_index" ]] || exit 21\n'
            + "done\n"
        )
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return [tuple(map(int, line.split())) for line in result.stdout.splitlines()]

    def test_combined_sequence_covers_every_pair_from_every_initial_state(self):
        modes = {
            "linear-horizontal": 2,
            "linear-vertical": 2,
            "orbit-2x2": 4,
            "orbit-3x3": 9,
        }
        for mode, count in modes.items():
            steps = 4 * count
            script = (
                self.functions
                + f'\nPIXEL_SHIFT=true MASK_ENABLED=true PIXEL_SHIFT_MODE="{mode}"\n'
                + f"for ((initial_phase=0; initial_phase<4; initial_phase++)); do\n"
                + f"  for ((initial_index=0; initial_index<{count}; initial_index++)); do\n"
                + "    PHASE=$initial_phase\n"
                + "    PIXEL_SHIFT_INDEX=$initial_index\n"
                + "    unset seen\n"
                + "    declare -A seen=()\n"
                + f"    for ((step=0; step<{steps}; step++)); do\n"
                + '      key="$PHASE:$PIXEL_SHIFT_INDEX"\n'
                + '      [[ -z "${seen[$key]+x}" ]] || exit 30\n'
                + '      seen[$key]=1\n'
                + "      previous_phase=$PHASE\n"
                + "      previous_index=$PIXEL_SHIFT_INDEX\n"
                + "      advance_active_effects || exit $?\n"
                + '      [[ "$PHASE" != "$previous_phase" ]] || exit 31\n'
                + '      [[ "$PIXEL_SHIFT_INDEX" != "$previous_index" ]] || exit 32\n'
                + "    done\n"
                + '    [[ "$PHASE" == "$initial_phase" ]] || exit 33\n'
                + '    [[ "$PIXEL_SHIFT_INDEX" == "$initial_index" ]] || exit 34\n'
                + f'    [[ "${{#seen[@]}}" -eq {steps} ]] || exit 35\n'
                + "  done\n"
                + "done\n"
                + "printf 'ok\\n'\n"
            )
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, f"{mode}: {result.stdout}{result.stderr}")
            self.assertEqual(result.stdout.strip(), "ok", mode)

    def test_full_pair_distribution_balances_the_exact_mask_formula(self):
        # This is the same output-space formula emitted by make_shader().
        self.assertIn(
            "float pos = mod(fc.x, 2.0) + 2.0 * mod(fc.y, 2.0);",
            SOURCE,
        )
        self.assertIn(
            "bool off = mod(pos - float(${PHASE}), 4.0) < float(${OFF_PIXELS});",
            SOURCE,
        )

        modes = (
            "linear-horizontal",
            "linear-vertical",
            "orbit-2x2",
            "orbit-3x3",
        )
        for mode in modes:
            count = int(self.run_bash(f'pixel_shift_mode_count "{mode}"'))
            pairs = self.run_combined_sequence(mode)
            self.assertEqual(len(pairs), 4 * count, mode)
            self.assertEqual(len(set(pairs)), 4 * count, mode)

            # Read actual (dx,dy) values from the production Bash function;
            # do not duplicate or approximate the mode's displacement table.
            offsets = []
            for index in range(count):
                offset = self.run_bash(
                    f'pixel_shift_position "{mode}" "{index}"'
                )
                offsets.append(tuple(map(int, offset.split())))

            for off_pixels in (1, 2, 3):
                masked_counts = [0, 0, 0, 0]
                for phase, index in pairs:
                    dx, dy = offsets[index]
                    for source_y in range(2):
                        for source_x in range(2):
                            # A source texel q is sampled at output q + (dx,dy).
                            output_x = (source_x + dx) % 2
                            output_y = (source_y + dy) % 2
                            pos = output_x + 2 * output_y
                            # GLSL mod(pos - phase, 4.0) for integer residues.
                            is_off = ((pos - phase) % 4) < off_pixels
                            if is_off:
                                masked_counts[source_x + 2 * source_y] += 1

                expected = count * off_pixels
                self.assertEqual(
                    masked_counts,
                    [expected] * 4,
                    f"{mode}, OFF_PIXELS={off_pixels}",
                )

    def test_previous_lockstep_was_not_uniform_for_non_coprime_modes(self):
        # Regression evidence: the former +1/+1 lockstep only visited lcm(4,K)
        # pairs and gave different off-pixel counts to 2x2 source parities.
        cases = {
            "linear-horizontal": 2,
            "orbit-2x2": 4,
        }
        for mode, count in cases.items():
            offsets = [
                tuple(map(int, self.run_bash(
                    f'pixel_shift_position "{mode}" "{index}"'
                ).split()))
                for index in range(count)
            ]
            # lcm(4, K) is four for both of these non-coprime modes.
            period = 4
            pairs = [(step % 4, step % count) for step in range(period)]
            off_counts = [0, 0, 0, 0]
            for phase, index in pairs:
                dx, dy = offsets[index]
                for source_y in range(2):
                    for source_x in range(2):
                        output_x = (source_x + dx) % 2
                        output_y = (source_y + dy) % 2
                        pos = output_x + 2 * output_y
                        if ((pos - phase) % 4) < 1:
                            off_counts[source_x + 2 * source_y] += 1
            self.assertNotEqual(len(set(off_counts)), 1, mode)

    def test_advance_active_effects_does_not_partially_mutate_on_invalid_mode(self):
        script = (
            self.functions
            + "\nMASK_ENABLED=true PIXEL_SHIFT=true PHASE=3 PIXEL_SHIFT_MODE=invalid PIXEL_SHIFT_INDEX=2\n"
            + "status=0\nadvance_active_effects || status=$?\n"
            + 'printf "%s %s %s\\n" "$PHASE" "$PIXEL_SHIFT_INDEX" "$status"\n'
        )
        result = subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "3 2 1")

    def test_invalid_mode_position_is_rejected(self):
        result = subprocess.run(
            ["bash", "-c", self.functions + '\npixel_shift_position orbit-2x2 4'],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0)


@unittest.skipUnless(shutil.which("jq"), "jq is not installed")
class RuntimeStatusTests(unittest.TestCase):
    def create_state(self, version, mask_enabled=True, pixel_shift=False,
                     pixel_shift_mode="orbit-2x2", pixel_shift_index=0):
        state = {
            "version": version,
            "status": "active",
            "phase": 0,
            "offPixels": 1,
            "monitor": 0,
            "area": None,
            "invert": False,
            "brightnessOriginal": 100,
            "brightnessMax": 528,
            "brightnessApplied": 100,
            "brightnessDevice": "intel_backlight",
            "activeShader": "/tmp/hyproled-test.glsl",
            "previousShader": "",
            "restoreTarget": None,
            "compensationPercent": 0,
            "brightnessEnabled": False,
            "brightnessCompensationEnabled": False,
            "pixelShift": pixel_shift,
            "maskEnabled": mask_enabled,
            "pixelShiftMode": pixel_shift_mode,
            "pixelShiftIndex": pixel_shift_index,
            "compensationPercentOfMaxByPixelCount": {
                "1": 10, "2": 20, "3": 30
            },
        }

        return state

    def run_status(self, state, runtime_dir):
        state_dir = Path(runtime_dir) / "hyproled"
        state_dir.mkdir(exist_ok=True)
        (state_dir / "state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = str(runtime_dir)

        result = subprocess.run(
            [str(HYPROLED), "--status"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assert_brightness_disabled(self, payload):
        self.assertIn("brightness-disabled", payload["class"])
        self.assertNotIn("brightness-enabled", payload["class"])
        self.assertIn("Brightness compensation: disabled", payload["tooltip"])
        self.assertIn("Brightness: unchanged", payload["tooltip"])

    def test_status_without_runtime_state_reports_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = directory

            result = subprocess.run(
                [str(HYPROLED), "--status"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["alt"], "disabled")
            self.assertEqual(payload["class"], ["disabled"])
            self.assertFalse((Path(directory) / "hyproled").exists())

    def test_v5_pixel_shift_only(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.create_state(
                5, False, True, pixel_shift_index=1
            )
            payload = self.run_status(state, directory)

        self.assertIn("pixel-shift-enabled", payload["class"])
        self.assertIn("pixel-shift-direction-east", payload["class"])
        self.assertIn("mask-0000", payload["class"])
        self.assertEqual(
            [name for name in payload["class"] if name.startswith("mask-")],
            ["mask-0000"],
        )
        self.assertIn("Mask: disabled", payload["tooltip"])
        self.assertIn("Pixel shift: enabled", payload["tooltip"])
        self.assert_brightness_disabled(payload)

    def test_v5_mask_only(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.create_state(5, True, False)
            payload = self.run_status(state, directory)

        self.assertIn("mask-1000", payload["class"])
        self.assertIn("pixel-shift-disabled", payload["class"])
        self.assert_brightness_disabled(payload)

    def test_v5_combined(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.create_state(
                5, True, True, pixel_shift_index=1
            )
            payload = self.run_status(state, directory)

        self.assertIn("mask-1000", payload["class"])
        self.assertIn("pixel-shift-enabled", payload["class"])
        self.assertIn("pixel-shift-direction-east", payload["class"])
        self.assert_brightness_disabled(payload)

    def test_v5_orbit_3x3_position(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.create_state(5, True, True, "orbit-3x3", 8)
            payload = self.run_status(state, directory)

        self.assertIn("pixel-shift-mode-orbit-3x3", payload["class"])
        self.assertIn("pixel-shift-direction-north-east", payload["class"])
        self.assertIn("Pixel Shift mode: orbit-3x3", payload["tooltip"])
        self.assertIn("Pixel Shift position: 9/9", payload["tooltip"])

    def test_v5_invalid_position_is_reported_as_error(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.create_state(5, True, True, "orbit-2x2", 4)
            payload = self.run_status(state, directory)

        self.assertEqual(payload["alt"], "error")
        self.assertIn("error", payload["class"])

    def test_v5_integral_float_position_is_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.create_state(5, True, True, "orbit-3x3", 8)
            state["pixelShiftIndex"] = 8.0
            payload = self.run_status(state, directory)

        self.assertEqual(payload["alt"], "active")
        self.assertIn("pixel-shift-direction-north-east", payload["class"])
        self.assertIn("Pixel Shift position: 9/9", payload["tooltip"])

    def test_v5_disabled_pixel_shift_uses_center_direction(self):
        with tempfile.TemporaryDirectory() as directory:
            state = self.create_state(5, True, False, "orbit-3x3", 8)
            payload = self.run_status(state, directory)

        self.assertIn("pixel-shift-direction-center", payload["class"])
        self.assertNotIn("pixel-shift-direction-north-east", payload["class"])
        self.assertIn("Pixel Shift direction: center", payload["tooltip"])
        self.assertIn("Pixel shift: disabled", payload["tooltip"])

    def test_v5_rejects_malformed_runtime_fields(self):
        invalid_cases = (
            ("fractional offPixels", "offPixels", 1.5),
            ("fractional monitor", "monitor", 0.5),
            ("fractional brightness baseline", "brightnessOriginal", 100.5),
            ("fractional brightness maximum", "brightnessMax", 528.5),
            ("fractional applied brightness", "brightnessApplied", 100.5),
            ("fractional compensation", "compensationPercent", 0.5),
            ("malformed area", "area", "0:0:10:10;invalid"),
            (
                "malformed compensation map",
                "compensationPercentOfMaxByPixelCount",
                {"1": 10, "2": "20", "3": 30},
            ),
            ("unexpected restore target", "restoreTarget", 100),
        )

        for label, field, value in invalid_cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                state = self.create_state(5)
                state[field] = value
                payload = self.run_status(state, directory)
                self.assertEqual(payload["alt"], "error")
                self.assertIn("error", payload["class"])

        missing_area = self.create_state(5)
        missing_area.pop("area")
        with tempfile.TemporaryDirectory() as directory:
            payload = self.run_status(missing_area, directory)
        self.assertEqual(payload["alt"], "error")

        missing_restore_target = self.create_state(5)
        missing_restore_target.pop("restoreTarget")
        with tempfile.TemporaryDirectory() as directory:
            payload = self.run_status(missing_restore_target, directory)
        self.assertEqual(payload["alt"], "error")

        restoring_without_target = self.create_state(5)
        restoring_without_target["status"] = "restoring"
        with tempfile.TemporaryDirectory() as directory:
            payload = self.run_status(restoring_without_target, directory)
        self.assertEqual(payload["alt"], "error")

        restoring_with_fractional_target = self.create_state(5)
        restoring_with_fractional_target["status"] = "restoring"
        restoring_with_fractional_target["restoreTarget"] = 10.5
        with tempfile.TemporaryDirectory() as directory:
            payload = self.run_status(restoring_with_fractional_target, directory)
        self.assertEqual(payload["alt"], "error")

    def test_legacy_runtime_state_versions_are_rejected(self):
        for version in range(1, 5):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                state = self.create_state(5)
                state["version"] = version
                payload = self.run_status(state, directory)
                self.assertEqual(payload["alt"], "error")
                self.assertIn("error", payload["class"])


@unittest.skipUnless(shutil.which("jq"), "jq is not installed")
class RuntimeStateLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        match = re.search(
            r"(?ms)^validate_state_file\(\) \{\n.*?^\}",
            SOURCE,
        )
        if match is None:
            raise AssertionError("validate_state_file() was not found")
        cls.validate_state_function = match.group(0)

        match = re.search(
            r"(?ms)^load_state\(\) \{\n.*?^\}",
            SOURCE,
        )
        if match is None:
            raise AssertionError("load_state() was not found")
        cls.load_state_function = match.group(0)

        match = re.search(
            r"(?ms)^is_pixel_shift_only_session\(\) \{\n.*?^\}",
            SOURCE,
        )
        if match is None:
            raise AssertionError("is_pixel_shift_only_session() was not found")
        cls.predicate_function = match.group(0)

        match = re.search(
            r"(?ms)^save_state\(\) \{\n.*?^\}",
            SOURCE,
        )
        if match is None:
            raise AssertionError("save_state() was not found")
        cls.save_state_function = match.group(0)

    def run_load_state(self, state, *, expected_returncode=0):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")

            script = (
                'die() { printf "%s\\n" "$*" >&2; exit 1; }\n'
                'STATE_FILE="$1"\n'
                "COMPENSATION_MAP='{}'\n"
                + self.validate_state_function
                + "\n"
                + self.load_state_function
                + '\nload_state\nprintf "%s\\n" '
                  '"$MASK_ENABLED" "$PIXEL_SHIFT" "$BRIGHTNESS_ENABLED" '
                  '"$PIXEL_SHIFT_MODE" "$PIXEL_SHIFT_INDEX"\n'
            )

            result = subprocess.run(
                ["bash", "-c", script, "hyproled-test", str(state_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, expected_returncode, result.stdout + result.stderr)
        return result.stdout.splitlines()

    def run_validate_state(self, state):
        return self.run_validate_state_document(json.dumps(state))

    def run_validate_state_document(self, document):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(document, encoding="utf-8")

            script = "\n".join((
                'STATE_FILE="$1"',
                self.validate_state_function,
                'validate_state_file "$STATE_FILE"',
            )) + "\n"

            result = subprocess.run(
                ["bash", "-c", script, "hyproled-test", str(state_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
        return result.returncode

    def run_pixel_shift_only_predicate(self, state):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")

            script = (
                'STATE_FILE="$1"\n'
                + self.predicate_function
                + '\nis_pixel_shift_only_session\n'
            )

            result = subprocess.run(
                ["bash", "-c", script, "hyproled-test", str(state_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

        return result.returncode == 0

    def test_v5_preserves_explicit_false_values(self):
        state = RuntimeStatusTests().create_state(
            5, False, True, "orbit-2x2", 0
        )

        self.assertEqual(
            self.run_load_state(state),
            ["false", "true", "false", "orbit-2x2", "0"],
        )

    def test_v5_preserves_selected_mode_when_pixel_shift_is_disabled(self):
        state = RuntimeStatusTests().create_state(
            5, False, False, "orbit-3x3", 7
        )

        self.assertEqual(
            self.run_load_state(state),
            ["false", "false", "false", "orbit-3x3", "7"],
        )

    def test_legacy_runtime_state_versions_are_rejected(self):
        for version in range(1, 5):
            with self.subTest(version=version):
                state = RuntimeStatusTests().create_state(5)
                state["version"] = version

                self.assertEqual(
                    self.run_load_state(state, expected_returncode=1),
                    [],
                )

    def test_v5_loads_selected_mode_and_position(self):
        state = RuntimeStatusTests().create_state(
            5, False, True, "orbit-3x3", 7
        )
        self.assertEqual(
            self.run_load_state(state),
            ["false", "true", "false", "orbit-3x3", "7"],
        )

    def test_v5_rejects_fractional_phase(self):
        state = RuntimeStatusTests().create_state(5)
        state["phase"] = 1.5

        self.assertEqual(
            self.run_load_state(state, expected_returncode=1),
            [],
        )

    def test_v5_load_state_rejects_malformed_fields(self):
        invalid_cases = (
            ("fractional offPixels", "offPixels", 1.5),
            ("fractional monitor", "monitor", 0.5),
            ("fractional brightness baseline", "brightnessOriginal", 100.5),
            ("fractional brightness maximum", "brightnessMax", 528.5),
            ("fractional applied brightness", "brightnessApplied", 100.5),
            ("fractional compensation", "compensationPercent", 0.5),
            ("malformed area", "area", "0:0:10:10;invalid"),
        )

        for label, field, value in invalid_cases:
            with self.subTest(case=label):
                state = RuntimeStatusTests().create_state(5)
                state[field] = value
                self.assertEqual(
                    self.run_load_state(state, expected_returncode=1),
                    [],
                )

        missing_area = RuntimeStatusTests().create_state(5)
        missing_area.pop("area")
        self.assertEqual(
            self.run_load_state(missing_area, expected_returncode=1),
            [],
        )

        restoring_without_target = RuntimeStatusTests().create_state(5)
        restoring_without_target["status"] = "restoring"
        self.assertEqual(
            self.run_load_state(restoring_without_target, expected_returncode=1),
            [],
        )

        restoring_with_fractional_target = RuntimeStatusTests().create_state(5)
        restoring_with_fractional_target["status"] = "restoring"
        restoring_with_fractional_target["restoreTarget"] = 10.5
        self.assertEqual(
            self.run_load_state(
                restoring_with_fractional_target,
                expected_returncode=1,
            ),
            [],
        )

    def test_v5_load_state_accepts_valid_restore_target(self):
        state = RuntimeStatusTests().create_state(5)
        state["status"] = "restoring"
        state["restoreTarget"] = 110

        self.assertEqual(
            self.run_load_state(state),
            ["true", "false", "false", "orbit-2x2", "0"],
        )

    def test_validate_state_file_accepts_boundaries_and_recovery_states(self):
        base = RuntimeStatusTests().create_state(5)

        valid_fields = (
            ("phase minimum", {"phase": 0}),
            ("phase maximum", {"phase": 3}),
            ("pixel count minimum", {"offPixels": 1}),
            ("pixel count maximum", {"offPixels": 3}),
            ("monitor minimum", {"monitor": 0}),
            ("large monitor ID", {"monitor": 2147483647}),
            ("null area", {"area": None}),
            ("valid area", {"area": "0:0:1:1"}),
            ("zero brightness baseline", {"brightnessOriginal": 0}),
            ("minimum brightness maximum", {"brightnessMax": 1}),
            ("zero applied brightness", {"brightnessApplied": 0}),
            ("empty previous shader", {"previousShader": ""}),
            ("minimum compensation", {"compensationPercent": 0}),
            ("maximum compensation", {"compensationPercent": 1000}),
            (
                "minimum compensation map",
                {"compensationPercentOfMaxByPixelCount": {
                    "1": 0, "2": 0, "3": 0,
                }},
            ),
            (
                "maximum compensation map",
                {"compensationPercentOfMaxByPixelCount": {
                    "1": 1000, "2": 1000, "3": 1000,
                }},
            ),
            (
                "boolean false values",
                {
                    "invert": False,
                    "pixelShift": False,
                    "maskEnabled": False,
                    "brightnessCompensationEnabled": False,
                    "brightnessEnabled": False,
                },
            ),
            ("unknown properties", {"futureStateField": {"value": True}}),
        )

        for label, updates in valid_fields:
            with self.subTest(case=label):
                state = base.copy()
                state.update(updates)
                self.assertEqual(self.run_validate_state(state), 0)

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
                    state = base.copy()
                    state["pixelShiftMode"] = mode
                    state["pixelShiftIndex"] = index
                    self.assertEqual(self.run_validate_state(state), 0)

        # restoreTarget is conditional on the state transition.
        valid_status_cases = (
            (
                "active without brightness ownership",
                {"status": "active", "restoreTarget": None,
                 "brightnessEnabled": False},
            ),
            (
                "active with brightness ownership",
                {"status": "active", "restoreTarget": None,
                 "brightnessEnabled": True},
            ),
            (
                "applying before ownership",
                {"status": "applying", "restoreTarget": None,
                 "brightnessEnabled": False},
            ),
            (
                "applying after ownership",
                {"status": "applying", "restoreTarget": 0,
                 "brightnessEnabled": True},
            ),
            (
                "restoring with brightness ownership",
                {"status": "restoring", "restoreTarget": 0,
                 "brightnessEnabled": True},
            ),
            (
                "restoring without brightness ownership flag",
                {"status": "restoring", "restoreTarget": 0,
                 "brightnessEnabled": False},
            ),
        )

        for label, updates in valid_status_cases:
            with self.subTest(case=label):
                state = base.copy()
                state.update(updates)
                self.assertEqual(self.run_validate_state(state), 0)

    def test_validate_state_file_rejects_invalid_fields_and_missing_keys(self):
        base = RuntimeStatusTests().create_state(5)
        invalid_cases = (
            ("version", 4),
            ("version", "5"),
            ("version", True),
            ("status", "idle"),
            ("status", None),
            ("phase", -1),
            ("phase", 4),
            ("phase", 1.5),
            ("phase", "1"),
            ("phase", None),
            ("offPixels", 0),
            ("offPixels", 4),
            ("offPixels", 1.5),
            ("offPixels", "1"),
            ("offPixels", None),
            ("monitor", -1),
            ("monitor", 0.5),
            ("monitor", "0"),
            ("monitor", None),
            ("area", "0:0:10"),
            ("area", ""),
            ("area", 42),
            ("invert", 0),
            ("invert", "false"),
            ("invert", None),
            ("brightnessOriginal", -1),
            ("brightnessOriginal", 1.5),
            ("brightnessOriginal", "100"),
            ("brightnessMax", 0),
            ("brightnessMax", -1),
            ("brightnessMax", 1.5),
            ("brightnessMax", "528"),
            ("brightnessApplied", -1),
            ("brightnessApplied", 1.5),
            ("brightnessApplied", "100"),
            ("brightnessDevice", ""),
            ("brightnessDevice", None),
            ("brightnessDevice", 42),
            ("activeShader", ""),
            ("activeShader", None),
            ("activeShader", False),
            ("previousShader", None),
            ("previousShader", False),
            ("compensationPercent", -1),
            ("compensationPercent", 1001),
            ("compensationPercent", 0.5),
            ("compensationPercent", "10"),
            ("restoreTarget", 100),
            ("brightnessEnabled", 0),
            ("brightnessEnabled", None),
            ("brightnessCompensationEnabled", 0),
            ("brightnessCompensationEnabled", "false"),
            ("compensationPercentOfMaxByPixelCount", None),
            ("compensationPercentOfMaxByPixelCount", {}),
            (
                "compensationPercentOfMaxByPixelCount",
                {"1": 10, "2": "20", "3": 30},
            ),
            (
                "compensationPercentOfMaxByPixelCount",
                {"1": 10, "2": 20, "3": 1001},
            ),
            (
                "compensationPercentOfMaxByPixelCount",
                {"1": 10, "2": 20.5, "3": 30},
            ),
            ("pixelShift", 0),
            ("pixelShift", "true"),
            ("pixelShift", None),
            ("maskEnabled", 0),
            ("maskEnabled", "false"),
            ("maskEnabled", None),
            ("pixelShiftMode", "unknown"),
            ("pixelShiftMode", None),
            ("pixelShiftMode", 2),
            ("pixelShiftIndex", -1),
            ("pixelShiftIndex", 9),
            ("pixelShiftIndex", 1.5),
            ("pixelShiftIndex", "1"),
            ("pixelShiftIndex", None),
        )
        for field, value in invalid_cases:
            with self.subTest(field=field, value=value):
                state = base.copy()
                state[field] = value
                self.assertNotEqual(self.run_validate_state(state), 0)

        missing_fields = (
            "version",
            "status",
            "phase",
            "offPixels",
            "monitor",
            "area",
            "invert",
            "brightnessOriginal",
            "brightnessMax",
            "brightnessApplied",
            "brightnessDevice",
            "activeShader",
            "previousShader",
            "compensationPercent",
            "restoreTarget",
            "brightnessEnabled",
            "brightnessCompensationEnabled",
            "compensationPercentOfMaxByPixelCount",
            "pixelShift",
            "maskEnabled",
            "pixelShiftMode",
            "pixelShiftIndex",
        )
        for field in missing_fields:
            with self.subTest(missing=field):
                state = base.copy()
                del state[field]
                self.assertNotEqual(self.run_validate_state(state), 0)

        applying_without_brightness_ownership = base.copy()
        applying_without_brightness_ownership.update({
            "status": "applying",
            "restoreTarget": 100,
            "brightnessEnabled": False,
        })
        self.assertNotEqual(
            self.run_validate_state(applying_without_brightness_ownership),
            0,
        )

        restoring_without_target = base.copy()
        restoring_without_target["status"] = "restoring"
        self.assertNotEqual(self.run_validate_state(restoring_without_target), 0)

        invalid_restore_cases = (
            (
                "applying target without brightness ownership",
                {"status": "applying", "restoreTarget": 0,
                 "brightnessEnabled": False},
            ),
            (
                "applying negative target",
                {"status": "applying", "restoreTarget": -1,
                 "brightnessEnabled": True},
            ),
            (
                "applying fractional target",
                {"status": "applying", "restoreTarget": 10.5,
                 "brightnessEnabled": True},
            ),
            (
                "applying string target",
                {"status": "applying", "restoreTarget": "10",
                 "brightnessEnabled": True},
            ),
            (
                "restoring negative target",
                {"status": "restoring", "restoreTarget": -1},
            ),
            (
                "restoring fractional target",
                {"status": "restoring", "restoreTarget": 10.5},
            ),
            (
                "restoring string target",
                {"status": "restoring", "restoreTarget": "10"},
            ),
            (
                "restoring boolean target",
                {"status": "restoring", "restoreTarget": True},
            ),
        )

        for label, updates in invalid_restore_cases:
            with self.subTest(case=label):
                state = base.copy()
                state.update(updates)
                self.assertNotEqual(self.run_validate_state(state), 0)

        invalid_mode_indices = (
            ("fixed", 1),
            ("linear-horizontal", 2),
            ("linear-vertical", 2),
            ("orbit-2x2", 4),
            ("orbit-3x3", 9),
        )

        for mode, index in invalid_mode_indices:
            with self.subTest(mode=mode, index=index):
                state = base.copy()
                state["pixelShiftMode"] = mode
                state["pixelShiftIndex"] = index
                self.assertNotEqual(self.run_validate_state(state), 0)

    def test_validate_state_file_rejects_invalid_documents(self):
        invalid_documents = (
            "",
            "{",
            "null",
            "[]",
            "42",
            '"runtime state"',
        )

        for document in invalid_documents:
            with self.subTest(document=document):
                self.assertNotEqual(
                    self.run_validate_state_document(document),
                    0,
                )

    def test_save_state_persists_v5_mode_and_index(self):
        with tempfile.TemporaryDirectory() as directory:
            script = (
                'STATE_DIR="$1"\n'
                'STATE_FILE="$STATE_DIR/state.json"\n'
                'TEMP_STATE_FILE=""\n'
                'STATUS="active" AREA="" BRIGHTNESS_DEVICE="intel_backlight"\n'
                'PREVIOUS_SHADER="" ACTIVE_SHADER="/tmp/hyproled-test.glsl" RESTORE_TARGET=""\n'
                'PIXEL_SHIFT_MODE="orbit-3x3" PIXEL_SHIFT_INDEX=6\n'
                'PHASE=0 OFF_PIXELS=1 MONITOR=0 INVERT=false MASK_ENABLED=false PIXEL_SHIFT=true\n'
                'BRIGHTNESS_ORIGINAL=0 BRIGHTNESS_MAX=1 BRIGHTNESS_APPLIED=0\n'
                'COMPENSATION_PERCENT=0 BRIGHTNESS_ENABLED=false BRIGHTNESS_COMPENSATION_ENABLED=false COMPENSATION_MAP="{}"\n'
                + 'save_preferences() { return 0; }\n'
                + self.save_state_function
                + '\nsave_state && cat "$STATE_FILE"\n'
            )
            result = subprocess.run(
                ["bash", "-c", script, "hyproled-test", directory],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state["version"], 5)
        self.assertEqual(state["pixelShiftMode"], "orbit-3x3")
        self.assertEqual(state["pixelShiftIndex"], 6)

    def test_pixel_shift_only_predicate(self):
        state = RuntimeStatusTests().create_state(5, False, True)
        self.assertTrue(self.run_pixel_shift_only_predicate(state))

        state["maskEnabled"] = True
        self.assertFalse(self.run_pixel_shift_only_predicate(state))

        legacy_state = RuntimeStatusTests().create_state(5, False, True)
        legacy_state["version"] = 4
        self.assertFalse(self.run_pixel_shift_only_predicate(legacy_state))

if __name__ == "__main__":
    unittest.main()
