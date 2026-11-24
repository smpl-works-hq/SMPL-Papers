"""Search scholarly indexes and verify selected passages against retrieved sources."""

import argparse
from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import unicodedata
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from records import digest, file_hash, identifier, json_hash, load_json, local_file, required_text, write_atomic, write_json
from roles import workplan


CROSSREF = "https://api.crossref.org/works"
SCHOLAR = "https://api.semanticscholar.org/graph/v1/paper/search"
LIMIT = 24 * 1024 * 1024


def public_url(url):
    target = urlsplit(url)
    if target.scheme != "https" or not target.hostname or target.username or target.password:
        raise ValueError("Use a public HTTPS source URL.")
    addresses = socket.getaddrinfo(target.hostname, target.port or 443, type=socket.SOCK_STREAM)
    if any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Source URL resolves to a nonpublic address.")
    return url


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, public_url(newurl))
        if redirected and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            redirected.remove_header("X-api-key")
            redirected.remove_header("X-goog-api-key")
        return redirected


def fetch(url, accept="*/*", headers=None, body=None):
    request = Request(public_url(url), data=body, headers={"User-Agent": "SMPL-Papers/2.0",
                                                "Accept": accept, **(headers or {})})
    with build_opener(PublicRedirect()).open(request, timeout=18) as response:
        data = response.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError("Source exceeds the retrieval size limit.")
        return data, response.geturl(), response.headers.get_content_type()


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


class ArticleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.metadata = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag == "meta" and (attrs.get("name") or "").lower() in {"citation_title", "citation_doi", "dc.title"}:
            self.metadata.append(attrs.get("content") or "")
        field = (attrs.get("name") or attrs.get("property") or "").lower()
        if tag == "meta" and field in {"citation_publication_date", "dc.date.issued", "article:published_time"}:
            self.metadata.append("Published: " + (attrs.get("content") or "").replace("/", "-"))

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def document(data, mime):
    if data.startswith(b"%PDF-"):
        try:
            import pymupdf
        except ImportError as exc:
            raise ValueError("Install the visual requirements to verify PDF passages.") from exc
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            pages = [normalized(page.get_text()) for page in pdf]
        return pages, pages[0][:4000] if pages else ""
    text = data.decode("utf-8-sig", errors="replace")
    if "html" in mime or "xml" in mime or re.match(r"\s*<(?:!doctype|html|\?xml)", text, re.I):
        parser = ArticleText()
        parser.feed(text)
        text = normalized(" ".join(parser.parts))
        return [text], " ".join(parser.metadata) + " " + text[:5000]
    return [normalized(text)], normalized(text)[:5000]


def publication_date(parts):
    if not parts:
        return ""
    # Use the last possible day for a partial date when applying a cutoff.
    from calendar import monthrange
    year, *rest = parts
    month = rest[0] if rest else 12
    day = rest[1] if len(rest) > 1 else monthrange(year, month)[1]
    return date(year, month, day).isoformat()


def query_index(provider, query, cutoff, limit):
    if provider == "google":
        token, model = os.environ.get("GEMINI_API_KEY"), os.environ.get("SMPL_SEARCH_MODEL")
        if not token or not model:
            raise ValueError("Google discovery requires GEMINI_API_KEY and SMPL_SEARCH_MODEL.")
        prompt = (f"Use Google Search to find up to {limit} primary research publications for the query below. "
                  "Return a JSON array of exact publication titles. Use only works backed by search results. "
                  f"Publication cutoff: {cutoff or 'none'}.\nQuery: {query}")
        endpoint = "https://generativelanguage.googleapis.com/v1beta/models/" + quote(model, safe="") + ":generateContent"
        body = {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]}
        response = json.loads(fetch(endpoint, "application/json", {"x-goog-api-key": token,
                                    "Content-Type": "application/json"}, json.dumps(body).encode())[0])
        candidate = response["candidates"][0]
        links = [c["web"]["uri"] for c in candidate.get("groundingMetadata", {}).get("groundingChunks", [])
                 if c.get("web", {}).get("uri")]
        if not links:
            raise ValueError("Google returned no grounding sources.")
        answer = "".join(p.get("text", "") for p in candidate["content"]["parts"]).strip()
        titles = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", answer))
        if not isinstance(titles, list) or any(not isinstance(t, str) for t in titles):
            raise ValueError("Google returned an invalid publication list.")
        rows = []
        for title in titles[:limit]:
            for item in query_index("crossref", title, cutoff, 3):
                if normalized(item["title"]).casefold() == normalized(title).casefold():
                    rows.append({**item, "grounding_links": links})
                    break
        return rows
    if provider == "crossref":
        params = {"query.bibliographic": query, "rows": limit}
        if cutoff:
            params["filter"] = "until-pub-date:" + cutoff
        data, _, _ = fetch(CROSSREF + "?" + urlencode(params))
        rows = json.loads(data)["message"]["items"]
        return [{"title": row["title"][0], "doi": row.get("DOI", ""),
                 "date": publication_date(row.get("published", {}).get("date-parts", [[]])[0]),
                 "url": row.get("URL", "")} for row in rows if row.get("title")]
    params = {"query": query, "limit": limit, "fields": "title,year,publicationDate,url,externalIds,openAccessPdf"}
    if cutoff:
        params["year"] = ":" + cutoff[:4]
    token = os.environ.get("S2_API_KEY")
    data, _, _ = fetch(SCHOLAR + "?" + urlencode(params), headers={"x-api-key": token} if token else {})
    return [{"title": row["title"], "doi": (row.get("externalIds") or {}).get("DOI", ""),
             "date": row.get("publicationDate") or publication_date([row["year"]] if row.get("year") else []),
             "url": (row.get("openAccessPdf") or {}).get("url") or row.get("url", "")}
            for row in json.loads(data)["data"] if row.get("title")]


