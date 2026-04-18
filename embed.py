import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import umap
from sentence_transformers import SentenceTransformer


NODES_PATH = Path("data/nodes.json")


def scale_to_unit_range(values: np.ndarray) -> np.ndarray:
    mins = values.min(axis=0)
    maxs = values.max(axis=0)
    ranges = maxs - mins
    ranges[ranges == 0] = 1.0
    scaled = 2.0 * ((values - mins) / ranges) - 1.0
    return scaled


def ensure_raw_text(node: Dict[str, Any]) -> str:
    raw_text = node.get("raw_text")
    if isinstance(raw_text, str) and raw_text.strip():
        return raw_text.strip()
    title = str(node.get("title", "")).strip()
    description = str(node.get("description", "")).strip()
    computed = f"{title}. {description}".strip()
    node["raw_text"] = computed
    return computed


def main() -> None:
    if not NODES_PATH.exists():
        raise FileNotFoundError("data/nodes.json not found. Run source.py first.")

    with NODES_PATH.open("r", encoding="utf-8") as f:
        nodes: List[Dict[str, Any]] = json.load(f)

    if not isinstance(nodes, list) or not nodes:
        raise ValueError("data/nodes.json must contain a non-empty JSON array.")

    missing_coords = [node for node in nodes if "coords" not in node]
    if not missing_coords:
        print("Embedded 0 nodes, UMAP complete. Coordinates written to data/nodes.json")
        return

    texts = [ensure_raw_text(node) for node in nodes]
    model = SentenceTransformer("all-MiniLM-L6-v2")
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=True)

    reducer = umap.UMAP(
        n_components=3,
        n_neighbors=15,
        min_dist=0.1,
        metric="cosine",
        random_state=42,
    )
    coords_3d = reducer.fit_transform(np.asarray(embeddings))
    coords_3d = scale_to_unit_range(coords_3d)

    for node, coord in zip(nodes, coords_3d):
        node["coords"] = {
            "x": round(float(coord[0]), 5),
            "y": round(float(coord[1]), 5),
            "z": round(float(coord[2]), 5),
        }

    with NODES_PATH.open("w", encoding="utf-8") as f:
        json.dump(nodes, f, ensure_ascii=False, indent=2)

    print(f"Embedded {len(missing_coords)} nodes, UMAP complete. Coordinates written to data/nodes.json")


if __name__ == "__main__":
    main()
