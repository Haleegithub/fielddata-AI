"""
End-to-end check of the running server (Stage 2). Start the server first, then in a second window run:
    python smoke_test.py
Uses only the standard library. It creates two throw-away test accounts in your local database.
"""
import json
import secrets
import sys
from pathlib import Path
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

BASE = "http://127.0.0.1:8000"
passed = failed = 0


class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with self.opener.open(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.load(e)
            except ValueError:
                return e.code, {}


    def raw(self, path):
        try:
            with self.opener.open(urllib.request.Request(BASE + path)) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    def upload(self, path, filename, content):
        boundary = "----fd" + secrets.token_hex(8)
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n').encode() + content + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(BASE + path, data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with self.opener.open(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.load(e)
            except ValueError:
                return e.code, {}


def check(name, ok):
    global passed, failed
    passed += ok
    failed += not ok
    print(("PASS  " if ok else "FAIL  ") + name)


try:
    urllib.request.urlopen(BASE + "/", timeout=5)
except Exception:
    sys.exit("Cannot reach the server. Start it first with: python -m uvicorn app.main:app")

tag = secrets.token_hex(4)
a, b = Client(), Client()
s, _ = a.call("GET", "/api/me");                                              check("not signed in -> 401", s == 401)
s, _ = a.call("GET", "/api/projects");                                        check("projects need sign-in -> 401", s == 401)
s, r = a.call("POST", "/api/register", {"name": "Test A", "email": f"a{tag}@example.org", "password": "correct horse"})
check("register -> 201", s == 201 and r.get("email") == f"a{tag}@example.org")
s, _ = a.call("GET", "/api/me");                                              check("signed in after register", s == 200)
s, _ = Client().call("POST", "/api/register", {"name": "X", "email": f"a{tag}@example.org", "password": "correct horse"})
check("duplicate email -> 409", s == 409)
s, _ = Client().call("POST", "/api/register", {"name": "X", "email": "bad", "password": "correct horse"})
check("invalid email -> 422", s == 422)
s, _ = Client().call("POST", "/api/register", {"name": "X", "email": f"x{tag}@example.org", "password": "short"})
check("short password -> 422", s == 422)
s, p = a.call("POST", "/api/projects", {"name": "Kano baseline", "description": "Test", "location": "Kano"})
check("create project -> 201", s == 201 and p.get("name") == "Kano baseline")
s, lst = a.call("GET", "/api/projects");                                      check("list shows the project", s == 200 and len(lst) == 1)
s, _ = a.call("POST", "/api/projects", {"name": "   "});                      check("blank project name -> 422", s == 422)
s, _ = b.call("POST", "/api/register", {"name": "Test B", "email": f"b{tag}@example.org", "password": "correct horse"})
s, lst = b.call("GET", "/api/projects");                                      check("user B sees no projects of A", s == 200 and lst == [])
s, _ = b.call("GET", f"/api/projects/{p['id']}");                             check("user B cannot open A's project -> 404", s == 404)
s, _ = b.call("DELETE", f"/api/projects/{p['id']}");                          check("user B cannot delete A's project -> 404", s == 404)
# ---- Stage 3: datasets ----------------------------------------------------------------------------------
sample = Path("sample_data/survey_sample.csv")
if not sample.exists():
    sys.exit("Run this from the fielddata-ai folder (sample_data/survey_sample.csv not found).")
csv_bytes = sample.read_bytes()
pid = p["id"]
s, d = a.upload(f"/api/projects/{pid}/datasets", "survey_sample.csv", csv_bytes)
check("upload CSV -> 201 with 615 rows", s == 201 and d.get("rows") == 615 and d.get("columns") == 17)
did = d.get("id")
s, _ = a.upload(f"/api/projects/{pid}/datasets", "virus.exe", b"MZ....");          check("upload .exe -> 415", s == 415)
s, _ = a.upload(f"/api/projects/{pid}/datasets", "empty.csv", b"");                check("upload empty file -> 422", s == 422)
s, lst = a.call("GET", f"/api/projects/{pid}/datasets");                           check("project lists 1 dataset", s == 200 and len(lst) == 1)
s, pv = a.call("GET", f"/api/datasets/{did}/preview?offset=0&limit=20")
check("preview returns 20 of 615 rows", s == 200 and len(pv["rows"]) == 20 and pv["total"] == 615)
s, info = a.call("GET", f"/api/datasets/{did}");                                   check("column profile present", s == 200 and len(info["column_details"]) == 17)
s, v = a.call("GET", f"/api/datasets/{did}/validation");                           check("validation not run yet", s == 200 and v.get("ran") is False)
s, _ = a.call("GET", f"/api/datasets/{did}/issues");                               check("issues before validation -> 404", s == 404)
s, rep = a.call("POST", f"/api/datasets/{did}/validate")
check("run validation -> score 73.2", s == 200 and rep["summary"]["quality_score"] == 73.2)
s, v = a.call("GET", f"/api/datasets/{did}/validation");                           check("validation report saved", s == 200 and v.get("ran") is True)
s, iss = a.call("GET", f"/api/datasets/{did}/issues?issue_type=duplicate_record");  check("filter duplicates -> 15", s == 200 and iss["total"] == 15)
s, _ = a.call("GET", f"/api/datasets/{did}/issues?severity=bogus");                check("bad severity -> 422", s == 422)
s, _ = a.call("GET", f"/api/datasets/{did}/issues?page_size=500");                 check("page_size 500 -> 422", s == 422)
s, _ = b.call("GET", f"/api/datasets/{did}");                                      check("user B cannot open A's dataset -> 404", s == 404)
s, _ = b.call("GET", f"/api/datasets/{did}/preview");                              check("user B cannot preview -> 404", s == 404)
s, _ = b.call("POST", f"/api/datasets/{did}/validate");                            check("user B cannot validate -> 404", s == 404)
s, _ = b.upload(f"/api/projects/{pid}/datasets", "x.csv", csv_bytes);              check("user B cannot upload into A's project -> 404", s == 404)
s, _ = a.call("DELETE", f"/api/datasets/{did}");                                   check("owner deletes dataset -> 200", s == 200)
s, _ = a.call("GET", f"/api/datasets/{did}");                                      check("dataset gone afterwards -> 404", s == 404)
s, d2 = a.upload(f"/api/projects/{pid}/datasets", "again.csv", csv_bytes);         check("upload again -> 201", s == 201)
did2 = d2.get("id")

# ---- Stage 4: cleaning --------------------------------------------------------------------------------------
s, g = a.call("POST", f"/api/datasets/{did2}/corrections/generate")
check("generate suggestions (runs the check itself)", s == 200 and g["by_rule"]["remove_duplicate"]["pending"] == 15)
s, lst = a.call("GET", f"/api/datasets/{did2}/corrections?rule=remove_duplicate");  check("filter suggestions by type -> 15", s == 200 and lst["total"] == 15)
s, _ = a.call("POST", f"/api/datasets/{did2}/corrections/review", {"status": "approved", "rule": "swap_gps"})
check("review with a bad status -> 422", s == 422)
s, _ = a.call("POST", f"/api/datasets/{did2}/corrections/review", {"status": "accepted"})
check("review without choosing anything -> 422", s == 422)
s, _ = b.call("POST", f"/api/datasets/{did2}/corrections/generate");                check("user B cannot generate -> 404", s == 404)
s, _ = b.call("POST", f"/api/datasets/{did2}/corrections/review", {"status": "accepted", "confidence": "high"})
check("user B cannot review -> 404", s == 404)
s, _, _ = b.raw(f"/api/datasets/{did2}/download?what=original");                    check("user B cannot download -> 404", s == 404)
s, _ = a.call("POST", f"/api/datasets/{did2}/clean");                               check("apply with nothing accepted -> 422", s == 422)
a.call("POST", f"/api/datasets/{did2}/corrections/review", {"status": "accepted", "confidence": "high"})
s, r = a.call("POST", f"/api/datasets/{did2}/corrections/review", {"status": "accepted", "confidence": "medium"})
check("accept high + medium -> 200", s == 200 and r["summary"]["by_status"]["accepted"] > 0)
s, rep = a.call("POST", f"/api/datasets/{did2}/clean")
check("apply -> 15 duplicates removed, score improves", s == 200 and rep["records_removed"] == 15 and rep["score_after"] > rep["score_before"])
s, rep2 = a.call("GET", f"/api/datasets/{did2}/cleaning");                          check("cleaning report saved", s == 200 and rep2.get("ran") is True)
s, ctype, data = a.raw(f"/api/datasets/{did2}/download?what=cleaned&format=csv")
check("download cleaned CSV (600 records)", s == 200 and ctype.startswith("text/csv") and len(data.decode("utf-8-sig").strip().splitlines()) == 601)
s, ctype, data = a.raw(f"/api/datasets/{did2}/download?what=cleaned&format=xlsx")
check("download cleaned Excel", s == 200 and "spreadsheetml" in ctype and data[:2] == b"PK")
s, _, _ = a.raw(f"/api/datasets/{did2}/download?what=secrets");                     check("download with a bad type -> 422", s == 422)

# ---- Stage 5: analytics -------------------------------------------------------------------------------------
s, an = a.call("GET", f"/api/datasets/{did2}/analytics")
check("analytics (original): 615 records, findings listed", s == 200 and an["overview"]["records_total"] == 615 and len(an["findings"]) >= 6)
s, an2 = a.call("GET", f"/api/datasets/{did2}/analytics?source=cleaned")
check("analytics (cleaned): 600 records, no duplicates", s == 200 and an2["overview"]["records_total"] == 600 and an2["overview"]["duplicates_excluded"] == 0)
s, _ = a.call("GET", f"/api/datasets/{did2}/analytics?source=bogus");               check("analytics with a bad source -> 422", s == 422)
s, _ = b.call("GET", f"/api/datasets/{did2}/analytics");                            check("user B cannot see A's analytics -> 404", s == 404)
s, sm = a.call("GET", f"/api/projects/{pid}/summary")
check("project summary lists the dataset", s == 200 and sm["totals"]["datasets"] >= 1 and sm["totals"]["cleaned"] >= 1)
s, _ = b.call("GET", f"/api/projects/{pid}/summary");                               check("user B cannot see A's project summary -> 404", s == 404)

s, _ = a.call("POST", "/api/logout");                                         check("logout -> 200", s == 200)
s, _ = a.call("GET", "/api/me");                                              check("signed out afterwards -> 401", s == 401)
s, _ = a.call("POST", "/api/login", {"email": f"a{tag}@example.org", "password": "wrong password"})
check("wrong password -> 401", s == 401)
s, _ = a.call("POST", "/api/login", {"email": f"a{tag}@example.org", "password": "correct horse"})
check("login -> 200", s == 200)
s, _ = a.call("DELETE", f"/api/projects/{p['id']}");                          check("owner can delete -> 200", s == 200)
s, _ = a.call("GET", f"/api/datasets/{did2}");                                     check("deleting a project removes its datasets -> 404", s == 404)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
