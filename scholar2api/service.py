"""FastAPI app: JSON endpoints for downstream consumers."""
import logging
import os
from contextlib import asynccontextmanager
from typing import List

import httpx
from fastapi import FastAPI, HTTPException, Query

from .client import ScholarClient
from .fetchers.base import ScholarBlocked, ScholarHTTPError
from .models import AuthorProfile, Paper, PaperDetail

logging.basicConfig(
    level=os.getenv("SCHOLAR2API_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

client = ScholarClient(cache_ttl=float(os.getenv("SCHOLAR2API_CACHE_TTL", "3600")))


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    client.close()


app = FastAPI(title="scholar2api", version="0.1.0", lifespan=lifespan,
              description="Google Scholar profile data as a JSON API")


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, ScholarBlocked):
        return HTTPException(503, f"blocked by scholar ({error.kind})")
    if isinstance(error, ScholarHTTPError):
        if error.status == 404:
            return HTTPException(404, "not found on Google Scholar")
        return HTTPException(502, f"Google Scholar returned {error.status}")
    if isinstance(error, ValueError):
        return HTTPException(400, str(error))
    if isinstance(error, httpx.HTTPError):
        return HTTPException(502, f"upstream request failed: {type(error).__name__}")
    log.exception("unhandled error")
    return HTTPException(500, "internal error")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/author", response_model=AuthorProfile)
def author_by_url(url: str = Query(..., description="Full Scholar profile URL"),
                  pagesize: int = Query(100, ge=1, le=100)):
    """Resolve an author from a full URL (avoids encoding issues in path)."""
    return author(url, pagesize)


@app.get("/author/{user_id}", response_model=AuthorProfile)
def author(user_id: str, pagesize: int = Query(100, ge=1, le=100)):
    """user_id is a bare Scholar id (vgcxKEcAAAAJ) or a URL."""
    try:
        return client.profile(user_id, pagesize)
    except Exception as error:  # mapped to an HTTP status
        raise _http_error(error)


@app.get("/author/{user_id}/papers", response_model=List[Paper])
def papers(user_id: str, cstart: int = Query(0, ge=0),
           pagesize: int = Query(100, ge=1, le=100),
           sortby: str = Query("cited", pattern="^(cited|pubdate)$"),
           all_pages: bool = False):
    try:
        if all_pages:
            return client.all_papers(user_id, sortby)
        return client.papers(user_id, cstart, pagesize, sortby)[0]
    except Exception as error:
        raise _http_error(error)


@app.get("/author/{user_id}/paper/{cluster}", response_model=PaperDetail)
def paper_detail(user_id: str, cluster: str):
    try:
        return client.paper_detail(user_id, cluster)
    except Exception as error:
        raise _http_error(error)
