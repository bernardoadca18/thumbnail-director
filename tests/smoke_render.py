"""Optional CPU smoke: python tests/smoke_render.py OUTPUT_DIRECTORY."""

import json
import sys
from pathlib import Path
from uuid import UUID

import pyvips
from app.domain.contracts import FAMILIES, Concept, Source
from app.domain.identity import sha
from app.services.renderer import render

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
xy = pyvips.Image.xyz(960, 540)
first = (xy[0] / 960 * 255).bandjoin([xy[1] / 540 * 200, xy[0].new_from_image(60)])
first = first.cast("uchar").copy(interpretation="srgb")
second = first.fliphor().linear([0.4, 1, 1.3], [20, 0, 20]).cast("uchar")
materials = []
for i, image in enumerate([first, second]):
    data = image.pngsave_buffer()
    materials.append(
        (
            data,
            Source(
                media_id=UUID(int=i + 1),
                media_sha256=sha(data),
                materialized_sha256=sha(data),
                source_type="image",
                filename=f"synthetic_{i}.png",
                search_query="synthetic",
                rank=1,
                score=1,
            ),
        )
    )
output = []
for key, template in FAMILIES.items():
    concept = Concept(
        candidate_id=key,
        template_id=template,
        intent=template,
        headline="O QUE ACONTECEU?" if key != "C" else "",
        primary_asset_query="synthetic",
        secondary_asset_query="synthetic comparison",
        primary_subject=template,
        secondary_subject="",
    )
    data, sources, params = render(
        concept, materials if key == "A" else materials[:1], "seis_estrelas_pt_v1"
    )
    image = pyvips.Image.new_from_buffer(data, "")
    assert (image.width, image.height, image.bands) == (1280, 720, 3)
    path = root / f"candidate_{key}.png"
    path.write_bytes(data)
    output.append(
        {
            "candidate_id": key,
            "path": str(path),
            "sha256": sha(data),
            "width": image.width,
            "height": image.height,
            "render_parameters": params,
            "sources": [s.model_dump(mode="json") for s in sources],
        }
    )
(root / "smoke_manifest.json").write_text(json.dumps(output, indent=2))
print(
    json.dumps(
        [
            {k: x[k] for k in ("candidate_id", "path", "sha256", "width", "height")}
            for x in output
        ],
        indent=2,
    )
)
