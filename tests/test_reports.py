import tempfile
import unittest
from pathlib import Path

from app import datasets, db, report_service, services

SAMPLE = Path(__file__).resolve().parent.parent / "sample_data" / "survey_sample.csv"


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        self.tmp = tempfile.TemporaryDirectory()
        self.storage = Path(self.tmp.name) / "uploads"
        self.user = services.register(self.conn, "Report Tester", "report@example.org", "correct horse")
        self.project = services.create_project(self.conn, self.user["id"], "Test field project", location="Kano")
        self.dataset = datasets.save_dataset(self.conn, self.user["id"], self.project["id"], "sample.csv", SAMPLE.read_bytes(), self.storage)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_pdf_report_is_generated(self):
        data = report_service.build_pdf(self.conn, self.user["id"], self.dataset["id"], self.storage)
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertGreater(len(data), 1000)

    def test_excel_report_is_generated_with_expected_sheets(self):
        from io import BytesIO
        from openpyxl import load_workbook
        data = report_service.build_xlsx(self.conn, self.user["id"], self.dataset["id"], self.storage)
        wb = load_workbook(BytesIO(data), read_only=True)
        self.assertEqual(wb.sheetnames, ["Overview", "Key Findings", "Missing by Column", "Numeric Summaries", "Submission Trend"])
        self.assertEqual(wb["Overview"]["B9"].value, 615)

    def test_report_cannot_be_generated_for_another_user(self):
        other = services.register(self.conn, "Other", "other@example.org", "correct horse")
        from app.services import ServiceError
        with self.assertRaises(ServiceError) as ctx:
            report_service.build_pdf(self.conn, other["id"], self.dataset["id"], self.storage)
        self.assertEqual(ctx.exception.status, 404)


class ReportRouteTests(unittest.TestCase):
    def test_authenticated_report_download_routes(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch
        from app import main
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(db, "DB_PATH", root / "app.db"), patch.object(main, "STORAGE", root / "uploads"):
                with TestClient(main.app) as client:
                    registered = client.post("/api/register", json={"name": "Route Tester", "email": "route@example.org", "password": "correct horse"})
                    self.assertEqual(registered.status_code, 201)
                    project = client.post("/api/projects", json={"name": "Route project"}).json()
                    uploaded = client.post(f"/api/projects/{project['id']}/datasets", files={"file": ("sample.csv", SAMPLE.read_bytes(), "text/csv")})
                    self.assertEqual(uploaded.status_code, 201)
                    dataset_id = uploaded.json()["id"]
                    pdf = client.get(f"/api/datasets/{dataset_id}/analytics/report?format=pdf")
                    self.assertEqual(pdf.status_code, 200)
                    self.assertTrue(pdf.content.startswith(b"%PDF"))
                    self.assertIn("application/pdf", pdf.headers["content-type"])
                    xlsx = client.get(f"/api/datasets/{dataset_id}/analytics/report?format=xlsx")
                    self.assertEqual(xlsx.status_code, 200)
                    self.assertTrue(xlsx.content.startswith(b"PK"))
                    self.assertIn("no-store", xlsx.headers.get("cache-control", ""))
                    self.assertEqual(client.get(f"/api/datasets/{dataset_id}/analytics/report?format=exe").status_code, 422)
                    blocked = client.post("/api/projects", json={"name": "Cross-site attempt"}, headers={"Origin": "https://evil.example"})
                    self.assertEqual(blocked.status_code, 403)
                    self.assertEqual(blocked.json()["error"], "Cross-site request rejected.")


if __name__ == "__main__":
    unittest.main()
