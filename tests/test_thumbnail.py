import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pyvips
from app.clients.media_indexer import MediaIndexer
from app.core.config import Settings
from app.domain.contracts import (
    FAMILIES,
    Concept,
    Concepts,
    MediaScope,
    Source,
    ThumbnailRequest,
)
from app.domain.identity import digest, sha
from app.services.artifact_store import ArtifactStore, IntegrityError
from app.services.generation import generate
from app.services.renderer import PROFILES, render, text_layer
from pydantic import ValidationError


def request():
    project = str(uuid4())
    body = dict(
        schema_version="thumbnail_request_v1",
        workflow_id=str(uuid4()),
        project_id=project,
        script_identity="a" * 64,
        content_locale="pt-BR",
        channel_profile="seis_estrelas_pt_v1",
        video_title="GTA IV e GTA V",
        script={
            "title": "GTA IV e GTA V",
            "blocks": [
                {
                    "order": 0,
                    "type": "intro",
                    "content": "GTA IV and GTA V vehicle physics",
                }
            ],
        },
        media_scope={
            "project_id": project,
            "session_ids": [],
            "static_images": {
                "enabled": True,
                "asset_classes": [],
                "collections": [],
                "source_owners": [],
                "source_classes": [],
            },
        },
        candidate_count=3,
        creative_revision=0,
        strategy_version="thumbnail_director_v2",
        concept_prompt_version="thumbnail_concepts_v2",
        template_set_version="thumbnail_templates_v1",
        renderer_version="libvips_thumbnail_v1",
    )
    return ThumbnailRequest(**body, request_fingerprint=digest(body))


def concepts():
    return Concepts(
        concepts=[
            Concept(
                candidate_id=k,
                template_id=t,
                intent=t,
                headline="O QUE ACONTECEU?" if k != "C" else "",
                primary_asset_query="car physics",
                secondary_asset_query="car crash",
                primary_subject="car",
                secondary_subject="cars",
            )
            for k, t in FAMILIES.items()
        ]
    )


def materials():
    xy = pyvips.Image.xyz(960, 540)
    a = (
        (xy[0] / 960 * 255)
        .bandjoin([xy[1] / 540 * 200, xy[0].new_from_image(60)])
        .cast("uchar")
        .copy(interpretation="srgb")
    )
    b = a.fliphor().rot180().linear([0.4, 1, 1.3], [20, 0, 20]).cast("uchar")
    result = []
    for index, image in enumerate([a, b]):
        data = image.pngsave_buffer()
        result.append(
            (
                data,
                Source(
                    media_id=uuid4(),
                    media_sha256=sha(data),
                    materialized_sha256=sha(data),
                    source_type="image",
                    filename=f"synthetic_{index}.png",
                    search_query="car",
                    rank=1,
                    score=0.9,
                ),
            )
        )
    return result


def test_contract_and_editorial():
    r = request().model_dump(mode="json")
    r["media_scope"]["project_id"] = str(uuid4())
    with pytest.raises(ValidationError):
        ThumbnailRequest.model_validate(r)
    c = concepts().model_dump()
    c["concepts"][0]["headline"] = "one two three four five six"
    with pytest.raises(ValidationError):
        Concepts.model_validate(c)
    c = concepts().model_dump()
    c["concepts"][1]["candidate_id"] = "A"
    with pytest.raises(ValidationError):
        Concepts.model_validate(c)


