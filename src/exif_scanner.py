"""Multi-root directory scanner, incremental ExifTool cache, RAW+JPG+XMP deduplication, and 3-tier burst folding."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .gear_normalizer import GearNormalizer
from .i18n import t
from .rating_sources import collect_all_software_ratings, merge_rating_pair

RENDERED_EXTS = {".jpg", ".jpeg", ".hif", ".heif", ".tif", ".tiff"}
RAW_EXTS = {
    ".arw",
    ".cr2",
    ".cr3",
    ".nef",
    ".nrw",
    ".raf",
    ".orf",
    ".rw2",
    ".pef",
    ".srw",
    ".dng",
}
IMAGE_EXTS = RENDERED_EXTS | RAW_EXTS
SIDECAR_EXTS = {".xmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".insv", ".lrv"}

DEFAULT_EXCLUDE_DIRS = {
    "backups",
    "backup",
    "clip",
    "lightroom",
    "cache",
    "cache2",
    "previews",
    "@eadir",
    ".git",
    "node_modules",
    "$recycle.bin",
    "system volume information",
    "tools",
    "exiftool_files",
    "恢复jpg星标",
    "清理无星标raw",
}

CACHE_VERSION = 3
EXIFTOOL_BATCH_SIZE = 1000
EXIFTOOL_TAGS = [
    "-FileType",
    "-Make",
    "-Model",
    "-LensModel",
    "-LensID",
    "-Lens",
    "-LensSpec",
    "-FocalLength#",
    "-FocalLengthIn35mmFormat#",
    "-DateTimeOriginal",
    "-SubSecTimeOriginal",
    "-CreateDate",
    "-ModifyDate",
    "-ImageWidth#",
    "-ImageHeight#",
    "-Orientation#",
    "-ReleaseMode",
    "-ReleaseMode2",
    "-ContinuousDrive",
    "-DriveMode",
    "-ShootingMode",
    "-SequenceNumber",
    "-SequenceImageNumber#",
    "-BurstUUID",
    "-ExposureBracketValue#",
    "-BracketShotNumber#",
    "-Rating#",
    "-RatingPercent#",
    "-StarRating#",
    "-Pick#",
    "-PickLabel#",
]


def _scalar_value(val: Any) -> Any:
    if isinstance(val, list):
        for item in val:
            if item not in (None, ""):
                return item
        return None
    return val


def _pick_tag(obj: dict[str, Any], suffix: str) -> Any:
    """Lookup tag regardless of ExifTool -G1 group prefix."""
    if suffix in obj:
        v = _scalar_value(obj[suffix])
        if v not in (None, ""):
            return v
    target = ":" + suffix
    for k, v in obj.items():
        if k.endswith(target):
            sv = _scalar_value(v)
            if sv not in (None, ""):
                return sv
    return None


def _to_float(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s:
        return None
    if "/" in s:
        parts = s.split("/", 1)
        try:
            num = float(parts[0])
            den = float(parts[1])
            return num / den if den != 0 else None
        except ValueError:
            return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            return None
    return None


def _to_int(val: Any) -> Optional[int]:
    f = _to_float(val)
    if f is None:
        return None
    return int(round(f))


def _parse_rating_and_pick(obj: dict[str, Any]) -> tuple[int, int]:
    """Extract (max_rating 0..5, is_picked 0..1) from an ExifTool JSON record."""
    best = 0
    picked = 0
    for k, raw in obj.items():
        tag = k.split(":")[-1]
        if tag not in ("Rating", "StarRating", "RatingPercent", "Pick", "PickLabel"):
            continue
        vals = raw if isinstance(raw, list) else [raw]
        for v in vals:
            iv = _to_int(v)
            if iv is None:
                continue
            if tag == "Pick":
                if iv == 1:
                    picked = 1
            elif tag == "PickLabel":
                if iv == 3:
                    picked = 1
            elif tag == "RatingPercent":
                mapped = (
                    5 if iv >= 88
                    else 4 if iv >= 63
                    else 3 if iv >= 38
                    else 2 if iv >= 13
                    else 1 if iv >= 1
                    else 0
                )
                if mapped > best:
                    best = mapped
            else:
                if 1 <= iv <= 5 and iv > best:
                    best = iv
                elif iv > 5:
                    best = 5
    return best, picked


def _parse_exif_datetime(
    raw: Any, subsec_raw: Any = None
) -> tuple[Optional[str], Optional[int], Optional[float], int]:
    """Parse '2026:01:02 11:26:29' + SubSecTimeOriginal into ('YYYY-MM-DD', hour, epoch_float, has_sub)."""
    if raw is None:
        return None, None, None, 0
    s = str(raw).strip()
    m = re.match(r"^(\d{4})[:-](\d{2})[:-](\d{2})(?:[T\s](\d{2}):(\d{2})(?::(\d{2}))?)?", s)
    if not m:
        return None, None, None, 0
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1990 or mo < 1 or mo > 12 or d < 1 or d > 31:
        return None, None, None, 0
    date_str = f"{y:04d}-{mo:02d}-{d:02d}"
    hour = int(m.group(4)) if m.group(4) is not None else None
    if hour is not None and not (0 <= hour <= 23):
        hour = None
    epoch_float: Optional[float] = None
    has_sub = 0
    if m.group(4) is not None and m.group(5) is not None:
        hh = int(m.group(4))
        mm = int(m.group(5))
        ss = int(m.group(6)) if m.group(6) is not None else 0
        try:
            base_ep = datetime(y, mo, d, hh, mm, ss).timestamp()
            digits = re.sub(r"\D", "", str(subsec_raw).strip()) if subsec_raw is not None else ""
            if digits:
                frac = float("0." + digits)
                epoch_float = round(base_ep + frac, 3)
                has_sub = 1
            else:
                epoch_float = float(int(base_ep))
        except ValueError:
            epoch_float = None
            has_sub = 0
    return date_str, hour, epoch_float, has_sub


def _compact_exif_record(obj: dict[str, Any]) -> dict[str, Any]:
    """Convert raw ExifTool JSON object into a compact cacheable metadata dict."""
    rating, pick = _parse_rating_and_pick(obj)
    dt_raw = (
        _pick_tag(obj, "DateTimeOriginal")
        or _pick_tag(obj, "CreateDate")
        or _pick_tag(obj, "ModifyDate")
    )
    sub_raw = _pick_tag(obj, "SubSecTimeOriginal") if _pick_tag(obj, "DateTimeOriginal") else None
    date_str, hour, epoch_float, has_sub = _parse_exif_datetime(dt_raw, sub_raw)

    bu = str(_pick_tag(obj, "BurstUUID") or "").strip()
    seq_raw = _pick_tag(obj, "SequenceNumber")
    is_explicit_single_seq = str(seq_raw or "").strip().lower() == "single"
    bsn = _to_int(_pick_tag(obj, "BracketShotNumber"))
    ebv = _to_float(_pick_tag(obj, "ExposureBracketValue"))

    seq_num = 0
    if not is_explicit_single_seq:
        sq = _to_int(seq_raw)
        if sq is not None and sq >= 1:
            seq_num = sq
        elif bsn is not None and bsn >= 1:
            seq_num = bsn
    elif bsn is not None and bsn >= 1:
        seq_num = bsn

    drive_parts = [
        str(_pick_tag(obj, tag) or "").strip()
        for tag in ("ReleaseMode", "ReleaseMode2", "ContinuousDrive", "DriveMode", "ShootingMode")
    ]
    drive_text = " ".join(p for p in drive_parts if p).lower()

    if (
        "bracket" in drive_text
        or "aeb" in drive_text
        or "hdr" in drive_text
        or (bsn is not None and bsn >= 1)
        or (ebv is not None and ebv != 0.0)
    ):
        drive_mode = "bracket"
    elif (
        any(tok in drive_text for tok in ("continuous", "burst", "speed priority", "cont.", "hi+"))
        or (seq_num >= 1 and not is_explicit_single_seq)
    ):
        drive_mode = "burst"
    elif is_explicit_single_seq or any(tok in drive_text for tok in ("normal", "single", "self-timer", "remote")):
        drive_mode = "single"
    else:
        drive_mode = ""

    return {
        "ft": str(_pick_tag(obj, "FileType") or "").strip(),
        "mk": str(_pick_tag(obj, "Make") or "").strip(),
        "md": str(_pick_tag(obj, "Model") or "").strip(),
        "lm": str(_pick_tag(obj, "LensModel") or "").strip(),
        "li": str(_pick_tag(obj, "LensID") or "").strip(),
        "ln": str(_pick_tag(obj, "Lens") or "").strip(),
        "ls": str(_pick_tag(obj, "LensSpec") or "").strip(),
        "fl": _to_float(_pick_tag(obj, "FocalLength")),
        "fl35": _to_float(_pick_tag(obj, "FocalLengthIn35mmFormat")),
        "w": _to_int(_pick_tag(obj, "ImageWidth")),
        "h": _to_int(_pick_tag(obj, "ImageHeight")),
        "or": _to_int(_pick_tag(obj, "Orientation")),
        "dt": date_str,
        "hr": hour,
        "ts": epoch_float,
        "sub": has_sub,
        "dm": drive_mode,
        "sq": seq_num,
        "bu": bu,
        "rt": rating,
        "pk": pick,
    }


def _run_exiftool_batch(exiftool_path: str, file_paths: list[str]) -> list[Optional[dict[str, Any]]]:
    """Invoke ExifTool using a UTF-8 argfile and return parsed compact dicts aligned 1:1 with file_paths."""
    if not file_paths:
        return []
    fd, arg_name = tempfile.mkstemp(prefix="lens_note_et_", suffix=".txt")
    os.close(fd)
    arg_path = Path(arg_name)
    try:
        arg_path.write_text("\n".join(file_paths), encoding="utf-8")
        cmd = [
            exiftool_path,
            "-fast",
            "-q",
            "-charset",
            "filename=UTF8",
            "-json",
            "-a",
            "-G1",
            *EXIFTOOL_TAGS,
            "-@",
            str(arg_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode not in (0, 1) or not proc.stdout.strip():
            return [None] * len(file_paths)
        parsed = json.loads(proc.stdout)
        if not isinstance(parsed, list) or len(parsed) != len(file_paths):
            by_src: dict[str, dict[str, Any]] = {}
            if isinstance(parsed, list):
                for item in parsed:
                    src = item.get("SourceFile")
                    if src:
                        by_src[os.path.normpath(src).lower()] = item
            out: list[Optional[dict[str, Any]]] = []
            for fp in file_paths:
                item = by_src.get(os.path.normpath(fp).lower())
                out.append(_compact_exif_record(item) if item else None)
            return out
        return [_compact_exif_record(item) for item in parsed]
    except Exception:
        return [None] * len(file_paths)
    finally:
        try:
            arg_path.unlink(missing_ok=True)
        except Exception:
            pass


def _logical_stem(filename: str) -> str:
    """Map DSC00065.JPG, DSC00065.ARW, DSC00065.xmp, DSC00065.ARW.xmp -> 'dsc00065'."""
    p = Path(filename)
    ext = p.suffix.lower()
    if ext == ".xmp":
        inner = Path(p.stem)
        if inner.suffix.lower() in IMAGE_EXTS:
            return inner.stem.lower()
    return p.stem.lower()


def _should_exclude_dir(dirname: str, exclude_set: set[str]) -> bool:
    low = dirname.lower()
    if low.startswith("."):
        return True
    if low in exclude_set:
        return True
    return False


def _focal_close(p1: dict[str, Any], p2: dict[str, Any]) -> bool:
    """True when two shots match focal length within 2mm (or if focal metadata missing on either)."""
    if p1.get("fl35") is not None and p2.get("fl35") is not None:
        return abs(float(p1["fl35"]) - float(p2["fl35"])) <= 2.0
    if p1.get("fl_phys") is not None and p2.get("fl_phys") is not None:
        return abs(float(p1["fl_phys"]) - float(p2["fl_phys"])) <= 2.0
    return (p1.get("fl35") is None and p2.get("fl35") is None) and (
        p1.get("fl_phys") is None and p2.get("fl_phys") is None
    )


def _apply_burst_folding(photos: list[dict[str, Any]]) -> tuple[int, int]:
    """Three-tier precision burst & AEB folding:
    1) BurstUUID grouping
    2) Explicit hardware single-shot exemption + hardware burst/bracket sequence grouping
    3) Sub-second (<=0.65s) or integer same-second (dt==0s) fallback when drive mode unknown
    """
    for p in photos:
        p["burst_rep"] = 1
        p["burst_rating"] = int(p.get("rating", 0))
        p["burst_pick"] = int(p.get("pick", 0))

    burst_groups = 0
    burst_folded_shots = 0

    def _flush(group: list[dict[str, Any]]) -> None:
        nonlocal burst_groups, burst_folded_shots
        if len(group) <= 1:
            return
        burst_groups += 1
        burst_folded_shots += len(group) - 1
        max_r = max(int(g.get("rating", 0)) for g in group)
        max_p = max(int(g.get("pick", 0)) for g in group)
        rep = max(group, key=lambda g: (int(g.get("rating", 0)), int(g.get("pick", 0)), -float(g.get("ts") or 0.0)))
        for g in group:
            if g is rep:
                g["burst_rep"] = 1
                g["burst_rating"] = max_r
                g["burst_pick"] = max_p
            else:
                g["burst_rep"] = 0
                g["burst_rating"] = int(g.get("rating", 0))
                g["burst_pick"] = int(g.get("pick", 0))

    # Pass 1: BurstUUID exact grouping
    handled_indices: set[int] = set()
    uuid_buckets: dict[tuple[str, str, str], list[tuple[int, dict[str, Any]]]] = {}
    for idx, p in enumerate(photos):
        bu = (p.get("burst_uuid") or "").strip()
        if bu:
            uuid_buckets.setdefault((p["outing"], p["camera"], bu), []).append((idx, p))
            handled_indices.add(idx)

    for items in uuid_buckets.values():
        items.sort(key=lambda x: (float(x[1].get("ts") or 0.0), x[1].get("stem", ""), x[0]))
        _flush([x[1] for x in items])

    # Pass 2: Hardware Drive Mode + SequenceNumber + SubSec fallback
    candidates = [
        (i, p)
        for i, p in enumerate(photos)
        if i not in handled_indices and p.get("ts") is not None
    ]
    candidates.sort(
        key=lambda item: (
            item[1]["outing"],
            item[1]["camera"],
            item[1]["lens"],
            float(item[1]["ts"]),
            item[1].get("stem", ""),
            item[0],
        )
    )

    def _should_merge(prev: dict[str, Any], cur: dict[str, Any]) -> bool:
        if (
            cur["outing"] != prev["outing"]
            or cur["camera"] != prev["camera"]
            or cur["lens"] != prev["lens"]
            or not _focal_close(prev, cur)
        ):
            return False

        dt = float(cur["ts"]) - float(prev["ts"])
        if dt < 0.0:
            return False

        pdm = prev.get("drive_mode") or ""
        cdm = cur.get("drive_mode") or ""
        psq = int(prev.get("seq") or 0)
        csq = int(cur.get("seq") or 0)

        if pdm == "burst" and cdm == "burst":
            if psq >= 1 and csq >= 1:
                return dt <= 2.5 and csq > psq
            return dt <= 1.2

        if pdm == "bracket" and cdm == "bracket":
            if psq >= 1 and csq >= 1:
                return dt <= 4.0 and csq > psq
            return dt <= 3.0

        if pdm == "" and cdm == "":
            if int(prev.get("has_sub") or 0) == 1 and int(cur.get("has_sub") or 0) == 1:
                return dt <= 0.65
            return dt == 0.0

        return False

    cur_group: list[dict[str, Any]] = []
    for _, p in candidates:
        if (p.get("drive_mode") or "") == "single":
            _flush(cur_group)
            cur_group = []
            continue
        if not cur_group:
            cur_group = [p]
            continue
        if _should_merge(cur_group[-1], p):
            cur_group.append(p)
        else:
            _flush(cur_group)
            cur_group = [p]
    _flush(cur_group)

    return burst_groups, burst_folded_shots


def scan_libraries(
    photo_roots: list[Path],
    exiftool_path: str,
    normalizer: GearNormalizer,
    cache_path: Optional[Path] = None,
    exclude_folders: Optional[list[str]] = None,
    use_cache: bool = True,
    use_db: bool = True,
    explicit_catalog: Optional[str] = None,
    lang: str = "zh",
    pixcake_db_root: Optional[str] = None,
    pixpretty_db_root: Optional[str] = None,
) -> dict[str, Any]:
    """Scan all photo_roots read-only, deduplicate RAW+JPG+XMP, merge ratings, and fold bursts."""
    t0 = time.perf_counter()
    resolved_roots: list[Path] = []
    seen_roots: set[str] = set()
    for r in photo_roots:
        rp = r.resolve()
        k = str(rp).lower()
        if rp.is_dir() and k not in seen_roots:
            seen_roots.add(k)
            resolved_roots.append(rp)

    exclude_set = set(DEFAULT_EXCLUDE_DIRS)
    if exclude_folders:
        for item in exclude_folders:
            cleaned = item.strip().lower()
            if cleaned:
                exclude_set.add(cleaned)

    print(t(lang, "scan_start"))
    print(t(lang, "scan_roots", " | ".join(str(r) for r in resolved_roots)))

    # 1. Walk directories & isolate interference
    scanned_files: list[dict[str, Any]] = []
    rendered_count = 0
    raw_count = 0
    xmp_count = 0
    skipped_videos = 0

    for root in resolved_roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not _should_exclude_dir(d, exclude_set))
            dp = Path(dirpath)
            rel_parts = dp.relative_to(root).parts
            for fn in filenames:
                ext = Path(fn).suffix.lower()
                if ext in VIDEO_EXTS:
                    skipped_videos += 1
                    continue
                if ext not in IMAGE_EXTS and ext not in SIDECAR_EXTS:
                    continue
                fp = dp / fn
                try:
                    st = fp.stat()
                except OSError:
                    continue
                if ext in RENDERED_EXTS:
                    rendered_count += 1
                elif ext in RAW_EXTS:
                    raw_count += 1
                else:
                    xmp_count += 1
                scanned_files.append(
                    {
                        "path": str(fp),
                        "norm_key": os.path.normpath(str(fp)).lower(),
                        "dir_norm": os.path.normpath(dirpath).rstrip("\\/").lower(),
                        "stem": _logical_stem(fn),
                        "ext": ext,
                        "mtime_ns": st.st_mtime_ns,
                        "size": st.st_size,
                        "root_name": root.name,
                        "top_sub": rel_parts[0] if rel_parts else None,
                    }
                )

    print(
        t(
            lang,
            "scan_walk_done",
            rendered_count + raw_count + xmp_count,
            rendered_count,
            raw_count,
            xmp_count,
            skipped_videos,
        )
    )

    # 2. Load incremental cache
    cache_entries: dict[str, dict[str, Any]] = {}
    if use_cache and cache_path and cache_path.is_file():
        try:
            loaded = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("version") == CACHE_VERSION:
                raw_entries = loaded.get("entries", {})
                if isinstance(raw_entries, dict):
                    cache_entries = raw_entries
        except Exception:
            cache_entries = {}

    to_scan_idx: list[int] = []
    file_metas: list[Optional[dict[str, Any]]] = [None] * len(scanned_files)
    cache_hits = 0

    for idx, f in enumerate(scanned_files):
        ce = cache_entries.get(f["norm_key"])
        if ce and ce.get("mtime_ns") == f["mtime_ns"] and ce.get("size") == f["size"] and isinstance(ce.get("meta"), dict):
            file_metas[idx] = ce["meta"]
            cache_hits += 1
        else:
            to_scan_idx.append(idx)

    print(t(lang, "scan_cache_hit", cache_hits, len(to_scan_idx)))

    # 3. Scan new/changed files via parallel ExifTool batches
    total_new = len(to_scan_idx)
    if total_new > 0:
        chunks = [
            to_scan_idx[start : min(start + EXIFTOOL_BATCH_SIZE, total_new)]
            for start in range(0, total_new, EXIFTOOL_BATCH_SIZE)
        ]
        max_workers = min(4, max(1, os.cpu_count() or 2), len(chunks))
        done_count = 0
        lock = threading.Lock()

        def _worker(batch_indices: list[int]) -> tuple[list[int], list[Optional[dict[str, Any]]]]:
            batch_paths = [scanned_files[i]["path"] for i in batch_indices]
            return batch_indices, _run_exiftool_batch(exiftool_path, batch_paths)

        if max_workers <= 1:
            for batch_indices in chunks:
                b_idx, results = _worker(batch_indices)
                done_count += len(b_idx)
                print(t(lang, "scan_exif_batch", done_count, total_new))
                for idx, meta in zip(b_idx, results):
                    if meta is not None:
                        file_metas[idx] = meta
                        f = scanned_files[idx]
                        cache_entries[f["norm_key"]] = {"mtime_ns": f["mtime_ns"], "size": f["size"], "meta": meta}
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futs = [pool.submit(_worker, ch) for ch in chunks]
                for fut in as_completed(futs):
                    b_idx, results = fut.result()
                    with lock:
                        done_count += len(b_idx)
                        print(t(lang, "scan_exif_batch", done_count, total_new))
                    for idx, meta in zip(b_idx, results):
                        if meta is not None:
                            file_metas[idx] = meta
                            f = scanned_files[idx]
                            cache_entries[f["norm_key"]] = {"mtime_ns": f["mtime_ns"], "size": f["size"], "meta": meta}

    active_keys = {f["norm_key"] for f in scanned_files}
    root_prefixes = tuple(os.path.normpath(str(r)).lower() + os.sep for r in resolved_roots)
    pruned_cache = {
        k: v
        for k, v in cache_entries.items()
        if k in active_keys or not k.startswith(root_prefixes)
    }

    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps({"version": CACHE_VERSION, "entries": pruned_cache}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    # 4. Read local photo software databases (fast live read)
    db_ratings: dict[tuple[str, str], tuple[int, int]] = {}
    db_labels: list[str] = []
    lock_warnings: list[str] = []
    db_warnings: list[str] = []
    if use_db:
        print(t(lang, "scan_db_start"))
        db_ratings, db_labels, lock_warnings, db_warnings = collect_all_software_ratings(
            resolved_roots,
            explicit_catalog=explicit_catalog,
            allowed_exts=IMAGE_EXTS,
            lang=lang,
            pixcake_db_root=pixcake_db_root,
            pixpretty_db_root=pixpretty_db_root,
        )

    # 5. Group by (dir_norm, stem) to deduplicate RAW + JPG + XMP
    groups: dict[tuple[str, str], list[tuple[dict[str, Any], Optional[dict[str, Any]]]]] = {}
    for f, m in zip(scanned_files, file_metas):
        key = (f["dir_norm"], f["stem"])
        groups.setdefault(key, []).append((f, m))

    outing_origins: dict[str, set[str]] = {}
    for (_, _), items in groups.items():
        has_img = any(f["ext"] in IMAGE_EXTS for f, _ in items)
        if not has_img:
            continue
        f0 = items[0][0]
        if f0["top_sub"]:
            outing_origins.setdefault(f0["top_sub"], set()).add(f0["root_name"])

    photos: list[dict[str, Any]] = []
    deduped_pairs = 0
    missing_exif_count = 0
    unknown_lens_count = 0
    rated_count = 0

    for (dnorm, stem), items in groups.items():
        img_items = [(f, m) for f, m in items if f["ext"] in IMAGE_EXTS]
        if not img_items:
            continue

        rendered_in_group = sum(1 for f, _ in img_items if f["ext"] in RENDERED_EXTS)
        raw_in_group = sum(1 for f, _ in img_items if f["ext"] in RAW_EXTS)
        if rendered_in_group > 0 and raw_in_group > 0:
            deduped_pairs += 1

        img_items.sort(key=lambda x: (0 if x[0]["ext"] in RENDERED_EXTS else 1, x[0]["ext"]))

        def _first_str(field: str) -> str:
            for _, m in img_items:
                if m and m.get(field):
                    return str(m[field])
            return ""

        def _first_num(field: str) -> Any:
            for _, m in img_items:
                if m and m.get(field) is not None:
                    return m[field]
            return None

        make = _first_str("mk")
        model = _first_str("md")
        lens_model = _first_str("lm")
        lens_id = _first_str("li")
        lens_tag = _first_str("ln")
        lens_spec = _first_str("ls")

        fl_phys_raw = _first_num("fl")
        fl_35_raw = _first_num("fl35")
        width = _first_num("w")
        height = _first_num("h")
        orient_num = _first_num("or")

        drive_mode = _first_str("dm")
        seq_num = int(_first_num("sq") or 0)
        burst_uuid = _first_str("bu")

        date_str = None
        hour = None
        ts_val = None
        has_sub = 0
        for _, m in img_items:
            if m and m.get("dt"):
                date_str = m["dt"]
                hour = m.get("hr")
                ts_val = m.get("ts")
                has_sub = int(m.get("sub") or 0)
                break

        f_rep = img_items[0][0]
        if not date_str:
            folder_cand = f_rep["top_sub"] or Path(f_rep["dir_norm"]).name
            dm = re.match(r"^(\d{4})-(\d{2})-(\d{2})", folder_cand)
            if dm:
                date_str = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"
            else:
                dt_obj = datetime.fromtimestamp(f_rep["mtime_ns"] / 1e9)
                date_str = dt_obj.strftime("%Y-%m-%d")
                if hour is None:
                    hour = dt_obj.hour

        if hour is None:
            hour = 12

        best_rating, best_pick = 0, 0
        for _, m in items:
            if m:
                best_rating, best_pick = merge_rating_pair(
                    (best_rating, best_pick),
                    int(m.get("rt") or 0),
                    int(m.get("pk") or 0),
                )
        if (dnorm, stem) in db_ratings:
            dr, dp = db_ratings[(dnorm, stem)]
            best_rating, best_pick = merge_rating_pair((best_rating, best_pick), dr, dp)

        if best_rating >= 1 or best_pick == 1:
            rated_count += 1

        if not make and not model and fl_phys_raw is None:
            missing_exif_count += 1

        cam_name, gear_type, crop_factor = normalizer.normalize_camera(make, model)
        lens_name = normalizer.normalize_lens(
            lens_model, lens_id, lens_tag, lens_spec, gear_type=gear_type, camera_name=cam_name
        )
        if not lens_name:
            unknown_lens_count += 1

        fl_35, fl_phys = normalizer.compute_focal_lengths(fl_phys_raw, fl_35_raw, crop_factor)
        orient = normalizer.compute_orientation(width, height, orient_num)

        if f_rep["top_sub"]:
            top_sub = f_rep["top_sub"]
            if len(outing_origins.get(top_sub, set())) > 1:
                outing_name = f"{f_rep['root_name']}/{top_sub}"
            else:
                outing_name = top_sub
        else:
            outing_name = date_str

        photos.append(
            {
                "date": date_str,
                "hour": int(hour),
                "ts": ts_val,
                "has_sub": has_sub,
                "stem": stem,
                "drive_mode": drive_mode,
                "seq": seq_num,
                "burst_uuid": burst_uuid,
                "outing": outing_name,
                "root": f_rep["root_name"],
                "camera": cam_name,
                "lens": lens_name,
                "gear_type": gear_type,
                "fl35": fl_35,
                "fl_phys": fl_phys,
                "rating": best_rating,
                "pick": best_pick,
                "orient": orient,
            }
        )

    # 6. Fold burst & AEB sequences via 3-tier precision engine
    burst_groups, burst_folded_shots = _apply_burst_folding(photos)

    elapsed = time.perf_counter() - t0
    pct = (rated_count / len(photos) * 100.0) if photos else 0.0
    print(t(lang, "build_summary", len(photos), deduped_pairs, rated_count, pct, elapsed))

    return {
        "photos": photos,
        "health": {
            "logical_photos": len(photos),
            "rendered_files": rendered_count,
            "raw_files": raw_count,
            "xmp_files": xmp_count,
            "deduped_pairs": deduped_pairs,
            "burst_groups": burst_groups,
            "burst_folded_shots": burst_folded_shots,
            "skipped_videos": skipped_videos,
            "missing_exif": missing_exif_count,
            "unknown_lens": unknown_lens_count,
            "rated_photos": rated_count,
            "db_sources": db_labels,
            "lock_warnings": lock_warnings,
            "db_warnings": db_warnings,
            "scanned_roots": [str(r) for r in resolved_roots],
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_sec": round(elapsed, 2),
        },
    }
