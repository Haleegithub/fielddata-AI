"""Tests for accounts, sessions and projects. Run:  python -m unittest discover tests -v"""
import time
import unittest

from app import db, security, services


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)
        services._failed.clear()

    def user(self, email="ada@example.org", name="Ada"):
        return services.register(self.conn, name, email, "correct horse")

    def test_password_is_hashed_not_stored(self):
        self.user()
        stored = self.conn.execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertNotIn("correct horse", stored)
        self.assertTrue(security.verify_password("correct horse", stored))
        self.assertFalse(security.verify_password("wrong password", stored))

    def test_register_validation(self):
        for name, email, pw in [("", "a@b.org", "longenough"), ("A", "not-an-email", "longenough"),
                                ("A", "a@b.org", "short"), ("A" * 101, "a@b.org", "longenough"), ("A", "a@b.org", "x" * 129)]:
            with self.assertRaises(services.ServiceError) as ctx:
                services.register(self.conn, name, email, pw)
            self.assertEqual(ctx.exception.status, 422)

    def test_duplicate_email_rejected_case_insensitive(self):
        self.user("ada@example.org")
        with self.assertRaises(services.ServiceError) as ctx:
            self.user("ADA@Example.org")
        self.assertEqual(ctx.exception.status, 409)

    def test_login_ok_and_wrong_password(self):
        self.user()
        self.assertEqual(services.login(self.conn, "ADA@example.org", "correct horse")["name"], "Ada")
        for email, pw in [("ada@example.org", "nope nope"), ("ghost@example.org", "correct horse"), ("ada@example.org", None)]:
            with self.assertRaises(services.ServiceError) as ctx:
                services.login(self.conn, email, pw)
            self.assertEqual(ctx.exception.status, 401)

    def test_login_locks_after_repeated_failures(self):
        self.user()
        for _ in range(services.MAX_FAILED):
            with self.assertRaises(services.ServiceError):
                services.login(self.conn, "ada@example.org", "bad password", "1.2.3.4")
        with self.assertRaises(services.ServiceError) as ctx:
            services.login(self.conn, "ada@example.org", "correct horse", "1.2.3.4")
        self.assertEqual(ctx.exception.status, 429)
        # a different address is not blocked
        self.assertEqual(services.login(self.conn, "ada@example.org", "correct horse", "9.9.9.9")["email"], "ada@example.org")

    def test_session_lifecycle(self):
        u = self.user()
        token = services.create_session(self.conn, u["id"])
        self.assertEqual(services.user_from_token(self.conn, token)["email"], "ada@example.org")
        stored = self.conn.execute("SELECT token_hash FROM sessions").fetchone()[0]
        self.assertNotEqual(stored, token)                                   # only a hash is stored
        self.assertIsNone(services.user_from_token(self.conn, "forged-token"))
        self.assertIsNone(services.user_from_token(self.conn, None))
        services.end_session(self.conn, token)
        self.assertIsNone(services.user_from_token(self.conn, token))

    def test_expired_session_rejected(self):
        u = self.user()
        token = services.create_session(self.conn, u["id"])
        self.conn.execute("UPDATE sessions SET expires_at = ?", (time.time() - 1,))
        self.assertIsNone(services.user_from_token(self.conn, token))

    def test_projects_crud(self):
        u = self.user()
        p = services.create_project(self.conn, u["id"], " Kano survey ", "Baseline", "Kano")
        self.assertEqual(p["name"], "Kano survey")
        self.assertEqual(len(services.list_projects(self.conn, u["id"])), 1)
        services.delete_project(self.conn, u["id"], p["id"])
        self.assertEqual(services.list_projects(self.conn, u["id"]), [])

    def test_project_validation(self):
        u = self.user()
        for kwargs in [{"name": "  "}, {"name": "x" * 101}, {"name": "ok", "description": "d" * 1001}]:
            with self.assertRaises(services.ServiceError) as ctx:
                services.create_project(self.conn, u["id"], **kwargs)
            self.assertEqual(ctx.exception.status, 422)

    def test_users_cannot_see_or_delete_each_others_projects(self):
        a, b = self.user("a@example.org", "A"), self.user("b@example.org", "B")
        pa = services.create_project(self.conn, a["id"], "A's project")
        self.assertEqual(services.list_projects(self.conn, b["id"]), [])
        for fn in (services.get_project, services.delete_project):
            with self.assertRaises(services.ServiceError) as ctx:
                fn(self.conn, b["id"], pa["id"])
            self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(len(services.list_projects(self.conn, a["id"])), 1)  # still there

    def test_sql_injection_text_is_stored_as_plain_text(self):
        u = self.user()
        p = services.create_project(self.conn, u["id"], "x'); DROP TABLE users;--")
        self.assertEqual(p["name"], "x'); DROP TABLE users;--")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
