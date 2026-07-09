#!/usr/bin/env python3
"""
Vibe-Trading Backend Builder — PyInstaller packaging script.

Builds the Python FastAPI backend (api_server.py) into a standalone executable
that can be used as a Tauri sidecar.

Usage:
    python desktop/build_backend.py              # production build (onedir)
    python desktop/build_backend.py --dev        # dev build (onefile, faster iteration)
    python desktop/build_backend.py --skip-smoke # skip post-build verification
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "agent"
OUTPUT_DIR = ROOT / "desktop" / "src-tauri" / "binaries"
WORK_DIR = ROOT / "desktop" / "build" / "pyi-work"
SPEC_DIR = ROOT / "desktop" / "build"

# Build venv paths
BUILD_ENV_DIR = ROOT / "desktop" / "build-env"
BUILD_REQUIREMENTS_TXT = ROOT / "desktop" / "requirements-build.txt"
BUILD_ENV_MARKER = BUILD_ENV_DIR / ".vibe-build-env.json"

TARGET_TRIPLE = "x86_64-pc-windows-msvc"
BACKEND_NAME = f"vibe-backend-{TARGET_TRIPLE}"
BACKEND_EXE = BACKEND_NAME + ".exe"

# =============================================================
#  Prerequisites checks
# =============================================================

def _ensure_build_venv(args) -> None:
    """Ensure we're running inside the dedicated build venv.

    If not, create/update the venv and re-execute this script with it.
    """
    if args.no_build_venv:
        print("[BUILD] Using current Python (--no-build-venv)")
        return

    # Check if we're already in the build venv
    current_exe = Path(sys.executable).resolve()
    build_python = (BUILD_ENV_DIR / "Scripts" / "python.exe").resolve()

    if current_exe == build_python:
        # Already in build venv — just verify deps are installed
        _verify_build_venv()
        return

    # We're NOT in the build venv — create it and re-execute
    print(f"[BUILD] Setting up build venv at {BUILD_ENV_DIR}")

    # Check Python version
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    if py_version != "3.11":
        print(f"[ERROR] Build venv requires Python 3.11, found {py_version}")
        print("        Install Python 3.11 from https://www.python.org/downloads/")
        sys.exit(1)

    # Create or recreate venv
    if args.recreate_build_venv and BUILD_ENV_DIR.exists():
        shutil.rmtree(BUILD_ENV_DIR)
        print("[BUILD] Removed old build-env (--recreate-build-venv)")

    if not BUILD_ENV_DIR.exists():
        import venv
        print("[BUILD] Creating build-env...")
        venv.main([str(BUILD_ENV_DIR)])

    # Upgrade pip
    subprocess.check_call(
        [str(build_python), "-m", "pip", "install", "-U", "pip", "setuptools", "wheel",
         "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
        timeout=120
    )

    # Install dependencies from lock file
    print(f"[BUILD] Installing dependencies from {BUILD_REQUIREMENTS_TXT}...")
    subprocess.check_call(
        [str(build_python), "-m", "pip", "install", "-r", str(BUILD_REQUIREMENTS_TXT),
         "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
        timeout=600
    )

    # Install the project itself (no deps — they come from the lock file)
    print("[BUILD] Installing project package (--no-deps)...")
    subprocess.check_call(
        [str(build_python), "-m", "pip", "install", "--no-deps", "-e", str(ROOT)],
        timeout=120
    )

    # Write marker
    _write_venv_marker()

    # Re-execute this script with the build venv
    print(f"[BUILD] Re-executing with {build_python}")
    # Forward all arguments except --recreate-build-venv
    new_argv = [str(a) for a in sys.argv if a != "--recreate-build-venv"]
    # Add --no-build-venv to prevent recursion
    if "--no-build-venv" not in new_argv:
        new_argv.append("--no-build-venv")

    os.execv(str(build_python), [str(build_python)] + new_argv)


def _verify_build_venv() -> None:
    """Verify the build venv has required packages."""
    try:
        import PyInstaller  # noqa: F401
        import src  # noqa: F401
        import httpx  # noqa: F401
        import uvicorn  # noqa: F401
        import fastapi  # noqa: F401
    except ImportError as e:
        print(f"[ERROR] Build venv is missing a required package: {e}")
        print("        Run with --update-build-deps to reinstall")
        sys.exit(1)

    # Verify source path
    src_file = Path(src.__file__).resolve()
    expected = AGENT_DIR / "src" / "__init__.py"
    if src_file != expected.resolve():
        print(f"[ERROR] 'src' package is from {src_file}, expected {expected}")
        sys.exit(1)


def _write_venv_marker() -> None:
    """Write a marker file to track the build venv state."""
    req_hash = hashlib.sha256(BUILD_REQUIREMENTS_TXT.read_bytes()).hexdigest()[:16]
    marker = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "requirements_build_sha256": req_hash,
        "project_root": str(ROOT.resolve()),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    BUILD_ENV_MARKER.parent.mkdir(parents=True, exist_ok=True)
    BUILD_ENV_MARKER.write_text(json.dumps(marker, indent=2))
    print(f"[BUILD] Venv marker written: {BUILD_ENV_MARKER}")


def _check_environment() -> None:
    """Verify the Python environment before building."""
    # 1. Ensure we're importing from the correct workspace
    try:
        import src
        src_file = Path(src.__file__).resolve()
        expected = AGENT_DIR / "src" / "__init__.py"
        if src_file != expected.resolve():
            print(f"[ERROR] 'src' package is from {src_file}, expected {expected}")
            print("        Run: pip install -e .  from the project root")
            sys.exit(1)
    except ImportError:
        print("[ERROR] Cannot import 'src' package. Is vibe-trading-ai installed?")
        print("        Run: pip install -e .  from the project root")
        sys.exit(1)

    # 2. Check PyInstaller is available
    try:
        import PyInstaller
    except ImportError:
        print("[ERROR] PyInstaller is not installed.")
        print("        Run: pip install pyinstaller")
        sys.exit(1)

    print(f"[OK]   Python environment: {sys.executable}")
    print(f"[OK]   Source path: {src_file}")
    print(f"[OK]   PyInstaller: {PyInstaller.__version__}")


def _kill_old_backend() -> None:
    """Kill any running vibe-backend processes to avoid file lock conflicts."""
    if sys.platform == "win32":
        cmd = ["taskkill", "/f", "/im", "vibe-backend*.exe", "/t"]
    else:
        cmd = ["pkill", "-f", "vibe-backend"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=10)
        print("[OK]   Killed old backend processes")
    except subprocess.TimeoutExpired:
        print("[WARN] Timed out while killing old processes")
    except FileNotFoundError:
        pass  # no processes to kill


# =============================================================
#  PyInstaller build
# =============================================================

def _build_onedir() -> Path:
    """Build backend with --onedir (production)."""
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--name", BACKEND_NAME,
        "--distpath", str(OUTPUT_DIR),
        "--workpath", str(WORK_DIR),
        "--specpath", str(SPEC_DIR),
        "--paths", str(AGENT_DIR),
    ]

    # ── collect data files ───────────────────────────────────
    data_sources = [
        f"{AGENT_DIR / 'src' / 'providers' / 'llm_providers.json'};src/providers/",
    ]
    for ds in data_sources:
        args.extend(["--add-data", ds])

    # ── collect package data ─────────────────────────────────
    for pkg in ["src", "cli", "backtest"]:
        args.extend(["--collect-data", pkg])

    # ── hidden imports ───────────────────────────────────────
    hidden = [
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "cli._version",
    ]
    for h in hidden:
        args.extend(["--hidden-import", h])

    # ── exclude heavy dev/test artifacts ─────────────────────
    excludes = [
        "PySide6", "PyQt5", "PyQt6",  # not needed for headless backend
        "tkinter",
        "matplotlib.tests",
        "pandas.tests",
        "scipy.tests",
        "numpy.testing",
    ]
    for ex in excludes:
        args.extend(["--exclude-module", ex])

    args.append(str(AGENT_DIR / "api_server.py"))

    print("=" * 60)
    print(" Building Vibe-Trading Backend (onedir)")
    print("=" * 60)
    print(f" Output : {OUTPUT_DIR / BACKEND_NAME}")
    print(f" Target : {TARGET_TRIPLE}")
    print("=" * 60)

    result = subprocess.run(args, cwd=str(AGENT_DIR))
    if result.returncode != 0:
        print(f"[ERROR] PyInstaller build failed with code {result.returncode}")
        sys.exit(result.returncode)

    onedir_dir = OUTPUT_DIR / BACKEND_NAME
    exe_path = onedir_dir / BACKEND_EXE
    internal_dir = onedir_dir / "_internal"

    if not exe_path.exists():
        print(f"[ERROR] Expected exe not found: {exe_path}")
        sys.exit(1)

    # ── Flatten onedir output for Tauri sidecar compatibility ──
    # Tauri expects a flat exe at binaries/NAME.exe, not a directory.
    # Move exe and _internal up one level so Tauri can find them.
    target_exe = OUTPUT_DIR / BACKEND_EXE
    target_internal = OUTPUT_DIR / "_internal"

    # Remove old artifacts if they exist
    if target_exe.exists():
        target_exe.unlink()
    if target_internal.exists():
        shutil.rmtree(target_internal)

    shutil.move(str(exe_path), str(target_exe))
    shutil.move(str(internal_dir), str(target_internal))
    shutil.rmtree(str(onedir_dir))

    file_size_mb = target_exe.stat().st_size / 1024 / 1024
    print(f"[OK]   Built: {target_exe} ({file_size_mb:.1f} MB)")
    print(f"[OK]   Dependencies: {target_internal}")
    return target_exe


def _build_onefile() -> Path:
    """Build backend with --onefile (dev mode, faster iteration)."""
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name", BACKEND_NAME,
        "--distpath", str(OUTPUT_DIR),
        "--workpath", str(WORK_DIR),
        "--specpath", str(SPEC_DIR),
        "--paths", str(AGENT_DIR),
        "--add-data", "src/providers/llm_providers.json;src/providers/",
        "--collect-data", "src",
        "--collect-data", "cli",
        "--hidden-import", "cli._version",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "uvicorn.lifespan.off",
        str(AGENT_DIR / "api_server.py"),
    ]

    print("=" * 60)
    print(" Building Vibe-Trading Backend (onefile — dev mode)")
    print("=" * 60)

    result = subprocess.run(args, cwd=str(AGENT_DIR))
    if result.returncode != 0:
        sys.exit(result.returncode)

    exe_path = OUTPUT_DIR / BACKEND_EXE
    file_size_mb = exe_path.stat().st_size / 1024 / 1024
    print(f"[OK]   Built: {exe_path} ({file_size_mb:.1f} MB)")
    return exe_path


# =============================================================
#  Smoke test
# =============================================================

def _smoke_test(exe_path: Path) -> None:
    """Start the backend, hit /health, then shut it down."""
    print()
    print("─" * 40)
    print(" Smoke test")
    print("─" * 40)

    backend = subprocess.Popen(
        [str(exe_path), "--port", "18999", "--host", "127.0.0.1"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        import httpx
        for attempt in range(1, 31):
            time.sleep(1)
            try:
                r = httpx.get("http://127.0.0.1:18999/health", timeout=3)
                if r.status_code == 200:
                    print(f"[OK]   Backend ready (attempt {attempt})")
                    break
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            if attempt == 30:
                raise RuntimeError("Backend did not become ready in 30s")
    except Exception as e:
        print(f"[FAIL] Smoke test: {e}")
        backend.kill()
        backend.wait()
        sys.exit(1)
    else:
        print("[OK]   /health responded 200")
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend.kill()
            backend.wait()
        print("[OK]   Backend stopped")


# =============================================================
#  Main
# =============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Build Vibe-Trading backend")
    parser.add_argument("--dev", action="store_true", help="Use --onefile (faster for dev)")
    parser.add_argument("--skip-smoke", action="store_true", help="Skip post-build smoke test")
    parser.add_argument("--no-build-venv", action="store_true", help="Use current Python, don't auto-switch to build venv")
    parser.add_argument("--recreate-build-venv", action="store_true", help="Delete and recreate desktop/build-env")
    parser.add_argument("--update-build-deps", action="store_true", help="Force reinstall dependencies from lock file")
    args = parser.parse_args()

    # Step 0: Ensure we're in the build venv (will re-execute if needed)
    _ensure_build_venv(args)

    start = time.time()

    print()
    _check_environment()
    _kill_old_backend()

    if args.dev:
        exe_path = _build_onefile()
    else:
        exe_path = _build_onedir()

    elapsed = time.time() - start
    print(f"[OK]   Build completed in {elapsed:.0f}s")

    if not args.skip_smoke:
        _smoke_test(exe_path)

    print()
    print("=" * 60)
    print(" Build successful!")
    print(f"  {exe_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
