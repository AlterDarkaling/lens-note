"""ExifTool discovery and automatic Windows 64-bit installation."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from .i18n import t

SIBLING_TOOLKIT_EXIFTOOL = Path(r"E:\Code\lightroom-raw-toolkit\tools\exiftool.exe")


def resolve_exiftool(explicit_path: Optional[str] = None, project_root: Optional[Path] = None) -> Optional[str]:
    """Locate exiftool executable across explicit path, local tools/, sibling toolkit, PATH, and LocalAppData."""
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))

    if project_root:
        candidates.append(project_root / "tools" / "exiftool.exe")
        candidates.append(project_root / "tools" / "exiftool")
        candidates.append(project_root / "exiftool.exe")

    # Also check sibling lightroom-raw-toolkit tools directory if present on this machine
    candidates.append(SIBLING_TOOLKIT_EXIFTOOL)

    for cand in candidates:
        if cand.is_file():
            return str(cand.resolve())

    which_et = shutil.which("exiftool") or shutil.which("exiftool.exe")
    if which_et:
        return str(Path(which_et).resolve())

    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        win_default = Path(local_app) / "Programs" / "ExifTool" / "ExifTool.exe"
        if win_default.is_file():
            return str(win_default.resolve())

    return None


def install_exiftool(target_dir: Path, zip_path: Optional[str] = None, lang: str = "zh") -> Optional[str]:
    """Download/extract official Windows 64-bit ExifTool into target_dir (or install from local zip_path)."""
    print()
    print(t(lang, "et_inst_banner"))
    target_dir = Path(target_dir)

    with tempfile.TemporaryDirectory(prefix="lens_note_et_") as tmp_str:
        tmp_dir = Path(tmp_str)
        use_zip: Path
        if zip_path:
            use_zip = Path(zip_path)
            if not use_zip.is_file():
                print(t(lang, "et_inst_fail", f"ZIP not found: {zip_path}"))
                return None
        else:
            try:
                print(t(lang, "et_inst_check"))
                dl_url = None
                try:
                    req = urllib.request.Request(
                        "https://exiftool.org/",
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        html = resp.read().decode("utf-8", errors="ignore")
                    m = re.search(r'href="(https?://[^"]*exiftool[^"]*64\.zip[^"]*)"', html, re.I)
                    if m:
                        dl_url = m.group(1)
                except Exception:
                    dl_url = None

                if not dl_url:
                    with urllib.request.urlopen("https://exiftool.org/ver.txt", timeout=20) as resp:
                        ver = resp.read().decode("utf-8", errors="ignore").strip()
                    if not ver:
                        raise RuntimeError("Empty version from exiftool.org/ver.txt")
                    dl_url = f"https://sourceforge.net/projects/exiftool/files/exiftool-{ver}_64.zip/download"

                print(t(lang, "et_inst_dl", dl_url))
                dl_zip = tmp_dir / "exiftool_64.zip"
                req = urllib.request.Request(dl_url, headers={"User-Agent": "curl/8.0"})
                with urllib.request.urlopen(req, timeout=120) as resp, open(dl_zip, "wb") as out_f:
                    shutil.copyfileobj(resp, out_f)
                if not dl_zip.is_file() or dl_zip.stat().st_size < 102400:
                    raise RuntimeError("Downloaded archive is invalid or too small")
                use_zip = dl_zip
            except Exception as exc:
                print(t(lang, "et_inst_fail", str(exc)))
                print(t(lang, "et_inst_manual"))
                return None

        try:
            print(t(lang, "et_inst_extract", str(target_dir)))
            extract_dir = tmp_dir / "extract"
            with zipfile.ZipFile(use_zip, "r") as zf:
                zf.extractall(extract_dir)

            exe_file = next(extract_dir.rglob("exiftool(-k).exe"), None)
            if exe_file is None:
                exe_file = next(extract_dir.rglob("exiftool.exe"), None)
            if exe_file is None:
                raise RuntimeError("exiftool.exe not found inside archive")

            target_dir.mkdir(parents=True, exist_ok=True)
            target_exe = target_dir / "exiftool.exe"
            shutil.copy2(exe_file, target_exe)

            src_files = exe_file.parent / "exiftool_files"
            if src_files.is_dir():
                dst_files = target_dir / "exiftool_files"
                if dst_files.exists():
                    shutil.rmtree(dst_files)
                shutil.copytree(src_files, dst_files)

            proc = subprocess.run(
                [str(target_exe), "-ver"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            ver_out = proc.stdout.strip() if proc.returncode == 0 else "ok"
            print(t(lang, "et_inst_ok", ver_out, str(target_exe)))
            return str(target_exe.resolve())
        except Exception as exc:
            print(t(lang, "et_inst_fail", str(exc)))
            print(t(lang, "et_inst_manual"))
            return None
