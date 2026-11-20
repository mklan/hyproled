#!/usr/bin/env python3
"""Image-based exposure analysis for the production Hyproled algorithms.

No third-party Python packages are required. The model reads the production
Bash mode-position and transition functions, models the exact gl_FragCoord
pixel-centre expression, and uses deterministic P2/PGM fixtures.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "hyproled"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "images"
MODE_NAMES = (
    "fixed",
    "linear-horizontal",
    "linear-vertical",
    "orbit-2x2",
    "orbit-3x3",
)
DYNAMIC_MODE_NAMES = MODE_NAMES[1:]
MASK_COUNTS = (1, 2, 3)


@dataclass(frozen=True)
class GrayImage:
    name: str
    width: int
    height: int
    # Pixel rows are stored bottom-to-top, matching OpenGL window coordinates.
    pixels: tuple[int, ...]

    def at(self, x: int, y: int) -> int:
        return self.pixels[y * self.width + x]

    @property
    def total(self) -> int:
        return sum(self.pixels)

    @property
    def peak(self) -> int:
        return max(self.pixels, default=0)


@dataclass(frozen=True)
class State:
    phase: int | None
    index: int
    dx: int
    dy: int


def _pgm_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for line in text.splitlines():
        line = line.split("#", 1)[0]
        tokens.extend(line.split())
    return tokens


def load_pgm(path: Path) -> GrayImage:
    tokens = _pgm_tokens(path.read_text(encoding="ascii"))
    if len(tokens) < 4 or tokens[0] != "P2":
        raise ValueError(f"{path}: expected an ASCII P2/PGM image")
    width, height, max_value = map(int, tokens[1:4])
    if width <= 0 or height <= 0 or not 1 <= max_value <= 65535:
        raise ValueError(f"{path}: invalid PGM dimensions or maximum value")
    raw = list(map(int, tokens[4:]))
    if len(raw) != width * height:
        raise ValueError(f"{path}: expected {width * height} pixels, found {len(raw)}")
    if any(value < 0 or value > max_value for value in raw):
        raise ValueError(f"{path}: pixel value outside PGM range")

    # PGM stores rows top-to-bottom. Reverse them so y=0 is the OpenGL bottom row.
    top_down = [raw[row * width:(row + 1) * width] for row in range(height)]
    bottom_up = [value for row in reversed(top_down) for value in row]
    if max_value != 255:
        bottom_up = [round(value * 255 / max_value) for value in bottom_up]
    return GrayImage(path.stem, width, height, tuple(bottom_up))


def load_fixtures() -> dict[str, GrayImage]:
    paths = sorted(FIXTURE_DIR.glob("*.pgm"))
    if not paths:
        raise RuntimeError(f"no PGM fixtures found in {FIXTURE_DIR}")
    images = {path.stem: load_pgm(path) for path in paths}
    dimensions = {(image.width, image.height) for image in images.values()}
    if len(dimensions) != 1:
        raise ValueError(f"fixture dimensions differ: {sorted(dimensions)}")
    width, height = next(iter(dimensions))
    if width % 2 or height % 2:
        raise ValueError("fixtures must have even dimensions for exact 2x2 mask coverage")
    return images


def _extract_function(source: str, name: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}", source)
    if match is None:
        raise RuntimeError(f"production function {name}() was not found in {SOURCE_PATH}")
    return match.group(0)


def _run_bash(script: str) -> str:
    result = subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"production Bash helper failed ({result.returncode}):\n"
            f"{result.stdout}{result.stderr}"
        )
    return result.stdout.strip()


def production_functions() -> str:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    names = ("pixel_shift_mode_count", "pixel_shift_position", "advance_active_effects")
    return "\n\n".join(_extract_function(source, name) for name in names)


def production_mode_positions(mode: str) -> tuple[tuple[int, int], ...]:
    functions = production_functions()
    script = (
        functions
        + f'\ncount=$(pixel_shift_mode_count "{mode}") || exit $?\n'
        + 'for ((index=0; index<count; index++)); do\n'
        + f'  pixel_shift_position "{mode}" "$index" || exit $?\n'
        + 'done\n'
    )
    rows = _run_bash(script).splitlines()
    positions = tuple(tuple(map(int, row.split())) for row in rows)
    expected_count = int(_run_bash(functions + f'\npixel_shift_mode_count "{mode}"'))
    if len(positions) != expected_count or any(len(position) != 2 for position in positions):
        raise RuntimeError(f"invalid production offset table for {mode}: {positions}")
    return positions


def production_combined_schedule(mode: str, phase: int = 0, index: int = 0) -> tuple[tuple[int, int], ...]:
    """Return (phase,index) states by executing production advance_active_effects()."""
    functions = production_functions()
    count = len(production_mode_positions(mode))
    steps = 4 * count
    script = (
        functions
        + f'\nMASK_ENABLED=true PIXEL_SHIFT=true PHASE={phase} '
          f'PIXEL_SHIFT_MODE="{mode}" PIXEL_SHIFT_INDEX={index}\n'
        + f"for ((step=0; step<{steps}; step++)); do\n"
        + '  printf "%s %s\\n" "$PHASE" "$PIXEL_SHIFT_INDEX"\n'
        + '  advance_active_effects || exit $?\n'
        + 'done\n'
    )
    rows = _run_bash(script).splitlines()
    states = tuple(tuple(map(int, row.split())) for row in rows)
    if len(states) != steps or len(set(states)) != steps:
        raise RuntimeError(f"production combined schedule for {mode} does not cover {steps} unique states")
    return states


def glsl_mask_is_off(x: int, y: int, phase: int, off_pixels: int) -> bool:
    """Evaluate the production GLSL expression at pixel-centre coordinates.

    gl_FragCoord is (x + 0.5, y + 0.5) under the default lower-left origin.
    GLSL mod(x, y) is x - y * floor(x / y), matching Python's positive modulo
    for this positive divisor.
    """
    fc_x = x + 0.5
    fc_y = y + 0.5
    pos = (fc_x % 2.0) + 2.0 * (fc_y % 2.0)
    return ((pos - float(phase)) % 4.0) < float(off_pixels)


def _mask_maps(width: int, height: int, off_pixels: int) -> tuple[tuple[bool, ...], ...]:
    return tuple(
        tuple(glsl_mask_is_off(x, y, phase, off_pixels)
              for y in range(height) for x in range(width))
        for phase in range(4)
    )


def _render_unmasked(image: GrayImage, dx: int, dy: int) -> tuple[int, ...]:
    output = [0] * (image.width * image.height)
    for y in range(image.height):
        source_y = y - dy
        if source_y < 0 or source_y >= image.height:
            continue
        row_offset = y * image.width
        source_row_offset = source_y * image.width
        for x in range(image.width):
            source_x = x - dx
            if 0 <= source_x < image.width:
                output[row_offset + x] = image.pixels[source_row_offset + source_x]
    return tuple(output)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _gini(values: list[float]) -> float:
    values = sorted(value for value in values if value > 0)
    if not values:
        return 0.0
    count = len(values)
    total = sum(values)
    weighted = sum((2 * index - count - 1) * value for index, value in enumerate(values, 1))
    return weighted / (count * total)


def measure(image: GrayImage, states: Iterable[State], off_pixels: int | None) -> dict[str, float | int]:
    states = tuple(states)
    if not states:
        raise ValueError("at least one state is required")
    width, height = image.width, image.height
    accumulated = [0] * (width * height)
    unmasked_energy = 0
    emitted_energy = 0
    mask_cache = _mask_maps(width, height, off_pixels) if off_pixels is not None else None

    for state in states:
        shifted = _render_unmasked(image, state.dx, state.dy)
        unmasked_energy += sum(shifted)
        if off_pixels is None:
            emitted = shifted
        else:
            if state.phase is None:
                raise ValueError("masked simulation state has no phase")
            mask = mask_cache[state.phase]
            emitted = tuple(0 if mask[index] else value for index, value in enumerate(shifted))
        emitted_energy += sum(emitted)
        for index, value in enumerate(emitted):
            accumulated[index] += value

    averages = [value / len(states) for value in accumulated]
    support = [value for value in averages if value > 0]
    total_mean_energy = sum(averages)
    square_sum = sum(value * value for value in averages)
    mean_support = sum(support) / len(support) if support else 0.0
    variance = (
        sum((value - mean_support) ** 2 for value in support) / len(support)
        if support else 0.0
    )
    peak = max(averages, default=0.0)
    p95 = _percentile(support, 0.95)
    source_sum = image.total
    source_peak = image.peak
    shift_retention = (unmasked_energy / len(states) / source_sum) if source_sum else 1.0
    mask_retention = (emitted_energy / unmasked_energy) if unmasked_energy else 1.0
    total_retention = (emitted_energy / len(states) / source_sum) if source_sum else 1.0

    return {
        "states": len(states),
        "source_energy": source_sum,
        "source_peak": source_peak,
        "mean_energy_retained": total_retention,
        "shift_energy_retained": shift_retention,
        "mask_energy_retained": mask_retention,
        "edge_loss_fraction": max(0.0, 1.0 - shift_retention),
        "peak_mean_exposure": peak,
        "peak_to_source_peak": (peak / source_peak) if source_peak else 0.0,
        "p95_mean_exposure_on_support": p95,
        "p95_to_source_peak": (p95 / source_peak) if source_peak else 0.0,
        "support_pixels": len(support),
        "effective_pixels": (total_mean_energy * total_mean_energy / square_sum) if square_sum else 0.0,
        "peak_share_of_mean_energy": (peak / total_mean_energy) if total_mean_energy else 0.0,
        "support_cv": (math.sqrt(variance) / mean_support) if mean_support else 0.0,
        "support_gini": _gini(support),
    }


def _state_sequence(kind: str, mode: str | None = None) -> tuple[State, ...]:
    if kind == "baseline":
        return (State(None, 0, 0, 0),)
    if kind == "mask-only":
        return tuple(State(phase, 0, 0, 0) for phase in range(4))
    if mode is None:
        raise ValueError("mode is required for Pixel Shift scenarios")
    offsets = production_mode_positions(mode)
    if kind == "pixel-shift-only":
        return tuple(State(None, index, dx, dy) for index, (dx, dy) in enumerate(offsets))
    if kind == "combined":
        schedule = production_combined_schedule(mode)
        return tuple(State(phase, index, *offsets[index]) for phase, index in schedule)
    raise ValueError(f"unknown scenario: {kind}")


def analyze_fixture(image: GrayImage) -> dict[str, object]:
    result: dict[str, object] = {
        "dimensions": {"width": image.width, "height": image.height},
        "baseline": measure(image, _state_sequence("baseline"), None),
        "mask_only": {},
        "pixel_shift_only": {},
        "combined": {},
    }
    mask_only = result["mask_only"]
    for count in MASK_COUNTS:
        mask_only[str(count)] = measure(image, _state_sequence("mask-only"), count)

    shift_only = result["pixel_shift_only"]
    combined = result["combined"]
    for mode in MODE_NAMES:
        shift_only[mode] = measure(image, _state_sequence("pixel-shift-only", mode), None)
        combined[mode] = {
            str(count): measure(image, _state_sequence("combined", mode), count)
            for count in MASK_COUNTS
        }
    return result


def _source_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return None

    return result.stdout.strip() if result.returncode == 0 else None


def build_report() -> dict[str, object]:
    source = SOURCE_PATH.read_text(encoding="utf-8")
    required = (
        "vec2 fc = gl_FragCoord.xy;",
        "float pos = mod(fc.x, 2.0) + 2.0 * mod(fc.y, 2.0);",
        "bool off = mod(pos - float(${PHASE}), 4.0) < float(${OFF_PIXELS});",
        "texelFetch(tex, sourceTexel, 0)",
    )
    missing = [needle for needle in required if needle not in source]
    if missing:
        raise RuntimeError(f"production shader no longer matches the image model; missing: {missing}")

    images = load_fixtures()
    modes = {mode: [list(position) for position in production_mode_positions(mode)] for mode in MODE_NAMES}
    return {
        "schema_version": 1,
        "project": "hyproled",
        "source_revision": _source_revision(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "model": {
            "pixel_coordinate_convention": "OpenGL lower-left window coordinates; gl_FragCoord at x+0.5,y+0.5",
            "pixel_shift_sampling": "output(x,y) = input(x-dx,y-dy); out-of-bounds samples are black",
            "mask": "exact production GLSL 2x2 phase expression; no area/invert clipping",
            "time_weighting": "each scheduled state has equal dwell time",
            "brightness_compensation": "excluded; reported exposure is input grayscale signal 0..255",
        },
        "mode_offsets": modes,
        "fixtures": {name: analyze_fixture(image) for name, image in images.items()},
    }


def markdown_report(report: dict[str, object]) -> str:
    lines = [
        "# Hyproled synthetic image exposure report",
        "",
        f"- Source revision: `{report.get('source_revision') or 'unknown'}`",
        f"- Generated (UTC): `{report['generated_at_utc']}`",
        f"- Model: {report['model']['pixel_coordinate_convention']}",
        "- Values are deterministic model outputs, not measured OLED degradation.",
        "",
        "| Fixture | Scenario | Peak / source peak | Effective px | Support px | Energy retained | Edge loss |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]

    def add(name: str, scenario: str, metrics: dict[str, object]) -> None:
        lines.append(
            f"| {name} | {scenario} | {metrics['peak_to_source_peak']:.4f} "
            f"| {metrics['effective_pixels']:.2f} | {metrics['support_pixels']} "
            f"| {metrics['mean_energy_retained']:.4f} | {metrics['edge_loss_fraction']:.4f} |"
        )

    for name, image_data in report["fixtures"].items():
        add(name, "baseline", image_data["baseline"])
        for count, metrics in image_data["mask_only"].items():
            add(name, f"mask / OFF_PIXELS={count}", metrics)
        for mode, metrics in image_data["pixel_shift_only"].items():
            add(name, f"shift / {mode}", metrics)
        for mode, counts in image_data["combined"].items():
            for count, metrics in counts.items():
                add(name, f"combined / {mode} / OFF_PIXELS={count}", metrics)

    lines += [
        "",
        "## Metric definitions",
        "",
        "- **Peak / source peak:** maximum per-panel-pixel time-averaged signal divided by the source image peak.",
        "- **Effective px:** inverse concentration, `(sum(E)^2 / sum(E^2))`, over the full per-pixel mean-exposure map.",
        "- **Support px:** number of panel pixels receiving non-zero mean signal in the simulated cycle.",
        "- **Energy retained:** emitted grayscale signal divided by source signal, averaged over states; includes mask attenuation and edge clipping.",
        "- **Edge loss:** fraction of source signal lost to out-of-bounds black samples before applying the mask.",
        "- **Support CV / Gini** (JSON only): dispersion of mean exposure among pixels that receive non-zero signal.",
        "",
        "## Interpretation limits",
        "",
        "This model evaluates the image signal delivered by the shader. It does not model OLED current, subpixel chemistry, temperature, panel compensation, temporal response, automatic brightness limiting, or a measured lifetime gain. The PGM fixtures are synthetic and intentionally small; they test geometry and regression properties, not photographic workload representativeness. All time averages assume equal dwell time per scheduler state.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-report", type=Path, help="write full machine-readable JSON report")
    parser.add_argument("--markdown-report", type=Path, help="write a human-readable Markdown report")
    args = parser.parse_args()

    report = build_report()
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {args.json_report}")
    if args.markdown_report:
        args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_report.write_text(markdown_report(report), encoding="utf-8")
        print(f"wrote {args.markdown_report}")
    if not args.json_report and not args.markdown_report:
        print(markdown_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
