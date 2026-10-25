#!/usr/bin/env python3
"""Build the ONE-FILE distribution: ``dist/GBVMonitor.exe``.

The portable app is assembled first as a normal folder
(``dist/GBVMonitor/`` = launcher exe + ``_internal/`` Python runtime + bundled
llama.cpp engines + GGUF models + Chromium). This script then

  1. compiles ``packaging/bootstrap.py`` into a small one-file stub
     (PyInstaller, console, no dependencies),
  2. packs the whole ``dist/GBVMonitor`` folder into a ZIP64 payload
     (already-compressed files are stored, everything else is deflated),
  3. appends payload + a 24-byte trailer (``GBV1PKG\\0`` + offset + size) to the
     stub and writes ``dist/GBVMonitor.exe``.

The result is a single self-extracting executable: double-click → it unpacks
itself once to ``%LOCALAPPDATA%\\GBVMonitor\\app-<build>`` (progress on the
console), keeps ``output/`` + ``logs/`` in a shared data folder, then runs the
app. No Python, no LM Studio, no installer, no admin rights.

Usage:
    python packaging/make_single_exe.py                 # dist/GBVMonitor -> dist/GBVMonitor.exe
    python packaging/make_single_exe.py --keep-work     # keep the intermediate stub/payload
    python packaging/make_single_exe.py --no-compress   # store everything (fastest build)

Requires: ``pip install -r packaging/requirements-build.txt`` (PyInstaller).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

MAGIC = b"GBV1PKG\x00"
TRAILER = struct.Struct("<8sQQ")
META_DIR = "_bundle"
APP_EXE = "GBVMonitor.exe"
STUB_NAME = "GBVMonitorStub"
# files that are already compressed — storing them makes builds much faster
STORED_SUFFIXES = {
    ".gguf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".7z", ".gz",
    ".xz", ".bz2", ".mp4", ".webm", ".woff", ".woff2", ".bin",
}
# runtime data that must never be baked into a release
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".log"}
SKIP_NAMES = {".gbv-extracted"}


def log(msg: str = "") -> None:
    print(msg, flush=True)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def build_id() -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    return f"{stamp}-{sha or uuid.uuid4().hex[:7]}"


def build_stub(work: Path, python: str) -> Path:
    """Compile packaging/bootstrap.py into a standalone one-file exe."""
    here = Path(__file__).resolve().parent
    dist = work / "stub-dist"
    cmd = [
        python, "-m", "PyInstaller",
        "--onefile", "--console", "--noconfirm", "--clean",
        "--name", STUB_NAME,
        "--distpath", str(dist),
        "--workpath", str(work / "stub-build"),
        "--specpath", str(work),
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "email",
        "--exclude-module", "http",
        "--exclude-module", "xml",
        "--exclude-module", "multiprocessing",
        "--exclude-module", "sqlite3",
        "--exclude-module", "ssl",
        "--exclude-module", "socketserver",
        "--exclude-module", "setuptools",
        "--exclude-module", "pip",
        str(here / "bootstrap.py"),
    ]
    log("[single] compiling the self-extractor stub …")
    res = subprocess.run(cmd, cwd=str(here), capture_output=True, text=True)
    if res.returncode != 0:
        tail = "\n".join((res.stdout + res.stderr).splitlines()[-30:])
        raise SystemExit(f"PyInstaller failed while building the stub:\n{tail}")
    stub = dist / f"{STUB_NAME}.exe"
    if not stub.is_file():
        raise SystemExit(f"PyInstaller did not produce {stub}")
    log(f"[single] stub: {human(stub.stat().st_size)}")
    return stub


def iter_files(root: Path):
    """Yield (path, arcname) for the payload; empty dirs are yielded separately."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        here = Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        if not dirnames and not filenames and rel_dir != ".":
            yield here, rel_dir + "/"
        for name in sorted(filenames):
            p = here / name
            if name in SKIP_NAMES or p.suffix.lower() in SKIP_SUFFIXES:
                continue
            if "?Zone.Identifier" in name:
                continue
            yield p, p.relative_to(root).as_posix()


