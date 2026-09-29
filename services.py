import hashlib
import html
import ipaddress
import json
import os
import re
import secrets
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import bleach
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
from slugify import slugify

from config import Config

ALLOWED_ARTICLE_TAGS = [
    "p", "br", "strong", "em", "b", "i", "u",
    "h2", "h3", "h4", "blockquote", "ul", "ol", "li",
    "a", "hr", "code", "pre", "table", "thead", "tbody",
    "tr", "th", "td"
]
ALLOWED_ARTICLE_ATTRIBUTES = {
    "a": ["href", "title", "target", "rel"],
    "th": ["colspan", "rowspan"],
    "td": ["colspan", "rowspan"],
}


def now():
    return datetime.now(timezone.utc)


def oid_text(value):
    return str(value) if value is not None else None


def clean_text(value, max_length=8000):
    if not value:
        return ""
    text = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()[:max_length]


def parse_json_object(raw):
    content = (raw or "").strip()
    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
    content = re.sub(r"\s*```$", "", content)
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("Model response must be a JSON object")
    return data


def strip_html_fence(raw):
    """Extract model-authored HTML and discard commentary outside the fenced block.

    Landing/page prompts deliberately request one ```html ... ``` block. Models can still
    prepend or append prose, so extraction must be structural rather than only trimming a
    fence at the beginning/end of the response.
    """
    text = str(raw or "").strip()
    fenced = list(re.finditer(r"```(?:html|htm)\s*(.*?)```", text, flags=re.I | re.S))
    if fenced:
        candidates = [match.group(1).strip() for match in fenced]
        for candidate in candidates:
            if re.search(r"(?is)<!doctype\s+html|<html(?:\s|>)", candidate):
                return candidate
        return candidates[0] if candidates else ""

    # Graceful fallback for providers that ignore the fence instruction. Keep only the
    # document itself, never explanatory text before/after it.
    start = re.search(r"(?is)<!doctype\s+html|<html(?:\s|>)", text)
    if start:
        fragment = text[start.start():]
        end_matches = list(re.finditer(r"(?is)</html\s*>", fragment))
        if end_matches:
            fragment = fragment[:end_matches[-1].end()]
        return fragment.strip()

    # Article fragments may be fenced as generic HTML snippets rather than documents.
    generic = re.search(r"```(?:[a-z0-9_-]+)?\s*(.*?)```", text, flags=re.I | re.S)
    if generic:
        return generic.group(1).strip()
    return text


def sanitize_article_html(raw):
    raw = strip_html_fence(raw)
    cleaned = bleach.clean(
        raw,
        tags=ALLOWED_ARTICLE_TAGS,
        attributes=ALLOWED_ARTICLE_ATTRIBUTES,
        protocols=["http", "https", "mailto"],
        strip=True,
    )
    return bleach.linkify(cleaned)


def model_client():
    if not Config.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    return OpenAI(api_key=Config.DEEPSEEK_API_KEY, base_url=Config.DEEPSEEK_BASE_URL)


def campaign_for_prompt(campaign):
    def safe(v):
        if isinstance(v, (dict, list)):
            return v
        return clean_text(v, 4000)
    return {
        "id": str(campaign["_id"]),
        "title": safe(campaign.get("title", "")),
        "website_url": safe(campaign.get("website_url", "")),
        "campaign_context": safe(campaign.get("campaign_context", "")),
        "target_audience": safe(campaign.get("target_audience", {})),
        "campaign_goal": safe(campaign.get("campaign_goal", {})),
        "content_overview": safe(campaign.get("content_overview", {})),
        "engagement_engine": safe(campaign.get("engagement_engine", {})),
        "filter_keywords": campaign.get("filter_keywords", []),
        "resources": campaign.get("resources", [])[:20],
    }


def metadata_from_article(article_html, campaign, hook):
    text = clean_text(article_html, 5000)
    title = clean_text(hook.get("title") or campaign.get("title") or "Article", 180)
    description = clean_text(hook.get("description") or text, 300)
    return {
        "title": title,
        "description": description[:160],
        "slug": slugify(title)[:100] or secrets.token_hex(4),
        "generated_at": now(),
    }


def public_article_url(article_id):
    return f"{Config.PUBLIC_BASE_URL}/public/articles/{article_id}"


