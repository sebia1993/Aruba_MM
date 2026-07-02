"""Validate the Windows release ZIP produced by GitHub Actions."""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Optional


REQUIRED_FILES = {
    "ArubaMMCleanupGUI.exe",
    "ArubaMMCleanupCLI.exe",
    "README.md",
    "USER_GUIDE_KO.md",
    "config/mock_scenarios/profiling_users.txt",
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an Aruba MM Cleanup Windows release ZIP.")
    parser.add_argument("--zip", dest="zip_path", type=Path, help="release ZIP path")
    parser.add_argument("--dist", type=Path, default=Path("dist"), help="directory containing a release ZIP")
    parser.add_argument("--smoke-cli", action="store_true", help="run ArubaMMCleanupCLI.exe --help on Windows")
    parser.add_argument("--smoke-gui", action="store_true", help="run ArubaMMCleanupGUI.exe in smoke mode on Windows")
    parser.add_argument(
        "--require-cli-smoke",
        action="store_true",
        help="fail if --smoke-cli is requested on a non-Windows host",
    )
    parser.add_argument(
        "--require-gui-smoke",
        action="store_true",
        help="fail if --smoke-gui is requested on a non-Windows host",
    )
    args = parser.parse_args(argv)

    zip_path = args.zip_path or _find_latest_zip(args.dist)
    if not zip_path.exists():
        raise SystemExit(f"Release ZIP does not exist: {zip_path}")
    if zip_path.stat().st_size <= 0:
        raise SystemExit(f"Release ZIP is empty: {zip_path}")

    names = _read_zip_names(zip_path)
    missing = sorted(REQUIRED_FILES - names)
    if missing:
        raise SystemExit("Release ZIP is missing required files:\n" + "\n".join(f"- {item}" for item in missing))

    if args.smoke_cli:
        _smoke_cli_help(zip_path, require=args.require_cli_smoke)
    if args.smoke_gui:
        _smoke_gui(zip_path, require=args.require_gui_smoke)
    print(f"Verified release package: {zip_path}")
    return 0


def _find_latest_zip(dist_dir: Path) -> Path:
    candidates = sorted(dist_dir.glob("*.zip"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit(f"No release ZIP was found in {dist_dir}")
    return candidates[0]


def _read_zip_names(zip_path: Path) -> set[str]:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            bad_file = archive.testzip()
            if bad_file:
                raise SystemExit(f"Release ZIP contains a corrupt entry: {bad_file}")
            return {name.replace("\\", "/").rstrip("/") for name in archive.namelist()}
    except zipfile.BadZipFile as exc:
        raise SystemExit(f"Release ZIP is not a valid ZIP file: {zip_path}") from exc


def _smoke_cli_help(zip_path: Path, *, require: bool) -> None:
    if platform.system() != "Windows":
        if require:
            raise SystemExit("CLI smoke test requires Windows.")
        print("Skipping CLI smoke test on non-Windows host.")
        return
    with tempfile.TemporaryDirectory(prefix="aruba_mm_cleanup_smoke_") as temp_dir:
        extract_dir = Path(temp_dir)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)
        cli_exe = extract_dir / "ArubaMMCleanupCLI.exe"
        completed = subprocess.run(
            [str(cli_exe), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        if completed.returncode != 0:
            raise SystemExit(f"CLI smoke command failed with exit code {completed.returncode}:\n{output.strip()}")
        if "--host" not in output or "--role" not in output:
            raise SystemExit("CLI help output did not include expected options: --host, --role")


def _smoke_gui(zip_path: Path, *, require: bool) -> None:
    if platform.system() != "Windows":
        if require:
            raise SystemExit("GUI smoke test requires Windows.")
        print("Skipping GUI smoke test on non-Windows host.")
        return
    with tempfile.TemporaryDirectory(prefix="aruba_mm_cleanup_gui_smoke_") as temp_dir:
        extract_dir = Path(temp_dir)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)
        gui_exe = extract_dir / "ArubaMMCleanupGUI.exe"
        env = os.environ.copy()
        env["ARUBA_MM_CLEANUP_GUI_SMOKE"] = "1"
        completed = subprocess.run(
            [str(gui_exe)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=env,
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        if completed.returncode != 0:
            raise SystemExit(f"GUI smoke command failed with exit code {completed.returncode}:\n{output.strip()}")


if __name__ == "__main__":
    raise SystemExit(main())
