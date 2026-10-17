"""Conservative grounding against existing Indexer descriptions, not visual inference."""

import json
import re
import unicodedata

# The existing corpus contains Portuguese and English descriptions. These few visual
# nouns bridge its common spellings; absence of evidence remains an unavailable option.
ALIASES = {
    "cars": "car",
    "vehicle": "car",
    "vehicles": "car",
    "carro": "car",
    "carros": "car",
    "veiculo": "car",
    "veiculos": "car",
    "cop": "police",
    "cops": "police",
    "officer": "police",
    "officers": "police",
    "policia": "police",
    "policial": "police",
    "policiais": "police",
    "dogs": "dog",
    "cao": "dog",
    "caes": "dog",
    "cachorro": "dog",
    "cachorros": "dog",
    "pedestrians": "pedestrian",
    "pedestre": "pedestrian",
    "pedestres": "pedestrian",
    "streets": "street",
    "rua": "street",
    "ruas": "street",
    "fight": "fight",
    "fighting": "fight",
    "briga": "fight",
    "traffic": "traffic",
    "transito": "traffic",
    "gun": "gun",
    "guns": "gun",
    "arma": "gun",
    "armas": "gun",
    "pistol": "gun",
}
IGNORED = {
    "a",
    "an",
    "the",
    "of",
    "in",
    "on",
    "with",
    "and",
    "um",
    "uma",
    "o",
    "de",
    "da",
    "do",
    "scene",
    "screenshot",
    "frame",
    "gameplay",
    "image",
}


def normalized(text):
    value = "".join(
        c
        for c in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(c)
    )
    value = value.replace("grand theft auto", "gta")
    value = re.sub(
        r"\bgta\s*(iii|iv|vi|v|3|4|5|6)\b",
        lambda m: "gta" + {"iii": "3", "iv": "4", "v": "5", "vi": "6"}.get(m[1], m[1]),
        value,
    )
    return value


def tokens(text):
    return {
        ALIASES.get(word, word)
        for word in re.findall(r"[a-z0-9]+", normalized(text))
        if word not in IGNORED
    }


def matches(item, query, subject):
    # Use only descriptive fields, never the search query/score as evidence of contents.
    description = " ".join(
        str(item.get(key) or "")
        for key in (
            "semantic_preview",
            "original_filename",
            "media_filename",
            "session_label",
            "transcript_preview",
        )
    )
    provenance = item.get("provenance") or {}
    description += " " + json.dumps(
        {
            key: provenance.get(key)
            for key in ("game", "declared_metadata", "semantic_support")
        },
        ensure_ascii=False,
    )
    evidence = tokens(description)
    required = tokens(subject)
    if not required or not required.issubset(evidence):
        return False
    # Distinct numbered games must never be silently substituted by semantic similarity.
    editions = set(re.findall(r"\bgta[3456]\b", normalized(query)))
    return editions.issubset(evidence)
