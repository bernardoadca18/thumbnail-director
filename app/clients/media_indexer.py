"""Only public scoped search and ID-based materialization. No source URLs/paths."""

import itertools
from uuid import UUID

import httpx

from app.domain.contracts import Source
from app.domain.identity import sha
from app.services.asset_evidence import matches

MAX_BYTES = 25 * 1024 * 1024


class MediaIndexer:
    def __init__(self, config):
        self.http = httpx.AsyncClient(
            base_url=config.media_indexer_api_url, timeout=45, follow_redirects=False
        )

    async def json(self, method, path, **kwargs):
        response = await self.http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    async def content(self, path, params=None):
        async with self.http.stream("GET", path, params=params) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("Materialized image too large")
                chunks.append(chunk)
            return b"".join(chunks)

    async def select(self, scope, query, subject, excluded=()):
        project = str(scope.project_id)
        static = scope.static_images
        candidates = []
        if static.enabled:
            keys = ("asset_class", "collection", "source_owner", "source_class")
            groups = (
                static.asset_classes,
                static.collections,
                static.source_owners,
                static.source_classes,
            )
            combinations = list(itertools.product(*(g or [None] for g in groups)))
            if len(combinations) > 16:
                raise ValueError("Too many image filter combinations")
            for values in combinations:
                filters = {k: v for k, v in zip(keys, values) if v is not None}
                try:
                    result = await self.json(
                        "POST",
                        f"/image-search/projects/{project}",
                        json={
                            "query": query,
                            "limit": 5,
                            "min_score": 0.25,
                            "filters": filters,
                        },
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 409:
                        continue  # No image index; video retrieval stays in the same scope.
                    raise
                if result["project_id"] != project:
                    raise ValueError("Image search scope mismatch")
                for item in result["results"]:
                    provenance = item.get("provenance", {})
                    if item["project_id"] != project or item["source_type"] != "image":
                        raise ValueError("Image scope mismatch")
                    if all(provenance.get(k) == v for k, v in filters.items()):
                        candidates.append(item)
            for item in sorted(candidates, key=lambda x: (-x["score"], x["media_id"])):
                if item["media_id"] in excluded or not matches(item, query, subject):
                    continue
                value = await self.materialize(item, scope, query, "image")
                if value:
                    return value
        body = {
            "query": query,
            "max_results": 5,
            "min_score": 0.25,
            "max_duration_seconds": 30,
        }
        if scope.session_ids:
            body["session_ids"] = [str(s) for s in scope.session_ids]
        try:
            result = await self.json(
                "POST", f"/media-search/projects/{project}", json=body
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:  # no compatible temporal index
                return None
            raise
        if result["project_id"] != project:
            raise ValueError("Video search scope mismatch")
        for item in sorted(
            result["results"],
            key=lambda item: (
                0 if "trailer" in item.get("session_label", "").casefold() else 1,
                item["rank"],
                item["media_id"],
            ),
        ):
            if item["media_id"] in excluded or not matches(item, query, subject):
                continue
            session_id = UUID(item["session_id"])
            if scope.session_ids and session_id not in scope.session_ids:
                raise ValueError("Unauthorized session")
            session = await self.json("GET", f"/sessions/{session_id}")
            if (
                session["project_id"] != project
                or session["media_id"] != item["media_id"]
            ):
                raise ValueError("Session scope mismatch")
            media_id = UUID(item["media_id"])
            start, end = item["start_seconds"], item["end_seconds"]
            if not 0 <= start < end or end - start > 30:
                continue
            quality = await self.json(
                "POST",
                f"/media-assets/{media_id}/video-window-quality",
                json={"start_seconds": start, "end_seconds": end},
            )
            if (
                quality["window_start_seconds"] != start
                or quality["window_end_seconds"] != end
            ):
                raise ValueError("Window quality identity mismatch")
            if quality["status"] != "clean":
                continue
            item["timestamp_seconds"] = (start + end) / 2
            value = await self.materialize(item, scope, query, "video_frame")
            if value:
                return value
        return None

    async def materialize(self, item, scope, query, kind):
        media_id = UUID(item["media_id"])
        asset = await self.json("GET", f"/media-assets/{media_id}")
        if (
            asset["id"] != str(media_id)
            or asset["project_id"] != str(scope.project_id)
            or asset["media_kind"] != ("image" if kind == "image" else "video")
        ):
            raise ValueError("Asset identity/scope mismatch")
        if kind == "image":
            if asset["byte_size"] > MAX_BYTES:
                return None
            data = await self.content(f"/media-assets/{media_id}/content")
            if sha(data) != asset["sha256"] or item["sha256"] != asset["sha256"]:
                raise ValueError("Source checksum mismatch")
        else:
            data = await self.content(
                f"/media-assets/{media_id}/frame",
                {"timestamp_seconds": item["timestamp_seconds"], "width": 1600},
            )
        source = Source(
            media_id=media_id,
            media_sha256=asset["sha256"],
            materialized_sha256=sha(data),
            source_type=kind,
            filename=item.get("original_filename", item.get("media_filename", "")),
            session_id=item.get("session_id"),
            session_label=item.get("session_label"),
            game=item.get("provenance", {}).get("game"),
            timestamp_seconds=item.get("timestamp_seconds"),
            search_query=query,
            rank=item["rank"],
            score=item["score"],
        )
        return data, source
