# n8n Search Proxy

Internet search & crawl gateway for air-gapped n8n servers.

Runs on a Windows 11 PC with internet access. n8n (on a machine without internet) calls this API to search and crawl the web.

```
PCN8N (no internet)          Windows 11 PC (has internet)
┌─────────────┐   HTTP API   ┌──────────────────┐          ┌──────────┐
│   n8n       │ ───────────> │  Search Proxy    │ ───────> │ Internet │
│  (workflow) │ <─────────── │  (FastAPI)       │ <─────── │          │
└─────────────┘   JSON       └──────────────────┘          └──────────┘
```

## Quick Start (Windows 11)

### 1. Install Python 3.10+

Download from [python.org](https://www.python.org/downloads/) — check "Add to PATH".

### 2. Install Dependencies

Double-click `install.bat` or run:

```cmd
pip install -r requirements.txt
```

### 3. Start the Server

Double-click `start.bat` or run:

```cmd
python server.py
```

Server starts on `http://0.0.0.0:5100` — accessible from any machine on the network.

### 4. Verify

Open in browser: `http://localhost:5100/docs` — interactive API documentation.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/search` | Web search (DuckDuckGo) |
| `POST` | `/api/news` | News search |
| `POST` | `/api/fetch` | Fetch URL content (raw HTML or plain text) |
| `POST` | `/api/crawl` | Crawl URL: extract text, links, images, metadata |

### POST /api/search — Web Search

```json
{
  "query": "ransomware prevention 2026",
  "max_results": 10,
  "region": "wt-wt",
  "time_range": "m"
}
```

**Response:**
```json
{
  "query": "ransomware prevention 2026",
  "results": [
    {
      "title": "How to Prevent Ransomware...",
      "url": "https://example.com/article",
      "snippet": "Top strategies for ransomware prevention..."
    }
  ],
  "count": 10,
  "timestamp": "2026-03-28T10:00:00"
}
```

**Parameters:**
- `query` (required) — Search keywords
- `max_results` — Number of results (default: 10, max: ~30)
- `region` — Region code: `wt-wt` (global), `vn-vi` (Vietnam), `us-en` (US), etc.
- `time_range` — `d` (day), `w` (week), `m` (month), `y` (year), or `null` (all time)

### POST /api/news — News Search

```json
{
  "query": "cybersecurity vietnam",
  "max_results": 10,
  "time_range": "w"
}
```

**Response:**
```json
{
  "query": "cybersecurity vietnam",
  "results": [
    {
      "title": "New Cyber Threat Targets...",
      "url": "https://news.example.com/article",
      "snippet": "Security researchers discovered...",
      "source": "SecurityWeek",
      "date": "2026-03-27T08:00:00"
    }
  ],
  "count": 10,
  "timestamp": "2026-03-28T10:00:00"
}
```

### POST /api/fetch — Fetch URL

```json
{
  "url": "https://example.com/page",
  "timeout": 30,
  "extract_text": true
}
```

**Response:**
```json
{
  "url": "https://example.com/page",
  "status_code": 200,
  "content_type": "text/html; charset=utf-8",
  "content": "The extracted plain text content...",
  "content_length": 4523,
  "timestamp": "2026-03-28T10:00:00"
}
```

**Parameters:**
- `url` (required) — URL to fetch
- `timeout` — Request timeout in seconds (default: 30)
- `extract_text` — `true` = return plain text, `false` = return raw HTML (default: false)

### POST /api/crawl — Crawl URL

```json
{
  "url": "https://example.com/article",
  "extract_links": true,
  "extract_images": false,
  "max_content_length": 50000
}
```

**Response:**
```json
{
  "url": "https://example.com/article",
  "title": "Article Title",
  "description": "Meta description...",
  "text": "Full article text extracted from HTML...",
  "text_length": 3200,
  "links": [
    {"url": "https://example.com/related", "text": "Related Article"}
  ],
  "images": [],
  "metadata": {
    "title": "Article Title",
    "description": "Meta description...",
    "keywords": "security, malware",
    "og_title": "Article Title",
    "language": "en"
  },
  "timestamp": "2026-03-28T10:00:00"
}
```

## n8n Configuration

### HTTP Request Node Setup

In n8n, use the **HTTP Request** node:

1. **Method:** POST
2. **URL:** `http://<windows-pc-ip>:5100/api/search`
3. **Body Content Type:** JSON
4. **Body:**
   ```json
   {
     "query": "{{ $json.searchQuery }}",
     "max_results": 10
   }
   ```

### Example Workflows

**Search + Summarize:**
```
[Trigger] → [HTTP Request: /api/search] → [Loop: /api/crawl each URL] → [AI: Summarize]
```

**News Monitor:**
```
[Schedule Trigger] → [HTTP Request: /api/news] → [Filter new items] → [Notify]
```

**Web Scraper:**
```
[Trigger] → [HTTP Request: /api/crawl] → [Extract data] → [Save to DB]
```

## Options

### Custom Port

```cmd
python server.py --port 8080
```

### Localhost Only (no network access)

```cmd
python server.py --host 127.0.0.1
```

### Multiple Workers (for heavy load)

```cmd
python server.py --workers 4
```

## Windows Firewall

If n8n can't connect, allow the port through Windows Firewall:

```cmd
netsh advfirewall firewall add rule name="n8n Search Proxy" dir=in action=allow protocol=TCP localport=5100
```

## Run as Windows Service (Optional)

To run at startup without a logged-in user, use [NSSM](https://nssm.cc/):

```cmd
nssm install n8n-search-proxy "C:\Python312\python.exe" "C:\path\to\server.py --port 5100"
nssm start n8n-search-proxy
```

## Requirements

| Package | Purpose |
|---------|---------|
| `fastapi` | Web framework (REST API) |
| `uvicorn` | ASGI server |
| `requests` | HTTP client for fetching URLs |
| `beautifulsoup4` | HTML parsing and text extraction |
| `duckduckgo-search` | Web search (no API key needed) |
| `pydantic` | Request/response validation |

## License

MIT License

## Author

SEHC IT Infrastructure Team
