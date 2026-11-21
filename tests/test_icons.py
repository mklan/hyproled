from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ICONS = ASSETS / "icons"
CSS = ASSETS / "waybar-icons.css"
HYPROLED = ROOT / "hyproled"
GENERATOR = ROOT / "scripts" / "generate-icons.py"
SVG_NS = "{http://www.w3.org/2000/svg}"

DIRECTION_CODES = {
    "north": "N", "north-east": "NE", "east": "E",
    "south-east": "SE", "south": "S", "south-west": "SW",
    "west": "W", "north-west": "NW",
}
DIRECTIONS = tuple(DIRECTION_CODES)
EXPECTED_SECTOR_PREFIX = {
    0: "M12 12 L12 21.2",
    1: "M12 12 L21.2 12",
    2: "M12 12 L2.8 12",
    3: "M12 12 L12 2.8",
}


def mask_from_positions(positions: set[int]) -> str:
    return f"{sum(1 << pos for pos in positions):04b}"


def shader_mask(phase: int, off_pixels: int) -> str:
    disabled = set()
    for pos in range(4):
        x, y = pos % 2, pos // 2
        fc_x, fc_y = x + 0.5, y + 0.5
        shader_pos = (fc_x % 2.0) + 2.0 * (fc_y % 2.0)
        if (shader_pos - phase) % 4.0 < off_pixels:
            disabled.add(pos)
    return mask_from_positions(disabled)


def active_masks() -> set[str]:
    return {
        shader_mask(phase, count)
        for phase in range(4)
        for count in (1, 2, 3)
    }


def icon_name(mask: str, direction: str = "center") -> str:
    suffix = "" if direction == "center" else f"-{DIRECTION_CODES[direction]}"
    return f"hyproled-{mask}{suffix}.svg"


def read_svg(name: str):
    return ET.parse(ICONS / name).getroot()


