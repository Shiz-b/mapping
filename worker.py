import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import urljoin

import httpx
import numpy as np
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer
import umap

from source import (
    DATA_DIR,
    NODES_PATH,
    USER_AGENT,
    assign_ids_and_raw_text,
    normalize_text,
    reconstruct_abstract,
    request_with_retries,
)

UPDATES_DIR = DATA_DIR / "updates"
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "300"))
UPDATE_MAX_AGE_S = 3600

LINK_SELECTORS = [
    "a.ai_link",
    "h2 a[href*='/ai/']",
    "h3 a[href*='/ai/']",
    "a[href*='/ai/']",
]


def scale_to_unit_range(values: np.ndarray) -> np.ndarray:
    mins = values.min(axis=0)
    maxs = values.max(axis=0)
    ranges = maxs - mins
    ranges[ranges == 0] = 1.0
    scaled = 2.0 * ((values - mins) / ranges) - 1.0
    return scaled


def load_nodes() -> List[Dict[str, Any]]:
    if not NODES_PATH.exists():
        return []
    try:
        with NODES_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def write_json_atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def cleanup_old_updates() -> None:
    if not UPDATES_DIR.exists():
        return
    now = time.time()
    for p in UPDATES_DIR.glob("*.json"):
        try:
            if now - p.stat().st_mtime > UPDATE_MAX_AGE_S:
                p.unlink()
        except OSError:
            pass


def node_to_assign_input(node: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "type": node.get("type"),
        "title": normalize_text(str(node.get("title", ""))),
        "description": normalize_text(str(node.get("description", ""))),
        "url": normalize_text(str(node.get("url", ""))),
    }
    image = node.get("image")
    if image:
        out["image"] = image
    return out


def merge_for_assign(existing: List[Dict[str, Any]], new_research: List[Dict[str, Any]], new_tools: List[Dict[str, Any]], new_technical: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_type: Dict[str, List[Dict[str, Any]]] = {"research": [], "tool": [], "technical": []}
    for n in existing:
        t = n.get("type")
        if t in by_type:
            by_type[t].append(node_to_assign_input(n))
    by_type["research"].extend(new_research)
    by_type["tool"].extend(new_tools)
    by_type["technical"].extend(new_technical)
    return by_type["research"] + by_type["tool"] + by_type["technical"]


def fetch_research_incremental(client: httpx.Client, existing_titles: Set[str]) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    response = request_with_retries(
        client,
        "GET",
        "https://api.openalex.org/works",
        params={
            "search": "artificial intelligence",
            "sort": "publication_date:desc",
            "per_page": 50,
        },
        timeout=30.0,
    )
    payload = response.json()
    for work in payload.get("results", []):
        title = normalize_text(work.get("title", ""))
        if not title:
            continue
        key = title.lower()
        if key in existing_titles:
            continue
        abstract = normalize_text(reconstruct_abstract(work.get("abstract_inverted_index")))
        if not abstract:
            continue
        primary_location = work.get("primary_location") or {}
        url = primary_location.get("landing_page_url") or work.get("doi") or ""
        if not url:
            continue
        collected.append(
            {
                "type": "research",
                "title": title,
                "description": abstract,
                "url": url,
            }
        )
    return collected


def fetch_tools_page1(client: httpx.Client, existing_urls: Set[str], limit: int = 50) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()
    browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://theresanaiforthat.com/",
    }

    listing_resp = client.get(
        "https://theresanaiforthat.com/ais/",
        params={"page": 1},
        headers=browser_headers,
        timeout=30.0,
    )
    listing_resp.raise_for_status()
    listing_soup = BeautifulSoup(listing_resp.text, "html.parser")
    links = []
    for selector in LINK_SELECTORS:
        links = listing_soup.select(selector)
        if links:
            break
    if not links:
        return []

    tool_urls: List[str] = []
    for link in links:
        if len(tool_urls) >= limit:
            break
        href = normalize_text(link.get("href", ""))
        if not href:
            continue
        tool_url = urljoin("https://theresanaiforthat.com", href)
        if tool_url in seen_urls:
            continue
        seen_urls.add(tool_url)
        tool_urls.append(tool_url)

    for tool_url in tool_urls:
        if len(collected) >= limit:
            break
        url_key = tool_url.lower()
        if url_key in existing_urls:
            continue
        try:
            resp = client.get(tool_url, headers=browser_headers, timeout=30.0)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            og_title = soup.find("meta", attrs={"property": "og:title"})
            og_description = soup.find("meta", attrs={"property": "og:description"})
            og_image = soup.find("meta", attrs={"property": "og:image"})
            title = normalize_text(og_title["content"]) if og_title and og_title.get("content") else ""
            description = normalize_text(og_description["content"]) if og_description and og_description.get("content") else ""
            image = normalize_text(og_image["content"]) if og_image and og_image.get("content") else ""
            if not title or not description:
                continue
            node: Dict[str, Any] = {"type": "tool", "title": title, "description": description, "url": tool_url}
            if image:
                node["image"] = image
            collected.append(node)
            time.sleep(0.12)
        except httpx.HTTPStatusError as e:
            print(f"  [worker taaft] HTTP {e.response.status_code} for {tool_url}")
        except Exception as e:
            print(f"  [worker taaft] error fetching {tool_url}: {e}")

    return collected


