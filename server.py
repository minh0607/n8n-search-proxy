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
  POST /api/search     — Web search (Google)
  POST /api/fetch      — Fetch URL content (raw HTML or text)
  POST /api/crawl      — Crawl URL: extract text, links, metadata
  POST /api/news       — Search news articles (Google News)
  GET  /api/health     — Health check
"""

import argparse
import re
import sys
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, urlparse, quote_plus, parse_qs, unquote

import requests
import urllib3
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="n8n Search Proxy",
    description="Internet search & crawl gateway for air-gapped n8n",
    version="1.1.0",
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
    language: str = "en"       # en, vi, ja, etc.
    country: str = ""          # countryVN, countryUS, etc. (empty = global)
    time_range: Optional[str] = None  # d=day, w=week, m=month, y=year

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str

class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    count: int
    engine: str
    timestamp: str

class FetchRequest(BaseModel):
    url: str
    timeout: int = 30
    extract_text: bool = False

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
    max_content_length: int = 50000

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
    language: str = "en"
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
    engine: str
    timestamp: str

# ─── Google Search Scraper ────────────────────────────────────────────────────

GOOGLE_TIME_MAP = {
    "d": "qdr:d",    # past day
    "w": "qdr:w",    # past week
    "m": "qdr:m",    # past month
    "y": "qdr:y",    # past year
}


def google_search(query, max_results=10, language="en", country="", time_range=None):
    """Scrape Google search results."""
    results = []
    seen_urls = set()
    start = 0

    while len(results) < max_results:
        params = {
            "q": query,
            "num": min(max_results - len(results) + 2, 20),  # request a few extra
            "start": start,
            "hl": language,
        }
        if country:
            params["cr"] = country
        if time_range and time_range in GOOGLE_TIME_MAP:
            params["tbs"] = GOOGLE_TIME_MAP[time_range]

        resp = requests.get(
            "https://www.google.com/search",
            params=params,
            headers=HEADERS,
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Find result divs
        found_any = False
        for div in soup.find_all("div", class_="g"):
            if len(results) >= max_results:
                break

            # Extract link
            a_tag = div.find("a", href=True)
            if not a_tag:
                continue
            url = a_tag["href"]
            if not url.startswith("http"):
                continue
            # Skip Google's own URLs
            if "google.com" in urlparse(url).netloc:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Extract title
            h3 = div.find("h3")
            title = h3.get_text(strip=True) if h3 else ""
            if not title:
                continue

            # Extract snippet
            snippet = ""
            # Try multiple snippet selectors
            for selector in [
                {"class_": "VwiC3b"},
                {"attrs": {"data-sncf": True}},
                {"class_": "IsZvec"},
                {"class_": "s3v9rd"},
            ]:
                snip_div = div.find("div", **selector) or div.find("span", **selector)
                if snip_div:
                    snippet = snip_div.get_text(strip=True)
                    break
            if not snippet:
                # Fallback: grab all text after h3
                all_text = div.get_text(separator=" ", strip=True)
                if title in all_text:
                    snippet = all_text.split(title, 1)[-1].strip()[:300]

            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
            })
            found_any = True

        # No more results on this page
        if not found_any:
            break

        start += 10
        if start >= 50:  # Don't go beyond page 5
            break
        time.sleep(0.5)  # Be polite

    return results[:max_results]


def google_news_search(query, max_results=10, language="en", time_range=None):
    """Search Google News via RSS feed (reliable, works from any IP)."""
    import xml.etree.ElementTree as ET

    results = []

    # Google News RSS endpoint
    params = {
        "q": query,
        "hl": language,
        "gl": language.upper() if len(language) == 2 else "US",
        "ceid": f"{language.upper()}:{language}",
    }
    # Time filter via "when:" operator in query
    time_query = query
    if time_range:
        time_map = {"d": "1d", "w": "7d", "m": "30d", "y": "365d"}
        if time_range in time_map:
            time_query = f"{query} when:{time_map[time_range]}"
        params["q"] = time_query

    resp = requests.get(
        "https://news.google.com/rss/search",
        params=params,
        headers=HEADERS,
        timeout=15,
        verify=False,
    )
    resp.raise_for_status()

    # Parse RSS XML
    root = ET.fromstring(resp.content)
    channel = root.find("channel")
    if channel is None:
        return results

    for item in channel.findall("item"):
        if len(results) >= max_results:
            break

        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        description_html = item.findtext("description", "")

        if not title or not link:
            continue

        # Extract source from title (Google News format: "Title - Source")
        source = ""
        if " - " in title:
            parts = title.rsplit(" - ", 1)
            title = parts[0].strip()
            source = parts[1].strip()

        # Extract snippet from description HTML
        snippet = ""
        if description_html:
            soup = BeautifulSoup(description_html, "html.parser")
            snippet = soup.get_text(separator=" ", strip=True)[:500]

        results.append({
            "title": title,
            "url": link,
            "snippet": snippet,
            "source": source,
            "date": pub_date,
        })

    return results[:max_results]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def clean_text(html_content):
    """Extract clean text from HTML."""
    soup = BeautifulSoup(html_content, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return text


def extract_metadata(soup, url):
    """Extract page metadata from BeautifulSoup."""
    meta = {}

    title_tag = soup.find("title")
    meta["title"] = title_tag.get_text(strip=True) if title_tag else ""

    desc_tag = soup.find("meta", attrs={"name": "description"})
    meta["description"] = desc_tag.get("content", "") if desc_tag else ""

    kw_tag = soup.find("meta", attrs={"name": "keywords"})
    meta["keywords"] = kw_tag.get("content", "") if kw_tag else ""

    for og in ["og:title", "og:description", "og:image", "og:type", "og:site_name"]:
        tag = soup.find("meta", attrs={"property": og})
        if tag:
            meta[og.replace(":", "_")] = tag.get("content", "")

    canonical = soup.find("link", attrs={"rel": "canonical"})
    meta["canonical_url"] = canonical.get("href", "") if canonical else ""

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
        "version": "1.1.0",
        "engine": "google",
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/search", response_model=SearchResponse)
def web_search(req: SearchRequest):
    """Search the web using Google."""
    try:
        raw = google_search(
            query=req.query,
            max_results=req.max_results,
            language=req.language,
            country=req.country,
            time_range=req.time_range,
        )

        results = [
            SearchResult(title=r["title"], url=r["url"], snippet=r["snippet"])
            for r in raw
        ]

        return SearchResponse(
            query=req.query,
            results=results,
            count=len(results),
            engine="google",
            timestamp=datetime.now().isoformat(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.post("/api/news", response_model=NewsResponse)
def news_search(req: NewsRequest):
    """Search news articles using Google News."""
    try:
        raw = google_news_search(
            query=req.query,
            max_results=req.max_results,
            language=req.language,
            time_range=req.time_range,
        )

        results = [
            NewsResult(
                title=r["title"],
                url=r["url"],
                snippet=r["snippet"],
                source=r["source"],
                date=r["date"],
            )
            for r in raw
        ]

        return NewsResponse(
            query=req.query,
            results=results,
            count=len(results),
            engine="google-news",
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

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        desc_tag = soup.find("meta", attrs={"name": "description"})
        description = desc_tag.get("content", "") if desc_tag else ""

        text = clean_text(resp.text)
        if len(text) > req.max_content_length:
            text = text[:req.max_content_length] + "...[truncated]"

        links = extract_links(soup, str(resp.url)) if req.extract_links else []
        images = extract_images(soup, str(resp.url)) if req.extract_images else []
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

    print(f"n8n Search Proxy v1.1.0 (Google)")
    print(f"Starting on http://{args.host}:{args.port}")
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