def discover(root, provider="both", limit=5):
    plan = workplan(root)
    searches = []
    cutoff = plan.get("cutoff")
    mode = plan.get("citation_mode", "live")
    providers = ("crossref", "semantic-scholar") if provider == "both" else (provider,)
    incomplete = []
    for section in plan["sections"]:
        for query in section["queries"]:
            result = {"section": section["id"], "query": query, "candidates": [], "errors": {}, "completed": []}
            seen = set()
            if mode == "live":
                for name in providers:
                    try:
                        for paper in query_index(name, query, cutoff, limit):
                            key = (paper["doi"] or paper["title"]).casefold()
                            if key in seen or cutoff and (not paper["date"] or paper["date"] > cutoff):
                                continue
                            result["candidates"].append({**paper, "provider": name})
                            seen.add(key)
                        result["completed"].append(name)
                    except (OSError, ValueError, KeyError) as exc:
                        result["errors"][name] = str(exc)
                if not result["completed"]:
                    incomplete.append(query)
            searches.append(result)
    write_json(root / "discovery.json", {"mode": mode, "plan_hash": json_hash(plan), "searches": searches})
    if incomplete:
        raise ValueError("Discovery failed for: " + ", ".join(incomplete) + ". See discovery.json and retry.")


def verify_one(root, request, plan):
    required_text(request, "key", "section", "title", "url", "quote", "locator", "supports")
    key = identifier(request["key"])
    if request["section"] not in {s["id"] for s in plan["sections"]}:
        raise ValueError(f"{key}: unknown section.")
    fields = ("key", "section", "title", "url", "quote", "locator", "supports", "doi", "author", "year")
    record = {field: request[field].strip() for field in fields if request.get(field)}
    mode = plan.get("citation_mode", "live")
    metadata = {}
    if mode == "fixture":
        record["fixture"] = local_file(root, request["fixture"], "materials")
        raw = (root / record["fixture"]).read_bytes()
        resolved, mime = record["url"], "text/plain"
    else:
        raw, resolved, mime = fetch(record["url"])
    pages, identity = document(raw, mime)
    title = normalized(record["title"]).casefold()
    doi = record.get("doi", "").lower()
    if title not in normalized(identity).casefold() and not (doi and doi in identity.lower()):
        raise ValueError(f"{key}: source does not identify the proposed work.")
    passage = normalized(record["quote"])
    matched = next((idx for idx, page in enumerate(pages, 1) if passage in page), None)
    cited_page = re.search(r"\b(?:page\s+|p\.\s*)(\d+)\b", record["locator"], re.I)
    if raw.startswith(b"%PDF-") and cited_page:
        page = int(cited_page[1])
        matched = page if 1 <= page <= len(pages) and passage in pages[page - 1] else None
    if matched is None:
        raise ValueError(f"{key}: quoted passage was not found in the retrieved source.")
    if doi and mode == "live":
        endpoint = CROSSREF + "/" + quote(doi, safe="/")
        metadata = json.loads(fetch(endpoint, "application/json")[0])["message"]
        registered_titles = [normalized(unescape(t)).casefold() for t in metadata.get("title", [])]
        if metadata["DOI"].lower() != doi or title not in registered_titles:
            raise ValueError(f"{key}: DOI and registered title do not match.")
        entry = fetch(endpoint + "/transform/application/x-bibtex", "application/x-bibtex")[0].decode().strip()
        entry, n = re.subn(r"^\s*(@\w+\s*[{(])[^,\s]+,", lambda m: m[1] + key + ",", entry, count=1)
        if n != 1:
            raise ValueError(f"{key}: provider returned invalid BibTeX.")
    else:
        escapes = str.maketrans({"{": "", "}": "", "\\": "", "&": r"\&", "%": r"\%",
                                 "#": r"\#", "$": r"\$", "_": r"\_"})
        safe_title = record["title"].translate(escapes)
        text = " ".join(pages)
        author = record.get("author", "")
        year = record.get("year", "")
        if author and normalized(author).casefold() not in text.casefold():
            raise ValueError(f"{key}: author is absent from the source.")
        if year and (not re.fullmatch(r"[12]\d{3}", year) or year not in text):
            raise ValueError(f"{key}: year is absent from the source.")
        if any(c in record["url"] for c in "{}\\\r\n"):
            raise ValueError(f"{key}: invalid bibliography URL.")
        label = ("  author = {{" + author.translate(escapes) + "}},\n" if author
                 else "  key = {{" + safe_title + "}},\n")
        entry = ("@misc{" + key + ",\n" + label + "  title = {{" + safe_title + "}},\n  year = {"
                 + (year or "n.d.") + "},\n  url = {" + record["url"] + "}\n}")
    if plan.get("cutoff"):
        parts = metadata.get("published", {}).get("date-parts", [[]])[0]
        published = publication_date(parts)
        if not published:
            published = request.get("published", "")
            dates = re.findall(r"\b(?:published(?:\s+online)?|publication date|issued)\s*[:\-]?\s*"
                               r"(\d{4}-\d{2}-\d{2})", identity + " " + " ".join(pages), re.I)
            if not published or date.fromisoformat(published).isoformat() not in dates:
                raise ValueError(f"{key}: cannot verify publication date for the cutoff.")
        if published > plan["cutoff"]:
            raise ValueError(f"{key}: work is newer than the cutoff.")
        record["published"] = published
    receipt = {"record": record, "mode": mode, "resolved_url": resolved, "source_sha256": digest(raw),
               "page": matched, "bibtex": entry, "retrieved_at": datetime.now(timezone.utc).isoformat()}
    path = "receipts/" + json_hash(receipt) + ".json"
    (root / "receipts").mkdir(exist_ok=True)
    write_json(root / path, receipt)
    return {**record, "verified": True, "receipt": path}, entry


