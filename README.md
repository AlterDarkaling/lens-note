# lens-note (镜头笔记)

[English](README.md) | [简体中文](README.zh-CN.md)

**lens-note** is a 100% offline, zero-dependency photography data retrospective generator built with **Python 3 (stdlib only) + ExifTool**. Point it at one or multiple photo folders (e.g. `2025` and `2026`), and it generates a single portable HTML dashboard analyzing your **focal length habits, keeper rates (star ratings), lens ROI, shooting rhythm, and golden hour performance**.

## Why lens-note?

- **Keeper Rate First (not just shutter counts)**: Measures which camera, lens, focal length, and shoot actually produce your **star-rated / picked** keepers.
- **Precision Burst Folding (one-click toggle)**: Uses hardware EXIF drive mode (`ReleaseMode`/`ContinuousDrive`), `SequenceNumber`, `BurstUUID`, AEB bracketing, and sub-second fallback (`<=0.65s`) to fold true bursts into 1 composition (inheriting max star/pick) without misclassifying normal single shots.
- **RAW + JPG Deduplication**: Pairs `DSC00001.JPG` + `DSC00001.ARW` + `DSC00001.xmp` in the same folder into **1 logical photo**, never double-counting shutters.
- **Multi-Software Rating Fusion (Read-Only)**: Combines embedded EXIF/XMP ratings, `.xmp` sidecars, and local catalogs from **Lightroom Classic** (`.lrcat`), **Capture One**, **PixCake**, **PixPretty**, and **Luminar Neo**.
- **Multi-Root & Interference Isolation**: Combine `2025/` and `2026/` while ignoring sibling unrelated folders and excluding `Lightroom/`, `backups/`, `CLIP/`, `.git/`, etc.
- **5 Auto-Hiding Gear Types & 7 Standard FOV Tiers**: Supports **Camera / Drone / Gimbal (e.g. Osmo Pocket 3) / Action Cam / Phone** (auto-hiding unused types), 7 industry-standard 35mm-eq focal length tiers (`≤20` / `21-30` / `31-44` / `45-64` / `65-115` / `116-250` / `>250`), plus lens drill-down to inspect any zoom lens's internal focal heatmap.
- **100% Offline Single-File HTML**: Pure inline SVG & Canvas charts, **Darkroom / Archival Paper** dual themes, live **EN / 中文** toggle, and 2x **PNG share card export**.
- **Parallel Incremental EXIF Cache**: Scans with multi-worker ExifTool batches and caches by `(path, mtime, size)` so subsequent updates take seconds. Never writes to your photo folders.

## Quick Start

1. Ensure **Python 3.9+** is installed (no `pip install` needed).
2. Double-click **`Start-LensNote.bat`** (or drag one or multiple photo folders directly onto `Start-LensNote.bat`).
   - If ExifTool is missing, it can auto-download the official 64-bit Windows build into `tools\`.
3. Generated HTML reports are saved in `output\` and opened automatically in your browser.

## CLI Usage

```bash
# Multi-year combined recap
python lens_note.py "D:\Photos\2025" "D:\Photos\2026"

# Skip reading photo software DBs (embedded & .xmp only)
python lens_note.py "D:\Photos\2026" --no-db

# Force full EXIF rescan
python lens_note.py "D:\Photos\2026" --no-cache

# Run isolated automated tests
python -m unittest discover -s tests -v
```

## License

[MIT License](LICENSE)
