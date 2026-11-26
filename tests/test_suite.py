"""Isolated automated test suite for lens-note."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.exif_scanner import _apply_burst_folding, scan_libraries
from src.exiftool_setup import resolve_exiftool
from src.gear_normalizer import GearNormalizer
from src.rating_sources import (
    read_capture_one_ratings,
    read_lightroom_ratings,
    read_luminar_ratings,
    read_pixcake_ratings,
    read_pixpretty_ratings,
)
from src.report_builder import build_html_report, build_report_payload


class TestGearNormalizer(unittest.TestCase):
    def setUp(self):
        self.norm = GearNormalizer(PROJECT_ROOT / "gear_aliases.json")

    def test_camera_aliases_and_five_gear_types(self):
        name, gtype, crop = self.norm.normalize_camera("SONY", "ILCE-6400")
        self.assertEqual(name, "Sony α6400")
        self.assertEqual(gtype, "camera")
        self.assertEqual(crop, 1.5)

        cname, cgtype, ccrop = self.norm.normalize_camera("Canon", "Canon EOS M50m2")
        self.assertEqual(cname, "Canon EOS M50 Mark II")
        self.assertEqual(cgtype, "camera")
        self.assertEqual(ccrop, 1.6)

        dname, dgtype, dcrop = self.norm.normalize_camera("DJI", "FC8582")
        self.assertEqual(dname, "DJI Mini 4 Pro")
        self.assertEqual(dgtype, "drone")
        self.assertGreater(dcrop, 3.0)

        gname, ggtype, _ = self.norm.normalize_camera("DJI", "PP-101")
        self.assertEqual(gname, "DJI Osmo Pocket 3")
        self.assertEqual(ggtype, "gimbal")

        aname, agtype, _ = self.norm.normalize_camera("GoPro", "HERO12 Black")
        self.assertEqual(agtype, "action")
        self.assertIn("HERO12", aname)

        _, agtype2, _ = self.norm.normalize_camera("Insta360", "X4")
        self.assertEqual(agtype2, "action")

        pname, pgtype, _ = self.norm.normalize_camera("Apple", "iPhone 15 Pro")
        self.assertEqual(pgtype, "phone")
        self.assertIn("iPhone 15 Pro", pname)

    def test_lens_and_focal_and_orientation(self):
        self.assertEqual(self.norm.normalize_lens("----", "", "FE 35mm F1.8", ""), "FE 35mm F1.8")
        self.assertEqual(self.norm.normalize_lens("0.0 mm f/0.0", "Unknown", "", ""), "")
        eq35, phys = self.norm.compute_focal_lengths(56.0, None, 1.5)
        self.assertEqual(eq35, 84)
        self.assertEqual(phys, 56.0)

        self.assertEqual(self.norm.compute_orientation(6000, 4000, 1), "L")
        self.assertEqual(self.norm.compute_orientation(6000, 4000, 6), "P")
        self.assertEqual(self.norm.compute_orientation(4000, 4000, 1), "S")
        self.assertEqual(self.norm.compute_orientation(None, 4000, 1), "U")


class TestBurstFolding(unittest.TestCase):
    def _mk(self, ts, **kw):
        base = {
            "date": "2026-05-01", "hour": 10, "ts": ts, "has_sub": 0,
            "stem": f"dsc{int(ts*1000)}", "drive_mode": "", "seq": 0, "burst_uuid": "",
            "outing": "ShootA", "camera": "Sony α6700", "lens": "E 17-70mm",
            "gear_type": "camera", "fl35": 52, "fl_phys": 35.0,
            "rating": 0, "pick": 0, "orient": "L",
        }
        base.update(kw)
        return base

    def test_explicit_single_never_folded(self):
        photos = [
            self._mk(1000.1, drive_mode="single", has_sub=1),
            self._mk(1000.9, drive_mode="single", has_sub=1),
            self._mk(1001.7, drive_mode="single", has_sub=1),
        ]
        groups, folded = _apply_burst_folding(photos)
        self.assertEqual(groups, 0)
        self.assertEqual(folded, 0)
        self.assertTrue(all(p["burst_rep"] == 1 for p in photos))

    def test_hardware_burst_with_seq_reset_and_star_inheritance(self):
        # Round 1: seq 7 -> 13 (deleted frames in between, dt=1.0s), frame #2 has 5 stars
        # Round 2: seq resets to 1 only 1.0s later -> must split into separate composition!
        photos = [
            self._mk(1000.0, drive_mode="burst", seq=7, rating=0),
            self._mk(1001.0, drive_mode="burst", seq=13, rating=5, pick=1),
            self._mk(1002.0, drive_mode="burst", seq=1, rating=0),
        ]
        groups, folded = _apply_burst_folding(photos)
        self.assertEqual(groups, 1)
        self.assertEqual(folded, 1)
        self.assertEqual(photos[0]["burst_rep"], 0)
        self.assertEqual(photos[1]["burst_rep"], 1)
        self.assertEqual(photos[1]["burst_rating"], 5)
        self.assertEqual(photos[1]["burst_pick"], 1)
        self.assertEqual(photos[2]["burst_rep"], 1)
        self.assertEqual(photos[2]["burst_rating"], 0)

    def test_bracketing_and_burst_uuid(self):
        photos = [
            self._mk(2000.0, drive_mode="bracket", seq=1),
            self._mk(2001.2, drive_mode="bracket", seq=2, rating=4),
            self._mk(2002.4, drive_mode="bracket", seq=3),
            self._mk(3000.0, burst_uuid="UUID-1", camera="iPhone 15 Pro"),
            self._mk(3000.2, burst_uuid="UUID-1", camera="iPhone 15 Pro", rating=3),
        ]
        groups, folded = _apply_burst_folding(photos)
        self.assertEqual(groups, 2)
        self.assertEqual(folded, 3)
        self.assertEqual(photos[1]["burst_rep"], 1)
        self.assertEqual(photos[1]["burst_rating"], 4)
        self.assertEqual(photos[4]["burst_rep"], 1)
        self.assertEqual(photos[4]["burst_rating"], 3)

    def test_unknown_drive_subsec_and_integer_fallback(self):
        # With subsec: 0.4s folded, 1.1s not folded
        p_sub = [
            self._mk(4000.1, drive_mode="", has_sub=1),
            self._mk(4000.5, drive_mode="", has_sub=1, rating=3),
            self._mk(4001.6, drive_mode="", has_sub=1),
        ]
        g1, f1 = _apply_burst_folding(p_sub)
        self.assertEqual((g1, f1), (1, 1))
        self.assertEqual(p_sub[1]["burst_rep"], 1)
        self.assertEqual(p_sub[2]["burst_rep"], 1)

        # Without subsec: exact same second folded, +1s not folded
        p_int = [
            self._mk(5000.0, drive_mode="", has_sub=0, stem="a"),
            self._mk(5000.0, drive_mode="", has_sub=0, stem="b"),
            self._mk(5001.0, drive_mode="", has_sub=0, stem="c"),
        ]
        g2, f2 = _apply_burst_folding(p_int)
        self.assertEqual((g2, f2), (1, 1))
        self.assertEqual(p_int[2]["burst_rep"], 1)


class TestRatingSources(unittest.TestCase):
    def test_all_five_databases_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            photo_dir = td / "2026" / "2026-05-01"
            photo_dir.mkdir(parents=True)
            dnorm = os.path.normpath(str(photo_dir)).rstrip("\\/").lower()

            lr_db = td / "test.lrcat"
            con = sqlite3.connect(lr_db)
            con.executescript("""
                CREATE TABLE AgLibraryRootFolder (id_local INTEGER PRIMARY KEY, absolutePath TEXT);
                CREATE TABLE AgLibraryFolder (id_local INTEGER PRIMARY KEY, rootFolder INTEGER, pathFromRoot TEXT);
                CREATE TABLE AgLibraryFile (id_local INTEGER PRIMARY KEY, folder INTEGER, baseName TEXT, extension TEXT);
                CREATE TABLE Adobe_images (id_local INTEGER PRIMARY KEY, rootFile INTEGER, rating INTEGER, pick INTEGER);
            """)
            con.execute("INSERT INTO AgLibraryRootFolder VALUES (1, ?)", (str(td / "2026"),))
            con.execute("INSERT INTO AgLibraryFolder VALUES (1, 1, '2026-05-01/')")
            con.execute("INSERT INTO AgLibraryFile VALUES (1, 1, 'DSC00001', 'ARW')")
            con.execute("INSERT INTO AgLibraryFile VALUES (2, 1, 'DSC00002', 'JPG')")
            con.execute("INSERT INTO Adobe_images VALUES (1, 1, 4, 0)")
            con.execute("INSERT INTO Adobe_images VALUES (2, 2, 0, 1)")
            con.commit()
            con.close()

            lr_map = read_lightroom_ratings(lr_db, allowed_exts={".arw", ".jpg"})
            self.assertEqual(lr_map[(dnorm, "dsc00001")], (4, 0))
            self.assertEqual(lr_map[(dnorm, "dsc00002")], (0, 1))

            c1_db = td / "test.cocatalogdb"
            con = sqlite3.connect(c1_db)
            con.executescript("""
                CREATE TABLE ZPATHLOCATION (Z_PK INTEGER PRIMARY KEY, ZWINROOT TEXT, ZRELATIVEPATH TEXT);
                CREATE TABLE ZIMAGE (Z_PK INTEGER PRIMARY KEY, ZIMAGELOCATION INTEGER, ZIMAGEFILENAME TEXT, ZISTRASHED INTEGER);
                CREATE TABLE ZVARIANT (Z_PK INTEGER PRIMARY KEY, ZIMAGE INTEGER);
                CREATE TABLE ZVARIANTLAYER (Z_PK INTEGER PRIMARY KEY, ZVARIANT INTEGER);
                CREATE TABLE ZVARIANTMETADATA (Z_PK INTEGER PRIMARY KEY, ZLAYER INTEGER, ZBASIC_RATING INTEGER);
            """)
            con.execute("INSERT INTO ZPATHLOCATION VALUES (1, ?, '2026-05-01')", (str(td / "2026"),))
            con.execute("INSERT INTO ZIMAGE VALUES (1, 1, 'DSC00003.ARW', 0)")
            con.execute("INSERT INTO ZIMAGE VALUES (2, 1, 'DSC00004.ARW', 1)")
            con.execute("INSERT INTO ZVARIANT VALUES (1, 1), (2, 2)")
            con.execute("INSERT INTO ZVARIANTLAYER VALUES (1, 1), (2, 2)")
            con.execute("INSERT INTO ZVARIANTMETADATA VALUES (1, 1, 5), (2, 2, 5)")
            con.commit()
            con.close()

            c1_map = read_capture_one_ratings(c1_db, allowed_exts={".arw"})
            self.assertEqual(c1_map[(dnorm, "dsc00003")], (5, 0))
            self.assertNotIn((dnorm, "dsc00004"), c1_map)

            px_db = td / "project.db"
            con = sqlite3.connect(px_db)
            con.execute(
                "CREATE TABLE thumbnail (originalImagePath TEXT, starLevel INTEGER, selectFlag INTEGER, isFavourite INTEGER, inRecycleBin INTEGER)"
            )
            con.execute("INSERT INTO thumbnail VALUES (?, 3, 1, 0, 0)", (str(photo_dir / "DSC00005.JPG"),))
            con.execute("INSERT INTO thumbnail VALUES (?, 5, 1, 1, 1)", (str(photo_dir / "DSC00006.JPG"),))
            con.commit()
            con.close()

            px_map = read_pixcake_ratings([px_db], allowed_exts={".jpg"})
            self.assertEqual(px_map[(dnorm, "dsc00005")], (3, 1))
            self.assertNotIn((dnorm, "dsc00006"), px_map)

            pp_db = td / "projects_v4.db"
            con = sqlite3.connect(pp_db)
            con.executescript("""
                CREATE TABLE projects (project_id INTEGER PRIMARY KEY, del_states INTEGER);
                CREATE TABLE images (project_id INTEGER, root_path_id TEXT, relative_path TEXT, name TEXT, level INTEGER, flag_mark INTEGER, is_del INTEGER);
            """)
            drv = photo_dir.drive
            rel = str(photo_dir)[len(drv):]
            con.execute("INSERT INTO projects VALUES (1, 0)")
            con.execute("INSERT INTO images VALUES (1, ?, ?, 'DSC00007.ARW', 2, 1, 0)", (drv + "\\", rel))
            con.execute("INSERT INTO images VALUES (1, ?, ?, 'DSC00008.ARW', 5, 1, 1)", (drv + "\\", rel))
            con.commit()
            con.close()

            pp_map = read_pixpretty_ratings([pp_db], allowed_exts={".arw"})
            self.assertEqual(pp_map[(dnorm, "dsc00007")], (2, 1))
            self.assertNotIn((dnorm, "dsc00008"), pp_map)

            lum_db = td / "test.luminarneo"
            con = sqlite3.connect(lum_db)
            con.executescript("""
                CREATE TABLE volumes (_id_int_64 INTEGER PRIMARY KEY, info_wide_ch TEXT, guid_wide_ch TEXT);
                CREATE TABLE paths (_id_int_64 INTEGER PRIMARY KEY, volume_id_int_64 INTEGER, path_wide_ch TEXT);
                CREATE TABLE images (_id_int_64 INTEGER PRIMARY KEY, path_wide_ch TEXT, marked_to_delete_bool INTEGER);
                CREATE TABLE paths_images (_key_id_int_64 INTEGER, _val_id_int_64 INTEGER);
                CREATE TABLE image_user_attributes (_out_id_int_64 INTEGER, rating INTEGER, marker INTEGER, marked_to_delete_bool INTEGER, trash_bool INTEGER);
            """)
            con.execute("INSERT INTO volumes VALUES (1, ?, '')", (drv + "\\",))
            con.execute("INSERT INTO paths VALUES (1, 1, ?)", (rel.lstrip("\\/"),))
            con.execute("INSERT INTO images VALUES (1, 'DSC00009.JPG', 0)")
            con.execute("INSERT INTO images VALUES (2, 'DSC00010.JPG', 0)")
            con.execute("INSERT INTO paths_images VALUES (1, 1), (1, 2)")
            con.execute("INSERT INTO image_user_attributes VALUES (1, 4, 1, 0, 0)")
            con.execute("INSERT INTO image_user_attributes VALUES (2, 5, 1, 0, 1)")
            con.commit()
            con.close()

            lum_map = read_luminar_ratings(lum_db, allowed_exts={".jpg"})
            self.assertEqual(lum_map[(dnorm, "dsc00009")], (4, 1))
            self.assertNotIn((dnorm, "dsc00010"), lum_map)


class TestScannerAndReport(unittest.TestCase):
    def test_multi_root_dedup_cache_and_html_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            root25 = td / "2025"
            root26 = td / "2026"
            other = td / "UnrelatedDocs"
            other.mkdir()
            (other / "ignore.jpg").write_bytes(b"fake")

            (root25 / "TripA").mkdir(parents=True)
            (root26 / "TripA").mkdir(parents=True)
            (root26 / "2026-08-01").mkdir(parents=True)
            (root26 / "backups").mkdir(parents=True)
            (root26 / "backups" / "ignored.jpg").write_bytes(b"fake")

            (root25 / "TripA" / "DSC0001.JPG").write_bytes(b"jpg1")
            (root25 / "TripA" / "DSC0001.ARW").write_bytes(b"arw1")
            (root25 / "TripA" / "DSC0001.ARW.xmp").write_bytes(b"xmp1")

            (root26 / "TripA" / "DJI_0001.DNG").write_bytes(b"dng2")
            (root26 / "TripA" / "DJI_0001.MP4").write_bytes(b"mp4")
            (root26 / "2026-08-01" / "DSC0003.JPG").write_bytes(b"jpg3")

            cache_path = td / "cache.json"
            norm = GearNormalizer(PROJECT_ROOT / "gear_aliases.json")

            def fake_exif_batch(_et, paths):
                res = []
                for p in paths:
                    name = Path(p).name.upper()
                    if name == "DSC0001.JPG":
                        res.append({
                            "ft": "JPEG", "mk": "SONY", "md": "ILCE-6400",
                            "lm": "Viltrox 56mm F1.7 E", "li": "", "ln": "", "ls": "",
                            "fl": 56.0, "fl35": 84.0, "w": 6000, "h": 4000, "or": 1,
                            "dt": "2025-05-10", "hr": 18, "ts": 1746871200.0, "sub": 0,
                            "dm": "single", "sq": 0, "bu": "", "rt": 0, "pk": 0,
                        })
                    elif name == "DSC0001.ARW":
                        res.append({
                            "ft": "ARW", "mk": "SONY", "md": "ILCE-6400",
                            "lm": "Viltrox 56mm F1.7 E", "li": "", "ln": "", "ls": "",
                            "fl": 56.0, "fl35": 84.0, "w": 6000, "h": 4000, "or": 1,
                            "dt": "2025-05-10", "hr": 18, "ts": 1746871200.0, "sub": 0,
                            "dm": "single", "sq": 0, "bu": "", "rt": 0, "pk": 0,
                        })
                    elif name == "DSC0001.ARW.XMP":
                        res.append({
                            "ft": "XMP", "mk": "", "md": "", "lm": "", "li": "", "ln": "", "ls": "",
                            "fl": None, "fl35": None, "w": None, "h": None, "or": None,
                            "dt": None, "hr": None, "ts": None, "sub": 0,
                            "dm": "", "sq": 0, "bu": "", "rt": 5, "pk": 1,
                        })
                    elif name == "DJI_0001.DNG":
                        res.append({
                            "ft": "DNG", "mk": "DJI", "md": "PP-101",
                            "lm": "6.5 mm f/2.0", "li": "", "ln": "", "ls": "",
                            "fl": 6.5, "fl35": 20.0, "w": 4000, "h": 3000, "or": 1,
                            "dt": "2026-02-01", "hr": 7, "ts": 1769900400.0, "sub": 0,
                            "dm": "", "sq": 0, "bu": "", "rt": 3, "pk": 0,
                        })
                    else:
                        res.append({
                            "ft": "JPEG", "mk": "SONY", "md": "ILCE-6400",
                            "lm": "", "li": "</script><script>alert(1)</script>", "ln": "", "ls": "",
                            "fl": 50.0, "fl35": 75.0, "w": 6000, "h": 4000, "or": 6,
                            "dt": "2026-08-01", "hr": 19, "ts": 1785582000.0, "sub": 0,
                            "dm": "single", "sq": 0, "bu": "", "rt": 0, "pk": 0,
                        })
                return res

            with mock.patch("src.exif_scanner._run_exiftool_batch", side_effect=fake_exif_batch) as m_et:
                ds1 = scan_libraries(
                    photo_roots=[root25, root26],
                    exiftool_path="exiftool",
                    normalizer=norm,
                    cache_path=cache_path,
                    use_cache=True,
                    use_db=False,
                    lang="en",
                )
                self.assertEqual(m_et.call_count, 1)
                self.assertEqual(ds1["health"]["logical_photos"], 3)
                self.assertEqual(ds1["health"]["deduped_pairs"], 1)
                self.assertEqual(ds1["health"]["skipped_videos"], 1)

                outings = {p["outing"] for p in ds1["photos"]}
                self.assertIn("2025/TripA", outings)
                self.assertIn("2026/TripA", outings)
                self.assertIn("2026-08-01", outings)

                gimbal_photos = [p for p in ds1["photos"] if p["camera"] == "DJI Osmo Pocket 3"]
                self.assertEqual(len(gimbal_photos), 1)
                self.assertEqual(gimbal_photos[0]["gear_type"], "gimbal")

                m_et.reset_mock()
                ds2 = scan_libraries(
                    photo_roots=[root25, root26],
                    exiftool_path="exiftool",
                    normalizer=norm,
                    cache_path=cache_path,
                    use_cache=True,
                    use_db=False,
                    lang="en",
                )
                self.assertEqual(m_et.call_count, 0)
                self.assertEqual(ds2["health"]["logical_photos"], 3)

                out_html = td / "report.html"
                build_html_report(ds1, PROJECT_ROOT / "templates" / "report_template.html", out_html, default_lang="zh")
                html_text = out_html.read_text(encoding="utf-8")

                self.assertIn('id="yearSelect"', html_text)
                self.assertIn('id="monthSelect"', html_text)
                self.assertIn('id="btnBurstFold"', html_text)
                self.assertNotIn('id="outingSearch"', html_text)
                self.assertNotIn('id="apertureChart"', html_text)
                self.assertNotRegex(html_text, r"https?://")
                self.assertNotIn("</script><script>alert(1)</script>", html_text)
                self.assertIn("<\\/script>", html_text)

                node = shutil.which("node")
                if node:
                    m = re.search(r"<script>\n(.*?)\n</script>", html_text, re.S)
                    self.assertIsNotNone(m)
                    js_file = td / "check.js"
                    js_file.write_text(m.group(1), encoding="utf-8")
                    proc = subprocess.run([node, "--check", str(js_file)], capture_output=True, text=True)
                    self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_real_exiftool_on_workspace_sample(self):
        et = resolve_exiftool(project_root=PROJECT_ROOT)
        sample_dir = Path(r"d:\Windows\文档\2026\2026-01-01")
        if not et or not sample_dir.is_dir():
            self.skipTest("ExifTool or sample workspace not present")
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            sub = td / "2026-01-01"
            sub.mkdir()
            for fn in ("DJI_20260101115854_0562_D.DNG", "DSC00004.JPG"):
                src = sample_dir / fn
                if src.is_file():
                    shutil.copy2(src, sub / fn)
            norm = GearNormalizer(PROJECT_ROOT / "gear_aliases.json")
            ds = scan_libraries([td], et, norm, use_cache=False, use_db=False, lang="en")
            self.assertEqual(ds["health"]["logical_photos"], 2)
            payload = build_report_payload(ds)
            self.assertEqual(len(payload["rows"]), 2)
            self.assertEqual(len(payload["rows"][0]), 14)


if __name__ == "__main__":
    unittest.main()
