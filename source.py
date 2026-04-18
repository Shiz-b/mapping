import argparse
import collections
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup


DATA_DIR = Path("data")
NODES_PATH = DATA_DIR / "nodes.json"
TARGET_PER_TYPE = 2000
USER_AGENT = "KnowledgeGraphBot/1.0"


def reconstruct_abstract(abstract_inverted_index: Optional[Dict[str, List[int]]]) -> str:
    if not abstract_inverted_index:
        return ""
    position_to_word: Dict[int, str] = {}
    for word, positions in abstract_inverted_index.items():
        for pos in positions:
            position_to_word[pos] = word
    ordered_words = [position_to_word[pos] for pos in sorted(position_to_word.keys())]
    return " ".join(ordered_words).strip()


def load_existing_nodes() -> List[Dict[str, Any]]:
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


def write_nodes(nodes: List[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with NODES_PATH.open("w", encoding="utf-8") as f:
        json.dump(nodes, f, ensure_ascii=False, indent=2)


def count_by_type(nodes: List[Dict[str, Any]], node_type: str) -> int:
    return sum(1 for node in nodes if node.get("type") == node_type)


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def dedupe_nodes(nodes: List[Dict[str, Any]], node_type: str) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()
    for node in nodes:
        if node.get("type") != node_type:
            continue
        url_key = normalize_text(node.get("url", "")).lower()
        if not url_key:
            # Keep nodes without URLs instead of collapsing by title.
            deduped.append(node)
            continue
        if url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        deduped.append(node)
    return deduped


def request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int = 4,
    retry_delay_s: float = 1.0,
    **kwargs: Any,
) -> httpx.Response:
    for attempt in range(max_retries + 1):
        response = client.request(method, url, **kwargs)
        if response.status_code < 400:
            return response

        is_retryable = response.status_code in {429, 500, 502, 503, 504}
        if not is_retryable or attempt >= max_retries:
            response.raise_for_status()

        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            wait_s = float(retry_after)
        else:
            wait_s = min(30.0, retry_delay_s * (2 ** attempt))
        time.sleep(wait_s)
    raise RuntimeError(f"Retries exhausted for {method} {url}")


def fetch_research_nodes(client: httpx.Client, needed: int) -> List[Dict[str, Any]]:
    per_page = 200
    max_page = max(1, (needed + per_page - 1) // per_page)
    collected: List[Dict[str, Any]] = []
    cursor = "*"
    pages = 0
    while len(collected) < needed and pages < max_page:
        response = request_with_retries(
            client,
            "GET",
            "https://api.openalex.org/works",
            params={
                "search": "artificial intelligence",
                "filter": "cited_by_count:>50",
                "sort": "cited_by_count:desc",
                "per_page": per_page,
                "cursor": cursor,
            },
            timeout=30.0,
        )
        payload = response.json()
        results = payload.get("results", [])
        for work in results:
            if len(collected) >= needed:
                break
            title = normalize_text(work.get("title", ""))
            abstract = normalize_text(reconstruct_abstract(work.get("abstract_inverted_index")))
            if not title or not abstract:
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
        meta = payload.get("meta", {})
        cursor = meta.get("next_cursor")
        pages += 1
        if not cursor:
            break
    return collected


def fetch_tool_nodes_producthunt(client: httpx.Client, needed: int) -> List[Dict[str, Any]]:
    producthunt_token = os.getenv("PRODUCTHUNT_TOKEN")
    if not producthunt_token:
        print("Error: PRODUCTHUNT_TOKEN is missing. Set it to fetch Product Hunt AI tools.")
        return []

    query = """
    query GetPosts($cursor: String) {
      posts(topic: "artificial-intelligence", order: VOTES, first: 20, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        edges {
          node {
            name
            tagline
            url
            thumbnail {
              url
            }
          }
        }
      }
    }
    """

    collected: List[Dict[str, Any]] = []
    seen_urls = set()
    cursor: Optional[str] = None
    has_next_page = True
    retry_attempts = 0
    while len(collected) < needed and has_next_page:
        response = client.post(
            "https://api.producthunt.com/v2/api/graphql",
            json={"query": query, "variables": {"cursor": cursor}},
            headers={
                "Authorization": f"Bearer {producthunt_token}",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            wait_s = float(retry_after) if retry_after and retry_after.isdigit() else min(30.0, 2.0 ** retry_attempts)
            retry_attempts += 1
            time.sleep(wait_s)
            if retry_attempts > 6:
                print("Warning: Product Hunt rate limited repeatedly, stopping early.")
                break
            continue
        retry_attempts = 0
        response.raise_for_status()
        payload = response.json()

        if payload.get("errors"):
            raise RuntimeError(f"Product Hunt GraphQL error: {payload['errors']}")

        posts = (((payload.get("data") or {}).get("posts")) or {})
        edges = posts.get("edges") or []
        if not edges:
            break

        for edge in edges:
            if len(collected) >= needed:
                break
            node = edge.get("node") or {}
            title = normalize_text(node.get("name", ""))
            description = normalize_text(node.get("tagline", ""))
            url = normalize_text(node.get("url", ""))
            thumbnail = (node.get("thumbnail") or {}).get("url")
            image = normalize_text(thumbnail or "")
            if not title or not description or not url:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            tool_node: Dict[str, Any] = {
                "type": "tool",
                "title": title,
                "description": description,
                "url": url,
            }
            if image:
                tool_node["image"] = image
            collected.append(tool_node)

        page_info = posts.get("pageInfo") or {}
        has_next_page = bool(page_info.get("hasNextPage"))
        cursor = page_info.get("endCursor")
        time.sleep(0.5)

    return collected[:needed]


def fetch_tool_nodes_taaft(client: httpx.Client, needed: int) -> List[Dict[str, Any]]:
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

    def append_tool_page(url: str) -> None:
        if len(collected) >= needed or url in seen_urls:
            return
        try:
            resp = client.get(url, headers=browser_headers, timeout=30.0)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            og_title = soup.find("meta", attrs={"property": "og:title"})
            og_description = soup.find("meta", attrs={"property": "og:description"})
            og_image = soup.find("meta", attrs={"property": "og:image"})

            title = normalize_text(og_title["content"]) if og_title and og_title.get("content") else ""
            description = normalize_text(og_description["content"]) if og_description and og_description.get("content") else ""
            image = normalize_text(og_image["content"]) if og_image and og_image.get("content") else ""

            if not title or not description:
                print(f"  [taaft] skipped {url} — missing OG title or description")
                return

            seen_urls.add(url)
            node: Dict[str, Any] = {"type": "tool", "title": title, "description": description, "url": url}
            if image:
                node["image"] = image
            collected.append(node)
            time.sleep(0.15)
        except httpx.HTTPStatusError as e:
            print(f"  [taaft] HTTP {e.response.status_code} for {url}")
        except Exception as e:
            print(f"  [taaft] error fetching {url}: {e}")

    LINK_SELECTORS = [
        "a.ai_link",           # most specific — their current class name
        "h2 a[href*='/ai/']",  # heading links to tool pages
        "h3 a[href*='/ai/']",
        "a[href*='/ai/']",     # broad fallback
    ]

    for page in range(1, 250):
        if len(collected) >= needed:
            break
        try:
            listing_resp = client.get(
                "https://theresanaiforthat.com/ais/",
                params={"page": page},
                headers=browser_headers,
                timeout=30.0,
            )
            if listing_resp.status_code in (403, 404):
                print(f"  [taaft] listing page returned {listing_resp.status_code}, stopping.")
                break
            if listing_resp.status_code >= 400:
                continue

            listing_soup = BeautifulSoup(listing_resp.text, "html.parser")

            # Try each selector in order; use the first that yields results
            links = []
            for selector in LINK_SELECTORS:
                links = listing_soup.select(selector)
                if links:
                    break

            if not links:
                print(f"  [taaft] no tool links found on page {page}, stopping.")
                break

            before = len(collected)
            for link in links:
                href = normalize_text(link.get("href", ""))
                if not href:
                    continue
                tool_url = urljoin("https://theresanaiforthat.com", href)
                # Deduplicate at the URL level before fetching
                if tool_url in seen_urls:
                    continue
                append_tool_page(tool_url)
                if len(collected) >= needed:
                    break

            if len(collected) == before:
                print(f"  [taaft] no new tools on page {page}, stopping.")
                break

            time.sleep(0.3)
        except Exception as e:
            print(f"  [taaft] listing page {page} error: {e}")
            continue

    # Sitemap fallback (unchanged in logic, but errors are now logged)
    if len(collected) < needed:
        try:
            sitemap_resp = client.get(
                "https://theresanaiforthat.com/sitemap.xml",
                headers=browser_headers,
                timeout=30.0,
            )
            if sitemap_resp.status_code < 400:
                sitemap = BeautifulSoup(sitemap_resp.text, "xml")
                urls = [normalize_text(loc.get_text()) for loc in sitemap.find_all("loc")]
                tool_urls = [u for u in urls if "/ai/" in u]
                print(f"  [taaft] sitemap fallback: found {len(tool_urls)} tool URLs")
                for tool_url in tool_urls:
                    append_tool_page(tool_url)
                    if len(collected) >= needed:
                        break
        except Exception as e:
            print(f"  [taaft] sitemap error: {e}")

    return collected[:needed]


def fetch_tool_nodes(client: httpx.Client, needed: int) -> List[Dict[str, Any]]:
    primary = fetch_tool_nodes_producthunt(client, needed)
    primary = dedupe_nodes(primary, "tool")
    if len(primary) >= needed:
        return primary[:needed]
    remaining = needed - len(primary)
    fallback = fetch_tool_nodes_taaft(client, remaining)
    merged = dedupe_nodes(primary + fallback, "tool")
    print(f"Tool source summary: Product Hunt={len(primary)}, TAAFT fallback={len(fallback)}, deduped={len(merged)}")
    return merged[:needed]


def fetch_technical_nodes_github_api(client: httpx.Client, needed: int) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()
    per_page = 100
    # Split into star buckets to avoid the 1000-result cap per single search query.
    star_buckets = [
        "stars:>=20000",
        "stars:10000..19999",
        "stars:5000..9999",
        "stars:2000..4999",
        "stars:1000..1999",
        "stars:500..999",
        "stars:200..499",
        "stars:100..199",
    ]

    for bucket in star_buckets:
        if len(collected) >= needed:
            break
        for page in range(1, 11):
            if len(collected) >= needed:
                break
            response = client.get(
                "https://api.github.com/search/repositories",
                params={
                    "q": f"topic:machine-learning topic:artificial-intelligence {bucket}",
                    "sort": "stars",
                    "order": "desc",
                    "per_page": per_page,
                    "page": page,
                },
                timeout=30.0,
            )
            if response.status_code in {500, 502, 503, 504, 429}:
                response = request_with_retries(
                    client,
                    "GET",
                    "https://api.github.com/search/repositories",
                    params={
                        "q": f"topic:machine-learning topic:artificial-intelligence {bucket}",
                        "sort": "stars",
                        "order": "desc",
                        "per_page": per_page,
                        "page": page,
                    },
                    timeout=30.0,
                )
            remaining = response.headers.get("X-RateLimit-Remaining")
            if response.status_code in {403, 429} and remaining == "0":
                reset_at = response.headers.get("X-RateLimit-Reset", "unknown")
                print(f"Warning: GitHub API rate limit reached (reset: {reset_at}), stopping API fetch early.")
                return collected[:needed]
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items", [])
            if not items:
                break
            for repo in items:
                if len(collected) >= needed:
                    break
                description = normalize_text(repo.get("description") or "")
                title = normalize_text(repo.get("full_name") or "")
                url = normalize_text(repo.get("html_url") or "")
                if not title or not description or not url or url in seen_urls:
                    continue
                seen_urls.add(url)
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
            if "Authorization" not in client.headers:
                time.sleep(1.0)
    return collected[:needed]


def fetch_technical_nodes_github_topics(client: httpx.Client, needed: int) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()
    page = 1
    while len(collected) < needed and page <= 120:
        response = client.get(
            "https://github.com/topics/artificial-intelligence",
            params={"o": "desc", "s": "stars", "page": page},
            timeout=30.0,
        )
        if response.status_code >= 400:
            break
        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select("article.border")
        if not cards:
            break
        added = 0
        for card in cards:
            if len(collected) >= needed:
                break
            link = card.select_one("h3 a[href]")
            desc_el = card.select_one("p.color-fg-muted")
            if not link:
                continue
            repo_path = normalize_text(link.get("href", ""))
            url = urljoin("https://github.com", repo_path)
            title = normalize_text(link.get_text(" ", strip=True)).replace(" / ", "/")
            description = normalize_text(desc_el.get_text(" ", strip=True) if desc_el else "")
            if not title or not description or not url or url in seen_urls:
                continue
            seen_urls.add(url)
            collected.append(
                {
                    "type": "technical",
                    "title": title,
                    "description": description,
                    "url": url,
                }
            )
            added += 1
        if added == 0:
            break
        page += 1
        time.sleep(0.2)
    return collected[:needed]


def fetch_technical_nodes(client: httpx.Client, needed: int) -> List[Dict[str, Any]]:
    primary = fetch_technical_nodes_github_api(client, needed)
    primary = dedupe_nodes(primary, "technical")
    if len(primary) >= needed:
        return primary[:needed]
    remaining = needed - len(primary)
    fallback = fetch_technical_nodes_github_topics(client, remaining)
    merged = dedupe_nodes(primary + fallback, "technical")
    print(f"Technical source summary: GitHub API={len(primary)}, Topics fallback={len(fallback)}, deduped={len(merged)}")
    return merged[:needed]


def assign_ids_and_raw_text(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_type = {"research": [], "tool": [], "technical": []}
    for node in nodes:
        node_type = node.get("type")
        if node_type in by_type:
            by_type[node_type].append(node)

    output: List[Dict[str, Any]] = []
    for node_type in ("research", "tool", "technical"):
        for idx, node in enumerate(by_type[node_type]):
            clean_node = {
                "id": f"{node_type}_{idx}",
                "type": node_type,
                "title": normalize_text(node.get("title", "")),
                "description": normalize_text(node.get("description", "")),
                "url": normalize_text(node.get("url", "")),
                "raw_text": f"{normalize_text(node.get('title', ''))}. {normalize_text(node.get('description', ''))}",
            }
            image = node.get("image")
            if image:
                clean_node["image"] = image
            output.append(clean_node)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch knowledge graph source nodes.")
    parser.add_argument("--force", action="store_true", help="Re-fetch all node types.")
    parser.add_argument(
        "--type",
        dest="types",
        action="append",
        choices=["research", "tool", "technical"],
        help="Specify one or more node types to fetch. Can be used multiple times.",
    )
    args = parser.parse_args()

    selected_types = set(args.types or ["research", "tool", "technical"])
    force_all_types = args.force and not args.types
    existing_nodes = [] if force_all_types else load_existing_nodes()

    existing_by_type = {
        "research": [n for n in existing_nodes if n.get("type") == "research"],
        "tool": [n for n in existing_nodes if n.get("type") == "tool"],
        "technical": [n for n in existing_nodes if n.get("type") == "technical"],
    }

    headers = {"User-Agent": USER_AGENT}
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    with httpx.Client(headers=headers, follow_redirects=True) as client:
        for source_type, fetcher in (
            ("research", fetch_research_nodes),
            ("tool", fetch_tool_nodes),
            ("technical", fetch_technical_nodes),
        ):
            if source_type not in selected_types:
                continue
            current_count = len(existing_by_type[source_type])
            force_this_type = args.force and (force_all_types or source_type in selected_types)
            if not force_this_type and current_count >= TARGET_PER_TYPE:
                continue
            needed = TARGET_PER_TYPE if force_this_type else max(0, TARGET_PER_TYPE - current_count)
            if needed == 0:
                continue
            try:
                fetched = fetcher(client, needed)
                if force_this_type:
                    existing_by_type[source_type] = dedupe_nodes(fetched, source_type)[:TARGET_PER_TYPE]
                else:
                    existing_by_type[source_type].extend(fetched)
                    existing_by_type[source_type] = dedupe_nodes(existing_by_type[source_type], source_type)[
                        :TARGET_PER_TYPE
                    ]
            except Exception as exc:
                print(f"Warning: failed fetching {source_type}: {exc}")
            finally:
                try:
                    merged_partial = (
                        existing_by_type["research"]
                        + existing_by_type["tool"]
                        + existing_by_type["technical"]
                    )
                    write_nodes(assign_ids_and_raw_text(merged_partial))
                except Exception as write_exc:
                    print(f"Warning: failed writing partial nodes after {source_type}: {write_exc}")

    final_nodes = assign_ids_and_raw_text(
        existing_by_type["research"][:TARGET_PER_TYPE]
        + existing_by_type["tool"][:TARGET_PER_TYPE]
        + existing_by_type["technical"][:TARGET_PER_TYPE]
    )
    write_nodes(final_nodes)

    counts = collections.Counter(node.get("type") for node in final_nodes)
    research_count = counts.get("research", 0)
    tool_count = counts.get("tool", 0)
    technical_count = counts.get("technical", 0)
    total_count = len(final_nodes)
    print(
        f"Wrote {research_count} research, {tool_count} tool, {technical_count} technical nodes (total: {total_count})"
    )


if __name__ == "__main__":
    main()
