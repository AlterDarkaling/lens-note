"""lens-note (镜头笔记) CLI & interactive menu entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path
from typing import Optional

from src.exif_scanner import DEFAULT_EXCLUDE_DIRS, scan_libraries
from src.exiftool_setup import install_exiftool, resolve_exiftool
from src.gear_normalizer import GearNormalizer
from src.i18n import detect_default_lang, t
from src.report_builder import build_html_report, make_output_filename

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.local.json"
CACHE_PATH = PROJECT_ROOT / "cache" / "exif_cache.json"
ALIASES_PATH = PROJECT_ROOT / "gear_aliases.json"
TEMPLATE_PATH = PROJECT_ROOT / "templates" / "report_template.html"
OUTPUT_DIR = PROJECT_ROOT / "output"


def load_local_config() -> dict:
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_local_config(photo_roots: list[str], exclude_folders: list[str], lang: str) -> None:
    payload = {
        "PhotoRoots": photo_roots,
        "ExcludeFolders": exclude_folders,
        "Lang": lang,
    }
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def open_in_browser(html_path: Path) -> None:
    uri = html_path.resolve().as_uri()
    try:
        if os.name == "nt":
            os.startfile(str(html_path.resolve()))
        else:
            webbrowser.open(uri)
    except Exception:
        webbrowser.open(uri)


def run_build(
    roots: list[Path],
    lang: str,
    explicit_exiftool: Optional[str] = None,
    explicit_catalog: Optional[str] = None,
    output_path: Optional[Path] = None,
    exclude_folders: Optional[list[str]] = None,
    use_cache: bool = True,
    use_db: bool = True,
    auto_open: bool = True,
) -> int:
    if not roots:
        print(t(lang, "no_roots_err"))
        return 1

    et = resolve_exiftool(explicit_exiftool, PROJECT_ROOT)
    if not et:
        print(t(lang, "et_missing_startup"))
        return 1

    normalizer = GearNormalizer(ALIASES_PATH)
    dataset = scan_libraries(
        photo_roots=roots,
        exiftool_path=et,
        normalizer=normalizer,
        cache_path=CACHE_PATH,
        exclude_folders=exclude_folders,
        use_cache=use_cache,
        use_db=use_db,
        explicit_catalog=explicit_catalog,
        lang=lang,
    )

    out = output_path if output_path else (OUTPUT_DIR / make_output_filename(roots))
    built = build_html_report(dataset, TEMPLATE_PATH, out, default_lang=lang)
    print(t(lang, "build_done", str(built)))
    if auto_open:
        print(t(lang, "opening_browser"))
        open_in_browser(built)
    return 0


def interactive_menu(args: argparse.Namespace) -> int:
    cfg = load_local_config()
    lang = args.lang if args.lang in ("zh", "en") else cfg.get("Lang") or detect_default_lang()
    if lang not in ("zh", "en"):
        lang = "zh"

    photo_roots: list[str] = [str(Path(r).resolve()) for r in cfg.get("PhotoRoots", []) if Path(r).is_dir()]
    exclude_folders: list[str] = list(cfg.get("ExcludeFolders", []))

    et = resolve_exiftool(args.exiftool, PROJECT_ROOT)
    if not et:
        print()
        print(t(lang, "et_missing_startup"))
        ans = input(t(lang, "et_install_prompt") + " ").strip()
        if ans == "" or ans.lower() == "y":
            install_exiftool(PROJECT_ROOT / "tools", lang=lang)

    while True:
        et = resolve_exiftool(args.exiftool, PROJECT_ROOT)
        print()
        print(t(lang, "app_title"))
        if photo_roots:
            print(t(lang, "menu_roots", len(photo_roots)))
            for idx, r in enumerate(photo_roots, 1):
                print(f"    [{idx}] {r}")
        else:
            print(t(lang, "menu_roots_empty"))

        excl_disp = ", ".join(exclude_folders) if exclude_folders else ", ".join(sorted(DEFAULT_EXCLUDE_DIRS)[:5]) + ", ..."
        print(t(lang, "menu_excludes", excl_disp))
        print(t(lang, "menu_lang"))
        print(t(lang, "menu_et_ok", et) if et else t(lang, "menu_et_missing"))
        print()
        print(t(lang, "menu_opt_1"))
        print(t(lang, "menu_opt_2"))
        print(t(lang, "menu_opt_3"))
        print(t(lang, "menu_opt_4"))
        print(t(lang, "menu_opt_5"))
        if not et:
            print(t(lang, "menu_opt_i"))
        print(t(lang, "menu_opt_l"))
        print(t(lang, "menu_opt_0"))
        print("=" * 46)

        choice = input(t(lang, "menu_prompt") + ": ").strip()
        if choice == "0":
            print(t(lang, "menu_bye"))
            return 0
        elif choice.lower() == "l":
            lang = "en" if lang == "zh" else "zh"
            save_local_config(photo_roots, exclude_folders, lang)
        elif choice.lower() == "i":
            install_exiftool(PROJECT_ROOT / "tools", lang=lang)
            input(t(lang, "press_enter"))
        elif choice == "1":
            run_build(
                roots=[Path(r) for r in photo_roots],
                lang=lang,
                explicit_exiftool=args.exiftool,
                explicit_catalog=args.catalog,
                exclude_folders=exclude_folders,
                use_cache=not args.no_cache,
                use_db=not args.no_db,
                auto_open=not args.no_open,
            )
            input(t(lang, "press_enter"))
        elif choice == "2":
            raw = input(t(lang, "prompt_add_root") + ": ").strip().strip('"')
            if raw:
                p = Path(raw)
                if p.is_dir():
                    rp = str(p.resolve())
                    if any(rp.lower() == x.lower() for x in photo_roots):
                        print(t(lang, "dup_root", rp))
                    else:
                        photo_roots.append(rp)
                        save_local_config(photo_roots, exclude_folders, lang)
                        print(t(lang, "added_root", rp))
                else:
                    print(t(lang, "err_no_dir", raw))
        elif choice == "3":
            if photo_roots:
                raw = input(t(lang, "prompt_remove_root") + ": ").strip()
                if raw.isdigit():
                    idx = int(raw)
                    if 1 <= idx <= len(photo_roots):
                        removed = photo_roots.pop(idx - 1)
                        save_local_config(photo_roots, exclude_folders, lang)
                        print(t(lang, "removed_root", removed))
        elif choice == "4":
            cur = ", ".join(exclude_folders)
            raw = input(t(lang, "prompt_excludes", cur) + ": ").strip()
            if raw:
                exclude_folders = [x.strip() for x in raw.split(",") if x.strip()]
                save_local_config(photo_roots, exclude_folders, lang)
                print(t(lang, "saved_excludes", ", ".join(exclude_folders)))
        elif choice == "5":
            if CACHE_PATH.is_file():
                CACHE_PATH.unlink()
            print(t(lang, "cache_cleared"))
            run_build(
                roots=[Path(r) for r in photo_roots],
                lang=lang,
                explicit_exiftool=args.exiftool,
                explicit_catalog=args.catalog,
                exclude_folders=exclude_folders,
                use_cache=True,
                use_db=not args.no_db,
                auto_open=not args.no_open,
            )
            input(t(lang, "press_enter"))
        else:
            print(t(lang, "menu_invalid", choice))


def main(argv: Optional[list[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="lens-note: Offline photography data retrospective")
    parser.add_argument("roots", nargs="*", help="One or more photo root directories")
    parser.add_argument("-o", "--output", help="Output HTML file path")
    parser.add_argument("--lang", choices=["auto", "zh", "en"], default="auto")
    parser.add_argument("--exiftool", help="Explicit path to exiftool executable")
    parser.add_argument("--catalog", help="Explicit Lightroom/Capture One/Luminar catalog path")
    parser.add_argument("--exclude", action="append", default=[], help="Additional folder names to exclude")
    parser.add_argument("--no-cache", action="store_true", help="Ignore incremental EXIF cache")
    parser.add_argument("--no-db", action="store_true", help="Skip reading photo software databases")
    parser.add_argument("--no-open", action="store_true", help="Do not open generated HTML in browser")
    parser.add_argument("--install-exiftool", action="store_true", help="Download and install ExifTool into tools/")
    args = parser.parse_args(argv)

    cfg = load_local_config()
    lang = args.lang if args.lang in ("zh", "en") else cfg.get("Lang") or detect_default_lang()
    if lang not in ("zh", "en"):
        lang = "zh"

    if args.install_exiftool:
        res = install_exiftool(PROJECT_ROOT / "tools", lang=lang)
        return 0 if res else 1

    if args.roots:
        roots = []
        for r in args.roots:
            p = Path(r.strip('"'))
            if not p.is_dir():
                print(t(lang, "err_no_dir", r))
                return 1
            roots.append(p)
        excludes = list(cfg.get("ExcludeFolders", [])) + list(args.exclude or [])
        out_path = Path(args.output) if args.output else None
        return run_build(
            roots=roots,
            lang=lang,
            explicit_exiftool=args.exiftool,
            explicit_catalog=args.catalog,
            output_path=out_path,
            exclude_folders=excludes,
            use_cache=not args.no_cache,
            use_db=not args.no_db,
            auto_open=not args.no_open,
        )

    return interactive_menu(args)


if __name__ == "__main__":
    raise SystemExit(main())
