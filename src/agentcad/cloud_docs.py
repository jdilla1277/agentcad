"""Read public service-owned guides without loading or transmitting credentials."""
from __future__ import annotations

import os
import re

from agentcad.account import DEFAULT_SITE, AccountError, origin

GUIDE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_BYTES = 1024 * 1024


class DocsError(Exception):
    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


class DocsClient:
    def __init__(self, site, *, transport=None):
        self.site = origin(site)
        try:
            import httpx
        except ImportError:
            raise DocsError("missing_dependency", "Install pip install 'agentcad[cloud]' to read live cloud guides.") from None
        self.httpx = httpx
        self.http = httpx.Client(base_url=self.site, transport=transport, timeout=15, follow_redirects=False)

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("AGENTCAD_DOCS_URL") or DEFAULT_SITE)

    def read(self, page=None):
        if page is not None and (len(page) > 100 or not GUIDE_ID.fullmatch(page)):
            raise DocsError("invalid_guide", "Use a guide ID returned by agentcad cloud docs.")
        path = "/api/docs/cloud" + ("/" + page if page else "")
        try:
            with self.http.stream("GET", path, headers={"Accept": "application/json", "Cache-Control": "no-cache"}) as response:
                if response.status_code == 404:
                    raise DocsError("guide_not_found", "Guide not found. Run agentcad cloud docs to list available guides.")
                if response.status_code != 200:
                    raise DocsError("docs_unavailable", "The documentation service is unavailable. Retry or read https://agentcad.dev/docs/cloud.")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError()
                import json
                result = json.loads(body)
                valid = isinstance(result, dict) and result.get("schema_version") == 1
                if page:
                    valid = valid and result.get("id") == page and isinstance(result.get("content"), str) and bool(result["content"].strip())
                else:
                    valid = valid and isinstance(result.get("pages"), list) and all(
                        isinstance(item, dict) and isinstance(item.get("id"), str)
                        and len(item["id"]) <= 100 and GUIDE_ID.fullmatch(item["id"])
                        and all(isinstance(item.get(key), str) for key in ("title", "url", "availability"))
                        for item in result["pages"])
                if not valid:
                    raise ValueError()
                return result
        except self.httpx.HTTPError:
            raise DocsError("docs_unavailable", "Could not reach the documentation service. Retry or read https://agentcad.dev/docs/cloud.") from None
        except (ValueError, UnicodeError):
            raise DocsError("invalid_docs_response", "The documentation service returned an unsupported response. Retry or use the website.") from None

    def close(self):
        self.http.close()


def read_docs(page=None):
    client = None
    try:
        client = DocsClient.from_env()
        return {**client.read(page), "command": "cloud", "operation": "docs", "mode": "cloud", "status": "success"}
    except (DocsError, AccountError) as exc:
        return {"command": "cloud", "operation": "docs", "mode": "cloud", "status": "error", "reason": exc.reason, "message": str(exc), "more_at": "https://agentcad.dev/docs/cloud"}
    finally:
        if client is not None:
            client.close()
