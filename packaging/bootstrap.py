#!/usr/bin/env python3
"""Self-extracting stub for the single-file GBV Monitor distribution.

`packaging/make_single_exe.py` appends a ZIP64 payload (the whole onedir app:
Python runtime, Streamlit, llama.cpp engine binaries, GGUF models, Chromium) to
this stub, producing ONE file: ``GBVMonitor.exe``.

On first launch this stub

  1. opens itself as a ZIP archive (the payload lives after the PyInstaller
     archive; zipfile finds the central directory at the end of the file),
  2. extracts the app **once** into ``%LOCALAPPDATA%\\GBVMonitor\\app-<build>``
     (progress on the console; ~5-7 GB, only on first run or after an upgrade),
  3. links ``output/`` and ``logs/`` inside the app folder to
     ``%LOCALAPPDATA%\\GBVMonitor\\data`` so case data, screenshots and reports
     survive later upgrades,
  4. starts the real ``GBVMonitor.exe`` from the extracted folder and waits for
     it, passing every command-line argument through
     (e.g. ``GBVMonitor.exe monitor run-due --enrich``).

Nothing is installed, nothing touches the registry, no admin rights are needed.

Environment / flags (all optional):
    GBV_HOME              override the extraction/data root
    GBV_SINGLE_FORCE=1    re-extract even if the cached copy exists
    --print-dir           print the extraction folder and exit
    --force-extract       re-extract from scratch
    --extract-only        extract, do not launch the app
    --payload-info        show the payload build id / size and exit
"""
from __future__ import annotations

import os
import shutil
import stat
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Optional

MAGIC = b"GBV1PKG\x00"
TRAILER = struct.Struct("<8sQQ")     # magic + payload offset + payload size
META_DIR = "_bundle"
BUILD_FILE = f"{META_DIR}/build_id.txt"
DONE_MARKER = ".gbv-extracted"
LOCK_NAME = "extract.lock"
LOCK_STALE_SECONDS = 60 * 60     # an extraction older than this is considered dead
APP_EXE = "GBVMonitor.exe"
DATA_LINKS = ("output", "logs")
CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------- utils
def log(msg: str = "") -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 2) -> int:
    log("")
    log(f"[GBV] ERROR: {msg}")
    log("")
    try:
        if getattr(sys, "frozen", False):
            input("Press Enter to close…")
    except EOFError:
        pass
    return code


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def self_path() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()


def home_dir() -> Path:
    override = os.environ.get("GBV_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "GBVMonitor"
    return Path.home() / ".gbvmonitor"


def read_trailer(exe: Path) -> Optional[tuple[int, int]]:
    """Return (payload_offset, payload_size) or None when this exe is not packed."""
    try:
        size = exe.stat().st_size
        if size < TRAILER.size:
            return None
        with open(exe, "rb") as f:
            f.seek(size - TRAILER.size)
            magic, offset, length = TRAILER.unpack(f.read(TRAILER.size))
    except OSError:
        return None
    if magic != MAGIC or offset < 0 or length <= 0 or offset + length > size:
        return None
    return offset, length


def open_payload(exe: Path) -> zipfile.ZipFile:
    """The payload ZIP is appended to the stub; zipfile copes with the prefix."""
    return zipfile.ZipFile(exe)


def payload_build_id(zf: zipfile.ZipFile, trailer: tuple[int, int]) -> str:
    try:
        return zf.read(BUILD_FILE).decode("utf-8", "replace").strip() or "unknown"
    except KeyError:
        return f"size{trailer[1]:x}"


# --------------------------------------------------------------------------- fs helpers
def is_reparse(path: Path) -> bool:
    try:
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (AttributeError, OSError):
        return path.is_symlink()


def make_junction(link: Path, target: Path) -> bool:
    """Directory junction (no admin needed). Returns True on success."""
    if os.name != "nt":
        try:
            link.symlink_to(target, target_is_directory=True)
            return True
        except OSError:
            return False
    try:
        res = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
        )
        return res.returncode == 0
    except OSError:
        return False


def link_persistent_data(app_dir: Path, root: Path) -> None:
    """Point app_dir/output and app_dir/logs at root/data/<name> so upgrades keep data."""
    data_root = root / "data"
    for name in DATA_LINKS:
        target = data_root / name
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log(f"[GBV]   ! could not create {target}: {e}")
            continue
        link = app_dir / name
        if link.exists() and is_reparse(link):
            continue
        if link.exists():
            # section shipped empty folders: move anything the app left there, then replace
            try:
                for item in link.iterdir():
                    dest = target / item.name
                    if not dest.exists():
                        shutil.move(str(item), str(dest))
                link.rmdir()
            except OSError:
                log(f"[GBV]   ! keeping {name}/ inside the extracted folder (could not move it)")
                continue
        if not link.exists():
            if not make_junction(link, target):
                log(f"[GBV]   ! {name}/ stays inside the extracted folder — case data will not "
                    f"survive an upgrade")
            else:
                log(f"[GBV]   {name}/ -> {target}")


def cleanup_old_versions(root: Path, keep: Path) -> None:
    try:
        entries = [p for p in root.glob("app-*") if p.is_dir() and p.resolve() != keep.resolve()]
    except OSError:
        return
    for old in entries:
        try:
            shutil.rmtree(old)
            log(f"[GBV]   removed previous version {old.name}")
        except OSError:
            pass  # running elsewhere / locked — harmless


