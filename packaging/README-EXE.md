# Portable distribution — one single `GBVMonitor.exe`

Goal: **one file you can send to anyone.** They double-click `GBVMonitor.exe` and the
whole tool runs — web UI, local AI model, embedding model, Chromium — with **zero
installs**: no Python, no LM Studio, no cloud, no admin rights.

```
GBVMonitor.exe          ← the entire distribution (≈ 6 GB, self-extracting)
GBVMonitor.exe.sha256   ← checksum to verify the transfer
```

## What the end user sees

1. Double-click `GBVMonitor.exe`.
2. A console opens: *"first start: unpacking the local AI toolkit"* with a progress
   read-out (one time only, 2–10 min depending on disk).
3. The app unpacks itself to `%LOCALAPPDATA%\GBVMonitor\app-<build>` and starts.
4. The console shows the bundled model loading, then the browser opens the interface.

Later starts skip step 2 entirely (~1 s) and go straight to the app.
`output/` (case dataset, reports, screenshots) and `logs/` live in
`%LOCALAPPDATA%\GBVMonitor\data` and **survive upgrades**: a new build is unpacked
next to the old one, the old `app-<build>` folder is deleted, and the data folder
is re-linked automatically.

Nothing is written to the registry, no installer is involved, and uninstalling is
"delete the exe and `%LOCALAPPDATA%\GBVMonitor`".

```
%LOCALAPPDATA%\GBVMonitor\
├── app-<build>\        ← unpacked app (do not edit; replaced on upgrade)
│   ├── GBVMonitor.exe  ← the real launcher (engines + Streamlit UI)
│   ├── _internal\      ← Python runtime + app source + libs
│   ├── runtime\        ← llama.cpp engine binaries (vulkan = GPU, cpu = fallback)
│   ├── models\         ← chat GGUF (Qwen2.5-7B-Instruct Q4_K_M) + nomic-embed
│   ├── ms-playwright\  ← bundled Chromium used by the scraper
│   ├── output\  -> ..\data\output   (junction)
│   └── logs\    -> ..\data\logs     (junction)
└── data\
    ├── output\         ← your reports, screenshots and gbv.db
    └── logs\           ← engine logs for diagnostics
```

## How it is built

`packaging/make_single_exe.py`:

1. compiles `packaging/bootstrap.py` into a small one-file stub with PyInstaller,
2. packs the assembled `dist/GBVMonitor/` folder into a ZIP64 payload
   (GGUF/PNG stay stored, everything else is deflated),
3. appends payload + a 24-byte trailer (`GBV1PKG\0`, offset, size) to the stub.

The stub opens **itself** as a zip (zipfile finds the central directory at the end),
untars/checks the build id, extracts once, links the data folders, and launches the
inner `GBVMonitor.exe` with every CLI argument passed through — so scheduler mode
works identically from the single file:

```
GBVMonitor.exe monitor run-due --enrich --verify
GBVMonitor.exe monitor watch --interval 900
GBVMonitor.exe monitor add-watch <page-url> --label "X"
```

## Build it

### Option A — one command on Windows

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

Needs Python 3.10–3.13 64-bit, internet, ~13 GB free disk (6 GB bundle + 6 GB exe).
It creates `.venv-build`, downloads the llama.cpp binaries + GGUF models, installs
Chromium, runs PyInstaller, assembles `dist/GBVMonitor/`, builds
`dist/GBVMonitor.exe` (+ `.sha256`) and a folder zip.

Useful switches:

| Switch | Effect |
|---|---|
| `-SkipVulkan` | CPU engine only (smaller, no GPU) |
| `-ChatPrefix qwen2.5-3b-instruct-q4_k_m` | ship the lighter 3B model |
| `-SkipSingleExe` | stop after the folder bundle (no single file) |
| `-SkipZip` | no folder zip |

### Option B — GitHub Actions (no Windows machine needed)

Run **Actions → build-windows-exe → Run workflow**. Inputs: `chat_prefix`, `backends`,
`publish_release`. The job builds the single exe and uploads it as `GBVMonitor-single-exe`
split into ≤1.5 GiB parts (`GBVMonitor.exe.001`, `.002`, …) plus the `.sha256`, because
GitHub caps release assets at 2 GiB per file.

Recombine on Windows (CMD):

```bat
copy /b GBVMonitor.exe.001+GBVMonitor.exe.002+GBVMonitor.exe.003+GBVMonitor.exe.004 GBVMonitor.exe
certutil -hashfile GBVMonitor.exe SHA256
```

Then compare with `GBVMonitor.exe.sha256`.

## Stub options (support & debugging)

| Invocation | Purpose |
|---|---|
| `GBVMonitor.exe` | normal start |
| `GBVMonitor.exe --payload-info` | show build id, payload size, entry count (no unpack) |
| `GBVMonitor.exe --print-dir` | print the extraction folder (where reports/models live) |
| `GBVMonitor.exe --extract-only` | unpack but don't launch |
| `GBVMonitor.exe --force-extract`, or `GBV_SINGLE_FORCE=1` | re-unpack from scratch |
| `GBVMonitor.exe --ctx 4096 --no-browser` | forwarded to the app launcher |
| env `GBV_HOME=D:\GBV` | keep the 7 GB unpack folder on another drive |

## Engine tuning (forwarded to llama.cpp)

| Variable | Meaning |
|---|---|
| `GBV_BACKEND` | force `cpu` / `vulkan` (default: vulkan → cpu fallback) |
| `GBV_CTX` | model context window (default 8192; also `--ctx N` at launch) |
| `GBV_ARGS` | extra llama-server flags (e.g. `--no-webui -t 8 -ngl 99`) |

## Distribution tips

* **Always ship the `.sha256`.** A 6 GB transfer that was cut short produces an exe
  that dies instantly with no message (PyInstaller's stub cannot start) — that is
  *always* an incomplete/corrupted file, not a missing dependency.
* Free space needed on the target machine: ~7 GB in `%LOCALAPPDATA%` (plus the exe).
* RAM for the shipped 7B model: ~7 GB free. Ship the 3B model for 8 GB laptops.
* The exe is unsigned, so SmartScreen shows *"Windows protected your PC"* →
  **More info → Run anyway**. Some antivirus engines flag every self-extracting exe;
  sign the binary (Authenticode) or whitelist it in the organisation.
* A USB stick or any file host works. GitHub itself only accepts ≤2 GiB per asset,
  hence the split parts there.
* Keep the **folder edition** (`dist/GBVMonitor/` zip) as a fallback for locked-down
  machines where self-extracting exes are blocked.

## Changing the model later

Replace the GGUF in `models/` (folder edition) or rebuild with
`-ChatPrefix <repo prefix>` / `--chat-prefix`. The launcher picks the first single-file
GGUF, or the first shard of a split one (`*-00001-of-*.gguf`). Keep an
`*embed*gguf` (nomic-embed-text) for OSINT theme clustering; restart to apply.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Window flashes and closes instantly | incomplete download → verify the SHA-256 | 
| SmartScreen / antivirus block | unsigned self-extractor → *More info → Run anyway* / whitelist |
| "Cannot create %LOCALAPPDATA%\GBVMonitor" | no write access / full disk → set `GBV_HOME` to a writable folder |
| Stuck at "Unpacking …" with no progress | another instance holds the lock (it waits) or disk is full |
| Console says the chat model failed to load | see `%LOCALAPPDATA%\GBVMonitor\data\logs\engine-chat.log`; usually RAM (close apps / use the 3B model) |
| 0 posts collected | Facebook DOM drift, not packaging — see the main README |
| Port already in use | the launcher always picks a free port above 8501 |
