import base64
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from html import unescape
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pymongo
import requests
from bs4 import BeautifulSoup
from bson import ObjectId
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont

from config import Config
from db import (
    articles, campaigns, hooks, social_media_posts, social_generation_jobs,
    landing_pages, domain_routes, domain_mappings,
)
from services import clean_text, model_client, now

logger = logging.getLogger(__name__)

SOCIAL_PLATFORM_RULES = {
    "linkedin": {
        "label": "LinkedIn", "max_chars": 3000, "hashtags": (3, 5),
        "instruction": "Professional editorial voice, strong first line, short paragraphs, one useful insight, discussion-oriented CTA.",
    },
    "x": {
        "label": "X", "max_chars": 280, "hashtags": (0, 2),
        "instruction": "Compact post, front-load the news angle, remove filler, leave room for the article link; never create a thread.",
    },
    "facebook": {
        "label": "Facebook", "max_chars": 5000, "hashtags": (1, 4),
        "instruction": "Accessible conversational lead, explain why the story matters, finish with a question or clear action.",
    },
    "instagram": {
        "label": "Instagram", "max_chars": 2200, "hashtags": (5, 12),
        "instruction": "Visual opening line, readable line breaks, save/share CTA; do not place a raw article URL in the caption, say link in bio.",
    },
    "threads": {
        "label": "Threads", "max_chars": 500, "hashtags": (0, 3),
        "instruction": "Natural and timely, focus on one observation and invite a response; do not create a multi-post thread.",
    },
    "bluesky": {
        "label": "Bluesky", "max_chars": 300, "hashtags": (0, 2),
        "instruction": "Concise, information-dense, human; state the news value before the CTA.",
    },
}


def social_defaults():
    return {
        "enabled": False,
        "platforms": ["linkedin", "x"],
        "auto_generate_newsjacking": True,
        "image_enabled": True,
        "auto_schedule": False,
        "posting_weekdays": [0, 1, 2, 3, 4],
        "timezone": "Europe/Skopje",
        "default_post_time": "09:00",
        "schedule_delay_minutes": 15,
        "platform_stagger_minutes": 5,
        "brand_voice": "Clear, useful, confident, and factual.",
        "image_style": (
            "Premium contemporary editorial photography; natural, believable, visually simple, "
            "strong single focal point, restrained palette, magazine-quality art direction."
        ),
        "image_text_enabled": True,
        "image_headline_max_chars": 105,
        "image_footer": "",
        "image_accent_color": "#149FE8",
        "image_text_area_ratio": 0.36,
    }


def social_settings(campaign):
    settings = social_defaults()
    stored = campaign.get("social_calendar") or {}
    if isinstance(stored, dict):
        for key in settings:
            if key in stored:
                settings[key] = stored[key]
    settings["platforms"] = [p for p in settings.get("platforms", []) if p in SOCIAL_PLATFORM_RULES]
    return settings


def _plain(value, max_length=18000):
    if not value:
        return ""
    text = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:max_length]


def _article_title(article, hook=None):
    metadata = article.get("metadata") or {}
    return str(metadata.get("title") or (hook or {}).get("title") or article.get("title") or "Untitled article")


def _best_article_url(article, campaign):
    # Prefer a verified custom-domain landing-page route so generated social copy points
    # at the actual publisher site rather than the NJS admin host.
    page = landing_pages.find_one({
        "campaign_id": campaign["_id"],
        "status": "published",
        "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}],
    }, sort=[("updated_at", -1)])
    if page:
        for route in domain_routes.find({"page_id": page["_id"]}).sort("updated_at", -1):
            domain = domain_mappings.find_one({"_id": route.get("domain_id"), "status": "verified"})
            if domain:
                route_path = str(route.get("path") or route.get("path_key") or "/")
                route_path = "/" if route_path == "/" else "/" + route_path.strip("/")
                base = f"https://{domain['domain']}{'' if route_path == '/' else route_path}"
                return f"{base}/articles/{article['_id']}"
    return f"{Config.PUBLIC_BASE_URL}/public/articles/{article['_id']}"