class IconGenerationTests(unittest.TestCase):
    def test_main_shader_formula_is_the_expected_source_of_truth(self):
        source = HYPROLED.read_text(encoding="utf-8")
        self.assertIn(
            "float pos = mod(fc.x, 2.0) + 2.0 * mod(fc.y, 2.0);",
            source,
        )
        self.assertIn(
            "bool off = mod(pos - float(${PHASE}), 4.0) < float(${OFF_PIXELS});",
            source,
        )

    def test_binary_bit_order(self):
        self.assertEqual(mask_from_positions({0}), "0001")
        self.assertEqual(mask_from_positions({0, 2}), "0101")
        self.assertEqual(mask_from_positions({3}), "1000")

    def test_exact_shader_table(self):
        expected = [
            ["1000", "1001", "1011"],
            ["0001", "0011", "0111"],
            ["0010", "0110", "1110"],
            ["0100", "1100", "1101"],
        ]
        actual = [
            [shader_mask(phase, count) for count in (1, 2, 3)]
            for phase in range(4)
        ]
        self.assertEqual(actual, expected)

    def test_exact_119_compact_icon_names(self):
        masks = active_masks() | {"0000"}
        expected = {"hyproled-off.svg", "hyproled-error.svg"}
        for mask in masks:
            expected.add(icon_name(mask))
            expected.update(icon_name(mask, direction) for direction in DIRECTIONS)
        actual = {path.name for path in ICONS.glob("hyproled-*.svg")}
        self.assertEqual(len(masks), 13)
        self.assertEqual(len(expected), 119)
        self.assertEqual(actual, expected)
        self.assertTrue((ICONS / "hyproled-1110.svg").is_file())
        self.assertTrue((ICONS / "hyproled-1110-SE.svg").is_file())
        self.assertTrue((ICONS / "hyproled-0000-SE.svg").is_file())
        self.assertFalse((ICONS / "hyproled-1110-C.svg").exists())
        self.assertFalse((ICONS / "hyproled-PS-SE.svg").exists())

    def test_center_icons_have_no_direction_ring_and_keep_original_mask(self):
        for mask in active_masks() | {"0000"}:
            root = read_svg(icon_name(mask))
            self.assertEqual(root.attrib.get("viewBox"), "0 0 28 28")
            groups = list(root.iter(f"{SVG_NS}g"))
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].attrib.get("transform"), "translate(2.6 2.6) scale(0.95)")
            paths = {
                element.attrib.get("id"): element
                for element in root.iter(f"{SVG_NS}path")
            }
            self.assertIn("outer-ring", paths)
            self.assertIn("grid-vertical", paths)
            self.assertIn("grid-horizontal", paths)
            self.assertNotIn("opacity", paths["outer-ring"].attrib)
            self.assertFalse(any(name.startswith("direction-") for name in paths))
            bits = int(mask, 2)
            for pos, prefix in EXPECTED_SECTOR_PREFIX.items():
                path = paths.get(f"pixel-{pos}")
                self.assertEqual(path is not None, bool(bits & (1 << pos)), (mask, pos))
                if path is not None:
                    self.assertTrue(path.attrib["d"].startswith(prefix), (mask, pos))

    def test_directional_icons_contain_only_the_selected_full_width_sector(self):
        for mask in active_masks() | {"0000"}:
            for selected in DIRECTIONS:
                root = read_svg(icon_name(mask, selected))
                self.assertEqual(root.attrib.get("viewBox"), "0 0 28 28")
                paths = {
                    element.attrib.get("id"): element
                    for element in root.iter(f"{SVG_NS}path")
                }
                self.assertIn("outer-ring", paths)
                direction_paths = {
                    name: path for name, path in paths.items()
                    if name.startswith("direction-")
                }
                self.assertEqual(set(direction_paths), {f"direction-{selected}"})
                path = direction_paths[f"direction-{selected}"]
                self.assertNotIn("opacity", path.attrib)
                # The outer radius stays fixed; angular coverage is exactly 45 degrees.
                self.assertIn("13.15 13.15", path.attrib["d"])
                self.assertIn("11.725 11.725", path.attrib["d"])
                # The original central symbol is slightly reduced around the same center.
                group = next(root.iter(f"{SVG_NS}g"))
                self.assertEqual(group.attrib.get("transform"), "translate(2.6 2.6) scale(0.95)")

    def test_generated_css_maps_every_icon_and_only_generated_icons(self):
        source = CSS.read_text(encoding="utf-8")
        pattern = re.compile(
            r'(?m)^#custom-hyproled(?P<classes>(?:\.[A-Za-z0-9-]+)+) \{\n'
            r'  background-image: url\("@HYPROLED_ICON_DIR@/(?P<file>hyproled-[^"]+\.svg)"\);\n\}'
        )
        matches = list(pattern.finditer(source))
        self.assertEqual(len(matches), 117)
        self.assertNotIn("-gtk-recolor", source)
        actual = {}
        for match in matches:
            classes = set(match.group("classes").split(".")[1:])
            masks = [value.removeprefix("mask-") for value in classes if value.startswith("mask-")]
            dirs = [value.removeprefix("pixel-shift-direction-") for value in classes if value.startswith("pixel-shift-direction-")]
            self.assertEqual(len(masks), 1, match.group(0))
            self.assertLessEqual(len(dirs), 1, match.group(0))
            direction = dirs[0] if dirs else "center"
            actual[match.group("file")] = (masks[0], direction)
        expected = {
            icon_name(mask, direction): (mask, direction)
            for mask in (active_masks() | {"0000"})
            for direction in ("center", *DIRECTIONS)
        }
        self.assertEqual(actual, expected)
        self.assertIn("Generated by scripts/generate-icons.py", source)

    def test_normal_icon_paths_keep_symbolic_foreground_palette_class(self):
        for mask in active_masks() | {"0000"}:
            for direction in ("center", *DIRECTIONS):
                root = read_svg(icon_name(mask, direction))
                for path in root.iter(f"{SVG_NS}path"):
                    self.assertEqual(path.attrib.get("class"), "success")

    def test_off_and_error_special_icons(self):
        off = read_svg("hyproled-off.svg")
        error = read_svg("hyproled-error.svg")
        self.assertEqual(off.attrib.get("viewBox"), "0 0 28 28")
        self.assertEqual(error.attrib.get("viewBox"), "0 0 28 28")
        off_paths = {e.attrib.get("id") for e in off.iter(f"{SVG_NS}path")}
        error_paths = {e.attrib.get("id") for e in error.iter(f"{SVG_NS}path")}
        self.assertEqual(off_paths, {"outer-ring", "grid-vertical", "grid-horizontal"})
        self.assertEqual(error_paths, {"outer-ring", "error-mark"})
        self.assertEqual({e.attrib.get("class") for e in off.iter(f"{SVG_NS}path")}, {"warning"})
        self.assertEqual({e.attrib.get("class") for e in error.iter(f"{SVG_NS}path")}, {"error"})

    def test_generator_check_mode(self):
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(shutil.which("jq"), "jq is required for hyproled --status tests")
class StatusMaskTests(unittest.TestCase):
    def test_status_mask_class_matches_shader_and_compact_icon_names(self):
        with tempfile.TemporaryDirectory() as runtime_dir:
            state_dir = Path(runtime_dir) / "hyproled"
            state_dir.mkdir()
            state_path = state_dir / "state.json"
            env = os.environ.copy()
            env["XDG_RUNTIME_DIR"] = runtime_dir
            state = {
                "version": 5, "status": "active", "phase": 0,
                "offPixels": 1, "monitor": 0, "area": None, "invert": False,
                "brightnessOriginal": 100, "brightnessMax": 528,
                "brightnessApplied": 110, "brightnessDevice": "intel_backlight",
                "activeShader": "/tmp/hyproled-test.glsl", "previousShader": "",
                "restoreTarget": None,
                "compensationPercent": 10, "brightnessEnabled": True,
                "brightnessCompensationEnabled": True,
                "pixelShift": False, "maskEnabled": True,
                "pixelShiftMode": "orbit-2x2", "pixelShiftIndex": 0,
                "compensationPercentOfMaxByPixelCount": {"1": 10, "2": 20, "3": 30},
            }
            for phase in range(4):
                for count in (1, 2, 3):
                    state["phase"], state["offPixels"] = phase, count
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                    result = subprocess.run(
                        [str(HYPROLED), "--status"], cwd=ROOT, env=env,
                        text=True, capture_output=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(result.stdout)
                    expected_mask = shader_mask(phase, count)
                    self.assertIn(f"mask-{expected_mask}", payload["class"])
                    self.assertIn("pixel-shift-direction-center", payload["class"])
                    self.assertTrue((ICONS / icon_name(expected_mask)).is_file())


if __name__ == "__main__":
    unittest.main()
