"""Bilingual (zh/en) CLI & launcher messages for lens-note."""

import locale
import os

MESSAGES = {
    "zh": {
        "app_title": "=========== lens-note · 镜头笔记 ===========",
        "menu_roots": " 照片目录 ({0}):",
        "menu_roots_empty": " 照片目录: (尚未配置)",
        "menu_excludes": " 排除目录: {0}",
        "menu_lang": " 当前语言: 简体中文 (zh)",
        "menu_et_ok": " ExifTool: 已就绪 ({0})",
        "menu_et_missing": " ExifTool: 未找到（可按 [I] 自动安装到 tools\\）",
        "menu_opt_1": "  [1] 生成摄影数据复盘报告 (HTML) 并打开",
        "menu_opt_2": "  [2] 添加照片文件夹（支持多目录/跨年份合并）",
        "menu_opt_3": "  [3] 移除已配置的照片文件夹",
        "menu_opt_4": "  [4] 设置排除干扰目录关键词",
        "menu_opt_5": "  [5] 强制全量重扫（清空增量缓存后重新生成）",
        "menu_opt_i": "  [I] 自动下载并安装 ExifTool 到 tools\\",
        "menu_opt_l": "  [L] Switch language (中文 / English)",
        "menu_opt_0": "  [0] 退出",
        "menu_prompt": "请选择操作编号",
        "menu_invalid": "[提示] 无效选项: {0}",
        "menu_bye": "再见！",
        "press_enter": "按回车键返回菜单...",
        "prompt_add_root": "请输入或拖入照片文件夹路径（直接回车取消）",
        "added_root": "[已添加] {0}",
        "dup_root": "[提示] 该目录已在列表中: {0}",
        "err_no_dir": "[错误] 找不到目录: {0}",
        "prompt_remove_root": "请输入要移除的序号（0 或回车取消）",
        "removed_root": "[已移除] {0}",
        "prompt_excludes": "当前排除目录名（逗号分隔）: {0}\n输入新的排除名列表（回车保持不变）",
        "saved_excludes": "[已保存] 排除目录关键词: {0}",
        "cache_cleared": "[已清理] 增量缓存已重置。",
        "no_roots_err": "[错误] 未配置任何照片目录，请先按 [2] 添加文件夹或拖入文件夹启动。",
        "et_missing_startup": "[提示] 未检测到 ExifTool（读取照片 EXIF 与星标必需）。",
        "et_install_prompt": "是否立即从官网自动下载并安装最新 64 位 ExifTool 到 tools\\？[Y/n] (回车默认 Yes)",
        "et_inst_banner": "===== 自动安装 ExifTool (官方 Windows 64位) =====",
        "et_inst_check": "正在从 exiftool.org 获取最新版本信息...",
        "et_inst_dl": "正在下载: {0}",
        "et_inst_extract": "正在解压配置到: {0}",
        "et_inst_ok": "[成功] ExifTool {0} 已就绪: {1}",
        "et_inst_fail": "[错误] ExifTool 自动安装失败: {0}",
        "et_inst_manual": "备选方案: 可运行 winget install OliverBetz.ExifTool 或从 https://exiftool.org/ 手动下载放到 tools\\",
        "scan_start": "正在扫描照片库...",
        "scan_roots": "扫描目录: {0}",
        "scan_walk_done": "共发现图片 {0} 个（成片 {1} / RAW {2} / XMP {3}），跳过视频 {4} 个。",
        "scan_cache_hit": "增量缓存命中 {0} 个文件，需通过 ExifTool 新扫 {1} 个文件...",
        "scan_exif_batch": "  正在读取 EXIF 元数据 ({0}/{1})...",
        "scan_db_start": "正在读取修图软件本地星标库...",
        "scan_db_hit": "  + {0}: 加载 {1} 条星标/旗标记录",
        "scan_db_none": "  未检测到修图软件本地库（仅使用照片内嵌与 .xmp 星标）。",
        "warn_lr_lock": "[警告] 检测到 Lightroom 正在运行（{0}.lock），刚打的星可能尚未写入主库！",
        "warn_db_err": "[警告] 读取数据库失败 ({0}): {1}（已安全跳过）",
        "build_done": "已生成复盘报告: {0}",
        "build_summary": "逻辑去重照片: {0} 张（合并 RAW+JPG {1} 对）| 打星/留用: {2} 张 ({3:.1f}%) | 用时: {4:.2f}s",
        "opening_browser": "正在用默认浏览器打开报告...",
    },
    "en": {
        "app_title": "=========== lens-note · Photo Recap ===========",
        "menu_roots": " Photo folders ({0}):",
        "menu_roots_empty": " Photo folders: (none configured)",
        "menu_excludes": " Excluded dirs: {0}",
        "menu_lang": " Language     : English (en)",
        "menu_et_ok": " ExifTool     : Ready ({0})",
        "menu_et_missing": " ExifTool     : NOT FOUND (press [I] to auto-install to tools\\)",
        "menu_opt_1": "  [1] Generate & open photography recap (HTML)",
        "menu_opt_2": "  [2] Add photo folder (supports multi-folder / multi-year)",
        "menu_opt_3": "  [3] Remove a configured photo folder",
        "menu_opt_4": "  [4] Configure excluded folder names",
        "menu_opt_5": "  [5] Force full rescan (clear incremental cache)",
        "menu_opt_i": "  [I] Auto-download & install ExifTool into tools\\",
        "menu_opt_l": "  [L] 切换语言 (English / 中文)",
        "menu_opt_0": "  [0] Exit",
        "menu_prompt": "Select an option",
        "menu_invalid": "[WARN] Invalid option: {0}",
        "menu_bye": "Goodbye!",
        "press_enter": "Press Enter to return to menu...",
        "prompt_add_root": "Enter or drop a photo folder path (Enter to cancel)",
        "added_root": "[Added] {0}",
        "dup_root": "[INFO] Folder already in list: {0}",
        "err_no_dir": "[ERROR] Directory not found: {0}",
        "prompt_remove_root": "Enter index to remove (0 or Enter to cancel)",
        "removed_root": "[Removed] {0}",
        "prompt_excludes": "Current excluded names (comma-separated): {0}\nEnter new list (Enter to keep)",
        "saved_excludes": "[Saved] Excluded folder names: {0}",
        "cache_cleared": "[Cleared] Incremental EXIF cache reset.",
        "no_roots_err": "[ERROR] No photo folders configured. Press [2] to add or drag folders onto Start-LensNote.bat.",
        "et_missing_startup": "[INFO] ExifTool not found (required to read EXIF & ratings).",
        "et_install_prompt": "Auto-download latest 64-bit Windows ExifTool into tools\\ now? [Y/n] (Enter = Yes)",
        "et_inst_banner": "===== Auto-Install ExifTool (Official Windows 64-bit) =====",
        "et_inst_check": "Checking latest ExifTool download URL from exiftool.org...",
        "et_inst_dl": "Downloading: {0}",
        "et_inst_extract": "Extracting into: {0}",
        "et_inst_ok": "[OK] ExifTool {0} ready at: {1}",
        "et_inst_fail": "[ERROR] ExifTool auto-install failed: {0}",
        "et_inst_manual": "Alternative: run 'winget install OliverBetz.ExifTool' or download from https://exiftool.org/ into tools\\",
        "scan_start": "Scanning photo library...",
        "scan_roots": "Roots: {0}",
        "scan_walk_done": "Found {0} files (rendered {1} / RAW {2} / XMP {3}), skipped {4} video(s).",
        "scan_cache_hit": "Incremental cache hit {0} file(s); scanning {1} new/changed file(s) via ExifTool...",
        "scan_exif_batch": "  Reading EXIF metadata ({0}/{1})...",
        "scan_db_start": "Reading local photo software rating databases...",
        "scan_db_hit": "  + {0}: loaded {1} rated/picked entry(ies)",
        "scan_db_none": "  No local software databases found (using embedded & .xmp ratings only).",
        "warn_lr_lock": "[WARN] Lightroom appears to be running ({0}.lock); recent ratings may not be flushed yet.",
        "warn_db_err": "[WARN] Failed to read database ({0}): {1} (safely skipped)",
        "build_done": "Report generated: {0}",
        "build_summary": "Logical photos: {0} (deduped {1} RAW+JPG pairs) | Rated/Picked: {2} ({3:.1f}%) | Elapsed: {4:.2f}s",
        "opening_browser": "Opening report in default browser...",
    },
}


def detect_default_lang() -> str:
    """Detect system default UI language ('zh' or 'en')."""
    try:
        loc = locale.getlocale()[0] or os.environ.get("LANG", "")
        if loc and ("zh" in loc.lower() or "chinese" in loc.lower()):
            return "zh"
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if (lang_id & 0x3FF) == 0x04:  # LANG_CHINESE
                return "zh"
        except Exception:
            pass
    return "en"


def t(lang: str, key: str, *args) -> str:
    """Translate message key with optional positional format arguments."""
    bundle = MESSAGES.get(lang, MESSAGES["zh"])
    template = bundle.get(key, MESSAGES["en"].get(key, key))
    if args:
        return template.format(*args)
    return template