def _parse_json(raw):
    value = str(raw or "").strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```$", "", value)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Social model response must be a JSON object")
    return parsed


def generate_social_copy(article, hook, campaign, settings, platforms):
    article_url = _best_article_url(article, campaign)
    requirements = {
        platform: {
            "max_characters_including_hashtags_and_link": SOCIAL_PLATFORM_RULES[platform]["max_chars"],
            "hashtag_range": list(SOCIAL_PLATFORM_RULES[platform]["hashtags"]),
            "format": SOCIAL_PLATFORM_RULES[platform]["instruction"],
        }
        for platform in platforms
    }
    payload = {
        "article": {
            "id": str(article["_id"]),
            "title": _article_title(article, hook),
            "body": _plain(article.get("content")),
            "summary": _plain((hook or {}).get("description") or (article.get("metadata") or {}).get("summary"), 1800),
            "source_url": (hook or {}).get("source_url"),
        },
        "campaign": {
            "title": campaign.get("title"),
            "target_audience": campaign.get("target_audience"),
            "campaign_goal": campaign.get("campaign_goal"),
            "content_overview": campaign.get("content_overview"),
            "website_url": campaign.get("website_url"),
        },
        "brand_voice": settings.get("brand_voice"),
        "article_url": article_url,
        "platform_requirements": requirements,
        "shared_image_style": settings.get("image_style"),
    }
    schema = {
        "posts": {p: {"text": "platform-ready copy", "hashtags": ["RelevantTag"]} for p in platforms},
        "image_prompt": "40-90 words describing one concrete editorial scene",
        "image_alt": "concise accessible description",
        "image_headline": "faithful 5-12 word editorial headline, max 105 chars",
        "image_highlight": "exact 1-4 consecutive words copied from image_headline",
    }
    prompt = f"""
Create social-media copy promoting the supplied article for every requested platform.

Rules:
- Stay faithful to the supplied article. Never invent statistics, quotes, people, events, product capabilities, or URLs.
- Adapt the copy meaningfully to each platform; do not reuse one paragraph everywhere.
- The final post including hashtags and any URL must fit the platform limit.
- Do not prefix the copy with labels such as 'LinkedIn post' or 'Caption'.
- Hashtags must not include spaces or the # character.
- Instagram must use a link-in-bio CTA rather than a raw URL in the caption.
- Return one shared image_prompt/image_alt/image_headline/image_highlight for all platforms.
- image_headline must be faithful to the article title, 5-12 words, <=105 characters, and contain no invented facts.
- image_highlight must be an exact consecutive phrase present in image_headline.
- image_prompt should describe one believable, grounded editorial scene with a clear focal point, setting, composition, lighting, mood and restrained palette.
- Do not ask the image model for readable text, UI, watermarks, third-party logos, split screens, multi-panel layouts, or imitation of a living artist.
- If the subject is abstract, use one simple visual metaphor rather than floating icons, fake dashboards, glowing networks, generic robots, or a collage.

Return valid JSON only, matching:
{json.dumps(schema, ensure_ascii=False)}

INPUT:
{json.dumps(payload, ensure_ascii=False, default=str)[:60000]}
"""
    response = model_client().chat.completions.create(
        model=Config.SOCIAL_TEXT_MODEL,
        messages=[
            {"role": "system", "content": "You are a precise multilingual social media editor. Match the article language unless the campaign requests another."},
            {"role": "user", "content": prompt},
        ],
        temperature=Config.SOCIAL_TEXT_TEMPERATURE,
    )
    result = _parse_json(response.choices[0].message.content)
    if not isinstance(result.get("posts"), dict):
        raise ValueError("Social model response is missing posts")
    return result


def _hashtags(values, maximum):
    if not isinstance(values, list):
        return []
    cleaned = []
    for value in values:
        tag = re.sub(r"[^\w]", "", str(value or ""), flags=re.UNICODE)
        if tag and tag.casefold() not in {x.casefold() for x in cleaned}:
            cleaned.append(tag)
        if len(cleaned) >= maximum:
            break
    return cleaned


def _trim(text, maximum):
    text = str(text or "").strip()
    if len(text) <= maximum:
        return text
    shortened = text[: max(1, maximum - 1)].rstrip()
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip(".,;:-") + "…"


def finalize_copy(platform, raw_post, article_url):
    rule = SOCIAL_PLATFORM_RULES[platform]
    raw_post = raw_post if isinstance(raw_post, dict) else {}
    text = str(raw_post.get("text") or "").strip()
    if not text:
        raise ValueError(f"Generated {platform} post has no text")
    link = "" if platform == "instagram" or not article_url or article_url in text else article_url
    tags = _hashtags(raw_post.get("hashtags"), rule["hashtags"][1])
    tag_text = " ".join(f"#{tag}" for tag in tags)
    suffix = "\n\n".join(x for x in (link, tag_text) if x)
    body_limit = rule["max_chars"] - len(suffix) - (2 if suffix else 0)
    if body_limit < 20:
        suffix = tag_text
        body_limit = rule["max_chars"] - len(suffix) - (2 if suffix else 0)
    body = _trim(text, body_limit)
    return _trim(f"{body}\n\n{suffix}" if suffix else body, rule["max_chars"])


def _image_prompt(scene, settings):
    scene = _plain(scene, 1400).strip(" .;:-") or "A clear editorial scene representing the article's central idea"
    style = _plain(settings.get("image_style"), 600) or "Premium contemporary editorial photography"
    return f"""
Create one polished square editorial social-media image.

SCENE
{scene}

ART DIRECTION
{style}

COMPOSITION
- One dominant focal point; one coherent scene, never a collage or split-screen.
- Keep the main subject in the upper and middle portions of the frame.
- Reserve roughly the lower 36% as clean editorial headline space: visually simple and free of faces, hands, products, symbols, or important objects.
- Let the lower area naturally become somewhat darker toward the bottom so white typography reads well.
- Use a clean foreground/midground/background hierarchy and enough negative space.
- Make the image immediately legible at small social-feed thumbnail size.

VISUAL QUALITY
- Believable materials, lighting, anatomy, scale, perspective, and depth.
- Natural detail and subtle imperfection; avoid the glossy overprocessed AI look.
- Controlled contrast, restrained palette, purposeful lighting, premium magazine finish.
- Avoid generic stock posing, excessive glow, clutter, repeated objects, distorted hands/faces, floating icons, fake dashboards, or decorative tech graphics.

HARD REQUIREMENTS
- No readable text, letters, numbers, captions, labels, signs, pseudo-text, or typography.
- No platform UI, screenshots, borders, frames, watermarks, or third-party logos.
- Do not add branding that was not explicitly requested.
""".strip()


def _font(size, bold=True):
    candidates = []
    configured = Config.SOCIAL_IMAGE_FONT_BOLD if bold else Config.SOCIAL_IMAGE_FONT_REGULAR
    if configured:
        candidates.append(configured)
    candidates += ([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ] if bold else [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ])
    for path in candidates:
        if path and Path(path).is_file():
            return ImageFont.truetype(path, max(8, int(size)))
    return ImageFont.load_default()


def _text_width(draw, text, font):
    try:
        return float(draw.textlength(text, font=font))
    except AttributeError:
        box = draw.textbbox((0, 0), text, font=font)
        return float(box[2] - box[0])


def _wrap(draw, headline, font, max_width):
    lines, current = [], []
    for word in [w for w in str(headline or "").split() if w]:
        candidate = " ".join(current + [word])
        if current and _text_width(draw, candidate, font) > max_width:
            lines.append(current)
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(current)
    return lines


def _highlight_indexes(words, highlight):
    import string
    target = [w.strip(string.punctuation + "“”‘’").casefold() for w in str(highlight or "").split() if w]
    source = [w.strip(string.punctuation + "“”‘’").casefold() for w in words]
    if not target:
        return set()
    for start in range(len(source) - len(target) + 1):
        if source[start:start + len(target)] == target:
            return set(range(start, start + len(target)))
    return set()


def _parse_hex(value, fallback=(20, 159, 232)):
    value = str(value or "").strip().lstrip("#")
    if len(value) == 6 and re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    return fallback


def apply_editorial_overlay(binary, headline, highlight, footer, settings, output_format="jpeg", compression=88):
    limit = int(settings.get("image_headline_max_chars", 105) or 105)
    headline = _trim(_plain(headline, limit + 40), limit)
    if not headline or not settings.get("image_text_enabled", True):
        return binary
    headline = headline.upper()
    highlight = _plain(highlight, 80).upper()
    footer = _plain(footer, 80)

    image = Image.open(BytesIO(binary)).convert("RGBA")
    width, height = image.size
    draw = ImageDraw.Draw(image)
    ratio = max(0.28, min(float(settings.get("image_text_area_ratio", 0.36) or 0.36), 0.46))
    gradient_start = int(height * (1.0 - ratio - 0.10))
    gradient = Image.new("L", (1, height), 0)
    px = gradient.load()
    denominator = max(height - gradient_start, 1)
    for y in range(gradient_start, height):
        progress = (y - gradient_start) / denominator
        px[0, y] = max(0, min(int(238 * (progress ** 1.18)), 238))
    gradient = gradient.resize((width, height))
    shade = Image.new("RGBA", image.size, (0, 0, 0, 255))
    shade.putalpha(gradient)
    image = Image.alpha_composite(image, shade)
    draw = ImageDraw.Draw(image)

    accent = (*_parse_hex(settings.get("image_accent_color")), 255)
    white = (255, 255, 255, 255)
    left, right = int(width * 0.085), int(width * 0.07)
    accent_width = max(5, int(width * 0.007))
    text_x = left + accent_width + int(width * 0.025)
    max_width = width - text_x - right
    top_target, bottom_limit = int(height * 0.665), int(height * 0.88)
    max_height = bottom_limit - top_target
    font_size, min_size = max(28, int(width * 0.056)), max(22, int(width * 0.033))
    line_words, spacing = [], 0
    while font_size >= min_size:
        headline_font = _font(font_size, True)
        line_words = _wrap(draw, headline, headline_font, max_width)
        spacing = max(4, int(font_size * 0.14))
        line_height = int(font_size * 1.08)
        total = len(line_words) * line_height + max(0, len(line_words) - 1) * spacing
        if len(line_words) <= 4 and total <= max_height:
            break
        font_size -= 2
    headline_font = _font(font_size, True)
    line_height = int(font_size * 1.08)
    total = len(line_words) * line_height + max(0, len(line_words) - 1) * spacing
    y = max(top_target, bottom_limit - total)
    if line_words:
        draw.rounded_rectangle((left, y, left + accent_width, y + total), radius=max(1, accent_width // 2), fill=accent)
    all_words = [word for line in line_words for word in line]
    highlight_set = _highlight_indexes(all_words, highlight)
    flat = 0
    space = _text_width(draw, " ", headline_font)
    for line in line_words:
        x = text_x
        for word in line:
            draw.text((x, y), word, font=headline_font, fill=accent if flat in highlight_set else white)
            x += _text_width(draw, word, headline_font) + space
            flat += 1
        y += line_height + spacing
    if footer:
        footer_font = _font(max(16, int(width * 0.024)), False)
        fw = _text_width(draw, footer, footer_font)
        draw.text((max(int(width * 0.05), (width - fw) / 2), int(height * 0.945) - max(16, int(width * 0.024))), footer, font=footer_font, fill=(235, 235, 235, 255))

    out = BytesIO()
    final = image.convert("RGB")
    if output_format == "webp":
        final.save(out, format="WEBP", quality=compression, method=6)
    elif output_format == "png":
        final.save(out, format="PNG", optimize=True)
    else:
        final.save(out, format="JPEG", quality=compression, optimize=True, progressive=True)
    return out.getvalue()


def generate_shared_image(article_id, scene_prompt, alt_text, settings, headline=None, highlight=None, footer=None):
    if not Config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required for social image generation")
    full_prompt = _image_prompt(scene_prompt, settings)
    model = Config.SOCIAL_IMAGE_MODEL or "gpt-image-2"
    size = Config.SOCIAL_IMAGE_SIZE if Config.SOCIAL_IMAGE_SIZE in {"1024x1024", "1024x1536", "1536x1024"} else "1024x1024"
    quality = Config.SOCIAL_IMAGE_QUALITY if Config.SOCIAL_IMAGE_QUALITY in {"low", "medium", "high", "auto"} else "low"
    output_format = Config.SOCIAL_IMAGE_OUTPUT_FORMAT if Config.SOCIAL_IMAGE_OUTPUT_FORMAT in {"png", "jpeg", "webp"} else "jpeg"
    compression = max(0, min(Config.SOCIAL_IMAGE_OUTPUT_COMPRESSION, 100))
    client = OpenAI(api_key=Config.OPENAI_API_KEY)
    kwargs = {"model": model, "prompt": full_prompt, "size": size, "quality": quality, "n": 1, "output_format": output_format}
    if output_format in {"jpeg", "webp"}:
        kwargs["output_compression"] = compression
    try:
        response = client.images.generate(**kwargs)
    except TypeError:
        kwargs.pop("output_format", None)
        kwargs.pop("output_compression", None)
        output_format = "png"
        response = client.images.generate(**kwargs)
    image_data = response.data[0]
    binary = None
    if getattr(image_data, "b64_json", None):
        binary = base64.b64decode(image_data.b64_json)
    elif getattr(image_data, "url", None):
        fetched = requests.get(image_data.url, timeout=60)
        fetched.raise_for_status()
        binary = fetched.content
    if not binary:
        raise RuntimeError("Image API returned no image bytes")
    binary = apply_editorial_overlay(binary, headline, highlight, footer, settings, output_format, compression)

    output_dir = Path(Config.SOCIAL_IMAGE_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(binary).hexdigest()[:16]
    extension = {"jpeg": "jpg", "webp": "webp", "png": "png"}.get(output_format, "png")
    filename = f"social-{article_id}-{digest}.{extension}"
    temp = output_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
    final = output_dir / filename
    temp.write_bytes(binary)
    os.replace(temp, final)
    return {
        "url": f"{Config.SOCIAL_IMAGE_URL_PREFIX.rstrip('/')}/{filename}",
        "filename": filename,
        "prompt": full_prompt,
        "alt": str(alt_text or "Editorial image for the article")[:500],
        "headline": headline,
        "highlight": highlight,
        "footer": footer,
        "model": model,
        "size": size,
        "quality": quality,
        "format": output_format,
        "typography": "pillow" if settings.get("image_text_enabled", True) else "none",
        "generated_at": now(),
    }


def _next_schedule(settings, current, platform_index):
    if not settings.get("auto_schedule"):
        return None
    try:
        tz = ZoneInfo(settings.get("timezone") or "UTC")
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    local = current.astimezone(tz) + timedelta(minutes=max(int(settings.get("schedule_delay_minutes", 15)), 0))
    allowed_days = []
    for raw in settings.get("posting_weekdays") or [0, 1, 2, 3, 4]:
        try:
            day = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6 and day not in allowed_days:
            allowed_days.append(day)
    allowed_days = allowed_days or [0, 1, 2, 3, 4]
    try:
        hour, minute = (int(x) for x in str(settings.get("default_post_time") or "09:00").split(":", 1))
        scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    except (ValueError, TypeError):
        scheduled = local.replace(second=0, microsecond=0)
    if scheduled < local:
        scheduled += timedelta(days=1)
    for _ in range(8):
        if scheduled.weekday() in allowed_days:
            break
        scheduled += timedelta(days=1)
    scheduled += timedelta(minutes=max(int(settings.get("platform_stagger_minutes", 5)), 0) * platform_index)
    return scheduled.astimezone(timezone.utc)


def get_or_create_job(article, campaign, platforms, source_type, regenerate_text=False, regenerate_image=False, job_id=None):
    if job_id:
        job = social_generation_jobs.find_one({"_id": ObjectId(job_id)})
        if not job or job.get("article_id") != article["_id"]:
            raise ValueError("Social generation job not found or mismatched")
        return job
    stamp = now()
    # Manual regeneration gets its own job; automatic initial generation stays idempotent.
    if regenerate_text or regenerate_image or source_type == "manual":
        job_key = f"manual:{article['_id']}:{uuid.uuid4().hex}"
    else:
        job_key = f"auto:{article['_id']}:v1"
    social_generation_jobs.update_one(
        {"job_key": job_key},
        {"$setOnInsert": {
            "job_key": job_key,
            "user_id": campaign.get("user_id"),
            "organization_id": campaign.get("organization_id"),
            "campaign_id": campaign["_id"],
            "article_id": article["_id"],
            "platforms": platforms,
            "source_type": source_type,
            "regenerate_text": bool(regenerate_text),
            "regenerate_image": bool(regenerate_image),
            "status": "queued",
            "created_at": stamp,
            "updated_at": stamp,
        }},
        upsert=True,
    )
    return social_generation_jobs.find_one({"job_key": job_key})


def generate_bundle(article_id, *, job_id=None, platforms=None, source_type="newsjacking", regenerate_text=False, regenerate_image=False):
    article_oid = ObjectId(article_id)
    article = articles.find_one({"_id": article_oid})
    if not article:
        return {"status": "failed", "error": "Article not found"}
    campaign = campaigns.find_one({"_id": article.get("campaign_id")})
    if not campaign:
        return {"status": "failed", "error": "Campaign not found"}
    hook = hooks.find_one({"_id": article.get("hook_id")}) if article.get("hook_id") else None
    settings = social_settings(campaign)
    requested = platforms if platforms is not None else settings.get("platforms", [])
    requested = list(dict.fromkeys(str(p).strip().lower() for p in requested if str(p).strip().lower() in SOCIAL_PLATFORM_RULES))
    if not requested:
        return {"status": "skipped", "reason": "no_platforms_selected"}
    if source_type in {"newsjacking", "hook_idea"} and (not settings.get("enabled") or not settings.get("auto_generate_newsjacking")):
        return {"status": "skipped", "reason": "automatic_generation_disabled"}

    job = get_or_create_job(article, campaign, requested, source_type, regenerate_text, regenerate_image, job_id)
    stamp = now()
    lock_token = uuid.uuid4().hex
    locked = articles.find_one_and_update(
        {"_id": article_oid, "$or": [
            {"social_generation.lock_until": {"$exists": False}},
            {"social_generation.lock_until": None},
            {"social_generation.lock_until": {"$lte": stamp}},
        ]},
        {"$set": {"social_generation.lock_token": lock_token, "social_generation.lock_until": stamp + timedelta(minutes=20)}},
        return_document=pymongo.ReturnDocument.AFTER,
    )
    if not locked:
        social_generation_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "retrying", "error": "Another worker is generating this article", "updated_at": now()}})
        raise RuntimeError("Another worker is generating social assets for this article")
    try:
        social_generation_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "generating", "started_at": stamp, "error": None, "updated_at": now()}})
        existing = {p["platform"]: p for p in social_media_posts.find({"article_id": article_oid, "platform": {"$in": requested}})}
        text_platforms = [p for p in requested if regenerate_text or not existing.get(p, {}).get("text") or existing.get(p, {}).get("status") == "archived"]
        image_post = social_media_posts.find_one({"article_id": article_oid, "image.url": {"$exists": True, "$ne": ""}})
        shared_image = (image_post or {}).get("image")
        needs_image = bool(settings.get("image_enabled") or regenerate_image) and (regenerate_image or not shared_image)

        generated = job.get("generated_payload") if isinstance(job.get("generated_payload"), dict) else None
        cached_posts = (generated or {}).get("posts", {}) if generated else {}
        if text_platforms and not all(p in cached_posts for p in text_platforms):
            generated = generate_social_copy(article, hook, campaign, settings, text_platforms)
            cached_posts = generated.get("posts", {})
            social_generation_jobs.update_one({"_id": job["_id"]}, {"$set": {"generated_payload": generated, "updated_at": now()}})

        article_url = _best_article_url(article, campaign)
        final_text = {p: finalize_copy(p, cached_posts.get(p), article_url) for p in text_platforms}
        if needs_image:
            image_prompt = (generated or {}).get("image_prompt") or f"A compelling editorial visualization of {_article_title(article, hook)}"
            image_alt = (generated or {}).get("image_alt")
            headline = (generated or {}).get("image_headline") or _article_title(article, hook)
            highlight = (generated or {}).get("image_highlight")
            footer = settings.get("image_footer") or campaign.get("title") or ""
            shared_image = generate_shared_image(str(article_oid), image_prompt, image_alt, settings, headline, highlight, footer)
            social_generation_jobs.update_one({"_id": job["_id"]}, {"$set": {"generated_image": shared_image, "updated_at": now()}})

        created = updated = 0
        title = _article_title(article, hook)
        for index, platform in enumerate(requested):
            old = existing.get(platform)
            fields = {
                "user_id": campaign.get("user_id"),
                "organization_id": campaign.get("organization_id"),
                "campaign_id": campaign["_id"],
                "article_id": article_oid,
                "hook_id": article.get("hook_id"),
                "article_title": title,
                "platform": platform,
                "bundle_key": f"article:{article_oid}",
                "source_type": source_type,
                "generation_status": "ready",
                "generation_error": None,
                "generation_model": Config.SOCIAL_TEXT_MODEL,
                "last_generated_at": now(),
                "updated_at": now(),
            }
            if platform in final_text:
                fields.update({"text": final_text[platform], "char_count": len(final_text[platform]), "is_manually_edited": False})
            if shared_image:
                fields["image"] = shared_image
            scheduled_at = _next_schedule(settings, stamp, index)
            inserts = {"created_at": stamp, "status": "scheduled" if scheduled_at else "draft", "scheduled_at": scheduled_at, "notes": ""}
            if old and old.get("status") == "archived":
                fields.update({"status": "scheduled" if scheduled_at else "draft", "scheduled_at": scheduled_at, "archived_at": None})
            result = social_media_posts.update_one({"article_id": article_oid, "platform": platform}, {"$set": fields, "$setOnInsert": inserts}, upsert=True)
            if result.upserted_id:
                created += 1
            elif result.modified_count:
                updated += 1
        if shared_image and needs_image:
            social_media_posts.update_many({"article_id": article_oid}, {"$set": {"image": shared_image, "updated_at": now()}})

        result_payload = {"created": created, "updated": updated, "platforms": requested, "shared_image_url": (shared_image or {}).get("url")}
        social_generation_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "completed", "completed_at": now(), "result": result_payload, "error": None, "updated_at": now()}})
        articles.update_one({"_id": article_oid}, {"$set": {"social_generation.last_completed_at": now(), "social_generation.platforms": requested}})
        return {"status": "completed", "job_id": str(job["_id"]), **result_payload}
    except Exception as exc:
        social_generation_jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "failed", "completed_at": now(), "error": str(exc)[:1500], "updated_at": now()}})
        raise
    finally:
        articles.update_one({"_id": article_oid, "social_generation.lock_token": lock_token}, {"$unset": {"social_generation.lock_until": "", "social_generation.lock_token": ""}})