def test_render_deterministic_and_text_bounds(tmp_path):
    inputs = materials()
    hashes = set()
    for concept in concepts().concepts:
        chosen = inputs if concept.candidate_id == "A" else inputs[:1]
        first, sources, params = render(concept, chosen, "seis_estrelas_pt_v1")
        second, _, _ = render(concept, chosen, "seis_estrelas_pt_v1")
        image = pyvips.Image.new_from_buffer(first, "")
        assert (image.width, image.height, image.bands) == (1280, 720, 3)
        assert first.startswith(b"\x89PNG") and sha(first) == sha(second)
        assert len(params["text_layout"]["lines"]) <= 2
        for source in sources:
            x, y, w, h = source.crop_box
            assert 0 <= x <= 960 - w and 0 <= y <= 540 - h
        hashes.add(sha(first))
        (tmp_path / f"{concept.candidate_id}.png").write_bytes(first)
    assert len(hashes) == 3
    with pytest.raises(ValueError):
        text_layer("W" * 100, PROFILES["seis_estrelas_pt_v1"], 720)
    _, layout = text_layer("GTA IV DID IT BETTER", PROFILES["six_stars_en_v1"], 720)
    assert len(layout["lines"]) <= 2 and layout["font_size"] >= 38


def test_frozen_package_reuses_without_llm_and_rejects_corruption(tmp_path):
    calls = []
    inputs = materials()

    async def director(r, c):
        calls.append(1)
        return concepts()

    class Media:
        def __init__(self, c):
            self.http = AsyncMock()

        async def select(self, scope, query, subject, excluded=()):
            return next(
                (data, source.model_copy(deep=True))
                for data, source in inputs
                if str(source.media_id) not in excluded
            )

    r = request()
    config = Settings()
    store = ArtifactStore(tmp_path)
    first = generate(r, config, store, director, Media)
    second = generate(r, config, store, director, Media)
    assert first == second and calls == [1]
    assert all(c.status == "available" for c in first.candidates)
    with pytest.raises(FileNotFoundError):
        store.artifact("../../etc/passwd")
    path, _ = store.artifact(first.candidates[0].artifact.artifact_id)
    path.write_bytes(b"corrupt")
    with pytest.raises(IntegrityError):
        generate(r, config, store, director, Media)
    assert calls == [1]


def test_video_scope_quality_and_frame_only():
    project, session, media = uuid4(), uuid4(), uuid4()
    paths = []

    def handler(req):
        paths.append(req.url.path)
        if req.url.path.startswith("/media-search"):
            return httpx.Response(
                200,
                json={
                    "project_id": str(project),
                    "results": [
                        {
                            "session_id": str(session),
                            "media_id": str(media),
                            "start_seconds": 1,
                            "end_seconds": 3,
                            "rank": 1,
                            "score": 0.9,
                            "media_filename": "car.mp4",
                        }
                    ],
                },
            )
        if req.url.path.startswith("/sessions"):
            return httpx.Response(
                200, json={"project_id": str(project), "media_id": str(media)}
            )
        if req.url.path.endswith("/video-window-quality"):
            return httpx.Response(
                200,
                json={
                    "status": "clean",
                    "window_start_seconds": 1,
                    "window_end_seconds": 3,
                },
            )
        if req.url.path.endswith("/frame"):
            assert req.url.params["timestamp_seconds"] == "2.0"
            return httpx.Response(200, content=b"frame")
        return httpx.Response(
            200,
            json={
                "id": str(media),
                "project_id": str(project),
                "media_kind": "video",
                "sha256": "a" * 64,
            },
        )

    async def run():
        client = MediaIndexer(Settings())
        await client.http.aclose()
        client.http = httpx.AsyncClient(
            base_url="http://media", transport=httpx.MockTransport(handler)
        )
        scope = MediaScope(
            project_id=project, session_ids=[session], static_images={"enabled": False}
        )
        assert await client.select(scope, "car", "car")
        scope.session_ids = [uuid4()]
        with pytest.raises(ValueError, match="Unauthorized"):
            await client.select(scope, "car", "car")
        await client.http.aclose()

    asyncio.run(run())
    assert not any(p.endswith("/content") for p in paths)


