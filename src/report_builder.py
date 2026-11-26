"""Dictionary-compress scanned photo metadata and build standalone offline HTML report."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .gear_normalizer import GEAR_TYPE_CODES, ORIENT_CODES


def build_report_payload(dataset: dict[str, Any]) -> dict[str, Any]:
    """Convert scan_libraries() dataset into compact dictionary-encoded payload for the HTML UI."""
    photos: list[dict[str, Any]] = dataset.get("photos", [])
    health: dict[str, Any] = dataset.get("health", {})

    date_set = sorted({p["date"] for p in photos if p.get("date")})
    date_idx = {d: i for i, d in enumerate(date_set)}
    years = sorted({d[:4] for d in date_set if len(d) >= 4})

    outing_meta: dict[str, dict[str, Any]] = {}
    for p in photos:
        oname = p["outing"]
        d = p["date"]
        if oname not in outing_meta:
            outing_meta[oname] = {
                "name": oname,
                "root": p.get("root", ""),
                "minDate": d,
                "maxDate": d,
                "year": d[:4],
                "month": d[:7],
            }
        else:
            if d < outing_meta[oname]["minDate"]:
                outing_meta[oname]["minDate"] = d
                outing_meta[oname]["year"] = d[:4]
                outing_meta[oname]["month"] = d[:7]
            if d > outing_meta[oname]["maxDate"]:
                outing_meta[oname]["maxDate"] = d

    outings = sorted(outing_meta.values(), key=lambda x: (x["minDate"], x["name"]), reverse=True)
    outing_idx = {o["name"]: i for i, o in enumerate(outings)}

    cameras = sorted({p["camera"] for p in photos})
    cam_idx = {c: i for i, c in enumerate(cameras)}

    lens_types: dict[str, str] = {}
    for p in photos:
        lname = p["lens"]
        if lname not in lens_types:
            lens_types[lname] = p["gear_type"]

    lens_names = sorted(lens_types.keys(), key=lambda s: (s == "", s.lower()))
    lens_idx = {ln: i for i, ln in enumerate(lens_names)}
    lenses = [
        {"name": ln, "gearType": GEAR_TYPE_CODES.get(lens_types[ln], 0)}
        for ln in lens_names
    ]

    # Encode rows (14 elements):
    # [dateIdx, hour, outingIdx, camIdx, lensIdx, gearTypeCode, fl35, flPhys,
    #  rating, pick, orientCode, burstRep, burstRating, burstPick]
    rows: list[list[Any]] = []
    for p in photos:
        rows.append(
            [
                date_idx[p["date"]],
                int(p.get("hour", 12)),
                outing_idx[p["outing"]],
                cam_idx[p["camera"]],
                lens_idx[p["lens"]],
                GEAR_TYPE_CODES.get(p.get("gear_type", "camera"), 0),
                p.get("fl35"),
                p.get("fl_phys"),
                int(p.get("rating", 0)),
                int(p.get("pick", 0)),
                ORIENT_CODES.get(p.get("orient", "U"), 3),
                int(p.get("burst_rep", 1)),
                int(p.get("burst_rating", p.get("rating", 0))),
                int(p.get("burst_pick", p.get("pick", 0))),
            ]
        )

    return {
        "v": 2,
        "years": years,
        "dates": date_set,
        "outings": outings,
        "cameras": cameras,
        "lenses": lenses,
        "rows": rows,
        "health": health,
    }


def make_output_filename(photo_roots: list[Path]) -> str:
    """Create a clean filename slug from scanned folder names."""
    names = []
    for r in photo_roots:
        cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", r.resolve().name).strip("_")
        if cleaned and cleaned not in names:
            names.append(cleaned)
    slug = "_".join(names[:4]) if names else "recap"
    if len(names) > 4:
        slug += f"_plus{len(names) - 4}"
    return f"{slug}_lens-note.html"


def build_html_report(
    dataset: dict[str, Any],
    template_path: Path,
    output_path: Path,
    default_lang: str = "zh",
) -> Path:
    """Render standalone offline HTML report to output_path."""
    payload = build_report_payload(dataset)
    json_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    json_safe = json_str.replace("</", "<\\/")

    tpl = Path(template_path).read_text(encoding="utf-8")
    lang_code = "en" if default_lang == "en" else "zh"

    html = tpl.replace("__LENS_NOTE_DATA__", json_safe).replace("__DEFAULT_LANG__", lang_code)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
