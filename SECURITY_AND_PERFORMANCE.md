# Security and performance review

Review scope: local portfolio prototype source, not a third-party penetration test.

## Controls verified or present

- Passwords are stored as salted `scrypt` hashes, not plaintext.
- Session tokens are generated with a cryptographically secure random generator;
  only SHA-256 token hashes are stored in SQLite.
- Session cookies are `HttpOnly` and `SameSite=Lax`; HTTPS deployments can enable
  `Secure` with `FIELDDATA_SECURE_COOKIES=1`.
- Dataset/project access is scoped by the authenticated user's ID. Cross-user
  ownership checks are covered by automated tests, including report generation.
- Upload names are sanitized and files are stored under generated internal names.
  CSV/XLSX extension, file size, row and column limits are checked. XLSX files
  are ZIP-size checked and streamed from the first worksheet.
- API responses use generic validation errors; browser UI inserts user data with
  `textContent`, reducing reflected/stored HTML injection risk.
- Security headers include `nosniff`, frame denial, same-origin referrer policy,
  a restrictive content policy on the app shell, and a permissions policy.
  Authenticated API responses are marked `no-store`.
- Authenticated state-changing browser requests marked cross-site are rejected.
- Downloaded cleaned data retains spreadsheet-formula safety handling in the
  existing export implementation.

## Remaining production work (do not describe this prototype as production-ready)

1. Replace process-local login throttling with a shared rate limiter; the current
   in-memory counter resets on restart and is not shared across workers.
2. Add a synchronizer CSRF token or strict deployment-aware Origin validation for
   all state-changing browser requests. Current cross-site metadata checks and
   `SameSite=Lax` are defense-in-depth, not a complete CSRF program.
3. Enforce HTTPS at the reverse proxy and set `FIELDDATA_SECURE_COOKIES=1`.
   The default is intentionally suitable for local HTTP development.
4. Use managed PostgreSQL, private object storage, encryption at rest, backups,
   retention/deletion policies, monitoring, and secret management for deployment.
5. Add MFA, account recovery, email verification, audit logging, and operational
   alerting before serving real organisations.
6. Run dependency vulnerability scanning and an independent penetration test
   before handling sensitive or regulated data.
7. Add a deployment-specific trusted-host/proxy configuration and test it behind
   the actual HTTPS reverse proxy before exposing the service publicly.

## Local synthetic-data benchmark

The following measurements were collected in the development container using
synthetic subsets of the included survey sample. They are not service-level
promises and will vary by machine and data shape.

| Rows | Upload + parse | Validation | Analytics | Suggestions | PDF report |
|---:|---:|---:|---:|---:|---:|
| 100 | 0.006 s | 0.041 s | 0.036 s | 0.013 s | 0.045 s |
| 250 | 0.006 s | 0.044 s | 0.042 s | 0.014 s | 0.045 s |
| 500 | 0.007 s | 0.070 s | 0.045 s | 0.015 s | 0.053 s |

Tested operations: upload/parse, validation, analytics, correction suggestion
creation, and PDF report generation. This benchmark does not measure concurrent
users, network transfer, browser rendering, or production storage.
