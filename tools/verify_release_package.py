"""Validate the Windows release ZIP produced by GitHub Actions."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
import platform
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator, Optional


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
    try:
        zip_exists = zip_path.exists()
    except OSError as exc:
        raise SystemExit(f"Release ZIP is not accessible: {zip_path}") from exc
    if not zip_exists:
        raise SystemExit(f"Release ZIP does not exist: {zip_path}")
    try:
        zip_size = zip_path.stat().st_size
    except OSError as exc:
        raise SystemExit(f"Release ZIP is not accessible: {zip_path}") from exc
    if zip_size <= 0:
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
    candidates: list[tuple[float, Path]] = []
    try:
        for path in dist_dir.glob("*.zip"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, path))
    except OSError as exc:
        raise SystemExit(f"Release ZIP directory is not accessible: {dist_dir}") from exc
    if not candidates:
        raise SystemExit(f"No release ZIP was found in {dist_dir}")
    return max(candidates, key=lambda item: item[0])[1]


def _read_zip_names(zip_path: Path) -> set[str]:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names: list[str] = []
            unsafe_paths: set[str] = set()
            for info in archive.infolist():
                name = info.filename.replace("\\", "/").rstrip("/")
                if _is_unsafe_zip_name(name):
                    unsafe_paths.add(info.filename)
                    continue
                if not info.is_dir():
                    names.append(name)
            if unsafe_paths:
                raise SystemExit(
                    "Release ZIP contains unsafe paths:\n"
                    + "\n".join(f"- {item}" for item in sorted(unsafe_paths))
                )
            seen: set[str] = set()
            duplicates: set[str] = set()
            for name in names:
                if name in seen:
                    duplicates.add(name)
                seen.add(name)
            if duplicates:
                raise SystemExit(
                    "Release ZIP contains duplicate entries:\n"
                    + "\n".join(f"- {item}" for item in sorted(duplicates))
                )
            bad_file = archive.testzip()
            if bad_file:
                raise SystemExit(f"Release ZIP contains a corrupt entry: {bad_file}")
            return set(names)
    except zipfile.BadZipFile as exc:
        raise SystemExit(f"Release ZIP is not a valid ZIP file: {zip_path}") from exc
    except OSError as exc:
        raise SystemExit(f"Release ZIP could not be read: {zip_path}: {exc}") from exc
    except RuntimeError as exc:
        raise SystemExit(f"Release ZIP could not be inspected: {zip_path}: {exc}") from exc


def _is_unsafe_zip_name(name: str) -> bool:
    if not name:
        return True
    if name.startswith(("/", "\\")):
        return True
    parts = name.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return True
    first = parts[0]
    return len(first) >= 2 and first[1] == ":"


def _extract_zip_safely(zip_path: Path, extract_dir: Path, *, label: str) -> None:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            unsafe_paths: set[str] = set()
            for info in archive.infolist():
                name = info.filename.replace("\\", "/").rstrip("/")
                if _is_unsafe_zip_name(name):
                    unsafe_paths.add(info.filename)
            if unsafe_paths:
                raise SystemExit(
                    f"{label} smoke ZIP contains unsafe paths:\n"
                    + "\n".join(f"- {item}" for item in sorted(unsafe_paths))
                )
            archive.extractall(extract_dir)
    except SystemExit:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise SystemExit(f"{label} smoke ZIP extraction failed: {exc}") from exc


def _smoke_cli_help(zip_path: Path, *, require: bool) -> None:
    if platform.system() != "Windows":
        if require:
            raise SystemExit("CLI smoke test requires Windows.")
        print("Skipping CLI smoke test on non-Windows host.")
        return
    _read_zip_names(zip_path)
    with _smoke_temp_directory("aruba_mm_cleanup_smoke_", "CLI") as temp_dir:
        extract_dir = Path(temp_dir)
        _extract_zip_safely(zip_path, extract_dir, label="CLI")
        cli_exe = extract_dir / "ArubaMMCleanupCLI.exe"
        try:
            completed = subprocess.run(
                [str(cli_exe), "--help"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SystemExit("CLI smoke command timed out after 60 seconds.") from exc
        except OSError as exc:
            raise SystemExit(f"CLI smoke command could not start: {exc}") from exc
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
    _read_zip_names(zip_path)
    with _smoke_temp_directory("aruba_mm_cleanup_gui_smoke_", "GUI") as temp_dir:
        extract_dir = Path(temp_dir)
        _extract_zip_safely(zip_path, extract_dir, label="GUI")
        gui_exe = extract_dir / "ArubaMMCleanupGUI.exe"
        env = os.environ.copy()
        env["ARUBA_MM_CLEANUP_GUI_SMOKE"] = "1"
        try:
            completed = subprocess.run(
                [str(gui_exe)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise SystemExit("GUI smoke command timed out after 60 seconds.") from exc
        except OSError as exc:
            raise SystemExit(f"GUI smoke command could not start: {exc}") from exc
        output = f"{completed.stdout}\n{completed.stderr}"
        if completed.returncode != 0:
            raise SystemExit(f"GUI smoke command failed with exit code {completed.returncode}:\n{output.strip()}")


@contextmanager
def _smoke_temp_directory(prefix: str, label: str) -> Iterator[str]:
    try:
        temp_dir = tempfile.TemporaryDirectory(prefix=prefix)
    except Exception as exc:
        raise SystemExit(f"{label} smoke temporary directory could not be created: {exc}") from exc
    try:
        yield temp_dir.name
    finally:
        try:
            temp_dir.cleanup()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
