"""Reverse image search: what the web already calls THIS picture.

The identify pass asks a vision model what it remembers about a photo. For a
print by a known artist that carries no name, that is the wrong question --
the right one is what pages already show this exact image, which is what a
reverse image search answers. Google Lens does that well for nearly every
print that has ever been sold online; SerpApi exposes it as an HTTP call.

Optional, and off without SERPAPI_KEY: the art lookup still runs on the
model's own recognition plus web search, it just has no leads. Never raises
-- a lead is a nice-to-have, and the draft is worth more than one.

The engine fetches the photo itself, so it needs a URL it can reach. That is
the copy in the bucket (main._reverse_image_leads presigns it); this module
only ever sees the URL.
"""
from __future__ import annotations

import httpx

from .. import config
from ..config import log

SERPAPI_URL = "https://serpapi.com/search.json"
# How many matches one lookup hands the model. The first dozen are the ones
# that agree with each other; past that it is poster shops and pins.
MAX_LEADS = 12
_TIMEOUT = 25.0


def enabled() -> bool:
    return config.serpapi_ready()


def reverse_image(url: str) -> list[dict]:
    """Visual matches for the image at `url`, best first, as
    [{"title", "source", "link"}]. [] when off, unreachable, or unmatched."""
    if not enabled() or not url:
        return []
    try:
        resp = httpx.get(SERPAPI_URL, params={
            "engine": "google_lens", "url": url, "hl": "en",
            "api_key": config.SERPAPI_KEY}, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - a lead is optional
        # The type only: httpx puts the request URL in its messages, and the
        # request URL carries the key.
        log.warning("image search: lookup failed (%s)", type(exc).__name__)
        return []
    leads = parse_leads(data)
    log.info("image search: %d match(es)", len(leads))
    return leads


def parse_leads(data) -> list[dict]:
    """The usable part of a Google Lens result. Defensive about shape: the
    knowledge graph is sometimes a list and sometimes one object, and a match
    with no title is no lead at all."""
    if not isinstance(data, dict):
        return []
    leads: list[dict] = []
    graph = data.get("knowledge_graph")
    entries = graph if isinstance(graph, list) else [graph] if isinstance(graph, dict) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        subtitle = str(entry.get("subtitle") or "").strip()
        leads.append({"title": (f"{title} -- {subtitle}" if subtitle else title)[:160],
                      "source": "knowledge graph",
                      "link": str(entry.get("link") or "")[:300]})
    for match in data.get("visual_matches") or []:
        if len(leads) >= MAX_LEADS:
            break
        if not isinstance(match, dict):
            continue
        title = str(match.get("title") or "").strip()
        if not title:
            continue
        leads.append({"title": title[:160],
                      "source": str(match.get("source") or "")[:80],
                      "link": str(match.get("link") or "")[:300]})
    return leads[:MAX_LEADS]
