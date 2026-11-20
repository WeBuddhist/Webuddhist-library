# openpecha-backend

FastAPI-based backend for OpenPecha.

## Project Structure

```
openpecha-backend/
├── main.py                 # FastAPI entry point
├── routers/                # API route handlers
├── database/               # Database layer
├── models/                 # Pydantic models
├── tests/                  # Test suite
├── requirements.txt        # Production dependencies
├── requirements_dev.txt    # Development dependencies
└── gunicorn.conf.py        # Production server config
```

## Setup

1. Clone the repo

2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. Install dependencies

```bash
pip install -r requirements.txt
pip install -r requirements_dev.txt  # For development
```

4. Copy environment template and configure

```bash
cp .env.example .env
# Edit .env with your Neo4j and AWS credentials
```

## Running the API

### Development

From the repository root:

```bash
source .venv/bin/activate
uvicorn main:app --reload
```

Or without activating the virtual environment:

```bash
./.venv/bin/uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **Liveness**: http://localhost:8000/__/health

The app initializes Neo4j and S3 when `NEO4J_URI` is set in `.env`. To run only
docs/health locally without database startup, temporarily unset `NEO4J_URI`:

```bash
export NEO4J_URI=""
./.venv/bin/uvicorn main:app --reload
```

### Production

```bash
gunicorn main:app -c gunicorn.conf.py
```

## Running Tests

Tests spin up a disposable Neo4j container automatically via
[testcontainers](https://testcontainers-python.readthedocs.io/). No manual Neo4j
installation or env vars are needed.

### Prerequisites

1. Install dev dependencies:
   ```bash
   pip install -r requirements_dev.txt
   ```

2. You need a running **Docker-compatible runtime** (Docker Desktop, Colima,
   Podman, etc.)

   - **Docker Desktop** works out of the box
   - **Other runtimes** — set `DOCKER_HOST`:
     ```bash
     export DOCKER_HOST=unix://$HOME/.colima/default/docker.sock
     ```

### Run tests

```bash
pytest tests/
```

Or with verbose output:

```bash
python -m pytest tests/ -v
```

The first run will pull the `neo4j:2026.07.1` image (~500 MB), which is cached for
subsequent runs.

## Deployment

GitHub Actions (`.github/workflows/deploy.yml`) deploys to three environments:

| Environment | Deploys when | API key | Data |
|---|---|---|---|
| `dev` | a push to `dev` | needed | test data, reset from prod by hand |
| `testing` | a merge (push) to `main` | needed | copy of prod, refreshed each release cycle |
| `prod` | a tag `vX.Y.Z` on a `main` commit | needed | real |

To release: merge `dev` into `main`, test on testing, then tag the tested commit:

```bash
git tag v2.16.0 <tested main commit> && git push origin v2.16.0
```

The tag must be on `main`, or the deploy stops. To roll back, run the deploy workflow on the
previous tag. `/__/health` and Swagger show the deployed version (`APP_VERSION`).

Each GitHub environment sets `EC2_INSTANCE_ID`, `APP_DIR`, `SERVICE_NAME`, the Neo4j, S3 and
OpenSearch values, `ENVIRONMENT`, and optionally `ROOT_PATH` (for example `/api`, once nginx
forwards that prefix). Testing shares the prod OpenSearch domain, so with `ENVIRONMENT=testing`
the API refuses to start unless both index names start with `testing-`.

### Refresh testing (or dev) from prod

Run **Actions → Sync environment from prod** (`.github/workflows/sync-from-prod.yml`) and pick
the target. It snapshots the prod Neo4j disk (prod keeps running), loads that copy into the
target Neo4j, copies the prod S3 files, installs constraints and triggers, and rebuilds the
target's search indexes. The target's own Neo4j password and settings stay. Pending data
migrations (`python -m scripts.migrate --apply`) are not run; rehearse them on testing after a
sync.

### Environment Configuration

- **Neo4j**: Configured via `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` in
  `.env`
- **AWS S3**: Configured via `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_S3_BUCKET` in `.env`
- **OpenSearch content search**: Configured via `OPENSEARCH_ENDPOINT`
- **OpenSearch catalog search** (person/text name and title search): Configured via
  `OPENSEARCH_ENDPOINT` and `OPENSEARCH_CATALOG_INDEX`. Requires the
  `analysis-tibetan` and `analysis-bdrc` OpenSearch plugins on the cluster; for
  local tests set `OPENSEARCH_TIBETAN_PLUGIN_ZIP` and `OPENSEARCH_BDRC_PLUGIN_ZIP`.
  See `docs/api-documentation/catalog-search-api.md`.

## Documentation

See the [API guide](docs/api-documentation/README.md) for route documentation and examples.
The running API exposes Swagger UI at `/docs`, ReDoc at `/redoc`, and its OpenAPI schema at `/openapi.json`.