def build_payload(root: Path, work: Path, bid: str, compress: bool) -> Path:
    payload = work / "payload.zip"
    log(f"[single] packing {root} …")
    total = 0
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED,
                         allowZip64=True, compresslevel=3) as zf:
        zf.writestr(f"{META_DIR}/build_id.txt", bid + "\n")
        for path, arc in iter_files(root):
            if arc.endswith("/"):
                zf.writestr(zipfile.ZipInfo(arc), b"")
                log(f"[single]   dir  {arc}")
                continue
            size = path.stat().st_size
            stored = (not compress) or path.suffix.lower() in STORED_SUFFIXES
            zf.write(path, arc, compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
            total += size
            log(f"[single]   {'store' if stored else 'deflate'}  {arc}  ({human(size)})")
    log(f"[single] payload: {human(payload.stat().st_size)} (from {human(total)} of files)")
    return payload


def assemble(stub: Path, payload: Path, out: Path) -> None:
    offset = stub.stat().st_size
    size = payload.stat().st_size
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".partial")
    log("[single] assembling the single executable …")
    with open(tmp, "wb") as dst:
        for part in (stub, payload):
            with open(part, "rb") as src:
                shutil.copyfileobj(src, dst, 8 * 1024 * 1024)
        dst.write(TRAILER.pack(MAGIC, offset, size))
    os.replace(tmp, out)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksum(out: Path) -> str:
    digest = sha256(out)
    sidecar = out.with_suffix(out.suffix + ".sha256")
    sidecar.write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    return digest


def verify(out: Path, bid: str) -> None:
    size = out.stat().st_size
    with open(out, "rb") as f:
        f.seek(size - TRAILER.size)
        magic, offset, length = TRAILER.unpack(f.read(TRAILER.size))
    if magic != MAGIC or offset + length + TRAILER.size != size:
        raise SystemExit("self-check failed: trailer is inconsistent")
    with zipfile.ZipFile(out) as zf:
        got = zf.read(f"{META_DIR}/build_id.txt").decode().strip()
        if got != bid:
            raise SystemExit(f"self-check failed: build id {got!r} != {bid!r}")
        names = zf.namelist()
    if f"{APP_EXE}" not in names:
        raise SystemExit(f"self-check failed: {APP_EXE} is not in the payload")
    log(f"[single] verified: {len(names)} entries, build {bid}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    root_dir = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="Build the one-file GBVMonitor.exe")
    ap.add_argument("--dist", default=str(root_dir / "dist" / "GBVMonitor"),
                    help="assembled onedir distribution (default: dist/GBVMonitor)")
    ap.add_argument("--out", default=str(root_dir / "dist" / "GBVMonitor.exe"),
                    help="output single executable (default: dist/GBVMonitor.exe)")
    ap.add_argument("--work", default="",
                    help="scratch folder (default: a temp folder next to dist)")
    ap.add_argument("--python", default=sys.executable,
                    help="interpreter that has PyInstaller installed")
    ap.add_argument("--no-compress", action="store_true",
                    help="store every file instead of deflating text/binary files")
    ap.add_argument("--keep-work", action="store_true", help="keep stub + payload")
    ap.add_argument("--no-hash", action="store_true",
                    help="skip the SHA-256 checksum (saves ~20 s on a 6 GB build)")
    args = ap.parse_args()

    dist = Path(args.dist).resolve()
    out = Path(args.out).resolve()
    if not (dist / APP_EXE).is_file():
        raise SystemExit(
            f"{dist / APP_EXE} not found — build/assemble dist/GBVMonitor first "
            "(packaging/build_windows.ps1 does this automatically)."
        )

    work = Path(args.work).resolve() if args.work else Path(
        tempfile.mkdtemp(prefix="gbv_single_", dir=str(out.parent)))
    work.mkdir(parents=True, exist_ok=True)
    bid = build_id()
    log(f"[single] build id: {bid}")
    started = time.monotonic()

    try:
        stub = build_stub(work, args.python)
        payload = build_payload(dist, work, bid, compress=not args.no_compress)
        assemble(stub, payload, out)
        verify(out, bid)
    finally:
        if not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)

    log("")
    log(f"[single] DONE in {time.monotonic() - started:.0f}s")
    log(f"[single] single-file distribution: {out}  ({human(out.stat().st_size)})")
    if not args.no_hash:
        log(f"[single] sha256: {write_checksum(out)}  (written to {out.name}.sha256)")
    log("[single] first start unpacks once into %LOCALAPPDATA%\\GBVMonitor\\app-<build>;")
    log("[single] output/ and logs/ live in %LOCALAPPDATA%\\GBVMonitor\\data and survive upgrades.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
