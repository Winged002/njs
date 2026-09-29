import base64
import hashlib
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from config import Config


def _now():
    return datetime.now(timezone.utc)


def _plain(value, limit=500):
    text = BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _model_candidates(preferred):
    values = [preferred, "gpt-image-2", "gpt-image-1.5", "gpt-image-1"]
    result = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _generate_image_bytes(model, prompt, size, quality, output_format, compression):
    """Call the OpenAI Images REST endpoint directly.

    This deliberately avoids relying on the installed Python SDK's generated method
    signature. NJS historically pins the SDK for the rest of the application; the REST
    call keeps the image pipeline compatible with newer GPT Image parameters.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
        "n": 1,
        "output_format": output_format,
    }
    if output_format in {"jpeg", "webp"}:
        payload["output_compression"] = compression
    response = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={
            "Authorization": f"Bearer {Config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=180,
    )
    if response.status_code >= 400:
        detail = ""
        try:
            data = response.json()
            detail = str((data.get("error") or {}).get("message") or data)[:1200]
        except Exception:
            detail = response.text[:1200]
        raise RuntimeError(f"OpenAI Images HTTP {response.status_code}: {detail}")
    data = response.json()
    rows = data.get("data") or []
    if not rows:
        raise RuntimeError("OpenAI Images returned no data rows")
    item = rows[0] or {}
    encoded = item.get("b64_json")
    if encoded:
        try:
            binary = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise RuntimeError(f"OpenAI Images returned invalid base64: {exc}") from exc
        if len(binary) < 1024:
            raise RuntimeError(f"OpenAI Images returned unexpectedly small image bytes ({len(binary)})")
        return binary
    url = item.get("url")
    if url:
        fetched = requests.get(url, timeout=90)
        fetched.raise_for_status()
        if len(fetched.content) < 1024:
            raise RuntimeError(f"OpenAI Images URL returned unexpectedly small image bytes ({len(fetched.content)})")
        return fetched.content
    raise RuntimeError("OpenAI Images returned neither b64_json nor url")


def generate_openai_image_asset(
    *,
    kind,
    entity_id,
    scene_prompt,
    alt_text,
    output_dir,
    url_prefix,
    model=None,
    size="1536x1024",
    quality="medium",
    output_format="jpeg",
    compression=88,
    extra_meta=None,
):
    if not Config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required for image generation")
    preferred = model or getattr(Config, "ARTICLE_IMAGE_MODEL", None) or getattr(Config, "SOCIAL_IMAGE_MODEL", None) or "gpt-image-2"
    size = str(size or "1536x1024").strip() or "1536x1024"
    quality = quality if quality in {"low", "medium", "high", "auto"} else "medium"
    output_format = output_format if output_format in {"png", "jpeg", "webp"} else "jpeg"
    compression = max(0, min(int(compression), 100))

    errors = []
    binary = None
    used_model = None
    for candidate in _model_candidates(preferred):
        try:
            binary = _generate_image_bytes(candidate, scene_prompt, size, quality, output_format, compression)
            used_model = candidate
            break
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    if not binary:
        raise RuntimeError("All OpenAI image models failed: " + " | ".join(errors)[:4000])

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(binary).hexdigest()[:16]
    extension = {"jpeg": "jpg", "webp": "webp", "png": "png"}.get(output_format, "png")
    filename = f"{kind}-{entity_id}-{digest}.{extension}"
    temp = output_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
    final = output_dir / filename
    temp.write_bytes(binary)
    os.replace(temp, final)
    if not final.exists() or final.stat().st_size < 1024:
        raise RuntimeError("Generated image file was not persisted correctly")

    asset = {
        "url": f"{url_prefix.rstrip('/')}/{filename}",
        "filename": filename,
        "prompt": scene_prompt,
        "alt": _plain(alt_text or f"{kind.title()} image", 500),
        "model": used_model,
        "size": size,
        "quality": quality,
        "format": output_format,
        "bytes": final.stat().st_size,
        "generated_at": _now(),
        "source": "openai_generated",
    }
    if errors:
        asset["model_fallbacks"] = errors
    if isinstance(extra_meta, dict):
        asset.update(extra_meta)
    return asset


_ARTICLE_FIGURE_CLASSES = ["article-visual"]


def _append_figure(soup, anchor, asset, *, role, loading="lazy"):
    figure = soup.new_tag("figure")
    figure["class"] = ["article-visual", f"article-visual-{role}"]
    figure["data-asset-source"] = str(asset.get("source") or role)
    figure["data-asset-role"] = role
    img = soup.new_tag("img")
    img["src"] = str(asset.get("url") or "").strip()
    img["alt"] = _plain(asset.get("alt") or asset.get("title") or "Editorial image", 300)
    img["loading"] = loading
    figure.append(img)
    caption_text = _plain(asset.get("caption") or asset.get("title") or "", 220)
    if caption_text:
        caption = soup.new_tag("figcaption")
        caption.string = caption_text
        figure.append(caption)
    if getattr(anchor, "name", None) in {"body", "[document]"}:
        anchor.append(figure)
    else:
        anchor.insert_after(figure)
    return figure


def insert_article_media_assets(article_html, hero_asset=None, collection_assets=None, max_collection_images=2):
    """Inject only application-approved media URLs into saved article HTML.

    Model-authored figure/img elements are removed first. The generated/fallback hero is
    inserted after the opening paragraph and Collection evidence is placed after later
    section headings. This keeps article.content self-contained and monolith-compatible.
    """
    soup = BeautifulSoup(article_html or "", "html.parser")
    # Never trust model-authored media markup. All article media is injected from verified
    # application assets after text generation. This also removes empty <img alt=...> placeholders.
    for figure in list(soup.find_all("figure")):
        figure.decompose()
    for img in list(soup.find_all("img")):
        img.decompose()

    hero = hero_asset if isinstance(hero_asset, dict) and str(hero_asset.get("url") or "").strip() else None
    collections = [
        asset for asset in (collection_assets or [])
        if isinstance(asset, dict) and str(asset.get("url") or "").strip()
    ][: max(0, int(max_collection_images or 0))]

    paragraphs = soup.find_all("p")
    headings = soup.find_all(["h2", "h3"])
    root = soup.body or soup

    if hero:
        hero_anchor = paragraphs[0] if paragraphs else (headings[0] if headings else root)
        _append_figure(soup, hero_anchor, hero, role="generated" if hero.get("source") == "openai_generated" else "hero", loading="eager")

    for index, asset in enumerate(collections):
        if headings:
            anchor = headings[min(index, len(headings) - 1)]
        elif paragraphs:
            anchor = paragraphs[min(index + 1, len(paragraphs) - 1)]
        else:
            anchor = root
        _append_figure(soup, anchor, asset, role="collection", loading="lazy")

    return str(soup)


def insert_inline_collection_images(article_html, image_assets, max_images=2):
    return insert_article_media_assets(
        article_html, hero_asset=None, collection_assets=image_assets,
        max_collection_images=max_images,
    )
