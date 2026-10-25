========================================================
  Facebook GBV & Hate-Speech Monitor — portable edition
========================================================

WHAT THIS IS
  A survey/documentation tool for civil-society teams: collect posts from a
  Facebook page, have the AI engine bundled with this tool classify gender-based
  cyberviolence & hate speech against women (fully local — nothing is sent to a
  cloud service), and export CSV / JSON / Markdown / PDF documentation.

  The whole toolkit — app, Python runtime, AI models, browser — is inside ONE
  file: GBVMonitor.exe. Nothing to install: no Python, no LM Studio, no drivers,
  no account, no admin rights.

HOW TO RUN (single-file edition)
  1) Double-click GBVMonitor.exe.
  2) First start only: the console unpacks the toolkit (a few minutes) into
     %LOCALAPPDATA%\GBVMonitor\app-<build>.
  3) Wait 15-90 s: the console then shows the local model loading — the
     interface opens itself in your browser.
  Every later start skips step 2 and goes straight to step 3.

  Your work does NOT live next to the exe:
     %LOCALAPPDATA%\GBVMonitor\data\output   reports, screenshots, gbv.db
     %LOCALAPPDATA%\GBVMonitor\data\logs     engine logs (diagnostics)
  Open the folder any time with:  GBVMonitor.exe --print-dir
  Keeps working across upgrades (a new exe unpacks a new app-<build>, your data
  folder is re-linked and kept).

IF YOU GOT THIS FILE INSTEAD OF THE SINGLE EXE
  You are inside the unpacked app folder. Start GBVMonitor.exe directly
  (everything it needs is in the folders below); the data folders are the
  local output\ and logs\.

SCHEDULER MODE (optional, without the window)
  GBVMonitor.exe monitor run-due --enrich --verify   one pass over watchlists
  GBVMonitor.exe monitor watch --interval 900        continuous loop
  GBVMonitor.exe monitor add-watch <page-url> --label "Label" --cadence-hours 24
  (Use Task Scheduler to run these regularly.)

FILE INTEGRITY
  Ship/keep GBVMonitor.exe.sha256 next to the exe and verify after transfer:
     certutil -hashfile GBVMonitor.exe SHA256
  If the file was cut short, the exe closes instantly with no message —
  re-copy it (or recombine the .001/.002/... parts with copy /b).

FOLDERS (inside the unpacked app)
  models\         the AI models (replace with another .gguf to change engines)
  runtime\        engine binaries (cpu = works everywhere, vulkan = GPU)
  ms-playwright\  bundled Chromium for browsing Facebook pages
  _internal\      Python runtime + app source (do not edit)

REQUIREMENTS
  Windows 10/11 x64 · ~7 GB free RAM for the shipped 7B model (~7 GB free disk
  for the unpack folder) · 8 GB disk total. On 8 GB laptops use a 3B model.

LEGAL, ETHICS & SAFETY NOTES
  * Facebook's Terms prohibit automated collection — use a DEDICATED account,
    not your personal one; your account may be restricted.
  * Your session cookies (c_user/xs) give full access to that Facebook
    account: only in memory for this session, never written by this tool.
    Never share them.
  * AI output is a first-pass flag, NOT proof: every flagged item must be
    reviewed by a human before being used as evidence.
  * Authors are pseudonymised by default. Content may contain third-party
    personal data — protect your export folder and follow your organisation's
    data-protection rules.
  * Reviewing this material can be distressing: plan for support.
