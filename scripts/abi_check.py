#!/usr/bin/env python3
"""Compare the ctypes HAL mirrors with the C compiler's struct layout."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sx1302_reticulum.structs import ABI_STRUCTS  # noqa: E402


def _find_source_root(root: Path, explicit: Path | None) -> Path:
    """Return the tree containing the libloragw headers."""
    if explicit is not None:
        source = explicit.resolve()
        if not (source / "libloragw/inc/loragw_hal.h").is_file():
            raise FileNotFoundError(f"not a sx1302_hal source tree: {source}")
        return source

    build_source = root / "build" / "sx1302_hal"
    if (build_source / "libloragw/inc/loragw_hal.h").is_file():
        return build_source
    return root / "vendor" / "sx1302_hal"


def _ensure_config_h(source_root: Path) -> None:
    """Generate libloragw/inc/config.h if the source tree has not been built yet."""
    config_h = source_root / "libloragw" / "inc" / "config.h"
    if config_h.is_file():
        return
    cmd = ["make", "-C", str(source_root / "libloragw"), "inc/config.h"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "failed to generate libloragw/inc/config.h:\n" + result.stdout
        )


def _find_compiler() -> str | None:
    explicit = os.environ.get("CC")
    if explicit:
        return explicit
    return shutil.which("cc") or shutil.which("gcc")


def _compile_probe(source_root: Path, output: Path) -> None:
    compiler = _find_compiler()
    if compiler is None:
        raise RuntimeError("no C compiler found (set CC or install gcc/cc)")
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        compiler,
        "-std=c99",
        "-Wall",
        "-Wextra",
        "-I",
        str(source_root / "libloragw" / "inc"),
        "-I",
        str(source_root / "libloragw"),
        "-I",
        str(source_root / "libtools" / "inc"),
        str(ROOT / "scripts" / "abi_probe.c"),
        "-o",
        str(output),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        raise RuntimeError("failed to compile ABI probe:\n" + result.stdout)


def _run_probe(probe: Path) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    result = subprocess.run([str(probe)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ABI probe failed with exit code {result.returncode}:\n{result.stderr}"
        )

    sizes: dict[str, int] = {}
    offsets: dict[str, dict[str, int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "SIZE" and len(parts) == 3:
            sizes[parts[1]] = int(parts[2])
        elif parts[0] == "OFFSET" and len(parts) == 4:
            offsets.setdefault(parts[1], {})[parts[2]] = int(parts[3])
        else:
            raise RuntimeError(f"unexpected ABI probe output: {line!r}")
    return sizes, offsets


def check(source_root: Path) -> list[str]:
    """Return a list of human-readable ABI mismatches."""
    _ensure_config_h(source_root)
    probe = ROOT / "build" / "abi_probe"
    _compile_probe(source_root, probe)
    c_sizes, c_offsets = _run_probe(probe)
    return _compare(c_sizes, c_offsets)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        help="sx1302_hal source tree (default: build copy, then vendor copy)",
    )
    parser.add_argument(
        "--probe",
        type=Path,
        help="use an already compiled ABI probe instead of compiling one",
    )
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    args = parser.parse_args(argv)

    try:
        source_root = _find_source_root(ROOT, args.source)
        if args.probe is not None:
            c_sizes, c_offsets = _run_probe(args.probe)
            errors = _compare(c_sizes, c_offsets)
        else:
            errors = check(source_root)
    except Exception as exc:  # noqa: BLE001 - report a concise CLI error
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"ABI check error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "source": str(source_root),
                    "structs": len(ABI_STRUCTS),
                    "errors": errors,
                }
            )
        )
    elif errors:
        print(f"ABI check failed for source tree: {source_root}")
        for error in errors:
            print(f"  - {error}")
    else:
        print(
            f"ABI check passed: {len(ABI_STRUCTS)} structures match "
            f"{source_root}"
        )

    return 0 if not errors else 1


def _compare(
    c_sizes: dict[str, int], c_offsets: dict[str, dict[str, int]]
) -> list[str]:
    errors: list[str] = []
    for struct_name, py_type in ABI_STRUCTS.items():
        c_size = c_sizes.get(struct_name)
        if c_size is None:
            errors.append(f"{struct_name}: missing from C probe output")
            continue
        if ctypes.sizeof(py_type) != c_size:
            errors.append(
                f"{struct_name}: Python size {ctypes.sizeof(py_type)} != C size {c_size}"
            )
        c_fields = c_offsets.get(struct_name, {})
        py_fields = [name for name, _ in py_type._fields_]
        for field in py_fields:
            if field not in c_fields:
                errors.append(f"{struct_name}.{field}: missing from C probe output")
                continue
            py_offset = getattr(py_type, field).offset
            if py_offset != c_fields[field]:
                errors.append(
                    f"{struct_name}.{field}: Python offset {py_offset} != C offset {c_fields[field]}"
                )
        for field in sorted(set(c_fields) - set(py_fields)):
            errors.append(f"{struct_name}.{field}: present in C but missing from Python")
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
