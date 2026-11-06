"""Tests for upload, preview, column profile and validation storage. Run:  python -m unittest discover tests -v"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from app import datasets, db, services
from app.services import ServiceError

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "survey_sample.csv"


class DatasetTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        services._failed.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Path(self.tmp.name) / "uploads"
        self.a = services.register(self.conn, "A", "a@example.org", "correct horse")
        self.b = services.register(self.conn, "B", "b@example.org", "correct horse")
        self.pa = services.create_project(self.conn, self.a["id"], "A project")
        self.csv = SAMPLE.read_bytes()

    def tearDown(self):
        self.tmp.cleanup()

    def upload(self, name="survey.csv", data=None, user=None, project=None):
        return datasets.save_dataset(self.conn, (user or self.a)["id"], (project or self.pa)["id"], name,
                                     self.csv if data is None else data, self.storage)

    def code(self, fn, *args, **kwargs):
        with self.assertRaises(ServiceError) as ctx:
            fn(*args, **kwargs)
        return ctx.exception.status

    # ---- reading files ---------------------------------------------------------------------------
    def test_parse_csv_variants(self):
        for data in (b"a,b\n1,2\n3,4\n", b"\xef\xbb\xbfa,b\n1,2\n", "a,b\ncaf\u00e9,2\n".encode("cp1252"), b"a;b\n1;2\n3;4\n", b"a\tb\n1\t2\n"):
            df = datasets.parse_upload("x.csv", data)
            self.assertEqual(list(df.columns), ["a", "b"])

    def test_parse_excel(self):
        buf = io.BytesIO()
        pd.DataFrame({"age": [25, 30], "state": ["Kano", "Lagos"]}).to_excel(buf, index=False)
        df = datasets.parse_upload("x.xlsx", buf.getvalue())
        self.assertEqual(df.shape, (2, 2))
        self.assertEqual(df["age"].tolist(), ["25", "30"])

    def test_parse_excel_enforces_row_limit_while_streaming(self):
        buf = io.BytesIO()
        pd.DataFrame({"age": [25, 30, 35], "state": ["Kano", "Lagos", "Abuja"]}).to_excel(buf, index=False)
        with mock.patch.object(datasets, "MAX_ROWS", 2):
            self.assertEqual(self.code(datasets.parse_upload, "x.xlsx", buf.getvalue()), 422)

    def test_parse_rejections(self):
        self.assertEqual(self.code(datasets.parse_upload, "x.exe", b"abc"), 415)
        self.assertEqual(self.code(datasets.parse_upload, "x.xls", b"abc"), 415)
        self.assertEqual(self.code(datasets.parse_upload, "x.csv", b""), 422)
        self.assertEqual(self.code(datasets.parse_upload, "x.csv", b"a,b\n"), 422)           # header only
        self.assertEqual(self.code(datasets.parse_upload, "x.xlsx", b"not really excel"), 422)
        with mock.patch.object(datasets, "MAX_BYTES", 10):
            self.assertEqual(self.code(datasets.parse_upload, "x.csv", b"a,b\n1,2\n3,4\n"), 413)
        with mock.patch.object(datasets, "MAX_COLS", 1):
            self.assertEqual(self.code(datasets.parse_upload, "x.csv", b"a,b\n1,2\n"), 422)
        with mock.patch.object(datasets, "MAX_ROWS", 1):
            self.assertEqual(self.code(datasets.parse_upload, "x.csv", b"a,b\n1,2\n3,4\n"), 422)

    def test_blank_and_duplicate_headers_are_made_usable(self):
        df = datasets.parse_upload("x.csv", b"a,,a\n1,2,3\n")
        self.assertEqual(len(set(df.columns)), 3)
        self.assertTrue(all(c.strip() for c in df.columns))

    def test_safe_name(self):
        self.assertEqual(datasets.safe_name("../../etc/passwd"), "passwd")
        self.assertEqual(datasets.safe_name("C:\\Users\\x\\data.csv"), "data.csv")
        self.assertEqual(datasets.safe_name("<script>.csv"), "_script_.csv")
        self.assertEqual(datasets.safe_name(""), "dataset")
        self.assertEqual(datasets.safe_name(None), "dataset")
        self.assertLessEqual(len(datasets.safe_name("a" * 500 + ".csv")), 100)

    # ---- storing, ownership --------------------------------------------------------------------------
    def test_upload_stores_under_generated_name(self):
        d = self.upload("../../evil.csv")
        self.assertEqual((d["rows"], d["columns"]), (615, 17))
        self.assertEqual(d["name"], "evil.csv")
        files = [p.name for p in self.storage.iterdir()]
        self.assertEqual(files, [f"ds_{d['id']}.csv"])
        self.assertEqual(len(datasets.list_datasets(self.conn, self.a["id"], self.pa["id"])), 1)

    def test_other_users_cannot_touch_my_data(self):
        d = self.upload()
        for fn, args in [(datasets.list_datasets, (self.pa["id"],)), (datasets.dataset_info, (d["id"], self.storage)),
                         (datasets.preview, (d["id"], self.storage)), (datasets.run_validation, (d["id"], self.storage)),
                         (datasets.get_validation, (d["id"],)), (datasets.delete_dataset, (d["id"], self.storage)),
                         (datasets.get_issues, (d["id"], self.storage)), (datasets.remove_project, (self.pa["id"], self.storage))]:
            self.assertEqual(self.code(fn, self.conn, self.b["id"], *args), 404, fn.__name__)
        self.assertEqual(self.code(datasets.save_dataset, self.conn, self.b["id"], self.pa["id"], "x.csv", self.csv, self.storage), 404)
        self.assertEqual(len(datasets.list_datasets(self.conn, self.a["id"], self.pa["id"])), 1)

    def test_preview_pagination(self):
        d = self.upload()
        p = datasets.preview(self.conn, self.a["id"], d["id"], self.storage, offset=0, limit=5)
        self.assertEqual((len(p["rows"]), p["total"], len(p["columns"])), (5, 615, 17))
        last = datasets.preview(self.conn, self.a["id"], d["id"], self.storage, offset=610, limit=100)
        self.assertEqual(len(last["rows"]), 5)
        big = datasets.preview(self.conn, self.a["id"], d["id"], self.storage, offset=-5, limit=100000)
        self.assertEqual((big["offset"], big["limit"]), (0, 100))

    def test_column_profile(self):
        d = self.upload()
        cols = {c["name"]: c for c in datasets.dataset_info(self.conn, self.a["id"], d["id"], self.storage)["column_details"]}
        self.assertEqual(cols["latitude"]["role"], "latitude")
        self.assertEqual(cols["gender"]["type"], "category")
        self.assertEqual(cols["interview_date"]["type"], "date")
        self.assertEqual(cols["household_size"]["type"], "number")
        self.assertEqual(cols["respondent_age"]["missing"], 20)

    # ---- validation ---------------------------------------------------------------------------------
    def test_validation_report_is_saved_and_readable(self):
        d = self.upload()
        self.assertEqual(datasets.get_validation(self.conn, self.a["id"], d["id"]), {"ran": False})
        self.assertEqual(self.code(datasets.get_issues, self.conn, self.a["id"], d["id"], self.storage), 404)
        rep = datasets.run_validation(self.conn, self.a["id"], d["id"], self.storage)
        self.assertEqual(rep["summary"]["quality_score"], 73.2)
        self.assertIsInstance(rep["summary"]["records"], int)                       # numbers stay numbers
        self.assertIsInstance(rep["summary"]["issues_by_type"]["missing_value"], int)
        json.dumps(rep)
        again = datasets.get_validation(self.conn, self.a["id"], d["id"])
        self.assertEqual(again["summary"]["quality_score"], 73.2)
        self.assertTrue(again["recommendations"])
        self.assertTrue(again["enumerators"] and "error_rate_pct" in again["enumerators"][0])
        datasets.run_validation(self.conn, self.a["id"], d["id"], self.storage)     # rerun replaces, not duplicates
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM validation_runs").fetchone()[0], 1)

    def test_issue_filters_and_pages(self):
        d = self.upload()
        datasets.run_validation(self.conn, self.a["id"], d["id"], self.storage)
        everything = datasets.get_issues(self.conn, self.a["id"], d["id"], self.storage, page_size=50)
        self.assertEqual(len(everything["rows"]), 50)
        self.assertGreater(everything["pages"], 1)
        errs = datasets.get_issues(self.conn, self.a["id"], d["id"], self.storage, severity="error", page_size=200)
        sev = errs["columns"].index("severity")
        self.assertTrue(all(r[sev] == "error" for r in errs["rows"]))
        dup = datasets.get_issues(self.conn, self.a["id"], d["id"], self.storage, issue_type="duplicate_record")
        self.assertEqual(dup["total"], 15)
        gps = datasets.get_issues(self.conn, self.a["id"], d["id"], self.storage, column="latitude", issue_type="invalid_gps")
        self.assertGreater(gps["total"], 0)
        far = datasets.get_issues(self.conn, self.a["id"], d["id"], self.storage, page=9999)
        self.assertEqual(far["page"], far["pages"])
        self.assertEqual(self.code(datasets.get_issues, self.conn, self.a["id"], d["id"], self.storage, severity="bogus"), 422)

    def test_validation_handles_data_with_no_known_columns(self):
        d = self.upload("odd.csv", b"colour,shape\nred,circle\nblue,square\n")
        rep = datasets.run_validation(self.conn, self.a["id"], d["id"], self.storage)
        self.assertEqual(rep["summary"]["quality_score"], 100.0)

    # ---- deleting ----------------------------------------------------------------------------------------
    def test_delete_dataset_removes_files_and_rows(self):
        d = self.upload()
        datasets.run_validation(self.conn, self.a["id"], d["id"], self.storage)
        self.assertEqual(len(list(self.storage.iterdir())), 2)
        datasets.delete_dataset(self.conn, self.a["id"], d["id"], self.storage)
        self.assertEqual(list(self.storage.iterdir()), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM validation_runs").fetchone()[0], 0)
        self.assertEqual(self.code(datasets.dataset_info, self.conn, self.a["id"], d["id"], self.storage), 404)

    def test_delete_project_removes_its_datasets_and_files(self):
        d = self.upload()
        datasets.run_validation(self.conn, self.a["id"], d["id"], self.storage)
        keep = services.create_project(self.conn, self.a["id"], "Other")
        d2 = self.upload("two.csv", project=keep)
        datasets.remove_project(self.conn, self.a["id"], self.pa["id"], self.storage)
        names = sorted(p.name for p in self.storage.iterdir())
        self.assertEqual(names, [f"ds_{d2['id']}.csv"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
