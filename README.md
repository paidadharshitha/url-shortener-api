# URL Shortener API

[![CI](https://github.com/paidadharshitha/url-shortener-api/actions/workflows/ci.yml/badge.svg)](https://github.com/paidadharshitha/url-shortener-api/actions/workflows/ci.yml)

A Python API for creating short links, redirecting visitors, and tracking clicks. Built with FastAPI and SQLite, with persistent storage and automated tests for the API's core rules.

## Quick start

Requires Python 3.11 or newer with SQLite 3.35 or newer.

```bash
git clone https://github.com/paidadharshitha/url-shortener-api.git
cd url-shortener-api
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload --env-file .env
```

On Windows, activate the environment with `.venv\Scripts\Activate.ps1` in PowerShell.

The API runs at <http://localhost:8000>. Interactive API documentation is available at <http://localhost:8000/docs>; the OpenAPI schema is at <http://localhost:8000/openapi.json>.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_PATH` | `data/links.sqlite3` | SQLite database file. Keep this file on persistent storage. |
| `BASE_URL` | `http://localhost:8000` | Public base URL used to construct returned short URLs. An optional path prefix is supported. |

Configuration is read when the application starts. Export environment variables directly, or use Uvicorn's `--env-file .env` option as above. The application does not load `.env` files itself. Set `BASE_URL` to the public HTTPS URL when deploying behind a reverse proxy; if it includes a path prefix, configure the proxy to route that prefix to this application.

## Endpoints

| Method | Path | Success | Description |
| --- | --- | --- | --- |
| `POST` | `/links` | `201 Created` | Create a short link. |
| `GET` | `/{code}` | `302 Found` | Redirect to the destination and record one click. |
| `GET` | `/links/{code}/stats` | `200 OK` | Read a link's metadata and click statistics. |

### Create a link

Only `url` is required. Omit `alias` to generate a short code, and omit `expires_at` for a link that does not expire.

```bash
curl -i -X POST http://localhost:8000/links \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/articles/introducing-our-product?source=share"}'
```

To use a custom alias and expiry:

```bash
curl -i -X POST http://localhost:8000/links \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://example.com/products/launch",
    "alias": "launch",
    "expires_at": "2099-12-31T23:59:59Z"
  }'
```

Example `201 Created` response; the creation timestamp reflects the request time:

```json
{
  "code": "launch",
  "url": "https://example.com/products/launch",
  "short_url": "http://localhost:8000/launch",
  "created_at": "2026-10-26T10:30:00Z",
  "expires_at": "2099-12-31T23:59:59Z"
}
```

Timestamps use ISO 8601 and are normalized to UTC. `expires_at` is `null` when omitted.

Validation rules:

- Destinations must be valid absolute HTTP or HTTPS URLs of at most 2,083 characters, without embedded credentials or control characters. URLs are normalized by the validator, for example by adding a trailing slash to a bare origin.
- Aliases contain 3–64 ASCII letters, digits, underscores, or hyphens, and must start with a letter or digit.
- Aliases are case sensitive: `Launch` and `launch` are different codes. Route names such as `links`, `docs`, `redoc`, and `openapi.json` are reserved, ignoring case.
- An alias already in use returns `409 Conflict`, including when its existing link has expired.
- Expiry must include a timezone and be in the future. A link expires when the current time reaches its expiry timestamp.
- Unrecognized request fields are rejected to catch misspellings.

### Follow a link

Inspect the redirect without following it:

```bash
curl -i http://localhost:8000/launch
```

The response has status `302`, a `Location` header pointing to the destination, and `Cache-Control: no-store`. Add `-L` to follow the redirect.

Every successful redirect request records one click, including repeat visits and retries. Unknown and expired links do not record clicks. Click totals count requests, not unique people; bots and link preview fetches may count too.

### Read statistics

```bash
curl http://localhost:8000/links/launch/stats
```

Example response after one redirect:

```json
{
  "code": "launch",
  "url": "https://example.com/products/launch",
  "created_at": "2026-10-26T10:30:00Z",
  "expires_at": "2099-12-31T23:59:59Z",
  "total_clicks": 1,
  "last_clicked_at": "2026-10-26T10:31:00Z",
  "is_expired": false
}
```

The response fields are:

| Field | Meaning |
| --- | --- |
| `code`, `url` | Short code and destination URL. |
| `created_at`, `expires_at` | Creation time and optional expiry time. |
| `total_clicks` | Number of successful redirect requests. |
| `last_clicked_at` | Most recent successful redirect time, or `null` before the first click. |
| `is_expired` | Whether the link has reached its expiry time. |

Reading statistics does not count as a click. Expired links remain available through the statistics endpoint.

### Errors

Errors use JSON responses with a `detail` field. Validation errors identify the invalid request field.

| Status | Meaning |
| --- | --- |
| `404 Not Found` | The requested code does not exist. |
| `409 Conflict` | The requested alias is already taken. |
| `410 Gone` | The link has expired; applies to redirects. |
| `422 Unprocessable Entity` | Invalid URL, alias, expiry, or request body. |
| `503 Service Unavailable` | A generated code could not be allocated after repeated collisions; retry the request. |

## Tests and code checks

Install the development dependencies from the quick start, then run:

```bash
pytest
pytest --cov=app --cov-report=term-missing
ruff check .
ruff format --check .
```

Tests use isolated temporary databases and FastAPI's [TestClient testing pattern](https://fastapi.tiangolo.com/tutorial/testing/). They exercise creation, URL and alias validation, duplicate aliases, redirects, expiry, missing codes, and click statistics. GitHub Actions runs the checks on Python 3.11, 3.12, and 3.13.

The current suite contains 82 test cases and reports 99% coverage locally. A separate CI job builds the Docker image and checks creation, redirects, click counts, and persistence after a container restart.

## Run with Docker

```bash
docker build -t url-shortener .
docker run --rm --name url-shortener \
  -p 8000:8000 \
  -e BASE_URL=http://localhost:8000 \
  -v url-shortener-data:/data \
  url-shortener
```

The container runs as an unprivileged user and stores the database in `/data/links.sqlite3`. The named volume preserves links after the container stops. If using a host bind mount instead, ensure UID `10001` can write to that directory.

## Design and scope

The application separates request validation, HTTP routing, and persistence:

```text
app/
├── main.py             # Application factory, routes, and HTTP error mapping
├── schemas.py          # Request validation and response schemas
├── service.py          # Link creation, generated-code retries, and expiry policy
├── repository.py       # SQLite schema, queries, and atomic click updates
├── domain.py           # Link model and domain errors
└── config.py           # Environment configuration
tests/
├── conftest.py         # Temporary database, test client, and controlled clock
├── test_links.py       # Redirects, statistics, expiry, persistence, and concurrency
├── test_validation.py  # Request validation and alias rules
└── test_config.py      # Configuration behavior
```

Generated codes use eight cryptographically random letters and digits; collisions are retried. SQLite keeps setup simple, and database constraints enforce code uniqueness. [Write-ahead logging](https://www.sqlite.org/wal.html) allows reads alongside writes. Click updates are atomic so concurrent requests do not overwrite each other's counts. Expiry is checked when a redirect is requested; no background cleanup job is needed.

SQLite is a good fit for a service on one machine with persistent storage. For a deployment spread across multiple machines or sustained high write traffic, use a shared database such as Postgres and introduce schema migrations.

Link creation and statistics are public in this implementation. A production service should define its authentication and statistics visibility policies, add rate limits and abuse prevention, and arrange database backups. The API validates URL syntax; it does not verify whether a destination is reachable or trustworthy.