def verify(root):
    plan = workplan(root)
    requests = load_json(root / "citation-requests.json")["sources"]
    if not isinstance(requests, list):
        raise ValueError("sources must be a list.")
    keys = [identifier(item["key"]).casefold() for item in requests]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate requested citation key.")
    results = [verify_one(root, item, plan) for item in requests]
    write_atomic(root / "references.bib", "".join(entry + "\n" for _, entry in results).encode())
    write_json(root / "literature.json", {"request_hash": json_hash(requests), "sources": [r for r, _ in results]})


def verified_sources(root):
    data = load_json(root / "literature.json")
    requests = load_json(root / "citation-requests.json")["sources"]
    if data.get("request_hash") != json_hash(requests):
        raise ValueError("Citation requests changed or lack verification. Run sources.py verify.")
    expected = [identifier(item["key"]) for item in requests]
    if [r.get("key") for r in data["sources"]] != expected or len(set(expected)) != len(expected):
        raise ValueError("Verified records do not match the requested citations.")
    linked, entries = [], []
    mode = workplan(root).get("citation_mode", "live")
    for request, record in zip(requests, data["sources"]):
        if not record.get("receipt"):
            raise ValueError("A verified flag alone is insufficient. Retrieve and verify the source.")
        path = local_file(root, record["receipt"], "receipts")
        receipt = load_json(root / path)
        payload = {k: v for k, v in record.items() if k not in {"receipt", "verified"}}
        if (record.get("verified") is not True or receipt["record"] != payload or receipt["mode"] != mode
                or Path(path).name != json_hash(receipt) + ".json"):
            raise ValueError("Citation evidence changed. Run sources.py verify.")
        for field in ("key", "section", "title", "url", "quote", "locator", "supports", "doi", "author", "year"):
            if payload.get(field, "") != request.get(field, "").strip():
                raise ValueError("Citation request differs from the retrieved evidence.")
        if mode == "fixture":
            fixture = local_file(root, payload["fixture"], "materials")
            if fixture != local_file(root, request["fixture"], "materials") or file_hash(root / fixture) != receipt["source_sha256"]:
                raise ValueError("Citation fixture changed.")
            linked.append(fixture)
        entries.append(receipt["bibtex"] + "\n")
        linked.append(path)
    if (root / "references.bib").read_text(encoding="utf-8") != "".join(entries):
        raise ValueError("Bibliography differs from the verified source records.")
    return set(expected), linked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="action", required=True)
    search = subs.add_parser("search")
    search.add_argument("run", type=Path)
    search.add_argument("--provider", choices=("both", "semantic-scholar", "crossref", "google"), default="both")
    subs.add_parser("verify").add_argument("run", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "search":
            discover(args.run.resolve(), args.provider)
        else:
            verify(args.run.resolve())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
