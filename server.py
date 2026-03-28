"""
n8n Search Proxy — Internet gateway for air-gapped n8n
======================================================
Runs on a Windows 11 PC with internet access.
Exposes REST API that n8n calls to search/crawl the web.

Usage:
  python server.py                     # Start on 0.0.0.0:5100
  python server.py --port 8080         # Custom port
  python server.py --host 127.0.0.1    # Localhost only
  python server.py --workers 4         # Multiple workers

API Endpoints:
  POST /api/search     — Web search (DuckDuckGo)
  POST /api/fetch      — Fetch URL content (raw HTML or text)
  POST /api/crawl      — Crawl URL: extract text, links, metadata
  POST /api/news       — Search news articles
  GET  /api/health     — Health check
"""

import argparse
import re
import sys
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="n8n Search Proxy",
    description="Internet search & crawl gateway for air-gapped n8n",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ─── Request/Response Models ─────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    max_results: int = 10
    region: str = "wt-wt"
    time_range: Optional[str] = None  # d=day, w=week, m=month, y=year

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str

class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    count: int
    timestamp: str

class FetchRequest(BaseModel):
    url: str
    timeout: int = 30
    extract_text: bool = False  # True = return plain text, False = return raw HTML

class FetchResponse(BaseModel):
    url: str
    status_code: int
    content_type: str
    content: str
    content_length: int
    timestamp: str

class CrawlRequest(BaseModel):
    url: str
    timeout: int = 30
    extract_links: bool = True
    extract_images: bool = False
    max_content_length: int = 50000  # Max chars of text content to return

class CrawlResponse(BaseModel):
    url: str
    title: str
    description: str
    text: str
    text_length: int
    links: list[dict]
    images: list[dict]
    metadata: dict
    timestamp: str

class NewsRequest(BaseModel):
    query: str
    max_results: int = 10
    region: str = "wt-wt"
    time_range: Optional[str] = None  # d=day, w=week, m=month, y=year

class NewsResult(BaseModel):
    title: str
    url: str
    snippet: str
    source: str
    date: str

class NewsResponse(BaseModel):
    query: str
    results: list[NewsResult]
    count: int
    timestamp: str

# ─── Helpers ──────────────────────────────────────────────────────────────────

def clean_text(html_content):
    """Extract clean text from HTML."""
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove script, style, nav, footer, header elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # Collapse whitespace
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text


def extract_metadata(soup, url):
    """Extract page metadata from BeautifulSoup."""
    meta = {}

    # Title
    title_tag = soup.find("title")
    meta["title"] = title_tag.get_text(strip=True) if title_tag else ""

    # Meta description
    desc_tag = soup.find("meta", attrs={"name": "description"})
    meta["description"] = desc_tag.get("content", "") if desc_tag else ""

    # Meta keywords
    kw_tag = soup.find("meta", attrs={"name": "keywords"})
    meta["keywords"] = kw_tag.get("content", "") if kw_tag else ""

    # Open Graph
    for og in ["og:title", "og:description", "og:image", "og:type", "og:site_name"]:
        tag = soup.find("meta", attrs={"property": og})
        if tag:
            meta[og.replace(":", "_")] = tag.get("content", "")

    # Canonical URL
    canonical = soup.find("link", attrs={"rel": "canonical"})
    meta["canonical_url"] = canonical.get("href", "") if canonical else ""

    # Language
    html_tag = soup.find("html")
    meta["language"] = html_tag.get("lang", "") if html_tag else ""

    return meta


def extract_links(soup, base_url):
    """Extract all links from page."""
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(base_url, href)
        if full_url not in seen:
            seen.add(full_url)
            text = a.get_text(strip=True)[:200]
            links.append({"url": full_url, "text": text})
    return links


