"""Fetch a PDF for an item that is already in the library (fork addition).

``zotero_add_by_doi`` finds an open-access PDF only while it creates a new
item. This module runs the same source cascade for an *existing* item, so a
reference saved without its PDF can be completed by key, without opening a
browser.

Order of attempts:

1. arXiv, when the item carries an arXiv ID (DOI ``10.48550/arXiv.*``,
   Archive ID, URL or an ``arXiv:`` line in Extra). Direct and reliable.
2. The open-access cascade of ``_helpers._try_attach_oa_pdf`` for a real
   DOI: Unpaywall, OpenAlex, the publisher page, arXiv via CrossRef,
   Semantic Scholar, PubMed Central.
3. With neither identifier, an OpenAlex title search; a hit is used only
   when its normalized title equals the item's, so a near miss never
   attaches the wrong paper.

When nothing downloads, the result names the best URL found and an
OpenURL link for the institution's resolver (``ZOTERO_MCP_OPENURL_RESOLVER``)
so a paywalled paper is one click away.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote, urlencode

import requests

from zotero_mcp import utils as _utils
from zotero_mcp._app import mcp
from zotero_mcp._context import Context
from zotero_mcp.client import with_zotero_api_lock
from zotero_mcp.identifiers import (
    arxiv_identity,
    arxiv_identity_from_extra,
    normalize_doi,
    normalize_title_for_matching,
)
from zotero_mcp.tools import _helpers

_DOI_IN_TEXT_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)
_DOI_EXTRA_RE = re.compile(r"^\s*DOI:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_OPENURL_ENV = "ZOTERO_MCP_OPENURL_RESOLVER"


def _existing_pdf(write_zot, item_key: str) -> str | None:
    """Key of an existing PDF attachment of ``item_key``, or None."""
    for child in write_zot.children(item_key) or []:
        data = child.get("data", {})
        if data.get("itemType") == "attachment" and data.get("contentType") == "application/pdf":
            return data.get("key") or child.get("key")
    return None


def _identifiers(data: dict) -> tuple[str | None, str | None]:
    """(DOI, arXiv ID) read from an item's fields. Either may be None."""
    extra = data.get("extra", "") or ""
    raw_doi = data.get("DOI") or ""
    if not raw_doi:
        m = _DOI_EXTRA_RE.search(extra)
        raw_doi = m.group(1) if m else ""
    if not raw_doi:
        m = _DOI_IN_TEXT_RE.search(data.get("url", "") or "")
        raw_doi = m.group(1) if m else ""
    doi = normalize_doi(raw_doi) if raw_doi else None

    arxiv = (
        arxiv_identity(doi)
        or arxiv_identity(data.get("archiveID"))
        or arxiv_identity(data.get("url"))
        or arxiv_identity_from_extra(extra)
    )
    return doi, arxiv


def _lookup_by_title(title: str, ctx: Context) -> tuple[str | None, str | None]:
    """(DOI, arXiv ID) of the OpenAlex work whose title matches exactly."""
    wanted = normalize_title_for_matching(title)
    if not wanted:
        return None, None
    try:
        resp = requests.get(
            "https://api.openalex.org/works",
            params={
                "search": title,
                "per_page": 8,
                "select": "doi,title,ids,locations",
                "mailto": "zotero-mcp@users.noreply.github.com",
            },
            headers={"User-Agent": _utils.USER_AGENT},
            timeout=15,
        )
        if resp.status_code != 200:
            return None, None
        for work in resp.json().get("results", []):
            if normalize_title_for_matching(work.get("title") or "") != wanted:
                continue
            doi = normalize_doi(work.get("doi") or "") if work.get("doi") else None
            arxiv = arxiv_identity(doi)
            for loc in work.get("locations") or []:
                arxiv = arxiv or arxiv_identity(loc.get("landing_page_url"))
            if doi or arxiv:
                ctx.info(f"Title matched OpenAlex work: doi={doi} arxiv={arxiv}")
                return doi, arxiv
    except Exception as e:  # network trouble is a soft failure
        ctx.info(f"OpenAlex title lookup failed: {e}")
    return None, None