# --------------------------------------------------------------------------- extraction
class Lock:
    """Cooperative lock so two double-clicks don't extract 6 GB twice."""

    def __init__(self, path: Path):
        self.path = path
        self.held = False

    def acquire(self, build_id: str, done: Path) -> None:
        deadline = time.monotonic() + 15 * 60
        while time.monotonic() < deadline:
            if done.is_file():
                return
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {build_id}\n".encode())
                os.close(fd)
                self.held = True
                return
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > LOCK_STALE_SECONDS:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                log("[GBV] Another copy is already unpacking the app — waiting…")
                time.sleep(2.0)
            except OSError as e:
                log(f"[GBV]   ! could not create the extraction lock ({e}) — continuing")
                return
        raise RuntimeError("Timed out waiting for another instance to finish unpacking.")

    def release(self) -> None:
        if self.held:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            self.held = False


def extract(zf: zipfile.ZipFile, target: Path, payload_size: int) -> None:
    infos = [i for i in zf.infolist() if not i.filename.startswith(META_DIR + "/")]
    total = sum(i.file_size for i in infos if not i.is_dir()) or 1

    staging = target.with_name(target.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    log(f"[GBV] Unpacking the app ({human(payload_size)} download, {human(total)} on disk)")
    log(f"[GBV]   -> {target}")
    log("[GBV] This happens only once (or after an upgrade) and can take a few minutes.")

    done_bytes = 0
    next_report = 0.0
    started = time.monotonic()
    for info in infos:
        dest = staging / info.filename.replace("\\", "/")
        if info.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 4 * 1024 * 1024)
        except OSError as e:
            raise RuntimeError(
                f"Could not write {dest} ({e}). Free some disk space and try again."
            ) from e
        done_bytes += info.file_size
        pct = 100.0 * done_bytes / total
        if pct >= next_report:
            elapsed = max(time.monotonic() - started, 0.1)
            log(f"[GBV]   {pct:5.1f}%  {human(done_bytes)} / {human(total)}"
                f"  ({human(done_bytes / elapsed)}/s)")
            next_report = pct + 10.0

    (staging / DONE_MARKER).write_text(
        f"build={target.name}\nextracted={time.strftime('%Y-%m-%d %H:%M:%S')}\n",
        encoding="utf-8",
    )
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)


# --------------------------------------------------------------------------- main
def main(argv: list[str]) -> int:
    exe = self_path()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    force = os.environ.get("GBV_SINGLE_FORCE", "") == "1" or "--force-extract" in argv
    extract_only = "--extract-only" in argv
    passthrough = [a for a in argv if a not in ("--force-extract", "--extract-only")]
    if "--print-dir" in argv or "--payload-info" in argv:
        passthrough = [a for a in passthrough if a not in ("--print-dir", "--payload-info")]

    trailer = read_trailer(exe)
    if trailer is None:
        return die(
            "This file looks incomplete. Copy the WHOLE GBVMonitor.exe (all split parts "
            "recombined, if it was delivered in parts) and try again."
        )
    offset, payload_size = trailer

    try:
        zf = open_payload(exe)
    except (zipfile.BadZipFile, OSError) as e:
        return die(f"The embedded app payload is damaged ({e}). Download the file again.")
    build_id = payload_build_id(zf, trailer)

    if "--payload-info" in argv:
        log(f"[GBV] payload build : {build_id}")
        log(f"[GBV] payload offset: {offset}")
        log(f"[GBV] payload size  : {human(payload_size)}")
        log(f"[GBV] entries       : {len(zf.infolist())}")
        return 0

    root = home_dir()
    app_dir = root / f"app-{build_id}"
    done = app_dir / DONE_MARKER

    if "--print-dir" in argv:
        log(str(app_dir))
        return 0

    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return die(f"Cannot create {root} ({e}). Set GBV_HOME to a writable folder.")

    lock = Lock(root / LOCK_NAME)
    try:
        lock.acquire(build_id, done)
        if force and app_dir.exists():
            shutil.rmtree(app_dir, ignore_errors=True)
        if not done.is_file():
            log("")
            log("=" * 66)
            log(" GBV Monitor — first start: unpacking the local AI toolkit")
            log("=" * 66)
            log("")
            extract(zf, app_dir, payload_size)
        elif "--payload-info" not in argv:
            log(f"[GBV] Using the unpacked app in {app_dir}")
    except RuntimeError as e:
        return die(str(e))
    finally:
        lock.release()
        if "--payload-info" not in argv:
            try:
                zf.close()
            except Exception:
                pass

    link_persistent_data(app_dir, root)
    cleanup_old_versions(root, app_dir)

    inner = app_dir / APP_EXE
    if not inner.is_file():
        return die(
            f"The unpacked app is incomplete (missing {inner.name}). "
            f"Delete {app_dir} and start again."
        )

    if extract_only:
        log(f"[GBV] Unpacked to {app_dir} (launch skipped).")
        return 0

    log(f"[GBV] Starting: {inner}")
    env = dict(os.environ)
    env["GBV_SINGLE_FILE"] = "1"
    env["GBV_APP_DIR"] = str(app_dir)
    try:
        proc = subprocess.Popen([str(inner), *passthrough], cwd=str(app_dir), env=env)
    except OSError as e:
        return die(f"Could not start {inner} ({e}).")
    try:
        return proc.wait()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