def extract_images(soup, base_url):
    """Extract all images from page."""
    images = []
    seen = set()
    for img in soup.find_all("img", src=True):
        src = img["src"].strip()
        if not src:
            continue
        full_url = urljoin(base_url, src)
        if full_url not in seen:
            seen.add(full_url)
            alt = img.get("alt", "")[:200]
            images.append({"url": full_url, "alt": alt})
    return images


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health_check():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "n8n-search-proxy",
        "version": "1.0.0",
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/search", response_model=SearchResponse)
def web_search(req: SearchRequest):
    """Search the web using DuckDuckGo."""
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(
                keywords=req.query,
                region=req.region,
                timelimit=req.time_range,
                max_results=req.max_results,
            ))

        results = [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("href", ""),
                snippet=r.get("body", ""),
            )
            for r in raw
        ]

        return SearchResponse(
            query=req.query,
            results=results,
            count=len(results),
            timestamp=datetime.now().isoformat(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.post("/api/news", response_model=NewsResponse)
def news_search(req: NewsRequest):
    """Search news articles using DuckDuckGo News."""
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.news(
                keywords=req.query,
                region=req.region,
                timelimit=req.time_range,
                max_results=req.max_results,
            ))

        results = [
            NewsResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("body", ""),
                source=r.get("source", ""),
                date=r.get("date", ""),
            )
            for r in raw
        ]

        return NewsResponse(
            query=req.query,
            results=results,
            count=len(results),
            timestamp=datetime.now().isoformat(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"News search failed: {str(e)}")


@app.post("/api/fetch", response_model=FetchResponse)
def fetch_url(req: FetchRequest):
    """Fetch a URL and return its content."""
    try:
        resp = requests.get(
            req.url,
            headers=HEADERS,
            timeout=req.timeout,
            verify=False,
            allow_redirects=True,
        )

        content_type = resp.headers.get("Content-Type", "")
        content = resp.text

        if req.extract_text and "html" in content_type.lower():
            content = clean_text(content)

        return FetchResponse(
            url=str(resp.url),
            status_code=resp.status_code,
            content_type=content_type,
            content=content,
            content_length=len(content),
            timestamp=datetime.now().isoformat(),
        )
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail=f"Timeout fetching {req.url}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=502, detail=f"Cannot connect to {req.url}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fetch failed: {str(e)}")


@app.post("/api/crawl", response_model=CrawlResponse)
def crawl_url(req: CrawlRequest):
    """Crawl a URL: extract text, links, images, and metadata."""
    try:
        resp = requests.get(
            req.url,
            headers=HEADERS,
            timeout=req.timeout,
            verify=False,
            allow_redirects=True,
        )

        soup = BeautifulSoup(resp.text, "html.parser")

        # Title
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # Description
        desc_tag = soup.find("meta", attrs={"name": "description"})
        description = desc_tag.get("content", "") if desc_tag else ""

        # Clean text
        text = clean_text(resp.text)
        if len(text) > req.max_content_length:
            text = text[:req.max_content_length] + "...[truncated]"

        # Links
        links = extract_links(soup, str(resp.url)) if req.extract_links else []

        # Images
        images = extract_images(soup, str(resp.url)) if req.extract_images else []

        # Metadata
        metadata = extract_metadata(soup, str(resp.url))

        return CrawlResponse(
            url=str(resp.url),
            title=title,
            description=description,
            text=text,
            text_length=len(text),
            links=links,
            images=images,
            metadata=metadata,
            timestamp=datetime.now().isoformat(),
        )
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail=f"Timeout crawling {req.url}")
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=502, detail=f"Cannot connect to {req.url}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Crawl failed: {str(e)}")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="n8n Search Proxy Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5100, help="Port (default: 5100)")
    parser.add_argument("--workers", type=int, default=1, help="Number of workers (default: 1)")
    args = parser.parse_args()

    print(f"n8n Search Proxy starting on http://{args.host}:{args.port}")
    print(f"API docs: http://{args.host}:{args.port}/docs")
    print(f"Health:   http://{args.host}:{args.port}/api/health")
    print()

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        reload=False,
    )
