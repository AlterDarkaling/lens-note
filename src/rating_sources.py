"""Read-only multi-software photo rating discovery and extraction.

Ported from E:/Code/lightroom-raw-toolkit/lib/PhotoMetaCommon.ps1:
- Adobe Lightroom Classic (.lrcat)
- Capture One (.cocatalogdb / .cosessiondb)
- PixCake / 像素蛋糕 (project.db)
- PixPretty / 影像之匠 (projects*.db)
- Luminar Neo (.luminarneo)
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import sqlite3
import string
from pathlib import Path
from typing import Iterable, Optional

from .i18n import t


def merge_rating_pair(
    current: Optional[tuple[int, int]],
    new_rating: int,
    new_pick: int,
) -> tuple[int, int]:
    """Merge (rating 0..5, pick 0..1) keeping max rating and max pick."""
    cur_r, cur_p = current if current is not None else (0, 0)
    r = max(cur_r, max(0, min(5, int(new_rating))))
    p = 1 if (cur_p == 1 or new_pick == 1) else 0
    return r, p


def _sqlite_ro_connect(db_path: Path, timeout: float = 5.0) -> sqlite3.Connection:
    uri = db_path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=timeout)


def _norm_dir_key(dir_path: str) -> str:
    try:
        p = os.path.abspath(os.path.normpath(dir_path))
    except Exception:
        p = os.path.normpath(dir_path)
    return p.rstrip("\\/").lower()


def _dir_in_scopes(dir_lower: str, scope_norms: list[str]) -> bool:
    if not scope_norms:
        return True
    for s in scope_norms:
        if dir_lower == s or dir_lower.startswith(s + os.sep) or dir_lower.startswith(s + "/"):
            return True
    return False


def _windows_special_dirs() -> list[Path]:
    dirs: list[Path] = []
    home = Path.home()
    for sub in ("Documents", "Pictures"):
        p = home / sub
        if p.is_dir():
            dirs.append(p)
    if os.name == "nt":
        try:
            # Query shell folders in case Documents/Pictures were relocated (e.g. D:\Windows\文档)
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                for val_name in ("Personal", "My Pictures"):
                    try:
                        raw, _ = winreg.QueryValueEx(k, val_name)
                        expanded = os.path.expandvars(str(raw))
                        p = Path(expanded)
                        if p.is_dir() and p not in dirs:
                            dirs.append(p)
                    except OSError:
                        pass
        except Exception:
            pass
    return dirs


def discover_lightroom_catalogs(photo_roots: list[Path], explicit_catalog: Optional[str] = None) -> list[Path]:
    if explicit_catalog:
        p = Path(explicit_catalog)
        if p.is_file() and p.suffix.lower() == ".lrcat":
            return [p.resolve()]
        return []

    search_dirs: list[Path] = []
    for root in photo_roots:
        parent = root.resolve().parent
        lr_dir = parent / "Lightroom"
        if lr_dir.is_dir() and lr_dir not in search_dirs:
            search_dirs.append(lr_dir)
    for sp in _windows_special_dirs():
        lr_dir = sp / "Lightroom"
        if lr_dir.is_dir() and lr_dir not in search_dirs:
            search_dirs.append(lr_dir)

    found: list[Path] = []
    seen: set[str] = set()
    for d in search_dirs:
        cands = []
        try:
            for item in d.iterdir():
                if item.is_file() and item.suffix.lower() == ".lrcat":
                    if not re.search(r"(Backup|Previews|Sync|Helper|Temporary)", item.name, re.I):
                        cands.append(item)
        except OSError:
            continue
        cands.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        if cands:
            resolved = cands[0].resolve()
            key = str(resolved).lower()
            if key not in seen:
                seen.add(key)
                found.append(resolved)
    return found


def discover_capture_one_catalogs(photo_roots: list[Path], explicit_catalog: Optional[str] = None) -> list[Path]:
    if explicit_catalog:
        p = Path(explicit_catalog)
        if p.is_file() and p.suffix.lower() in (".cocatalogdb", ".cosessiondb"):
            return [p.resolve()]
        return []

    search_dirs: list[Path] = []
    for root in photo_roots:
        r = root.resolve()
        if r.is_dir():
            search_dirs.append(r)
        parent = r.parent
        if parent.is_dir():
            try:
                for sub in parent.iterdir():
                    if sub.is_dir() and sub.name.lower().startswith("capture one"):
                        search_dirs.append(sub)
            except OSError:
                pass
    for sp in _windows_special_dirs():
        try:
            for sub in sp.iterdir():
                if sub.is_dir() and sub.name.lower().startswith("capture one"):
                    search_dirs.append(sub)
        except OSError:
            pass

    found: list[Path] = []
    seen: set[str] = set()
    for sd in search_dirs:
        for dirpath, dirnames, filenames in os.walk(sd):
            rel_depth = Path(dirpath).relative_to(sd).parts
            if len(rel_depth) >= 2:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if "backup" not in d.lower()]
            for fn in filenames:
                low = fn.lower()
                if (low.endswith(".cocatalogdb") or low.endswith(".cosessiondb")) and not re.search(
                    r"(Backup|Previews)", fn, re.I
                ):
                    p = (Path(dirpath) / fn).resolve()
                    k = str(p).lower()
                    if k not in seen:
                        seen.add(k)
                        found.append(p)
    found.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return found[:5]


def discover_pixcake_dbs(explicit_root: Optional[str] = None) -> list[Path]:
    if explicit_root and explicit_root.upper() == "NONE":
        return []
    roots: list[Path] = []
    if explicit_root:
        roots.append(Path(explicit_root))
    else:
        appdata = os.environ.get("APPDATA", "")
        localapp = os.environ.get("LOCALAPPDATA", "")
        if appdata:
            for sub in ("PixCake-qt_pro/db", "PixCake-qt/db", "PixCake/db"):
                roots.append(Path(appdata) / sub)
        if localapp:
            roots.append(Path(localapp) / "PixCake-qt_pro/db")

    found: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        if not r.is_dir():
            continue
        for f in r.rglob("project.db"):
            if f.is_file():
                rf = f.resolve()
                k = str(rf).lower()
                if k not in seen:
                    seen.add(k)
                    found.append(rf)
    found.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return found


def discover_pixpretty_dbs(explicit_root: Optional[str] = None) -> list[Path]:
    if explicit_root and explicit_root.upper() == "NONE":
        return []
    roots: list[Path] = []
    if explicit_root:
        roots.append(Path(explicit_root))
    else:
        localapp = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")
        if localapp:
            roots.append(Path(localapp) / "YanYu Software" / "PixPretty" / "config")
        if appdata:
            roots.append(Path(appdata) / "YanYu Software" / "PixPretty" / "config")

    found: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        if not r.is_dir():
            continue
        for f in r.rglob("projects*.db"):
            if f.is_file():
                if re.search(r"(-wal|-shm|backup)", f.name, re.I) or "backup" in str(f.parent).lower():
                    continue
                rf = f.resolve()
                k = str(rf).lower()
                if k not in seen:
                    seen.add(k)
                    found.append(rf)
    found.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return found


def discover_luminar_catalogs(photo_roots: list[Path], explicit_catalog: Optional[str] = None) -> list[Path]:
    if explicit_catalog:
        p = Path(explicit_catalog)
        if p.is_file() and p.suffix.lower() == ".luminarneo":
            return [p.resolve()]
        return []

    search_dirs: list[Path] = []
    for root in photo_roots:
        r = root.resolve()
        if r.is_dir():
            search_dirs.append(r)
        parent = r.parent
        if parent.is_dir():
            search_dirs.append(parent)
            try:
                for sub in parent.iterdir():
                    if sub.is_dir() and sub.name.lower().startswith("luminar neo"):
                        search_dirs.append(sub)
            except OSError:
                pass
    for sp in _windows_special_dirs():
        try:
            for sub in sp.iterdir():
                if sub.is_dir() and sub.name.lower().startswith("luminar neo"):
                    search_dirs.append(sub)
        except OSError:
            pass

    found: list[Path] = []
    seen: set[str] = set()
    for sd in search_dirs:
        if not sd.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(sd):
            rel_depth = Path(dirpath).relative_to(sd).parts
            if len(rel_depth) >= 2:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if not re.search(r"(Backup|Cache)", d, re.I)]
            for fn in filenames:
                if fn.lower().endswith(".luminarneo") and not re.search(r"(Backup|Cache)", fn, re.I):
                    p = (Path(dirpath) / fn).resolve()
                    k = str(p).lower()
                    if k not in seen:
                        seen.add(k)
                        found.append(p)
    found.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return found[:5]


def read_lightroom_ratings(
    cat_path: Path,
    allowed_exts: Optional[set[str]] = None,
    scope_norms: Optional[list[str]] = None,
) -> dict[tuple[str, str], tuple[int, int]]:
    ratings: dict[tuple[str, str], tuple[int, int]] = {}
    ext_where = ""
    if allowed_exts:
        quoted = ",".join("'" + e.lstrip(".").lower().replace("'", "''") + "'" for e in sorted(allowed_exts))
        ext_where = f" AND LOWER(f.extension) IN ({quoted})"

    sql = f"""
        SELECT rf.absolutePath, fld.pathFromRoot, f.baseName,
               MAX(CAST(COALESCE(img.rating, 0) AS INTEGER)),
               MAX(CASE WHEN CAST(COALESCE(img.pick, 0) AS INTEGER) = 1 THEN 1 ELSE 0 END)
        FROM Adobe_images img
        JOIN AgLibraryFile f ON img.rootFile = f.id_local
        JOIN AgLibraryFolder fld ON f.folder = fld.id_local
        JOIN AgLibraryRootFolder rf ON fld.rootFolder = rf.id_local
        WHERE (COALESCE(img.rating, 0) >= 1 OR COALESCE(img.pick, 0) = 1){ext_where}
        GROUP BY rf.absolutePath, fld.pathFromRoot, LOWER(f.baseName)
    """
    con = _sqlite_ro_connect(cat_path)
    try:
        for absp, pfr, bn, rt, pk in con.execute(sql):
            full_dir = os.path.join(absp or "", pfr or "")
            dkey = _norm_dir_key(full_dir)
            if scope_norms and not _dir_in_scopes(dkey, scope_norms):
                continue
            bkey = (bn or "").strip().lower()
            if not bkey:
                continue
            key = (dkey, bkey)
            ratings[key] = merge_rating_pair(ratings.get(key), int(rt or 0), int(pk or 0))
    finally:
        con.close()
    return ratings


def read_capture_one_ratings(
    cat_path: Path,
    allowed_exts: Optional[set[str]] = None,
    scope_norms: Optional[list[str]] = None,
) -> dict[tuple[str, str], tuple[int, int]]:
    ratings: dict[tuple[str, str], tuple[int, int]] = {}
    cat_dir = str(cat_path.resolve().parent)
    sql = """
        SELECT COALESCE(p.ZWINROOT, ''), COALESCE(p.ZRELATIVEPATH, ''), i.ZIMAGEFILENAME,
               MAX(CAST(COALESCE(m.ZBASIC_RATING, 0) AS INTEGER))
        FROM ZVARIANTMETADATA m
        JOIN ZVARIANTLAYER l ON m.ZLAYER = l.Z_PK
        JOIN ZVARIANT v ON l.ZVARIANT = v.Z_PK
        JOIN ZIMAGE i ON v.ZIMAGE = i.Z_PK
        JOIN ZPATHLOCATION p ON i.ZIMAGELOCATION = p.Z_PK
        WHERE COALESCE(m.ZBASIC_RATING, 0) >= 1
          AND COALESCE(i.ZISTRASHED, 0) = 0
        GROUP BY p.Z_PK, LOWER(i.ZIMAGEFILENAME)
    """
    con = _sqlite_ro_connect(cat_path)
    try:
        for winroot, relp, fn, rt in con.execute(sql):
            if not fn:
                continue
            ext = Path(fn).suffix.lower()
            if allowed_exts and ext not in allowed_exts:
                continue
            base_root = winroot.strip() if winroot and winroot.strip() else cat_dir
            rel_clean = (relp or "").strip().lstrip("\\/")
            dir_part = os.path.join(base_root, rel_clean) if rel_clean else base_root
            dkey = _norm_dir_key(dir_part)
            if scope_norms and not _dir_in_scopes(dkey, scope_norms):
                continue
            bkey = Path(fn).stem.lower()
            key = (dkey, bkey)
            ratings[key] = merge_rating_pair(ratings.get(key), int(rt or 0), 0)
    finally:
        con.close()
    return ratings


def read_pixcake_ratings(
    db_paths: Iterable[Path],
    allowed_exts: Optional[set[str]] = None,
    scope_norms: Optional[list[str]] = None,
) -> dict[tuple[str, str], tuple[int, int]]:
    ratings: dict[tuple[str, str], tuple[int, int]] = {}
    sql = """
        SELECT originalImagePath,
               MAX(CAST(COALESCE(starLevel, 0) AS INTEGER)),
               MAX(CASE WHEN COALESCE(selectFlag, 0) = 1 OR COALESCE(isFavourite, 0) = 1 THEN 1 ELSE 0 END)
        FROM thumbnail
        WHERE COALESCE(inRecycleBin, 0) = 0
          AND originalImagePath IS NOT NULL AND originalImagePath <> ''
          AND (COALESCE(starLevel, 0) >= 1 OR COALESCE(selectFlag, 0) = 1 OR COALESCE(isFavourite, 0) = 1)
        GROUP BY originalImagePath
    """
    for dbp in db_paths:
        try:
            con = _sqlite_ro_connect(dbp, timeout=3.0)
            try:
                for img_path, st, fl in con.execute(sql):
                    if not img_path:
                        continue
                    p = Path(img_path.strip())
                    if allowed_exts and p.suffix.lower() not in allowed_exts:
                        continue
                    dkey = _norm_dir_key(str(p.parent))
                    if scope_norms and not _dir_in_scopes(dkey, scope_norms):
                        continue
                    key = (dkey, p.stem.lower())
                    ratings[key] = merge_rating_pair(ratings.get(key), int(st or 0), int(fl or 0))
            finally:
                con.close()
        except Exception:
            continue
    return ratings


def _windows_volume_guid_map() -> dict[str, str]:
    guid_to_drive: dict[str, str] = {}
    if os.name != "nt":
        return guid_to_drive
    try:
        k32 = ctypes.windll.kernel32
        mask = k32.GetLogicalDrives()
        buf = ctypes.create_unicode_buffer(128)
        for idx, ch in enumerate(string.ascii_uppercase):
            if not (mask & (1 << idx)):
                continue
            rt = ch + ":\\"
            if k32.GetDriveTypeW(rt) in (2, 3):
                if k32.GetVolumeNameForVolumeMountPointW(rt, buf, 128):
                    guid_to_drive[buf.value.rstrip("\\/").lower()] = rt
    except Exception:
        pass
    return guid_to_drive


def _windows_volume_serial_map() -> dict[str, str]:
    serial_to_drive: dict[str, str] = {}
    if os.name != "nt":
        return serial_to_drive
    try:
        k32 = ctypes.windll.kernel32
        mask = k32.GetLogicalDrives()
        for idx, ch in enumerate(string.ascii_uppercase):
            if not (mask & (1 << idx)):
                continue
            rt = ch + ":\\"
            if k32.GetDriveTypeW(rt) in (2, 3):
                ser = ctypes.c_ulong(0)
                if k32.GetVolumeInformationW(rt, None, 0, ctypes.byref(ser), None, None, None, 0):
                    serial_to_drive[f"{ser.value:08X}"] = rt
    except Exception:
        pass
    return serial_to_drive


def read_pixpretty_ratings(
    db_paths: Iterable[Path],
    allowed_exts: Optional[set[str]] = None,
    scope_norms: Optional[list[str]] = None,
) -> dict[tuple[str, str], tuple[int, int]]:
    ratings: dict[tuple[str, str], tuple[int, int]] = {}
    guid_to_drive = _windows_volume_guid_map()
    sql = """
        SELECT COALESCE(i.root_path_id, ''), COALESCE(i.relative_path, ''), COALESCE(i.name, ''),
               MAX(CAST(COALESCE(i.level, 0) AS INTEGER)),
               MAX(CASE WHEN CAST(COALESCE(i.flag_mark, 0) AS INTEGER) = 1 THEN 1 ELSE 0 END)
        FROM images i
        LEFT JOIN projects p ON i.project_id = p.project_id
        WHERE COALESCE(i.is_del, 0) = 0 AND COALESCE(p.del_states, 0) = 0
          AND (COALESCE(i.level, 0) >= 1 OR COALESCE(i.flag_mark, 0) = 1)
        GROUP BY i.root_path_id, i.relative_path, i.name
    """
    for dbp in db_paths:
        try:
            con = _sqlite_ro_connect(dbp, timeout=3.0)
            try:
                for rid, relp, fn, lv, fl in con.execute(sql):
                    rkey = (rid or "").strip().rstrip("\\/").lower()
                    rel_clean = (relp or "").strip().replace("/", "\\")
                    fullp = ""
                    if rkey in guid_to_drive:
                        fullp = os.path.normpath(os.path.join(guid_to_drive[rkey], rel_clean.lstrip("\\/")))
                    elif re.match(r"^[a-zA-Z]:([\\/]|$)", (rid or "").strip()):
                        drv = rid.strip()[:2] + "\\"
                        fullp = os.path.normpath(os.path.join(drv, rel_clean.lstrip("\\/")))
                    elif re.match(r"^[a-zA-Z]:[\\/]", rel_clean):
                        fullp = os.path.normpath(rel_clean)
                    if not fullp:
                        continue
                    if fn and os.path.basename(fullp).lower() != fn.strip().lower():
                        fullp = os.path.join(fullp, fn.strip())
                    p = Path(fullp)
                    if allowed_exts and p.suffix.lower() not in allowed_exts:
                        continue
                    dkey = _norm_dir_key(str(p.parent))
                    if scope_norms and not _dir_in_scopes(dkey, scope_norms):
                        continue
                    key = (dkey, p.stem.lower())
                    ratings[key] = merge_rating_pair(ratings.get(key), int(lv or 0), int(fl or 0))
            finally:
                con.close()
        except Exception:
            continue
    return ratings


def read_luminar_ratings(
    cat_path: Path,
    allowed_exts: Optional[set[str]] = None,
    scope_norms: Optional[list[str]] = None,
) -> dict[tuple[str, str], tuple[int, int]]:
    ratings: dict[tuple[str, str], tuple[int, int]] = {}
    serial_to_drive = _windows_volume_serial_map()
    sql = """
        SELECT COALESCE(v.info_wide_ch, ''), COALESCE(v.guid_wide_ch, ''),
               COALESCE(p.path_wide_ch, ''), i.path_wide_ch,
               MAX(CAST(COALESCE(u.rating, 0) AS INTEGER)),
               MAX(CASE WHEN CAST(COALESCE(u.marker, 0) AS INTEGER) = 1 THEN 1 ELSE 0 END)
        FROM image_user_attributes u
        JOIN images i ON u._out_id_int_64 = i._id_int_64
        JOIN paths_images pi ON pi._val_id_int_64 = i._id_int_64
        JOIN paths p ON pi._key_id_int_64 = p._id_int_64
        LEFT JOIN volumes v ON p.volume_id_int_64 = v._id_int_64
        WHERE COALESCE(u.marked_to_delete_bool, 0) = 0 AND COALESCE(u.trash_bool, 0) = 0
          AND COALESCE(i.marked_to_delete_bool, 0) = 0
          AND (COALESCE(u.rating, 0) >= 1 OR COALESCE(u.marker, 0) = 1)
        GROUP BY v._id_int_64, p._id_int_64, LOWER(i.path_wide_ch)
    """
    con = _sqlite_ro_connect(cat_path, timeout=3.0)
    try:
        for vinfo, vguid, ppath, fn, rt, mk in con.execute(sql):
            if not fn:
                continue
            drv = ""
            if vinfo:
                s_up = vinfo.strip()
                if s_up.startswith("{"):
                    try:
                        vsn = str(json.loads(s_up).get("VolumeSerialNumber", "")).strip().upper()
                        if vsn in serial_to_drive:
                            drv = serial_to_drive[vsn]
                    except Exception:
                        pass
                elif re.match(r"^[a-zA-Z]:([\\/]|$)", s_up):
                    drv = s_up[:2] + "\\"
            if not drv and vguid and re.match(r"^[a-zA-Z]:([\\/]|$)", vguid.strip()):
                drv = vguid.strip()[:2] + "\\"

            pclean = (ppath or "").strip().replace("/", "\\")
            if drv:
                full_dir = os.path.normpath(os.path.join(drv, pclean.lstrip("\\/")))
            elif re.match(r"^[a-zA-Z]:[\\/]", pclean):
                full_dir = os.path.normpath(pclean)
            else:
                continue

            ext = Path(fn.strip()).suffix.lower()
            if allowed_exts and ext not in allowed_exts:
                continue
            dkey = _norm_dir_key(full_dir)
            if scope_norms and not _dir_in_scopes(dkey, scope_norms):
                continue
            bkey = Path(fn.strip()).stem.lower()
            key = (dkey, bkey)
            ratings[key] = merge_rating_pair(ratings.get(key), int(rt or 0), int(mk or 0))
    finally:
        con.close()
    return ratings


def collect_all_software_ratings(
    photo_roots: list[Path],
    explicit_catalog: Optional[str] = None,
    allowed_exts: Optional[set[str]] = None,
    lang: str = "zh",
    pixcake_db_root: Optional[str] = None,
    pixpretty_db_root: Optional[str] = None,
) -> tuple[dict[tuple[str, str], tuple[int, int]], list[str], list[str], list[str]]:
    """Aggregate read-only ratings across Lightroom, Capture One, PixCake, PixPretty, and Luminar Neo.

    Returns (ratings_map, db_source_labels, lock_warnings, error_warnings).
    """
    merged: dict[tuple[str, str], tuple[int, int]] = {}
    labels: list[str] = []
    locks: list[str] = []
    warnings: list[str] = []

    scope_norms = [_norm_dir_key(str(r)) for r in photo_roots]

    def _merge_dict(d: dict[tuple[str, str], tuple[int, int]]) -> int:
        for k, (r, p) in d.items():
            merged[k] = merge_rating_pair(merged.get(k), r, p)
        return len(d)

    # 1. Lightroom Classic
    for cat in discover_lightroom_catalogs(photo_roots, explicit_catalog):
        if Path(str(cat) + ".lock").is_file():
            locks.append(str(cat))
            print(t(lang, "warn_lr_lock", str(cat)))
        try:
            cnt = _merge_dict(read_lightroom_ratings(cat, allowed_exts=allowed_exts, scope_norms=scope_norms))
            lbl = f"Lightroom ({cat.name})"
            labels.append(lbl)
            print(t(lang, "scan_db_hit", lbl, cnt))
        except Exception as exc:
            msg = t(lang, "warn_db_err", cat.name, str(exc))
            warnings.append(msg)
            print(msg)

    # 2. Capture One
    for c1 in discover_capture_one_catalogs(photo_roots, explicit_catalog):
        try:
            cnt = _merge_dict(read_capture_one_ratings(c1, allowed_exts=allowed_exts, scope_norms=scope_norms))
            if cnt > 0 or explicit_catalog:
                lbl = f"Capture One ({c1.name})"
                labels.append(lbl)
                print(t(lang, "scan_db_hit", lbl, cnt))
        except Exception as exc:
            msg = t(lang, "warn_db_err", c1.name, str(exc))
            warnings.append(msg)
            print(msg)

    if not explicit_catalog:
        # 3. PixCake
        px_dbs = discover_pixcake_dbs(pixcake_db_root)
        if px_dbs:
            cnt = _merge_dict(read_pixcake_ratings(px_dbs, allowed_exts=allowed_exts, scope_norms=scope_norms))
            if cnt > 0:
                lbl = f"PixCake ({len(px_dbs)} DBs)"
                labels.append(lbl)
                print(t(lang, "scan_db_hit", lbl, cnt))

        # 4. PixPretty
        pp_dbs = discover_pixpretty_dbs(pixpretty_db_root)
        if pp_dbs:
            cnt = _merge_dict(read_pixpretty_ratings(pp_dbs, allowed_exts=allowed_exts, scope_norms=scope_norms))
            if cnt > 0:
                lbl = f"PixPretty ({len(pp_dbs)} DBs)"
                labels.append(lbl)
                print(t(lang, "scan_db_hit", lbl, cnt))

    # 5. Luminar Neo
    for lum in discover_luminar_catalogs(photo_roots, explicit_catalog):
        try:
            cnt = _merge_dict(read_luminar_ratings(lum, allowed_exts=allowed_exts, scope_norms=scope_norms))
            if cnt > 0 or explicit_catalog:
                lbl = f"Luminar Neo ({lum.name})"
                labels.append(lbl)
                print(t(lang, "scan_db_hit", lbl, cnt))
        except Exception as exc:
            msg = t(lang, "warn_db_err", lum.name, str(exc))
            warnings.append(msg)
            print(msg)

    if not labels:
        print(t(lang, "scan_db_none"))

    return merged, labels, locks, warnings