def test_asset_evidence_rejects_wrong_subject_and_game():
    from app.services.asset_evidence import matches

    item = {
        "original_filename": "Boobie_Ike_03.jpg",
        "semantic_preview": "A man talking in a nightclub.",
        "provenance": {"game": "GTA VI"},
    }
    assert not matches(item, "gta vi dog in hallway", "dog")
    item = {
        "semantic_preview": "Cars in a street with traffic.",
        "provenance": {"game": "Grand Theft Auto VI"},
    }
    assert not matches(item, "gta iv street cars", "car")
    assert matches(item, "gta vi street cars", "car")
    item = {"semantic_preview": "Um cachorro na rua.", "provenance": {"game": "GTA 6"}}
    assert matches(item, "gta vi dog", "dog")


def test_director_non_streaming_strict_schema(monkeypatch):
    from app.clients.concept_llm import direct

    seen = []
    original_client = httpx.AsyncClient

    valid = concepts()
    for concept in valid.concepts:
        concept.primary_asset_query = "GTA IV " + concept.primary_asset_query
        concept.secondary_asset_query = "GTA V " + concept.secondary_asset_query

    def handler(req):
        import json

        payload = json.loads(req.content)
        seen.append(payload)
        assert payload["stream"] is False
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert (
            payload["response_format"]["json_schema"]["name"] == "thumbnail_concepts_v2"
        )
        assert (
            request().media_scope.model_dump_json()
            not in payload["messages"][1]["content"]
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": valid.model_dump_json()}}]},
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(handler), **kw),
    )
    assert len(asyncio.run(direct(request(), Settings())).concepts) == 3
    assert len(seen) == 1


@pytest.mark.parametrize(
    "candidate,field,query,accepted",
    [
        ("A", "primary_asset_query", "street cars", False),
        ("A", "secondary_asset_query", "dog in hallway", False),
        ("B", "primary_asset_query", "Niko Bellic motorcycle fall", False),
        ("C", "primary_asset_query", "GTA III street cars", False),
        ("A", "primary_asset_query", "GTA IV Niko Bellic motorcycle fall", True),
        (
            "A",
            "secondary_asset_query",
            "Grand Theft Auto V Michael motorcycle crash",
            True,
        ),
        ("B", "primary_asset_query", "GTA VI police car chase", True),
        ("C", "primary_asset_query", "Grand Theft Auto IV street cars", True),
        ("B", "secondary_asset_query", "street cars", True),
        ("C", "secondary_asset_query", "GTA III street cars", True),
    ],
)
def test_used_queries_require_script_edition(candidate, field, query, accepted):
    from app.clients.concept_llm import validate_query_editions

    req = request()
    req.script.blocks[0].content = "GTA IV, Grand Theft Auto V and GTA VI"
    items = concepts()
    for item in items.concepts:
        item.primary_asset_query = "GTA IV street cars"
        item.secondary_asset_query = "GTA V street cars"
        if item.candidate_id == candidate:
            setattr(item, field, query)
    if accepted:
        validate_query_editions(items, req)
    else:
        with pytest.raises(
            ValueError, match="Every used asset query must name one GTA edition"
        ):
            validate_query_editions(items, req)


def test_non_gta_script_does_not_apply_query_edition_rule():
    from app.clients.concept_llm import validate_query_editions

    req = request()
    req.video_title = req.script.title = "Street cars"
    req.script.blocks[0].content = "Car physics and crashes"
    validate_query_editions(concepts(), req)


def test_edition_validation_uses_existing_bounded_retry(monkeypatch):
    import json
    from app.clients.concept_llm import (
        ConceptGenerationError,
        EDITION_CORRECTION,
        direct,
    )

    seen = []
    original = httpx.AsyncClient

    def handler(req):
        seen.append(json.loads(req.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": concepts().model_dump_json()}}]},
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )
    with pytest.raises(ConceptGenerationError):
        asyncio.run(direct(request(), Settings()))
    assert len(seen) == 2
    assert EDITION_CORRECTION in seen[1]["messages"][-1]["content"]
