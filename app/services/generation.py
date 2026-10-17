import asyncio

import httpx

from app.clients.concept_llm import direct
from app.clients.media_indexer import MediaIndexer
from app.domain.contracts import PROMPT, STRATEGY, Artifact, Candidate, Package
from app.domain.identity import digest, sha
from app.services.renderer import render


async def build(request, config, director=direct, media_factory=MediaIndexer):
    concepts = await director(request, config)
    media = media_factory(config)
    candidates, artifacts = [], {}
    try:
        for concept in sorted(concepts.concepts, key=lambda c: c.candidate_id):
            sources, parameters, artifact, error = [], {}, None, None
            try:
                primary = await media.select(
                    request.media_scope,
                    concept.primary_asset_query,
                    concept.primary_subject,
                )
                secondary = None
                if primary and concept.candidate_id == "A":
                    secondary = await media.select(
                        request.media_scope,
                        concept.secondary_asset_query,
                        concept.secondary_subject,
                        excluded=[str(primary[1].media_id)],
                    )
                if primary is None or (
                    concept.candidate_id == "A" and secondary is None
                ):
                    error = "no_assets_found"
                else:
                    materials = [primary] + ([secondary] if secondary else [])
                    sources = [s for _, s in materials]
                    try:
                        data, sources, parameters = render(
                            concept, materials, request.channel_profile
                        )
                        artifact = Artifact(
                            artifact_id=f"thumb_{request.request_fingerprint}_{concept.candidate_id}",
                            sha256=sha(data),
                        )
                        artifacts[f"candidate_{concept.candidate_id}.png"] = data
                    except Exception:
                        error = "render_failed"
            except (httpx.HTTPError, ValueError, KeyError):
                error = "source_materialization_failed"
            body = dict(
                candidate_id=concept.candidate_id,
                template_id=concept.template_id,
                headline=concept.headline,
                status="available" if artifact else "unavailable",
                error_category=error,
                artifact=artifact.model_dump() if artifact else None,
                concept=concept.model_dump(),
                sources=[s.model_dump(mode="json") for s in sources],
                render_parameters=parameters,
            )
            candidates.append(Candidate(**body, candidate_identity=digest(body)))
    finally:
        await media.http.aclose()
    package = Package(
        request_fingerprint=request.request_fingerprint,
        result_fingerprint="",
        candidates=candidates,
    )
    package.result_fingerprint = digest(
        package.model_dump(mode="json", exclude={"result_fingerprint"})
    )
    return package, artifacts


def generate(request, config, store, director=direct, media_factory=MediaIndexer):
    with store.lock(request.request_fingerprint):
        existing = store.load(request.request_fingerprint)
        if existing:
            return existing
        if (
            request.strategy_version != STRATEGY
            or request.concept_prompt_version != PROMPT
        ):
            raise ValueError(
                "Generation versions have changed; regenerate with the current strategy"
            )

        async def bounded():
            return await asyncio.wait_for(
                build(request, config, director, media_factory),
                config.generation_timeout_seconds,
            )

        package, artifacts = asyncio.run(bounded())
        return store.publish(package, artifacts)
