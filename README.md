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
└── openpecha-api.service   # systemd unit (uvicorn --workers)
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
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
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

Deploy checks out the release and installs its Python dependencies. It installs Neo4j constraints
and indexes followed by triggers, restarts the API, and checks `/__/health`. For releases that
need a data migration or search rebuild, follow the [maintenance runbook](docs/design/cutover.md).

On hosts still using Gunicorn, update the installed unit's `ExecStart` to the Uvicorn command
in [openpecha-api.service](openpecha-api.service), using that environment's virtualenv path.
Run `sudo systemctl daemon-reload` before deploying the revision that removes `gunicorn.conf.py`.

Each GitHub environment sets `EC2_INSTANCE_ID`, `APP_DIR`, `SERVICE_NAME`, the Neo4j, S3 and
OpenSearch values, `ENVIRONMENT`, and optionally `ROOT_PATH` (for example `/api`, once nginx
forwards that prefix). Testing shares the prod OpenSearch domain, so with `ENVIRONMENT=testing`
the API refuses to start unless both index names start with `testing-`.

### Refresh testing (or dev) from prod

Run **Actions → Sync environment from prod** and select `testing` or `dev`. Prod stays online.

1. The workflow snapshots the prod Neo4j disk, then copies three S3 prefixes to the target
   bucket by default. Edition text lives under `base_texts/` and audio under `recordings/`.
   Sync also copies `objects/` so a source awaiting the [storage migration](docs/design/cutover.md#retire-the-objects-prefix) can be restored.
2. It saves the target's API keys, stops the target API, and loads the snapshot's `neo4j`
   database. The target keeps its own Neo4j credentials and service settings.
3. It restores the target's saved API keys, then runs `python -m scripts.migrate --apply --content`
   from the target's deployed code. That command audits the graph, installs constraints, indexes and
   triggers, converts legacy recordings, and verifies edition content and backfills missing metadata.
4. After migration succeeds, it runs `python -m scripts.reindex --writes-paused`, starts the target
   API and checks `/__/health`.

On failure, the workflow's cleanup attempts to restart the target API. If the database swap succeeded
but key restoration failed, the target database can still contain production API keys.

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
