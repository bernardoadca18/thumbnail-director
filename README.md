# NeoVids Thumbnail Director

Small synchronous FastAPI service for textual art direction, scoped Media Indexer retrieval,
and deterministic libvips composition. No image generation model, database, queue, video
pipeline phase, automatic winner or publishing. Orchestrator owns durable execution and
human selection; the service owns immutable image artifacts.

## Run

The integrated `../scripts/neovids up --build` includes this service, its persistent
`thumbnail-artifacts` volume and the shared `neovids` network. Port: **8014** on localhost;
internal address: `http://thumbnail-director:8000`.

For a targeted service update, with the existing network and provider key configured:

```sh
docker compose build thumbnail-director
docker compose up -d --no-deps thumbnail-director
```

Configuration follows the existing Ideas Hub gateway convention:
`NINEROUTER_BASE_URL` (default `http://9router:20128`), `NINEROUTER_API_KEY`,
`THUMBNAIL_LLM_MODEL` (default `neovids-standard`). Requests explicitly disable streaming
and require strict JSON Schema output. There is at most one correction retry, with
Pydantic validation. The integrated CLI reuses the existing gateway key without printing it.
`MEDIA_INDEXER_API_URL`, `ARTIFACT_ROOT` (default `/data/thumbnails`),
`GENERATION_TIMEOUT_SECONDS` (300) and `THUMBNAIL_LLM_TIMEOUT_SECONDS` (90) are configurable.
The Docker runtime uses two libvips threads and has a 1 GiB memory limit.

## HTTP contracts

- `GET /health/live`, `GET /health/ready`: local runtime/storage/font checks only.
- `POST /v1/thumbnails/generate`: `thumbnail_request_v1` → `thumbnail_package_v1`.
- `GET /v1/artifacts/{artifact_id}`: known manifest artifacts only; PNG, SHA header and ETag.

The versioned Pydantic contract is in `app/domain/contracts.py`, mirrored at the
Orchestrator boundary in `backend/app/schemas/thumbnail.py`. Change both together.
Requests contain a frozen script, workflow and script identities, locale, versioned
channel profile, authorized media scope, revision and strategy/prompt/template/renderer
versions. The Orchestrator computes SHA-256 over canonical UTF-8 JSON (sorted keys,
compact separators, no NaN), excluding `request_fingerprint`. All defaulted fields
participate. The service verifies the identity before doing work.

Each result has exactly three slots: `A/split_compare_v1`, `B/hero_focus_v1`,
`C/curiosity_focus_v1`. Missing/invalid sources or rendering failures produce an explicit
`unavailable` slot, never random media. Available slots contain 1280×720 sRGB PNG artifacts,
full concepts, source SHA, materialized SHA, query/rank/score, frame timestamp if applicable,
actual crop coordinates, transforms, font layout, profile and renderer/libvips versions.
Result and candidate fingerprints cover their frozen content, excluding their own hash.

PT-BR and EN-US use the same visual family. Headlines have at most five words/two lines;
actual Pango glyph measurement selects a font size from 88 down to 38, without breaking
words or overflowing safe margins. Attention crop preserves proportions. Comparison uses
a divider, hero uses a dominant frame and right-hand text, curiosity uses a tighter crop
with optional text. All photographic pixels come from real Media Indexer assets.

## Media scope and materialization

Only public project-scoped image/video search APIs are used. Explicit session restrictions
and static image filters are preserved and checked. Descriptive metadata must support the concrete subject, and named GTA editions
must match; embedding score alone is insufficient. This conservative check may mark a
visually suitable but poorly described asset unavailable. There is no global fallback. Static
images are tried first; their downloaded bytes must match the registered source SHA.
Videos are never downloaded: window quality must be `clean`, then the midpoint is fetched
through `GET /media-assets/{media_id}/frame?timestamp_seconds=…&width=1600`.
This small Media Indexer endpoint resolves managed storage internally and performs a
15-second bounded FFmpeg seek for one JPEG, with width/height capped at 1920. No new jobs.

## Storage and retries

Artifacts live at `/data/thumbnails/<request_fingerprint>/candidate_{A,B,C}.png` alongside
`manifest.json`. A process-safe advisory file lock serializes a fingerprint. Temporary
files and directory are fsynced, then the complete directory is atomically renamed.
A valid existing manifest returns immediately without calling the model. SHA/identity
validation failure is an error, never permission to overwrite. Public APIs expose IDs,
not paths. Failed requests without a published manifest can retry the same fingerprint.
Bump the appropriate version when changing profile, prompt or renderer behavior.
Current strategy/prompt are `thumbnail_director_v2` / `thumbnail_concepts_v2`: the initial
real smoke exposed semantic near-matches (wrong subject/game), so v2 adds descriptive
evidence checks and rejects asset/session IDs used as creative subjects. Published v1
packages remain readable and frozen; regeneration uses v2.

## Operator integration

Migration `021_thumbnail_packages.py` creates the Orchestrator's
`workflow_thumbnail_packages` table (request/result JSONB, revision, lifecycle, selection,
errors and timestamps). The existing Celery worker owns generation and bounded retry;
pending/running rows recover on worker startup. Duplicate terminal deliveries are ignored;
only explicit bounded retry or an Operator retry may retry a failed package. The immutable script source is
`WorkflowExecucao.roteiro_snapshot`, never a fresh Ideas Hub lookup.

Under `/operator/workflows/{workflow_id}/thumbnails`:

- `POST /generate`: create/reuse current package; retry a failed request.
- `POST /regenerate`: next revision/new package; preserve prior results and selection history.
- `GET`: current package, historical metadata, selected candidate/artifact identity.
- `POST /select`: `{"candidate_id":"A","package_id":"…"}`; package ID is optional for API
  compatibility and sent by the Console to reject stale choices.
- `GET /{candidate_id}/content`: validated artifact proxy, optional current `package_id`.

Client configuration: `THUMBNAIL_DIRECTOR_BASE_URL` and
`THUMBNAIL_DIRECTOR_TIMEOUT_SECONDS` (330). The Console's **Thumbnails** tab displays
status, revision, A/B/C cards, sources, selection and regeneration. No choice is automatic.

## Focused validation

```sh
VIPS_CONCURRENCY=2 PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_thumbnail.py
PYTHONPATH=. .venv/bin/python tests/smoke_render.py data/validation
```

The smoke uses only two synthetic images. Its output is not a product-quality sample.
With installed libvips, install `requirements.txt`; the local test environment may use
`pyvips-binary`, while the Docker image uses Debian libvips and bundled DejaVu fonts.
Use the Docker runtime for reproducible production checks.

Orchestrator: focused `tests/test_thumbnails.py`, plus optional
`PYTHONPATH=. .venv/bin/python tests/smoke_thumbnail_persistence.py` against an existing
PostgreSQL database with migration 021. The latter uses synthetic rows and an outer
transaction rollback; it never dispatches workers. Media Indexer:
`tests/test_thumbnail_frame.py`. Frontend: existing `npm run build`.
