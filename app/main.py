import pyvips
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.clients.concept_llm import ConceptGenerationError
from app.core.config import settings
from app.domain.contracts import Package, ThumbnailRequest
from app.services.artifact_store import ArtifactStore, IntegrityError
from app.services.generation import generate
from app.services.renderer import PROFILES

app = FastAPI(title="NeoVids Thumbnail Director", version="1.0.0")


@app.get("/health/live")
def live():
    return {"status": "live"}


@app.get("/health/ready")
def ready():
    try:
        store = ArtifactStore(settings.artifact_root)
        with (store.root / ".ready").open("a"):
            pass
        pyvips.Image.text("Aa", font=PROFILES["seis_estrelas_pt_v1"]["text"]["font"])
    except Exception:
        raise HTTPException(503, "Renderer or artifact store unavailable") from None
    return {"status": "ready"}


@app.post("/v1/thumbnails/generate", response_model=Package)
def generation(request: ThumbnailRequest):
    try:
        return generate(request, settings, ArtifactStore(settings.artifact_root))
    except ConceptGenerationError as exc:
        raise HTTPException(
            503, {"category": "concept_generation_failed", "message": str(exc)}
        ) from None
    except IntegrityError:
        raise HTTPException(
            409,
            {
                "category": "artifact_integrity_failed",
                "message": "Frozen artifact failed integrity validation",
            },
        ) from None
    except ValueError:
        raise HTTPException(
            422,
            {
                "category": "contract_error",
                "message": "Regenerate using current strategy versions",
            },
        ) from None
    except Exception:
        raise HTTPException(
            503,
            {
                "category": "concept_generation_failed",
                "message": "Thumbnail generation unavailable; retry the same package",
            },
        ) from None


@app.get("/v1/artifacts/{artifact_id}")
def artifact(artifact_id: str):
    try:
        path, checksum = ArtifactStore(settings.artifact_root).artifact(artifact_id)
    except FileNotFoundError:
        raise HTTPException(404, "Unknown artifact") from None
    except IntegrityError:
        raise HTTPException(409, "Artifact integrity failure") from None
    return FileResponse(
        path,
        media_type="image/png",
        headers={"ETag": f'"{checksum}"', "X-Content-SHA256": checksum},
    )
