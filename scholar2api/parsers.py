"""HTML parsers for Google Scholar pages.

Selectors were reverse-engineered from live pages (see docs/analysis.md) and
are covered by offline tests in tests/fixtures/.
"""
import re
from html import unescape
from typing import Dict, List, Optional

from .models import AuthorProfile, CoAuthor, Metric, Paper, PaperDetail

_TAG = re.compile(r"<[^>]+>")
_STATS = {"Citations": "citations", "h-index": "h_index", "i10-index": "i10_index"}


def _text(raw: Optional[str]) -> str:
    return unescape(_TAG.sub("", raw or "")).strip()


def _int(raw: Optional[str]) -> int:
    digits = re.sub(r"\D", "", raw or "")
    return int(digits) if digits else 0


def extract_user_id(id_or_url: str) -> str:
    """Accept a bare Scholar id or any URL containing ?user=..."""
    m = re.search(r"[?&]user=([A-Za-z0-9_-]+)", id_or_url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{6,}", id_or_url.strip()):
        return id_or_url.strip()
    raise ValueError(f"cannot extract scholar user id from: {id_or_url!r}")


def _histogram(html: str, year_cls: str, bar_cls: str) -> Dict[int, int]:
    """Year labels and bars are absolutely positioned; a bar sits 5px to the
    right of its year label (bar_offset == year_offset + 5)."""
    years = {int(off): int(yr) for off, yr in re.findall(
        rf'<span class="{year_cls}" style="right:(\d+)px">(\d{{4}})</span>', html)}
    out = {}
    for off, val in re.findall(
            rf'class="{bar_cls}" style="right:(\d+)px;[^"]*">'
            rf'<span class="{bar_cls}l">(\d+)</span>', html):
        if year := years.get(int(off) - 5):
            out[year] = int(val)
    if not out:  # fallback: DOM order (bars missing for zero-citation years)
        ys = re.findall(rf'<span class="{year_cls}"[^>]*>(\d{{4}})</span>', html)
        vs = re.findall(rf'class="{bar_cls}l">(\d+)</span>', html)
        out = {int(y): int(v) for y, v in zip(ys, vs)}
    return dict(sorted(out.items()))


def parse_profile(html: str, user_id: str) -> AuthorProfile:
    p = AuthorProfile(user_id=user_id, name="")

    if m := re.search(r'<div id="gsc_prf_in"[^>]*>(.*?)</div>', html, re.S):
        p.name = _text(m.group(1))
    if m := re.search(r'<div class="gsc_prf_il"[^>]*>(.*?)</div>', html, re.S):
        p.affiliation = _text(m.group(1))
    if m := re.search(r'id="gsc_prf_ivh"[^>]*>(.*?)</div>', html, re.S):
        if d := re.search(r"Verified email at\s+([^\s<-]+)", _text(m.group(1))):
            p.email_domain = d.group(1)
    if m := re.search(r'<a class="gsc_prf_ila"[^>]*href="([^"]+)"', html):
        p.homepage = unescape(m.group(1))
    if m := re.search(r'<div[^>]*id="gsc_prf_int"[^>]*>(.*?)</div>', html, re.S):
        p.interests = [t for x in re.findall(r"<a [^>]*>([^<]*)</a>", m.group(1))
                       if (t := _text(x))]

    if tbl := re.search(r'<table id="gsc_rsb_st">(.*?)</table>', html, re.S):
        body = tbl.group(1)
        if sy := re.search(r"Since (\d{4})", body):
            p.stats.since_year = int(sy.group(1))
        for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
            lab = re.search(r'class="gsc_rsb_sc1">.*?>([^<]+)</a>', row, re.S)
            vals = re.findall(r'class="gsc_rsb_std">(\d*)</td>', row)
            if lab and vals and (key := _STATS.get(_text(lab.group(1)))):
                setattr(p.stats, key, Metric(
                    all=_int(vals[0]), since_5y=_int(vals[1]) if len(vals) > 1 else 0))

    p.citations_per_year = _histogram(html, "gsc_g_t", "gsc_g_a")

    if co := re.search(r'id="gsc_rsb_co".*?<ul class="gsc_rsb_a">(.*?)</ul>', html, re.S):
        for uid, name, aff in re.findall(
                r'href="/citations\?user=([A-Za-z0-9_-]+)[^"]*"[^>]*>([^<]+)</a>'
                r'<span class="gsc_rsb_a_ext">([^<]*)</span>', co.group(1)):
            p.coauthors.append(CoAuthor(user_id=uid, name=unescape(name),
                                        affiliation=_text(aff) or None))

    p.papers = parse_paper_list(html)
    return p


def parse_paper_list(html: str) -> List[Paper]:
    papers = []
    for row in re.findall(r'<tr class="gsc_a_tr">(.*?)</tr>', html, re.S):
        t = (re.search(r'<a href="([^"]*citation_for_view=([^"&]+)[^"]*)"'
                       r'[^>]*class="gsc_a_at"[^>]*>(.*?)</a>', row, re.S)
             or re.search(r'<a class="gsc_a_at"[^>]*href="([^"]*citation_for_view=([^"&]+)[^"]*)"'
                          r'[^>]*>(.*?)</a>', row, re.S))
        if not t:
            continue
        grays = re.findall(r'<div class="gs_gray">(.*?)</div>', row, re.S)
        cite = re.search(r'<a\b[^>]*class="gsc_a_ac[^"]*"[^>]*>(\d*)</a>', row)
        href = re.search(r'href="([^"]*)"', cite.group(0)) if cite else None
        year = re.search(r'class="gsc_a_h[^"]*"[^>]*>(\d{4})?</span>', row)
        papers.append(Paper(
            citation_for_view=unescape(t.group(2)),
            title=_text(t.group(3)),
            authors=_text(grays[0]) if grays else None,
            venue=_text(grays[1]) if len(grays) > 1 else None,
            year=int(year.group(1)) if year and year.group(1) else None,
            citations=int(cite.group(1)) if cite and cite.group(1) else 0,
            cited_by_url=unescape(href.group(1)) if href else None,
        ))
    return papers


_FIELDS = {"Authors": "authors", "Publication date": "publication_date",
           "Journal": "venue", "Conference": "venue", "Book": "venue",
           "Publisher": "publisher", "Description": "description"}


def parse_paper_detail(html: str, citation_for_view: str) -> PaperDetail:
    d = PaperDetail(citation_for_view=citation_for_view, title="")

    if t := re.search(r'<div id="gsc_oci_title">(?:<a[^>]*href="([^"]*)"[^>]*>)?'
                      r'(.*?)(?:</a>)?</div>', html, re.S):
        d.url = unescape(t.group(1)) if t.group(1) else None
        d.title = _text(t.group(2))
    if m := re.search(r'class="gsc_oci_title_ggi"[^>]*>\s*<a href="([^"]+)"', html):
        d.pdf_url = unescape(m.group(1))

    for k, v in re.findall(r'<div class="gsc_oci_field"[^>]*>(.*?)</div>\s*'
                           r'<div class="gsc_oci_value"[^>]*>(.*?)</div>', html, re.S):
        key, val = _text(k), _text(v)
        if key in _FIELDS:
            setattr(d, _FIELDS[key], val)
        elif key.startswith("Total citations"):
            if m := re.search(r"Cited by (\d+)", val):
                d.total_citations = int(m.group(1))
    if not d.total_citations and (m := re.search(r"Cited by (\d+)", html)):
        d.total_citations = int(m.group(1))

    if m := re.search(r"cites=(\d+)", html):
        d.cited_by_cluster_id = m.group(1)
    d.citations_per_year = _histogram(html, "gsc_oci_g_t", "gsc_oci_g_a")
    return d
