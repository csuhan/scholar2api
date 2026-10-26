"""Offline parser tests against captured Scholar HTML (tests/fixtures)."""
import pathlib

from scholar2api.fetchers.base import detect_block
from scholar2api.parsers import extract_user_id, parse_paper_detail, parse_paper_list, parse_profile

FIX = pathlib.Path(__file__).parent / "fixtures"
UID = "vgcxKEcAAAAJ"


def html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_extract_user_id():
    assert extract_user_id(UID) == UID
    assert extract_user_id(
        "https://scholar.google.com.hk/citations?user=vgcxKEcAAAAJ&hl=zh-CN") == UID


def test_profile_basics():
    p = parse_profile(html("profile.html"), UID)
    assert p.name == "Jiaming Han"
    assert "CUHK" in (p.affiliation or "")
    assert p.email_domain == "link.cuhk.edu.hk"
    assert "Computer Vision" in p.interests


def test_profile_stats():
    p = parse_profile(html("profile.html"), UID)
    assert p.stats.citations.all == 7209
    assert p.stats.h_index.all == 16
    assert p.stats.i10_index.all == 18
    assert p.stats.since_year == 2021
    assert p.citations_per_year[2024] == 1992
    assert sum(p.citations_per_year.values()) > 7000


def test_profile_papers_and_coauthors():
    p = parse_profile(html("profile.html"), UID)
    assert len(p.papers) == 20
    top = p.papers[0]
    assert top.citation_for_view == f"{UID}:u5HHmVD_uO8C"
    assert "Oriented Object Detection" in top.title
    assert top.citations == 1538 and top.year == 2021
    assert any(c.name == "Xiangyu Yue" for c in p.coauthors)


def test_paper_list_page_two():
    papers = parse_paper_list(html("page2.html"))
    assert len(papers) == 9
    assert papers[0].citation_for_view.startswith(f"{UID}:")


def test_paper_detail():
    d = parse_paper_detail(html("cite.html"), f"{UID}:u5HHmVD_uO8C")
    assert "Oriented Object Detection" in d.title
    assert d.authors and "Jiaming Han" in d.authors
    assert d.publication_date == "2021/3/12"
    assert "IEEE" in (d.venue or "")
    assert d.total_citations == 1538
    assert d.cited_by_cluster_id == "15278380461775262117"
    assert d.pdf_url and "arxiv.org" in d.pdf_url


def test_detect_block_captcha_page():
    assert detect_block(200, "https://scholar.google.com/x",
                        html("block.html")) == "captcha_form"
    assert detect_block(429, "https://scholar.google.com/x", "") == "rate_limited"
    assert detect_block(200, "https://scholar.google.com/sorry/index", "") == "sorry"
    assert detect_block(200, "https://scholar.google.com/x", "ok") is None