def stable_guid(entry):
    guid = entry.get("id") or entry.get("link")
    if guid:
        return str(guid)
    material = "|".join([
        str(entry.get("title") or ""),
        str(entry.get("published", entry.get("updated", "")) or ""),
        str(entry.get("summary") or ""),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_feed_url(url):
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("A valid http(s) RSS/Atom URL is required")
    return url


def _validate_public_http_url(url):
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Use a complete http(s) website URL")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("Local/private website addresses cannot be researched")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("The website hostname could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Local/private website addresses cannot be researched")
    return url


def normalize_website_url(url):
    if not (url or "").strip():
        return ""
    return _validate_public_http_url(url)


def research_website(url):
    """Fetch a bounded public website page for campaign context, with SSRF protections."""
    current = _validate_public_http_url(url)
    session = requests.Session()
    headers = {
        "User-Agent": "NewsjackingCore/1.2 (+campaign research)",
        "Accept": "text/html,application/xhtml+xml",
    }
    response = None
    for _ in range(4):
        response = session.get(
            current,
            headers=headers,
            timeout=Config.WEBSITE_RESEARCH_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            target = response.headers.get("Location")
            response.close()
            if not target:
                raise RuntimeError("Website redirected without a destination")
            current = _validate_public_http_url(urljoin(current, target))
            continue
        break
    if response is None:
        raise RuntimeError("Website request failed")
    response.raise_for_status()
    ctype = (response.headers.get("content-type") or "").lower()
    if "html" not in ctype and "text/" not in ctype:
        response.close()
        raise ValueError("The supplied URL does not appear to be an HTML page")
    chunks = []
    size = 0
    for chunk in response.iter_content(65536):
        if not chunk:
            continue
        size += len(chunk)
        if size > Config.WEBSITE_RESEARCH_MAX_BYTES:
            break
        chunks.append(chunk)
    encoding = response.encoding or "utf-8"
    final_url = current
    response.close()
    raw = b"".join(chunks).decode(encoding, errors="replace")
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    title = clean_text(soup.title.string if soup.title and soup.title.string else "", 300)
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    description = clean_text(meta.get("content") if meta else "", 800)
    body_text = clean_text(soup.get_text(" ", strip=True), 18000)
    links = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(final_url, anchor.get("href"))
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != urlparse(final_url).netloc:
            continue
        href = href.split("#", 1)[0]
        if href in seen:
            continue
        seen.add(href)
        links.append({"url": href, "label": clean_text(anchor.get_text(" "), 120)})
        if len(links) >= 20:
            break
    return {
        "url": url,
        "final_url": final_url,
        "title": title,
        "description": description,
        "text_excerpt": body_text,
        "links": links,
        "researched_at": now(),
    }


def _clean_option(option, kind, index):
    option = option if isinstance(option, dict) else {}
    prefixes = {"audience": "aud", "objective": "obj", "blueprint": "blue", "engagement": "eng"}
    doc = {
        "id": f"{prefixes[kind]}-{index + 1}",
        "name": clean_text(option.get("name"), 160) or f"Option {index + 1}",
        "recommended_reason": clean_text(option.get("recommended_reason"), 600),
    }
    if kind == "audience":
        doc["summary"] = clean_text(option.get("summary"), 1200)
        doc["key_points"] = [clean_text(x, 240) for x in (option.get("key_points") or []) if clean_text(x, 240)][:6]
    elif kind == "objective":
        doc["description"] = clean_text(option.get("description"), 1200)
        doc["benefits"] = [clean_text(x, 240) for x in (option.get("benefits") or []) if clean_text(x, 240)][:6]
    elif kind == "blueprint":
        doc["approach"] = clean_text(option.get("approach"), 1600)
        doc["tactics"] = [clean_text(x, 240) for x in (option.get("tactics") or []) if clean_text(x, 240)][:8]
    else:
        doc["method"] = clean_text(option.get("method"), 1200)
        doc["techniques"] = [clean_text(x, 240) for x in (option.get("techniques") or []) if clean_text(x, 240)][:8]
    return doc


def normalize_strategy_suggestions(raw):
    raw = raw if isinstance(raw, dict) else {}
    output = {
        "campaign_summary": clean_text(raw.get("campaign_summary"), 1800),
        "keywords": [clean_text(x, 100) for x in (raw.get("keywords") or []) if clean_text(x, 100)][:15],
    }
    source_keys = {
        "audience": "audience_options",
        "objective": "objective_options",
        "blueprint": "blueprint_options",
        "engagement": "engagement_options",
    }
    for kind, key in source_keys.items():
        items = raw.get(key) if isinstance(raw.get(key), list) else []
        output[key] = [_clean_option(item, kind, i) for i, item in enumerate(items[:4])]
        if not output[key]:
            raise ValueError(f"Model returned no {kind} suggestions")
    recommended = raw.get("recommended") if isinstance(raw.get("recommended"), dict) else {}
    output["recommended"] = {
        "audience": clean_text(recommended.get("audience"), 40) or output["audience_options"][0]["id"],
        "objective": clean_text(recommended.get("objective"), 40) or output["objective_options"][0]["id"],
        "blueprint": clean_text(recommended.get("blueprint"), 40) or output["blueprint_options"][0]["id"],
        "engagement": clean_text(recommended.get("engagement"), 40) or output["engagement_options"][0]["id"],
    }
    valid = {
        "audience": {x["id"] for x in output["audience_options"]},
        "objective": {x["id"] for x in output["objective_options"]},
        "blueprint": {x["id"] for x in output["blueprint_options"]},
        "engagement": {x["id"] for x in output["engagement_options"]},
    }
    for kind in valid:
        if output["recommended"][kind] not in valid[kind]:
            output["recommended"][kind] = next(iter(valid[kind]))
    return output


def strategy_option(suggestions, kind, option_id):
    key = {
        "audience": "audience_options",
        "objective": "objective_options",
        "blueprint": "blueprint_options",
        "engagement": "engagement_options",
    }[kind]
    for option in (suggestions or {}).get(key, []):
        if option.get("id") == option_id:
            return option
    return None


def render_generated_landing_html(page, campaign, article_docs):
    headline = html.escape(page.get("headline") or campaign.get("title") or "Latest coverage")
    subheadline = html.escape(page.get("subheadline") or "")
    intro = html.escape(page.get("intro") or "")
    cards = []
    for article in article_docs:
        meta = article.get("metadata") or {}
        title = html.escape(meta.get("title") or "Article")
        desc = html.escape(meta.get("description") or clean_text(article.get("content"), 220))
        url = html.escape(public_article_url(article["_id"]))
        cards.append(
            f'<article class="story"><h2><a href="{url}">{title}</a></h2>'
            f'<p>{desc}</p><a class="read" href="{url}">Read article</a></article>'
        )
    cards_html = "\n".join(cards) or "<p>No generated articles are attached to this page yet.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{headline}</title>
<style>
body{{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;background:#f7f8fa;color:#15171a}}
main{{max-width:980px;margin:0 auto;padding:72px 24px}}
header{{max-width:760px;margin-bottom:42px}}
h1{{font-size:clamp(38px,7vw,72px);line-height:1.02;letter-spacing:-.04em;margin:0 0 18px}}
.lead{{font-size:20px;line-height:1.6;color:#525866}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}}
.story{{background:white;border:1px solid #e5e7eb;border-radius:18px;padding:24px;box-shadow:0 8px 26px rgba(0,0,0,.04)}}
.story h2{{font-size:22px;line-height:1.25;margin:0 0 12px}}
.story a{{color:inherit;text-decoration:none}}
.story p{{color:#5d6470;line-height:1.6}}
.read{{display:inline-block;margin-top:12px;font-weight:700}}
</style>
</head>
<body><main><header><h1>{headline}</h1><p class="lead">{subheadline or intro}</p></header>
<section class="grid">{cards_html}</section></main></body></html>"""


# ---------------------------------------------------------------------------
# v1.5 editorial + landing-page generation helpers
# ---------------------------------------------------------------------------

LANDING_ARTICLES_SLOT = "[[LANDING_ARTICLES]]"
LANDING_MAX_HTML_BYTES = 2 * 1024 * 1024


def _bounded_list(values, limit=12, item_limit=600):
    result = []
    for value in values or []:
        if isinstance(value, dict):
            result.append(value)
        else:
            text = clean_text(value, item_limit)
            if text:
                result.append(text)
        if len(result) >= limit:
            break
    return result


def research_news_source(url):
    """Fetch bounded article/page context for editorial grounding."""
    if not url:
        return None
    current = _validate_public_http_url(url)
    session = requests.Session()
    response = None
    headers = {
        "User-Agent": "NewsjackingCore/1.5 (+editorial source research)",
        "Accept": "text/html,application/xhtml+xml",
    }
    for _ in range(4):
        response = session.get(current, headers=headers, timeout=Config.WEBSITE_RESEARCH_TIMEOUT_SECONDS,
                               allow_redirects=False, stream=True)
        if response.status_code in {301, 302, 303, 307, 308}:
            target = response.headers.get("Location")
            response.close()
            if not target:
                raise RuntimeError("Source redirected without a destination")
            current = _validate_public_http_url(urljoin(current, target))
            continue
        break
    if response is None:
        raise RuntimeError("Source request failed")
    response.raise_for_status()
    ctype = (response.headers.get("content-type") or "").lower()
    if "html" not in ctype and "text/" not in ctype:
        response.close()
        return {"url": url, "final_url": current, "non_html": True}
    chunks, size = [], 0
    for chunk in response.iter_content(65536):
        if not chunk:
            continue
        size += len(chunk)
        if size > Config.WEBSITE_RESEARCH_MAX_BYTES:
            break
        chunks.append(chunk)
    encoding = response.encoding or "utf-8"
    response.close()
    raw = b"".join(chunks).decode(encoding, errors="replace")
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template", "form"]):
        tag.decompose()

    def meta(*pairs):
        for attr, value in pairs:
            node = soup.find("meta", attrs={attr: re.compile(rf"^{re.escape(value)}$", re.I)})
            if node and node.get("content"):
                return clean_text(node.get("content"), 1200)
        return ""

    title = clean_text(
        meta(("property", "og:title"), ("name", "twitter:title"))
        or (soup.title.string if soup.title and soup.title.string else ""), 500
    )
    description = clean_text(meta(("property", "og:description"), ("name", "description")), 1600)
    author = clean_text(meta(("name", "author"), ("property", "article:author")), 500)
    published = clean_text(meta(("property", "article:published_time"), ("name", "date")), 200)
    site_name = clean_text(meta(("property", "og:site_name")), 300)
    headings = [clean_text(h.get_text(" "), 400) for h in soup.find_all(["h1", "h2", "h3"])][:24]

    source_images, seen_images = [], set()
    for attr, value in (("property", "og:image"), ("name", "twitter:image"), ("property", "twitter:image")):
        node = soup.find("meta", attrs={attr: re.compile(rf"^{re.escape(value)}$", re.I)})
        if node and node.get("content"):
            image_url = urljoin(current, node.get("content").strip())
            if image_url.startswith(("http://", "https://")) and image_url not in seen_images:
                seen_images.add(image_url)
                source_images.append({"url": image_url, "alt": title or description, "source": "source_meta"})
    for node in soup.find_all("img"):
        src = node.get("src") or node.get("data-src") or node.get("data-lazy-src")
        if not src:
            continue
        image_url = urljoin(current, src)
        if not image_url.startswith(("http://", "https://")) or image_url in seen_images:
            continue
        seen_images.add(image_url)
        source_images.append({"url": image_url, "alt": clean_text(node.get("alt"), 300), "source": "source_page"})
        if len(source_images) >= 8:
            break

    article_node = soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs = []
    for p in article_node.find_all(["p", "li"]):
        text = clean_text(p.get_text(" "), 1800)
        if len(text) >= 40:
            paragraphs.append(text)
        if sum(len(x) for x in paragraphs) >= 18000:
            break
    body_excerpt = "\n\n".join(paragraphs)[:20000]
    canonical = ""
    canonical_node = soup.find("link", attrs={"rel": lambda v: v and "canonical" in v})
    if canonical_node and canonical_node.get("href"):
        canonical = urljoin(current, canonical_node.get("href"))
    return {
        "url": url,
        "final_url": current,
        "canonical_url": canonical,
        "title": title,
        "description": description,
        "author": author,
        "published": published,
        "site_name": site_name,
        "headings": headings,
        "images": source_images,
        "image_url": (source_images[0].get("url") if source_images else ""),
        "body_excerpt": body_excerpt,
        "researched_at": now(),
    }


def extract_brand_context_from_html(raw, base_url):
    soup = BeautifulSoup(raw or "", "html.parser")
    images, seen = [], set()
    selectors = [
        ("meta", {"property": "og:image"}, "content"),
        ("meta", {"name": "twitter:image"}, "content"),
    ]
    for tag, attrs, field in selectors:
        node = soup.find(tag, attrs=attrs)
        if node and node.get(field):
            src = urljoin(base_url, node.get(field))
            if src.startswith(("http://", "https://")) and src not in seen:
                seen.add(src)
                images.append({"url": src, "alt": "", "source": "website_meta"})
    for node in soup.find_all("img"):
        src = node.get("src") or node.get("data-src")
        if not src:
            continue
        src = urljoin(base_url, src)
        if not src.startswith(("http://", "https://")) or src in seen:
            continue
        seen.add(src)
        images.append({
            "url": src,
            "alt": clean_text(node.get("alt"), 300),
            "width": clean_text(node.get("width"), 30),
            "height": clean_text(node.get("height"), 30),
            "source": "website",
        })
        if len(images) >= 14:
            break
    colors = []
    theme = soup.find("meta", attrs={"name": re.compile("^theme-color$", re.I)})
    if theme and theme.get("content"):
        colors.append(theme.get("content").strip())
    css_text = " ".join(node.get_text(" ") for node in soup.find_all("style"))[:60000]
    for match in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", css_text):
        normalized = match.lower()
        if normalized not in colors:
            colors.append(normalized)
        if len(colors) >= 10:
            break
    return {"images": images, "colors": colors[:10]}


def research_website(url):
    """v1.5 website research: bounded copy + links + visual/brand context."""
    current = _validate_public_http_url(url)
    session = requests.Session()
    headers = {
        "User-Agent": "NewsjackingCore/1.5 (+campaign research)",
        "Accept": "text/html,application/xhtml+xml",
    }
    response = None
    for _ in range(4):
        response = session.get(current, headers=headers, timeout=Config.WEBSITE_RESEARCH_TIMEOUT_SECONDS,
                               allow_redirects=False, stream=True)
        if response.status_code in {301, 302, 303, 307, 308}:
            target = response.headers.get("Location")
            response.close()
            if not target:
                raise RuntimeError("Website redirected without a destination")
            current = _validate_public_http_url(urljoin(current, target))
            continue
        break
    if response is None:
        raise RuntimeError("Website request failed")
    response.raise_for_status()
    ctype = (response.headers.get("content-type") or "").lower()
    if "html" not in ctype and "text/" not in ctype:
        response.close()
        raise ValueError("The supplied URL does not appear to be an HTML page")
    chunks, size = [], 0
    for chunk in response.iter_content(65536):
        if not chunk:
            continue
        size += len(chunk)
        if size > Config.WEBSITE_RESEARCH_MAX_BYTES:
            break
        chunks.append(chunk)
    encoding = response.encoding or "utf-8"
    response.close()
    raw = b"".join(chunks).decode(encoding, errors="replace")
    original_soup = BeautifulSoup(raw, "html.parser")
    brand = extract_brand_context_from_html(raw, current)
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    title = clean_text(soup.title.string if soup.title and soup.title.string else "", 300)
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    description = clean_text(meta.get("content") if meta else "", 800)
    headings = [clean_text(h.get_text(" "), 360) for h in soup.find_all(["h1", "h2", "h3"])][:24]
    body_text = clean_text(soup.get_text(" ", strip=True), 22000)
    links, seen = [], set()
    for anchor in original_soup.find_all("a", href=True):
        href = urljoin(current, anchor.get("href"))
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != urlparse(current).netloc:
            continue
        href = href.split("#", 1)[0]
        if href in seen:
            continue
        seen.add(href)
        links.append({"url": href, "label": clean_text(anchor.get_text(" "), 120)})
        if len(links) >= 24:
            break
    return {
        "url": url,
        "final_url": current,
        "title": title,
        "description": description,
        "headings": headings,
        "text_excerpt": body_text,
        "links": links,
        "images": brand["images"],
        "colors": brand["colors"],
        "researched_at": now(),
    }


def campaign_for_prompt(campaign):
    def safe(v, max_len=5000):
        if isinstance(v, (dict, list)):
            return v
        return clean_text(v, max_len)
    research = campaign.get("website_research") if isinstance(campaign.get("website_research"), dict) else {}
    return {
        "id": str(campaign["_id"]),
        "title": safe(campaign.get("title", ""), 500),
        "website_url": safe(campaign.get("website_url", ""), 1000),
        "campaign_context": safe(campaign.get("campaign_context", ""), 5000),
        "target_audience": safe(campaign.get("target_audience", {})),
        "campaign_goal": safe(campaign.get("campaign_goal", {})),
        "content_overview": safe(campaign.get("content_overview", {})),
        "engagement_engine": safe(campaign.get("engagement_engine", {})),
        "filter_keywords": _bounded_list(campaign.get("filter_keywords"), 20, 120),
        "resources": _bounded_list(campaign.get("resources"), 20),
        "website_research": {
            "title": clean_text(research.get("title"), 500),
            "description": clean_text(research.get("description"), 1200),
            "headings": _bounded_list(research.get("headings"), 20, 350),
            "text_excerpt": clean_text(research.get("text_excerpt"), 16000),
            "images": _bounded_list(research.get("images"), 10),
            "colors": _bounded_list(research.get("colors"), 10, 30),
        } if research else {},
    }


def product_for_prompt(product):
    """Return a bounded, factual product brief for article matching/generation."""
    def safe(v, max_len=5000):
        if isinstance(v, (dict, list)):
            return v
        return clean_text(v, max_len)
    return {
        "id": str(product.get("_id") or ""),
        "name": safe(product.get("name"), 300),
        "tagline": safe(product.get("tagline"), 600),
        "description": safe(product.get("description"), 5000),
        "positioning": safe(product.get("positioning"), 5000),
        "product_url": safe(product.get("product_url"), 1200),
        "cta_label": safe(product.get("cta_label"), 160),
        "cta_url": safe(product.get("cta_url") or product.get("product_url"), 1200),
        "proof_points": _bounded_list(product.get("proof_points"), 20, 500),
        "claims_to_avoid": _bounded_list(product.get("claims_to_avoid"), 20, 500),
        "evidence_snapshot": safe(product.get("evidence_snapshot"), 24000),
    }


def article_quality_audit(article_html, plan=None):
    soup = BeautifulSoup(article_html or "", "html.parser")
    text = clean_text(article_html, 30000)
    words = re.findall(r"\b[\w’'-]+\b", text)
    headings = soup.find_all(["h2", "h3"])
    links = soup.find_all("a", href=True)
    score = 100
    issues = []
    if len(words) < 700:
        score -= 20
        issues.append("Article is shorter than the preferred editorial depth.")
    if len(headings) < 3:
        score -= 15
        issues.append("Article needs a clearer section structure.")
    if len(soup.find_all("p")) < 6:
        score -= 10
        issues.append("Article has too few readable paragraphs.")
    if not links:
        score -= 5
        issues.append("No supporting link is present.")
    if plan and isinstance(plan, dict):
        desired = len(plan.get("sections") or [])
        if desired >= 4 and len(headings) < min(4, desired):
            score -= 10
            issues.append("The final article does not fully reflect the planned section depth.")
    return {
        "score": max(0, score),
        "word_count": len(words),
        "heading_count": len(headings),
        "link_count": len(links),
        "issues": issues,
    }


def landing_visual_assets(campaign):
    assets, seen = [], set()
    research = campaign.get("website_research") if isinstance(campaign.get("website_research"), dict) else {}
    for image in research.get("images") or []:
        if not isinstance(image, dict):
            continue
        url = str(image.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        assets.append({
            "url": url,
            "alt": clean_text(image.get("alt"), 300),
            "source": image.get("source") or "campaign_website",
        })
        if len(assets) >= Config.LANDING_MAX_VISUAL_ASSETS:
            break
    for resource in campaign.get("resources") or []:
        if len(assets) >= Config.LANDING_MAX_VISUAL_ASSETS:
            break
        if not isinstance(resource, dict):
            continue
        url = str(resource.get("url") or resource.get("image_url") or "").strip()
        kind = str(resource.get("type") or resource.get("kind") or "").lower()
        if url.startswith(("http://", "https://")) and url not in seen and ("image" in kind or re.search(r"\.(?:png|jpe?g|webp|gif)(?:\?|$)", url, re.I)):
            seen.add(url)
            assets.append({"url": url, "alt": clean_text(resource.get("label") or resource.get("name"), 300), "source": "campaign_resource"})
    return assets


def ensure_article_slot(document):
    document = strip_html_fence(document)
    if LANDING_ARTICLES_SLOT in document:
        # keep only one slot
        first = document.find(LANDING_ARTICLES_SLOT)
        before = document[:first + len(LANDING_ARTICLES_SLOT)]
        after = document[first + len(LANDING_ARTICLES_SLOT):].replace(LANDING_ARTICLES_SLOT, "")
        return before + after
    soup = BeautifulSoup(document, "html.parser")
    target = soup.body or soup
    section = soup.new_tag("section")
    section["id"] = "articles"
    section["class"] = ["landing-articles"]
    section.string = LANDING_ARTICLES_SLOT
    target.append(section)
    return str(soup)


def sanitize_landing_html(document):
    """Sanitize a complete generated document while retaining self-contained CSS."""
    document = strip_html_fence(document)
    soup = BeautifulSoup(document, "html.parser")
    for node in soup.find_all(["script", "iframe", "object", "embed", "form", "input", "button", "textarea", "select", "video", "audio", "canvas", "base", "frame", "frameset", "foreignobject"]):
        node.decompose()
    for link in soup.find_all("link"):
        link.decompose()
    for meta in list(soup.find_all("meta")):
        if str(meta.get("http-equiv") or "").lower() == "refresh":
            meta.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            low = attr.lower()
            if low.startswith("on") or low in {"srcdoc", "formaction"}:
                del tag.attrs[attr]
                continue
            value = tag.attrs.get(attr)
            if low == "style" and isinstance(value, str):
                if re.search(r"(?i)(?:javascript:|expression\s*\(|behavior\s*:|-moz-binding)", value):
                    del tag.attrs[attr]
                    continue
            if low in {"href", "src", "poster"} and isinstance(value, str):
                v = value.strip().lower()
                if v.startswith(("javascript:", "vbscript:")):
                    del tag.attrs[attr]
                elif v.startswith("data:") and not (low == "src" and v.startswith("data:image/")):
                    del tag.attrs[attr]
    for style in soup.find_all("style"):
        css = style.get_text()[:250000]
        css = re.sub(r"@import[^;]+;", "", css, flags=re.I)
        css = re.sub(r"url\(\s*['\"]?javascript:[^)]+\)", "none", css, flags=re.I)
        style.string = css
    # Only serialize the document root. This guarantees model commentary outside <html>
    # can never be stored/rendered even if an upstream provider ignored the fenced contract.
    result = str(soup.html) if soup.html else str(soup)
    if not re.match(r"(?is)^\s*<!doctype", result):
        result = "<!doctype html>\n" + result
    if len(result.encode("utf-8")) > LANDING_MAX_HTML_BYTES:
        raise ValueError("Generated HTML exceeded the landing-page size limit")
    return result


def render_landing_article_cards(article_docs, article_base_url=None):
    cards = []
    base = (article_base_url or "").rstrip("/")
    for article in article_docs:
        meta = article.get("metadata") or {}
        title = html.escape(str(meta.get("title") or "Article"))
        desc = html.escape(str(meta.get("description") or meta.get("summary") or clean_text(article.get("content"), 260)))
        if base:
            url = f"{base}/articles/{article['_id']}"
        else:
            url = public_article_url(article["_id"])
        url = html.escape(url)
        cards.append(
            '<article class="landing-article-card">'
            f'<a class="landing-article-card__link" href="{url}">'
            '<div class="landing-article-card__body">'
            f'<h3>{title}</h3><p>{desc}</p><span>Read article →</span>'
            '</div></a></article>'
        )
    if not cards:
        return '<div class="landing-articles-empty">New campaign stories will appear here as Newsjacking publishes them.</div>'
    return '<div class="landing-articles-grid">' + "".join(cards) + '</div>'


def _inject_landing_metadata(document, metadata, public_url=""):
    soup = BeautifulSoup(document, "html.parser")
    if not soup.html:
        wrapper = BeautifulSoup("<!doctype html><html><head></head><body></body></html>", "html.parser")
        wrapper.body.append(soup)
        soup = wrapper
    if not soup.head:
        head = soup.new_tag("head")
        soup.html.insert(0, head)
    head = soup.head
    # Replace title/SEO/social metadata with application-owned values.
    for node in list(head.find_all("title")):
        node.decompose()
    for node in list(head.find_all("meta")):
        name = str(node.get("name") or "").lower()
        prop = str(node.get("property") or "").lower()
        if node.get("data-njs-meta") == "1" or name in {"description", "keywords", "robots", "twitter:card", "twitter:title", "twitter:description"} or prop.startswith("og:"):
            node.decompose()
    for node in list(head.find_all("link")):
        rel = [str(x).lower() for x in (node.get("rel") or [])]
        if node.get("data-njs-meta") == "1" or "canonical" in rel:
            node.decompose()
    for node in list(head.find_all("script", attrs={"data-njs-meta": "1"})):
        node.decompose()
    title = soup.new_tag("title")
    title["data-njs-meta"] = "1"
    title.string = str(metadata.get("title") or "Landing page")
    head.append(title)
    for name, value in [
        ("description", metadata.get("meta_description")),
        ("keywords", ", ".join(metadata.get("keywords") or [])),
        ("robots", "index,follow"),
    ]:
        if not value:
            continue
        node = soup.new_tag("meta")
        node["name"] = name
        node["content"] = str(value)
        node["data-njs-meta"] = "1"
        head.append(node)
    for prop, value in [
        ("og:type", "website"),
        ("og:title", metadata.get("title")),
        ("og:description", metadata.get("meta_description")),
        ("og:url", public_url),
    ]:
        if not value:
            continue
        node = soup.new_tag("meta")
        node["property"] = prop
        node["content"] = str(value)
        node["data-njs-meta"] = "1"
        head.append(node)
    for name, value in [
        ("twitter:card", "summary"),
        ("twitter:title", metadata.get("title")),
        ("twitter:description", metadata.get("meta_description")),
    ]:
        if not value:
            continue
        node = soup.new_tag("meta")
        node["name"] = name
        node["content"] = str(value)
        node["data-njs-meta"] = "1"
        head.append(node)
    if public_url:
        canonical = soup.new_tag("link")
        canonical["rel"] = "canonical"
        canonical["href"] = public_url
        canonical["data-njs-meta"] = "1"
        head.append(canonical)
        structured = {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": metadata.get("title") or "Landing page",
            "description": metadata.get("meta_description") or "",
            "url": public_url,
            "keywords": metadata.get("keywords") or [],
            "inLanguage": metadata.get("language") or "en",
        }
        script = soup.new_tag("script", type="application/ld+json")
        script["data-njs-meta"] = "1"
        script.string = json.dumps(structured, ensure_ascii=False).replace("</", "<\\/")
        head.append(script)
    return "<!doctype html>\n" + str(soup).replace("<!DOCTYPE html>", "").replace("<!doctype html>", "")


def _safe_site_color(value, fallback):
    value = str(value or "").strip()
    return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback


def _inject_site_chrome(document, site=None, navigation=None, current_path=""):
    """Inject one application-owned header/footer across every page in a website.

    Generated page HTML remains page-specific. The website's navigation is a shared
    manifest rendered at request time, so adding /about can update the homepage and
    every sibling page immediately without regenerating their body content.
    """
    if not site:
        return document
    soup = BeautifulSoup(document or "", "html.parser")
    if not soup.html:
        return document
    if not soup.body:
        body = soup.new_tag("body")
        soup.html.append(body)
    for node in list(soup.find_all(attrs={"data-njs-site-chrome": "1"})):
        node.decompose()

    system = site.get("design_system") if isinstance(site.get("design_system"), dict) else {}
    colors = system.get("colors") if isinstance(system.get("colors"), dict) else {}
    typography = system.get("typography") if isinstance(system.get("typography"), dict) else {}
    layout = system.get("layout") if isinstance(system.get("layout"), dict) else {}
    bg = _safe_site_color(colors.get("background"), "#ffffff")
    surface = _safe_site_color(colors.get("surface"), "#ffffff")
    text = _safe_site_color(colors.get("text"), "#171717")
    muted = _safe_site_color(colors.get("muted"), "#666666")
    accent = _safe_site_color(colors.get("accent"), "#1f5eff")
    border = _safe_site_color(colors.get("border"), "#e7e7e7")
    type_hint = (clean_text(typography.get("body"), 180) + " " + clean_text(typography.get("heading"), 180)).lower()
    font_stack = 'Georgia,"Times New Roman",serif' if "serif" in type_hint and "sans" not in type_hint else 'Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'
    max_width = str(layout.get("max_width") or "1180px").strip()
    if not re.fullmatch(r"(?:[7-9]\d\d|1[0-4]\d\d)px", max_width):
        max_width = "1180px"
    radius = str(layout.get("radius") or "16px").strip()
    if not re.fullmatch(r"(?:[4-9]|[12]\d|3[0-2])px", radius):
        radius = "16px"
    brand = clean_text(site.get("brand_name") or site.get("name"), 100) or "Website"
    nav_rows = [row for row in (navigation or []) if isinstance(row, dict) and row.get("href")]
    home_href = next((row.get("href") for row in nav_rows if row.get("path") == "/"), None) or (nav_rows[0].get("href") if nav_rows else "/")

    style = soup.new_tag("style")
    style["data-njs-site-chrome"] = "1"
    style.string = f"""
:root{{--njs-site-bg:{bg};--njs-site-surface:{surface};--njs-site-text:{text};--njs-site-muted:{muted};--njs-site-accent:{accent};--njs-site-border:{border};--njs-site-radius:{radius};--njs-site-max:{max_width};}}
.njs-site-header{{position:relative;z-index:40;background:color-mix(in srgb,var(--njs-site-surface) 94%,transparent);color:var(--njs-site-text);border-bottom:1px solid var(--njs-site-border);font-family:{font_stack};}}
.njs-site-header__inner{{max-width:var(--njs-site-max);margin:0 auto;min-height:72px;padding:0 24px;display:flex;align-items:center;justify-content:space-between;gap:28px;}}
.njs-site-brand{{color:var(--njs-site-text);text-decoration:none;font-weight:760;letter-spacing:-.025em;font-size:1.02rem;white-space:nowrap;}}
.njs-site-nav{{display:flex;align-items:center;justify-content:flex-end;gap:6px;flex-wrap:wrap;}}
.njs-site-nav a{{color:var(--njs-site-muted);text-decoration:none;padding:9px 12px;border-radius:10px;font-size:.92rem;font-weight:610;transition:background .15s ease,color .15s ease;}}
.njs-site-nav a:hover,.njs-site-nav a:focus-visible{{background:color-mix(in srgb,var(--njs-site-accent) 9%,transparent);color:var(--njs-site-text);outline:none;}}
.njs-site-nav a[aria-current="page"]{{background:color-mix(in srgb,var(--njs-site-accent) 11%,transparent);color:var(--njs-site-accent);}}
.njs-site-footer{{background:var(--njs-site-bg);color:var(--njs-site-muted);border-top:1px solid var(--njs-site-border);font-family:{font_stack};}}
.njs-site-footer__inner{{max-width:var(--njs-site-max);margin:0 auto;padding:30px 24px;display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap;font-size:.86rem;}}
.njs-site-footer__links{{display:flex;gap:16px;flex-wrap:wrap;}}
.njs-site-footer a{{color:inherit;text-decoration:none;}}.njs-site-footer a:hover{{color:var(--njs-site-text);}}
@media(max-width:720px){{.njs-site-header__inner{{align-items:flex-start;flex-direction:column;padding-top:18px;padding-bottom:16px;gap:12px;}}.njs-site-nav{{width:100%;justify-content:flex-start;overflow-x:auto;flex-wrap:nowrap;padding-bottom:2px;}}.njs-site-nav a{{white-space:nowrap;}}}}
"""
    if soup.head:
        soup.head.append(style)
    else:
        soup.html.insert(0, style)

    header = soup.new_tag("header")
    header["class"] = "njs-site-header"
    header["data-njs-site-chrome"] = "1"
    inner = soup.new_tag("div"); inner["class"] = "njs-site-header__inner"
    brand_link = soup.new_tag("a", href=str(home_href)); brand_link["class"] = "njs-site-brand"; brand_link.string = brand
    inner.append(brand_link)
    nav = soup.new_tag("nav"); nav["class"] = "njs-site-nav"; nav["aria-label"] = "Website"
    for row in nav_rows:
        link = soup.new_tag("a", href=str(row.get("href")))
        if str(row.get("path") or "") == str(current_path or ""):
            link["aria-current"] = "page"
        link.string = clean_text(row.get("label"), 80) or "Page"
        nav.append(link)
    inner.append(nav); header.append(inner)
    soup.body.insert(0, header)

    footer = soup.new_tag("footer")
    footer["class"] = "njs-site-footer"; footer["data-njs-site-chrome"] = "1"
    f_inner = soup.new_tag("div"); f_inner["class"] = "njs-site-footer__inner"
    copy = soup.new_tag("span"); copy.string = f"© {datetime.now(timezone.utc).year} {brand}"
    f_inner.append(copy)
    links = soup.new_tag("div"); links["class"] = "njs-site-footer__links"
    for row in nav_rows:
        link = soup.new_tag("a", href=str(row.get("href"))); link.string = clean_text(row.get("label"), 80) or "Page"; links.append(link)
    f_inner.append(links); footer.append(f_inner); soup.body.append(footer)
    result = str(soup)
    if not re.match(r"(?is)^\s*<!doctype", result):
        result = "<!doctype html>\n" + result
    return result



def sanitize_manual_landing_html(document: str) -> str:
    """Sanitize operator-authored landing HTML while preserving self-contained CSS."""
    raw = str(document or "").strip()
    if not raw:
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    for tag in list(soup.find_all(["script", "iframe", "object", "embed", "base"])):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if str(attr).lower().startswith("on"):
                del tag.attrs[attr]
        for attr in ("href", "src", "action", "formaction"):
            if attr not in tag.attrs:
                continue
            value = str(tag.attrs.get(attr) or "").strip()
            lower = value.lower()
            if lower.startswith("javascript:") or lower.startswith("vbscript:"):
                tag.attrs[attr] = "#" if attr == "href" else ""
            elif lower.startswith("data:") and attr != "src":
                tag.attrs[attr] = ""
    output = str(soup)
    if not re.match(r"(?is)^\s*<!doctype", output):
        output = "<!doctype html>\n" + output
    return output


def manual_landing_document(
    title: str,
    headline: str = "",
    subheadline: str = "",
    body: str = "",
    cta_label: str = "",
    cta_url: str = "#",
) -> str:
    """Create a clean responsive starter page for human-authored landing pages."""
    title = clean_text(title, 300) or "Landing page"
    headline = clean_text(headline, 500) or title
    subheadline = clean_text(subheadline, 1200)
    body = clean_text(body, 12000)
    cta_label = clean_text(cta_label, 120) or "Learn more"
    cta_url = str(cta_url or "#").strip() or "#"
    from html import escape
    title_e = escape(title)
    headline_e = escape(headline)
    sub_e = escape(subheadline)
    body_e = "".join(f"<p>{escape(p.strip())}</p>" for p in body.split("\n") if p.strip())
    cta_label_e = escape(cta_label)
    cta_url_e = escape(cta_url, quote=True)
    subtitle_html = f'<p class="lead">{sub_e}</p>' if sub_e else ""
    body_html = body_e or '<p>Start editing this page from Landing Page Studio.</p>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title_e}</title>
<style>
:root{{--page-bg:#f6f7f9;--card:#fff;--text:#15171a;--muted:#626971;--line:#e1e5e9;--accent:#0a66ff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--page-bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.6}}
main{{max-width:1120px;margin:0 auto;padding:88px 28px}}.hero{{background:var(--card);border:1px solid var(--line);border-radius:24px;padding:clamp(34px,7vw,76px);box-shadow:0 18px 60px rgba(15,23,42,.07)}}
h1{{font-size:clamp(2.35rem,6vw,5.4rem);line-height:1.02;letter-spacing:-.055em;margin:0;max-width:920px}}.lead{{font-size:clamp(1.05rem,2vw,1.35rem);color:var(--muted);max-width:760px;margin:26px 0 0}}
.copy{{max-width:760px;margin-top:38px;font-size:1.04rem}}.cta{{display:inline-flex;margin-top:30px;padding:13px 20px;border-radius:12px;background:var(--accent);color:#fff;text-decoration:none;font-weight:700}}
@media(max-width:700px){{main{{padding:28px 16px}}.hero{{padding:30px 22px;border-radius:18px}}}}
</style>
</head>
<body><main><section class="hero"><h1>{headline_e}</h1>{subtitle_html}<div class="copy">{body_html}</div><a class="cta" href="{cta_url_e}">{cta_label_e}</a></section></main></body>
</html>"""

def compile_landing_html(page, article_docs, public=False, public_url=None, article_base_url=None, site=None, navigation=None, current_path=""):
    document = page.get("html") or ""
    cards = render_landing_article_cards(article_docs, article_base_url=article_base_url)
    document = document.replace(LANDING_ARTICLES_SLOT, cards)
    document = _inject_site_chrome(document, site=site, navigation=navigation, current_path=current_path)
    metadata = page.get("metadata") or {}
    if public and not public_url:
        public_url = f"{Config.PUBLIC_BASE_URL}/p/{page.get('public_id')}"
    return _inject_landing_metadata(document, metadata, public_url=public_url or "")


def landing_metadata_fallback(document, campaign, page):
    soup = BeautifulSoup(document or "", "html.parser")
    h1 = soup.find("h1")
    visible = clean_text(soup.get_text(" "), 6000)
    title = clean_text((h1.get_text(" ") if h1 else "") or page.get("title") or campaign.get("title"), 70)
    desc = clean_text(page.get("prompt") or campaign.get("campaign_context") or visible, 170)
    return {
        "title": title or "Campaign",
        "meta_description": desc[:165],
        "summary": desc[:320],
        "primary_keyword": clean_text((campaign.get("filter_keywords") or [campaign.get("title") or "campaign"])[0], 120),
        "keywords": _bounded_list(campaign.get("filter_keywords"), 15, 120),
        "language": "en",
        "generated_at": now(),
    }


def landing_quality_audit(document, visual_assets=None, require_article_slot=True):
    soup = BeautifulSoup(document or "", "html.parser")
    text = clean_text(document, 30000)
    score, issues = 100, []
    if not soup.find("h1"):
        score -= 20; issues.append("Missing a clear H1.")
    if require_article_slot and LANDING_ARTICLES_SLOT not in document:
        score -= 25; issues.append("Missing the dynamic article slot.")
    if not soup.find("style"):
        score -= 15; issues.append("Missing self-contained styling.")
    if not soup.find(["a"], href=True):
        score -= 10; issues.append("Missing a clear CTA/link.")
    if "@media" not in document:
        score -= 10; issues.append("No explicit responsive breakpoint was generated.")
    if len(text.split()) < 180:
        score -= 10; issues.append("Landing page is unusually sparse.")
    if visual_assets:
        used = any(str(a.get("url")) in document for a in visual_assets if isinstance(a, dict) and a.get("url"))
        if not used:
            score -= 10; issues.append("Supplied visual assets were not used.")
    return {"score": max(0, score), "issues": issues}



def enforce_landing_asset_urls(document, visual_assets, campaign=None):
    """Enforce discovered image assets and bounded campaign CTA destinations."""
    allowed = {str(item.get("url")) for item in (visual_assets or []) if isinstance(item, dict) and item.get("url")}
    allowed_hosts = set()
    campaign = campaign or {}
    for value in [campaign.get("website_url")]:
        if value:
            host = urlparse(str(value)).netloc.lower()
            if host:
                allowed_hosts.add(host)
    research = campaign.get("website_research") if isinstance(campaign.get("website_research"), dict) else {}
    for link in research.get("links") or []:
        if isinstance(link, dict) and link.get("url"):
            host = urlparse(str(link["url"])).netloc.lower()
            if host:
                allowed_hosts.add(host)
    soup = BeautifulSoup(document or "", "html.parser")
    for img in list(soup.find_all("img")):
        src = str(img.get("src") or "").strip()
        if not src or src not in allowed:
            img.decompose()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if href.startswith(("#", "/", "mailto:")):
            continue
        parsed = urlparse(href)
        if parsed.scheme in {"http", "https"} and parsed.netloc.lower() in allowed_hosts:
            anchor["rel"] = "noopener"
            continue
        # Unknown outbound destinations are converted to the article section rather than trusted.
        anchor["href"] = "#articles"
    def scrub_css_urls(css):
        def repl(match):
            raw = match.group(1).strip(" \t\r\n'\"")
            if raw.startswith(("http://", "https://")) and raw not in allowed:
                return "none"
            if raw.lower().startswith(("javascript:", "data:")):
                return "none"
            return match.group(0)
        return re.sub(r"url\(([^)]+)\)", repl, css, flags=re.I)
    for style in soup.find_all("style"):
        style.string = scrub_css_urls(style.get_text())
    for tag in soup.find_all(style=True):
        tag["style"] = scrub_css_urls(str(tag.get("style") or ""))
    result = str(soup)
    if not re.match(r"(?is)^\s*<!doctype", result):
        result = "<!doctype html>\n" + result
    return result