def fetch_technical_incremental(client: httpx.Client, existing_urls: Set[str]) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    response = request_with_retries(
        client,
        "GET",
        "https://api.github.com/search/repositories",
        params={
            "q": "topic:machine-learning topic:artificial-intelligence",
            "sort": "updated",
            "order": "desc",
            "per_page": 50,
            "page": 1,
        },
        timeout=30.0,
    )
    if response.status_code in {403, 429} and response.headers.get("X-RateLimit-Remaining") == "0":
        reset_at = response.headers.get("X-RateLimit-Reset", "unknown")
        print(f"  [worker github] rate limit reached (reset: {reset_at})")
        return []
    response.raise_for_status()
    payload = response.json()
    for repo in payload.get("items", []):
        description = normalize_text(repo.get("description") or "")
        title = normalize_text(repo.get("full_name") or "")
        url = normalize_text(repo.get("html_url") or "")
        if not title or not description or not url:
            continue
        if url.lower() in existing_urls:
            continue
        owner = repo.get("owner") or {}
        collected.append(
            {
                "type": "technical",
                "title": title,
                "description": description,
                "url": url,
                "image": owner.get("avatar_url"),
            }
        )
    return collected


def tick_once(client: httpx.Client, model: SentenceTransformer) -> None:
    cleanup_old_updates()
    existing = load_nodes()
    existing_ids = {str(n.get("id", "")) for n in existing if n.get("id")}

    existing_titles = {normalize_text(str(n.get("title", ""))).lower() for n in existing if n.get("type") == "research"}
    existing_tool_urls = {normalize_text(str(n.get("url", ""))).lower() for n in existing if n.get("type") == "tool"}
    existing_tech_urls = {normalize_text(str(n.get("url", ""))).lower() for n in existing if n.get("type") == "technical"}

    new_research: List[Dict[str, Any]] = []
    new_tools: List[Dict[str, Any]] = []
    new_technical: List[Dict[str, Any]] = []

    try:
        new_research = fetch_research_incremental(client, existing_titles)
    except Exception as exc:
        print(f"  [worker] research fetch failed: {exc}")

    try:
        new_tools = fetch_tools_page1(client, existing_tool_urls, limit=50)
    except Exception as exc:
        print(f"  [worker] tools fetch failed: {exc}")

    try:
        new_technical = fetch_technical_incremental(client, existing_tech_urls)
    except Exception as exc:
        print(f"  [worker] technical fetch failed: {exc}")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] Tick: +{len(new_research)} research, +{len(new_tools)} tools, +{len(new_technical)} technical")

    if not new_research and not new_tools and not new_technical:
        return

    merged_input = merge_for_assign(existing, new_research, new_tools, new_technical)
    full_nodes = assign_ids_and_raw_text(merged_input)

    texts = [str(n.get("raw_text", "")).strip() for n in full_nodes]
    embeddings = model.encode(texts, batch_size=64, show_progress_bar=False)
    reducer = umap.UMAP(
        n_components=3,
        n_neighbors=15,
        min_dist=0.1,
        metric="cosine",
        random_state=42,
    )
    coords_3d = reducer.fit_transform(np.asarray(embeddings))
    coords_3d = scale_to_unit_range(coords_3d)

    for node, coord in zip(full_nodes, coords_3d):
        node["coords"] = {
            "x": round(float(coord[0]), 5),
            "y": round(float(coord[1]), 5),
            "z": round(float(coord[2]), 5),
        }

    new_with_coords = [n for n in full_nodes if str(n.get("id", "")) not in existing_ids]

    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    if new_with_coords:
        update_path = UPDATES_DIR / f"{int(time.time() * 1000)}.json"
        write_json_atomic(update_path, new_with_coords)

    write_json_atomic(NODES_PATH, full_nodes)


def main() -> None:
    headers = {"User-Agent": USER_AGENT}
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    model = SentenceTransformer("all-MiniLM-L6-v2")
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        while True:
            try:
                tick_once(client, model)
            except Exception as exc:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                print(f"[{ts}] Tick error: {exc}")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