def _crossref_record(doi: str, ctx: Context) -> dict | None:
    try:
        resp = requests.get(
            f"https://api.crossref.org/works/{quote(doi, safe='/')}",
            headers={"User-Agent": _utils.USER_AGENT},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("message")
    except Exception as e:
        ctx.info(f"CrossRef lookup failed: {e}")
    return None


def _zotero_fallback(item_key: str) -> str:
    """Where to go when no source would hand a PDF to a script."""
    return (
        f"\nIn Zotero: zotero://select/library/items/{item_key} → right-click → "
        "Find Available PDF (查找可用的 PDF). Zotero's own lookup also uses "
        "your library's resolver and works for sites that refuse scripts."
    )


def _openurl_link(doi: str | None, title: str) -> str | None:
    resolver = os.environ.get(_OPENURL_ENV, "").strip()
    if not resolver:
        return f"https://doi.org/{doi}" if doi else None
    params = {"rft_id": f"info:doi/{doi}"} if doi else {"rft.atitle": title}
    sep = "&" if "?" in resolver else "?"
    return f"{resolver}{sep}{urlencode(params)}"


@mcp.tool(
    name="zotero_fetch_pdf",
    description=(
        "Find and attach a PDF to an EXISTING item that has none, without the "
        "user opening a browser. Tries arXiv (when the item has an arXiv ID), "
        "then open-access sources for its DOI (Unpaywall, OpenAlex, publisher "
        "page, Semantic Scholar, PMC); with no DOI/arXiv ID it looks the title "
        "up on OpenAlex and only uses an exact title match. If no PDF can be "
        "downloaded (paywalled), returns the best URL plus an institutional "
        "OpenURL link to give the user. item_key: the regular item's key. "
        "force: attach even if the item already has a PDF."
    ),
)
@with_zotero_api_lock
def fetch_pdf(item_key: str, force: bool = False, *, ctx: Context) -> str:
    try:
        _read_zot, write_zot = _helpers._get_write_client(ctx)
    except ValueError as e:
        return str(e)

    try:
        item = write_zot.item(item_key)
    except Exception as e:
        return f"Error: Item '{item_key}' not found ({e})."
    data = item.get("data", {}) or {}
    title = data.get("title", "") or ""
    if data.get("itemType") in ("attachment", "note", "annotation"):
        parent = data.get("parentItem")
        hint = f" Use its parent '{parent}'." if parent else ""
        return f"Error: '{item_key}' is a {data.get('itemType')}, not a regular item.{hint}"

    if not force:
        existing = _existing_pdf(write_zot, item_key)
        if existing:
            return f"`{item_key}` already has a PDF (attachment `{existing}`); pass force=True to add another."

    doi, arxiv = _identifiers(data)
    looked_up = False
    if not doi and not arxiv and title:
        doi, arxiv = _lookup_by_title(title, ctx)
        looked_up = bool(doi or arxiv)
    label = f"“{title[:80]}” (`{item_key}`)"
    found_via = " (identifier found by title search)" if looked_up else ""

    if arxiv:
        url = f"https://arxiv.org/pdf/{arxiv}"
        ctx.info(f"Trying arXiv: {url}")
        suffix = _helpers._download_and_attach_pdf(write_zot, item_key, url, doi, ctx)
        if suffix is not None:
            return f"PDF attached to {label} from arXiv {arxiv}{found_via}{suffix}"

    if doi and not arxiv_identity(doi):
        cr = _crossref_record(doi, ctx)
        result = _helpers._try_attach_oa_pdf(
            write_zot, item_key, doi, ctx, crossref_metadata=cr,
            pages=data.get("pages"),
        )
        if result.startswith("PDF attached"):
            return f"{result} → {label}{found_via}"
        link = _openurl_link(doi, title)
        return (
            f"No PDF attached to {label}{found_via}: {result}."
            + _zotero_fallback(item_key)
            + (f"\nOr in the browser (institution login): {link}" if link else "")
        )

    if not doi and not arxiv:
        link = _openurl_link(None, title)
        return (
            f"No PDF attached to {label}: no DOI or arXiv ID on the item, and no "
            "exact title match on OpenAlex. Add a DOI to the item, or open: "
            f"{link or '(no resolver configured)'}"
            + _zotero_fallback(item_key)
        )
    return (f"No PDF attached to {label}: the arXiv download failed (arXiv {arxiv})."
            + _zotero_fallback(item_key))
