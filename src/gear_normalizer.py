"""Camera/lens normalization, device classification, crop-factor 35mm eq focal length, and orientation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

GEAR_TYPE_CODES = {"camera": 0, "drone": 1, "gimbal": 2, "action": 3, "phone": 4}
ORIENT_CODES = {"L": 0, "P": 1, "S": 2, "U": 3}

JUNK_LENS_VALUES = {
    "",
    "----",
    "unknown",
    "n/a",
    "none",
    "0.0 mm f/0.0",
    "0 mm f/0",
    "0mm f/0",
    "ef0mm",
}


class GearNormalizer:
    """Normalize camera models, lens names, device types, focal lengths, and image orientations."""

    def __init__(self, aliases_path: Optional[Path] = None):
        self.cameras: dict[str, dict[str, Any]] = {}
        self.cameras_lower: dict[str, dict[str, Any]] = {}
        self.lenses: dict[str, str] = {}
        self.lenses_lower: dict[str, str] = {}
        self.gimbal_re = re.compile(r"(?i)(Osmo\s*Pocket|Pocket\s*[23]|PP-101|OT-110|OT-210|Feiyu\s*Pocket)")
        self.action_re = re.compile(r"(?i)(GoPro|HERO\d+|Osmo\s*Action|Action\s*[2-5]|AC00\d|Insta360|Ace\s*Pro|\bX[234]\b|\bGO\s*[23]\b)")
        self.drone_re = re.compile(r"(?i)(DJI|FC\d{4}|L2D-20c|Mavic|Phantom|Inspire|Avata)")
        self.phone_re = re.compile(
            r"(?i)(iPhone|Xiaomi|Redmi|HUAWEI|HONOR|OPPO|vivo|OnePlus|Pixel|SM-[A-Z]\d+|Samsung|Galaxy)"
        )
        self.crop_rules: list[tuple[re.Pattern[str], float]] = []

        if aliases_path and Path(aliases_path).is_file():
            try:
                data = json.loads(Path(aliases_path).read_text(encoding="utf-8"))
                for k, v in data.get("cameras", {}).items():
                    self.cameras[k] = v
                    self.cameras_lower[k.strip().lower()] = v
                for k, v in data.get("lenses", {}).items():
                    self.lenses[k] = v
                    self.lenses_lower[k.strip().lower()] = v
                heur = data.get("heuristics", {})
                if heur.get("gimbal_regex"):
                    self.gimbal_re = re.compile(heur["gimbal_regex"])
                if heur.get("action_regex"):
                    self.action_re = re.compile(heur["action_regex"])
                if heur.get("drone_regex"):
                    self.drone_re = re.compile(heur["drone_regex"])
                if heur.get("phone_regex"):
                    self.phone_re = re.compile(heur["phone_regex"])
                for rule in heur.get("crop_rules", []):
                    if rule.get("regex") and rule.get("crop"):
                        self.crop_rules.append((re.compile(rule["regex"]), float(rule["crop"])))
            except Exception:
                pass

    def _lookup_camera_meta(self, model: str, make: str) -> Optional[dict[str, Any]]:
        for cand in (model, f"{make} {model}".strip()):
            if not cand:
                continue
            if cand in self.cameras:
                return self.cameras[cand]
            low = cand.lower()
            if low in self.cameras_lower:
                return self.cameras_lower[low]
        return None

    def normalize_camera(self, make: str, model: str) -> tuple[str, str, float]:
        """Return (display_camera_name, gear_type, crop_factor)."""
        make_clean = (make or "").strip()
        model_clean = (model or "").strip()
        meta = self._lookup_camera_meta(model_clean, make_clean)

        if meta:
            name = str(meta.get("name") or model_clean or make_clean or "Unknown Camera")
            gtype = str(meta.get("type") or "camera")
            crop = float(meta.get("crop") or 1.0)
            return name, gtype, crop

        if not model_clean and not make_clean:
            return "Unknown Camera", "camera", 1.0

        combined = model_clean
        if make_clean and model_clean:
            if not model_clean.lower().startswith(make_clean.lower().split()[0]):
                brand = make_clean.title() if make_clean.isupper() else make_clean
                combined = f"{brand} {model_clean}"
        elif make_clean:
            combined = make_clean

        gtype = "camera"
        if self.gimbal_re.search(combined) or self.gimbal_re.search(model_clean):
            gtype = "gimbal"
        elif self.action_re.search(combined) or self.action_re.search(model_clean):
            gtype = "action"
        elif self.drone_re.search(combined) or self.drone_re.search(model_clean):
            gtype = "drone"
        elif self.phone_re.search(combined) or self.phone_re.search(model_clean):
            gtype = "phone"

        crop = 1.0
        for pat, c in self.crop_rules:
            if pat.search(combined) or pat.search(model_clean):
                crop = c
                break

        return combined, gtype, crop

    def normalize_lens(
        self,
        lens_model: str,
        lens_id: str,
        lens_tag: str,
        lens_spec: str,
        gear_type: str = "camera",
        camera_name: str = "",
    ) -> str:
        """Pick best human-readable lens name, returning '' for unknown/manual lenses."""
        chosen = ""
        for raw in (lens_model, lens_id, lens_tag, lens_spec):
            s = (raw or "").strip()
            if s.lower() in JUNK_LENS_VALUES:
                continue
            chosen = s
            break

        if not chosen:
            return ""

        if chosen in self.lenses:
            return self.lenses[chosen]
        low = chosen.lower()
        if low in self.lenses_lower:
            return self.lenses_lower[low]

        if gear_type in ("drone", "gimbal", "action") and camera_name:
            brand_tok = camera_name.split()[0].lower()
            if brand_tok not in low and "dji" not in low:
                return f"{camera_name} ({chosen})"

        return chosen

    @staticmethod
    def compute_focal_lengths(
        fl_phys: Optional[float],
        fl_35: Optional[float],
        crop_factor: float,
    ) -> tuple[Optional[float], Optional[float]]:
        """Return (fl_35_mm, fl_phys_mm) rounded cleanly."""
        phys = round(fl_phys, 1) if fl_phys and fl_phys > 0 else None
        eq35 = round(fl_35, 1) if fl_35 and fl_35 > 0 else None
        if eq35 is None and phys is not None:
            eq35 = round(phys * (crop_factor if crop_factor > 0 else 1.0))
        return eq35, phys

    @staticmethod
    def compute_orientation(
        width: Optional[int],
        height: Optional[int],
        exif_orientation: Optional[int],
    ) -> str:
        """Return 'L' (landscape), 'P' (portrait), 'S' (square), or 'U' (unknown)."""
        if not width or not height or width <= 0 or height <= 0:
            return "U"
        w, h = int(width), int(height)
        if exif_orientation in (5, 6, 7, 8):
            w, h = h, w
        if w > h:
            return "L"
        if h > w:
            return "P"
        return "S"
