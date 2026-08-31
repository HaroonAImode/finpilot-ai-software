# Auth Service (port 8001)

Issues and verifies identity for every other FinPilot service. Implements
section 5.2 of the architecture report.

It stores **credentials and sessions only**. A company's profile (NTN, address,
tax settings) belongs to the Settings service, keyed by the same `company_id`
this service mints at signup.

---

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/auth/signup` | Create a company and its first admin user |
| `POST` | `/api/v1/auth/login` | Exchange email + password for tokens |
| `POST` | `/api/v1/auth/refresh` | Rotate the refresh token, get a new access token |
| `POST` | `/api/v1/auth/logout` | Revoke the current refresh token |
| `GET`  | `/api/v1/auth/me` | Who the bearer token belongs to |
| `GET`  | `/health` | Liveness |

`signup`, `login` and `refresh` return the access token in the body and set the
refresh token as an **httpOnly cookie** — page JavaScript, and therefore any XSS
on the page, cannot read it.

---

## Security decisions worth knowing

**Passwords use Argon2id.** Slow and memory-hard on purpose, so a stolen
database is expensive to crack. Hashes are re-hashed transparently on login if
the parameters have since been strengthened.

**Refresh tokens are stored as SHA-256 digests, never raw.** They are 256-bit
random values we generated, so guessing is hopeless and a slow hash would buy
nothing — but storing only the digest means a database leak does not hand out
working sessions.

**Refresh tokens rotate on every use.** The old row is revoked and a new one
issued. If a revoked token is presented — the signature of a stolen token being
replayed after the real user already refreshed — **every session for that user is
revoked** and they must log in again.

**Login failures are indistinguishable.** An unknown email and a wrong password
return the same message, and an unknown email still runs a password hash so the
two take similar time. Otherwise anyone could enumerate registered addresses
with a stopwatch.

**Rate limiting** (Redis) caps login attempts per IP per minute. If Redis is
down the limiter **fails open** and logs loudly: locking every user out is worse
than a briefly unthrottled endpoint, and passwords are still Argon2 hashed.

**A short `JWT_SECRET_KEY` refuses to boot.** A weak signing key makes every
token in the system forgeable, so it fails at startup rather than at the first
login.

---

## Running it

With the rest of the stack:

```bash
cd backend/infra
docker compose up --build -d auth
curl http://localhost:8001/health      # → {"status":"ok"}
```

First create the env file and a real signing key:

```bash
cd backend/services/auth
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
# put that value in JWT_SECRET_KEY
```

⚠️ **Every service that verifies a token needs the same `JWT_SECRET_KEY`.** If
they disagree, tokens minted here will be rejected everywhere else.

`.env` is gitignored — along with every `.env.*` variant — and must never be
committed.

### Locally, without Docker

```bash
python -m venv .venv
.venv/Scripts/pip install -e ../../libs/shared
.venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/pytest tests/ -v
```

The shared library is a path dependency and must be installed first.

---

## Try it

```bash
curl -X POST http://localhost:8001/api/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"company_name":"Khan Enterprises","full_name":"Ayesha Khan",
       "email":"ayesha@khan.pk","password":"a-long-enough-passphrase"}'
```

Returns an access token plus the created user, and sets the refresh cookie.

---

## Structure

Follows section 7 of the architecture report:

```
app/
├── main.py                 FastAPI app, CORS, shared error shape
├── api/v1/auth.py          HTTP layer — cookies, status codes
├── services/auth_service.py  business rules — the security decisions
├── repositories/           database access only, no rules
├── models/                 Company, User, RefreshToken
├── schemas/                request/response validation
└── core/                   config, database, security, rate limiting
```

JWT creation and verification live in `backend/libs/shared` rather than here, so
the Gateway and every other service verify tokens with exactly the same code.
