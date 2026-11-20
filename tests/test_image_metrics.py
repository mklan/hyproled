from __future__ import annotations

import math
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import image_metrics as metrics

ROOT = Path(__file__).resolve().parents[1]


class ProductionShaderModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = metrics.SOURCE_PATH.read_text(encoding="utf-8")
        cls.images = metrics.load_fixtures()

    def test_image_fixtures_are_deterministic_and_checked_in(self):
        generator = ROOT / "tests" / "fixtures" / "generate_images.py"
        result = subprocess.run(
            [sys.executable, str(generator), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.images), 9)

    def test_production_shader_primitives_match_the_model(self):
        for fragment in (
            "vec2 fc = gl_FragCoord.xy;",
            "float pos = mod(fc.x, 2.0) + 2.0 * mod(fc.y, 2.0);",
            "bool off = mod(pos - float(${PHASE}), 4.0) < float(${OFF_PIXELS});",
            "ivec2 sourceTexel = texel - ivec2(${PIXEL_SHIFT_DX}, ${PIXEL_SHIFT_DY});",
            "texelFetch(tex, sourceTexel, 0)",
            "orig = vec4(0.0, 0.0, 0.0, 1.0);",
        ):
            self.assertIn(fragment, self.source)

    def test_gl_fragcoord_centres_define_the_exact_phase_to_pixel_mapping(self):
        # Coordinates are (x parity, y parity) in OpenGL's lower-left origin.
        expected_phase_for_position = {
            (1, 1): 0,
            (0, 0): 1,
            (1, 0): 2,
            (0, 1): 3,
        }
        for (x, y), expected_phase in expected_phase_for_position.items():
            selected = [
                phase for phase in range(4)
                if metrics.glsl_mask_is_off(x, y, phase, 1)
            ]
            self.assertEqual(selected, [expected_phase], ((x, y), selected))

    def test_every_mask_phase_turns_off_exactly_n_of_each_four_pixels(self):
        width = height = 16
        for off_pixels in (1, 2, 3):
            for y in range(height):
                for x in range(width):
                    off_count = sum(
                        metrics.glsl_mask_is_off(x, y, phase, off_pixels)
                        for phase in range(4)
                    )
                    self.assertEqual(off_count, off_pixels, (x, y, off_pixels))

    def test_status_mask_formula_accounts_for_pixel_centres(self):
        # emit_status() must map physical pixel parities to the same bit positions
        # used by tests/test_icons.py and the generated SVG sectors.
        self.assertIn("(($pos - $s.phase + 5) % 4) < $s.offPixels", self.source)

    def test_production_position_tables_are_complete_and_unique(self):
        expected = {
            "fixed": ((1, 0),),
            "linear-horizontal": ((0, 0), (1, 0)),
            "linear-vertical": ((0, 0), (0, 1)),
            "orbit-2x2": ((0, 0), (1, 0), (1, 1), (0, 1)),
            "orbit-3x3": (
                (0, 0), (1, 0), (1, 1), (0, 1), (-1, 1),
                (-1, 0), (-1, -1), (0, -1), (1, -1),
            ),
        }
        for mode, positions in expected.items():
            actual = metrics.production_mode_positions(mode)
            self.assertEqual(actual, positions, mode)
            self.assertEqual(len(set(actual)), len(actual), mode)

    def test_production_scheduler_covers_all_combined_states(self):
        for mode in metrics.MODE_NAMES:
            count = len(metrics.production_mode_positions(mode))
            schedule = metrics.production_combined_schedule(mode)
            expected_count = 4 if mode == "fixed" else 4 * count
            self.assertEqual(len(schedule), expected_count, mode)
            self.assertEqual(len(set(schedule)), expected_count, mode)
            self.assertEqual({phase for phase, _ in schedule}, set(range(4)), mode)
            self.assertEqual({index for _, index in schedule}, set(range(count)), mode)


class ExposureMetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.images = metrics.load_fixtures()

    def test_single_pixel_distribution_matches_theoretical_spatial_support(self):
        image = self.images["single-hot-pixel"]
        baseline = metrics.measure(image, metrics._state_sequence("baseline"), None)
        self.assertAlmostEqual(baseline["effective_pixels"], 1.0, places=10)

        expected_positions = {
            "fixed": 1,
            "linear-horizontal": 2,
            "linear-vertical": 2,
            "orbit-2x2": 4,
            "orbit-3x3": 9,
        }
        for mode, expected in expected_positions.items():
            shift = metrics.measure(image, metrics._state_sequence("pixel-shift-only", mode), None)
            self.assertAlmostEqual(shift["effective_pixels"], expected, places=10, msg=mode)
            for off_pixels in (1, 2, 3):
                combined = metrics.measure(
                    image,
                    metrics._state_sequence("combined", mode),
                    off_pixels,
                )
                self.assertAlmostEqual(
                    combined["effective_pixels"], expected, places=10,
                    msg=f"{mode}, OFF_PIXELS={off_pixels}",
                )
                self.assertAlmostEqual(
                    combined["mean_energy_retained"],
                    1.0 - off_pixels / 4.0,
                    places=10,
                    msg=f"{mode}, OFF_PIXELS={off_pixels}",
                )

    def test_mask_reduces_signal_but_does_not_spatially_shift_a_fixed_pixel(self):
        image = self.images["single-hot-pixel"]
        for off_pixels in (1, 2, 3):
            mask = metrics.measure(image, metrics._state_sequence("mask-only"), off_pixels)
            self.assertAlmostEqual(mask["effective_pixels"], 1.0, places=10)
            self.assertAlmostEqual(mask["mean_energy_retained"], 1.0 - off_pixels / 4.0, places=10)

    def test_linear_shift_is_more_effective_perpendicular_to_a_one_pixel_line(self):
        vertical = self.images["vertical-1px-line"]
        horizontal = self.images["horizontal-1px-line"]

        vertical_x = metrics.measure(vertical, metrics._state_sequence("pixel-shift-only", "linear-horizontal"), None)
        vertical_y = metrics.measure(vertical, metrics._state_sequence("pixel-shift-only", "linear-vertical"), None)
        horizontal_x = metrics.measure(horizontal, metrics._state_sequence("pixel-shift-only", "linear-horizontal"), None)
        horizontal_y = metrics.measure(horizontal, metrics._state_sequence("pixel-shift-only", "linear-vertical"), None)

        self.assertGreater(vertical_x["effective_pixels"], vertical_y["effective_pixels"])
        self.assertGreater(horizontal_y["effective_pixels"], horizontal_x["effective_pixels"])
        self.assertLess(vertical_x["peak_to_source_peak"], vertical_y["peak_to_source_peak"])
        self.assertLess(horizontal_y["peak_to_source_peak"], horizontal_x["peak_to_source_peak"])

    def test_pixel_shift_does_not_reduce_uniform_region_interior_exposure(self):
        image = self.images["uniform-rectangle"]
        for mode in metrics.MODE_NAMES:
            shifted = metrics.measure(image, metrics._state_sequence("pixel-shift-only", mode), None)
            self.assertAlmostEqual(shifted["peak_to_source_peak"], 1.0, places=10, msg=mode)
            for off_pixels in (1, 2, 3):
                combined = metrics.measure(image, metrics._state_sequence("combined", mode), off_pixels)
                self.assertAlmostEqual(
                    combined["peak_to_source_peak"],
                    1.0 - off_pixels / 4.0,
                    places=10,
                    msg=f"{mode}, OFF_PIXELS={off_pixels}",
                )

    def test_mask_attenuation_is_content_independent_for_every_fixture_and_mode(self):
        for image_name, image in self.images.items():
            mask_only = metrics._state_sequence("mask-only")
            for off_pixels in (1, 2, 3):
                mask_metrics = metrics.measure(image, mask_only, off_pixels)
                self.assertAlmostEqual(
                    mask_metrics["mask_energy_retained"],
                    1.0 - off_pixels / 4.0,
                    places=10,
                    msg=f"{image_name}, mask only, OFF_PIXELS={off_pixels}",
                )
                for mode in metrics.MODE_NAMES:
                    combined = metrics.measure(
                        image,
                        metrics._state_sequence("combined", mode),
                        off_pixels,
                    )
                    self.assertAlmostEqual(
                        combined["mask_energy_retained"],
                        1.0 - off_pixels / 4.0,
                        places=10,
                        msg=f"{image_name}, {mode}, OFF_PIXELS={off_pixels}",
                    )
                    self.assertLessEqual(combined["mean_energy_retained"], 1.0 + 1e-10)
                    self.assertLessEqual(combined["peak_to_source_peak"], 1.0 + 1e-10)

    def test_black_edge_sampling_loss_is_measured_separately_from_mask_attenuation(self):
        image = self.images["edge-markers"]
        for mode in metrics.MODE_NAMES:
            shifted = metrics.measure(image, metrics._state_sequence("pixel-shift-only", mode), None)
            self.assertGreater(shifted["edge_loss_fraction"], 0.0, mode)
            for off_pixels in (1, 2, 3):
                combined = metrics.measure(image, metrics._state_sequence("combined", mode), off_pixels)
                self.assertAlmostEqual(
                    combined["mask_energy_retained"],
                    1.0 - off_pixels / 4.0,
                    places=10,
                    msg=f"{mode}, OFF_PIXELS={off_pixels}",
                )
                self.assertAlmostEqual(
                    combined["edge_loss_fraction"],
                    shifted["edge_loss_fraction"],
                    places=10,
                    msg=f"{mode}, OFF_PIXELS={off_pixels}",
                )

    def test_source_revision_is_optional_when_git_is_unavailable(self):
        with patch(
            "image_metrics.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            self.assertIsNone(metrics._source_revision())

    def test_all_fixture_scenarios_produce_finite_nonnegative_metrics(self):
        report = metrics.build_report()
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(set(report["fixtures"]), set(self.images))

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if isinstance(item, (int, float)) and not isinstance(item, bool):
                        self.assertTrue(math.isfinite(item), key)
                        if key not in ("mean_energy_retained", "shift_energy_retained", "mask_energy_retained"):
                            self.assertGreaterEqual(item, 0.0, key)
                    else:
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk(report["fixtures"])
        for name, image_data in report["fixtures"].items():
            self.assertEqual(set(image_data["mask_only"]), {"1", "2", "3"}, name)
            self.assertEqual(set(image_data["pixel_shift_only"]), set(metrics.MODE_NAMES), name)
            self.assertEqual(set(image_data["combined"]), set(metrics.MODE_NAMES), name)


if __name__ == "__main__":
    unittest.main()
