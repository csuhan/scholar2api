"""Response models."""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Metric(BaseModel):
    all: int = 0
    since_5y: int = 0


class AuthorStats(BaseModel):
    citations: Metric = Field(default_factory=Metric)
    h_index: Metric = Field(default_factory=Metric)
    i10_index: Metric = Field(default_factory=Metric)
    since_year: Optional[int] = None  # "Since 2021"


class CoAuthor(BaseModel):
    user_id: str
    name: str
    affiliation: Optional[str] = None


class Paper(BaseModel):
    citation_for_view: str  # "{user_id}:{cluster}"
    title: str
    authors: Optional[str] = None
    venue: Optional[str] = None
    year: Optional[int] = None
    citations: int = 0
    cited_by_url: Optional[str] = None


class AuthorProfile(BaseModel):
    user_id: str
    name: str
    affiliation: Optional[str] = None
    email_domain: Optional[str] = None
    homepage: Optional[str] = None
    interests: List[str] = Field(default_factory=list)
    stats: AuthorStats = Field(default_factory=AuthorStats)
    citations_per_year: Dict[int, int] = Field(default_factory=dict)
    coauthors: List[CoAuthor] = Field(default_factory=list)
    papers: List[Paper] = Field(default_factory=list)  # first page


class PaperDetail(BaseModel):
    citation_for_view: str
    title: str
    authors: Optional[str] = None
    publication_date: Optional[str] = None
    venue: Optional[str] = None
    publisher: Optional[str] = None
    description: Optional[str] = None
    total_citations: int = 0
    citations_per_year: Dict[int, int] = Field(default_factory=dict)
    cited_by_cluster_id: Optional[str] = None
    pdf_url: Optional[str] = None
    url: Optional[str] = None
