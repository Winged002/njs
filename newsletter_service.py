import html
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bson import ObjectId

from config import Config
from db import articles, campaigns, newsletter_editions, newsletter_schedules, social_media_posts
from services import clean_text, model_client, now
from social_service import _article_title, _best_article_url, _plain
from blackbook_service import marketing_audience_preview, marketing_context


def newsletter_defaults():
    return {
        "enabled": True,
        "timezone": "Europe/Skopje",
        "weekdays": [0, 2, 4],
        "ready_time": "07:30",
        "generation_lead_minutes": 90,
        "lookback_days": 7,
        "min_stories": 3,
        "max_stories": 7,
        "editorial_voice": "Concise, useful, factual, and editorial. Explain why each story matters without hype.",
        "audience_note": "Busy readers who want the most important developments and why they matter.",
        "subject_style": "Specific and informative; avoid clickbait and vague curiosity gaps.",
        "sender_name": "Newsjacking Briefing",
        "cta_label": "Read the full story",
        "cta_url": "",
        "visual_design_prompt": "",
    }


def normalize_schedule(schedule):
    defaults = newsletter_defaults()
    result = dict(defaults)
    result.update({k: v for k, v in (schedule or {}).items() if k in defaults})
    weekdays = []
    for value in result.get("weekdays") or []:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6 and day not in weekdays:
            weekdays.append(day)
    result["weekdays"] = sorted(weekdays) or defaults["weekdays"]
    try:
        result["generation_lead_minutes"] = max(0, min(1440, int(result.get("generation_lead_minutes") or 90)))
        result["lookback_days"] = max(1, min(30, int(result.get("lookback_days") or 7)))
        result["min_stories"] = max(1, min(20, int(result.get("min_stories") or 3)))
        result["max_stories"] = max(result["min_stories"], min(25, int(result.get("max_stories") or 7)))
    except (TypeError, ValueError):
        pass
    return result


def schedule_timezone(schedule):
    try:
        return ZoneInfo(str(schedule.get("timezone") or "UTC"))
    except ZoneInfoNotFoundError:
        return timezone.utc


def schedule_due_at(schedule, local_date):
    tz = schedule_timezone(schedule)
    raw_time = str(schedule.get("ready_time") or "07:30")
    try:
        hour, minute = [int(x) for x in raw_time.split(":", 1)]
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, TypeError):
        hour, minute = 7, 30
    local_due = datetime(local_date.year, local_date.month, local_date.day, hour, minute, tzinfo=tz)
    return local_due.astimezone(timezone.utc)


def due_local_date_key(schedule, due_at):
    return due_at.astimezone(schedule_timezone(schedule)).date().isoformat()


def schedule_is_due(schedule, reference=None):
    reference = reference or now()
    tz = schedule_timezone(schedule)
    local_now = reference.astimezone(tz)
    if local_now.weekday() not in (schedule.get("weekdays") or []):
        return False, None
    due_at = schedule_due_at(schedule, local_now.date())
    lead = timedelta(minutes=int(schedule.get("generation_lead_minutes") or 90))
    return reference >= due_at - lead, due_at


def _campaign_scope(schedule):
    ids = [x for x in (schedule.get("campaign_ids") or []) if isinstance(x, ObjectId)]
    if ids:
        return {"$in": ids}
    clauses = []
    if schedule.get("user_id"):
        clauses.append({"user_id": schedule["user_id"]})
    if schedule.get("organization_id"):
        clauses.append({"organization_id": schedule["organization_id"]})
    return clauses


def _previously_used_article_ids(schedule_id, limit=12):
    used = set()
    cursor = newsletter_editions.find(
        {"schedule_id": schedule_id, "status": {"$in": ["draft", "ready", "sent", "archived"]}},
        {"article_ids": 1},
    ).sort("due_at", -1).limit(limit)
    for edition in cursor:
        used.update(edition.get("article_ids") or [])
    return used


def _article_score(article, reference):
    quality = article.get("quality") or {}
    quality_score = float(quality.get("score") or 0)
    created = article.get("created_at") or article.get("completed_at") or reference
    try:
        age_hours = max(0.0, (reference - created).total_seconds() / 3600.0)
    except Exception:
        age_hours = 999
    recency = max(0.0, 30.0 - min(30.0, age_hours / 12.0))
    news_bonus = 8.0 if (article.get("source_type") or "") == "newsjacking" else 0.0
    return quality_score + recency + news_bonus


def select_articles(schedule, due_at):
    # Preserve Mongo/workspace fields (_id, user_id, organization_id, campaign_ids)
    # while applying validated newsletter defaults. v3.5.0 normalized too early.
    schedule = {**schedule, **normalize_schedule(schedule)}
    lookback_days = int(schedule["lookback_days"])
    max_stories = int(schedule["max_stories"])
    min_stories = int(schedule["min_stories"])
    campaign_scope = _campaign_scope(schedule)
    query = {"created_at": {"$lte": due_at}}
    if isinstance(campaign_scope, dict):
        query["campaign_id"] = campaign_scope
    elif campaign_scope:
        query["$or"] = campaign_scope
    used = _previously_used_article_ids(schedule["_id"])

    def fetch(window_days, exclude_used=True):
        q = dict(query)
        q["created_at"] = {"$gte": due_at - timedelta(days=window_days), "$lte": due_at}
        if exclude_used and used:
            q["_id"] = {"$nin": list(used)}
        rows = list(articles.find(q).sort("created_at", -1).limit(250))
        rows.sort(key=lambda a: _article_score(a, due_at), reverse=True)
        return rows

    candidates = fetch(lookback_days, exclude_used=True)
    if len(candidates) < min_stories:
        wider = min(30, max(lookback_days * 2, lookback_days + 7))
        seen = {row["_id"] for row in candidates}
        for row in fetch(wider, exclude_used=True):
            if row["_id"] not in seen:
                candidates.append(row)
                seen.add(row["_id"])
            if len(candidates) >= max_stories:
                break
    if not candidates:
        # Final fallback: allow recent previously used stories rather than fabricating filler.
        candidates = fetch(min(30, max(lookback_days * 2, 14)), exclude_used=False)
    return candidates[:max_stories]


def _story_image_url(article):
    metadata = article.get("metadata") or {}
    social_post = social_media_posts.find_one({
        "article_id": article["_id"],
        "image.url": {"$exists": True, "$ne": ""},
    }) or {}
    image_url = (
        (social_post.get("image") or {}).get("url")
        or (metadata.get("image_info") or {}).get("url")
        or (article.get("thumbnail") or {}).get("url")
        or ""
    )
    image_url = str(image_url).strip()
    if image_url.startswith("/"):
        image_url = Config.PUBLIC_BASE_URL + image_url
    return image_url if image_url.startswith(("http://", "https://")) else ""


def _story_payload(article):
    campaign = campaigns.find_one({"_id": article.get("campaign_id")}) or {}
    metadata = article.get("metadata") or {}
    return {
        "article_id": str(article["_id"]),
        "title": _article_title(article),
        "summary": clean_text(metadata.get("summary") or metadata.get("description") or _plain(article.get("content"), 1200), 1200),
        "key_takeaways": [clean_text(x, 300) for x in (metadata.get("key_takeaways") or [])][:5],
        "campaign": campaign.get("title") or "",
        "url": _best_article_url(article, campaign),
        "image_url": _story_image_url(article),
        "quality_score": (article.get("quality") or {}).get("score"),
        "created_at": (article.get("created_at") or now()).isoformat(),
    }


def _parse_json(raw):
    value = str(raw or "").strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```$", "", value)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Newsletter model response must be a JSON object")
    return parsed




def hydrate_blackbook_audience(schedule):
    """Refresh aggregate BlackBook audience intelligence for newsletter generation.

    The model receives aggregate audience/campaign signals only. NJS does not feed
    individual people records or contact details into the newsletter-generation prompt.
    """
    schedule = dict(schedule or {})
    local_org_id = schedule.get("organization_id")
    segment_ids = schedule.get("blackbook_segment_ids") or []
    person_ids = schedule.get("blackbook_person_ids") or []
    include_all = bool(schedule.get("blackbook_include_all_eligible"))
    engagement_buckets = schedule.get("blackbook_engagement_buckets") or []
    interest_ids = schedule.get("blackbook_interest_ids") or []
    interest_match = schedule.get("blackbook_interest_match") or "any"
    if not local_org_id or not (segment_ids or person_ids or include_all or engagement_buckets or interest_ids):
        return schedule
    try:
        preview = marketing_audience_preview(
            local_org_id, segment_ids=segment_ids, person_ids=person_ids,
            include_all_eligible=include_all, engagement_buckets=engagement_buckets,
            interest_ids=interest_ids, interest_match=interest_match,
        )
        context = marketing_context(local_org_id)
        recent = []
        for row in (context.get("recent_campaigns") or [])[:8]:
            recent.append({
                "subject": row.get("subject") or row.get("title") or "",
                "emails_sent": row.get("emails_sent") or 0,
                "unique_opens": row.get("unique_opens") or 0,
                "unique_clicks": row.get("unique_clicks") or 0,
                "open_rate": row.get("open_rate") or 0,
                "click_rate": row.get("click_rate") or 0,
                "send_time": row.get("send_time"),
            })
        snapshot = {
            "audience_counts": preview.get("counts") or {},
            "average_relationship_strength": preview.get("average_relationship_strength") or 0,
            "audience_categories": (preview.get("categories") or [])[:15],
            "engagement_distribution": preview.get("engagement_distribution") or {},
            "audience_criteria": preview.get("criteria") or {
                "engagement_buckets": engagement_buckets, "interest_ids": interest_ids, "interest_match": interest_match,
            },
            "selected_engagement_buckets": engagement_buckets,
            "selected_interest_ids": interest_ids,
            "interest_match": interest_match,
            "recent_campaign_performance": recent,
            "mailchimp_configured": bool((context.get("mailchimp") or {}).get("configured")),
            "refreshed_at": now(),
        }
        schedule["blackbook_audience_snapshot"] = snapshot
        try:
            newsletter_schedules.update_one({"_id": schedule.get("_id")}, {"$set": {
                "blackbook_audience_snapshot": snapshot, "blackbook_audience_refreshed_at": now(),
            }})
        except Exception:
            pass
    except Exception as exc:
        schedule["blackbook_audience_error"] = clean_text(str(exc), 700)
    return schedule


def generate_newsletter_payload(schedule, selected_articles, due_at):
    stories = [_story_payload(article) for article in selected_articles]
    schema = {
        "subject": "Specific subject line",
        "preheader": "Useful preview text",
        "intro": "2-4 sentence editorial introduction",
        "stories": [
            {
                "article_id": "id from input",
                "headline": "faithful concise headline",
                "summary": "2-4 sentence recap",
                "why_it_matters": "1-2 sentence relevance explanation",
                "cta_label": "Read the full story",
            }
        ],
        "closing": "Short closing paragraph",
    }
    prompt = f"""
Create an email newsletter edition from the supplied generated articles.

Rules:
- Use only facts present in the supplied story data. Never invent statistics, quotes, events, products, or URLs.
- Prioritize the strongest and most recent stories, but preserve every supplied article_id exactly.
- Do not manufacture filler. If the edition is short, make it intentionally concise.
- Explain why each story matters to the configured audience without overstating certainty.
- Subject line must be specific, useful, and non-clickbait.
- Preheader should complement, not repeat, the subject.
- Keep the introduction editorial and compact.
- Story summaries should make sense even when the reader does not click.
- CTA labels should be short and action-oriented.
- Return valid JSON only.

NEWSLETTER SETTINGS:
{json.dumps({
    'name': schedule.get('name'),
    'editorial_voice': schedule.get('editorial_voice'),
    'audience_note': schedule.get('audience_note'),
    'subject_style': schedule.get('subject_style'),
    'sender_name': schedule.get('sender_name'),
    'default_cta_label': schedule.get('cta_label'),
    'due_at': due_at.isoformat(),
    'blackbook_audience_intelligence': schedule.get('blackbook_audience_snapshot') or {},
}, ensure_ascii=False, default=str)}

STORIES:
{json.dumps(stories, ensure_ascii=False, default=str)[:60000]}

OUTPUT SHAPE:
{json.dumps(schema, ensure_ascii=False)}
"""
    response = model_client().chat.completions.create(
        model=Config.NEWSLETTER_TEXT_MODEL,
        messages=[
            {"role": "system", "content": "You are a precise newsletter editor. Return JSON only and never invent facts."},
            {"role": "user", "content": prompt},
        ],
        temperature=Config.NEWSLETTER_TEXT_TEMPERATURE,
    )
    payload = _parse_json(response.choices[0].message.content)
    by_id = {str(article["_id"]): article for article in selected_articles}
    normalized_stories = []
    seen = set()
    for item in payload.get("stories") or []:
        article_id = str((item or {}).get("article_id") or "")
        if article_id not in by_id or article_id in seen:
            continue
        article = by_id[article_id]
        campaign = campaigns.find_one({"_id": article.get("campaign_id")}) or {}
        default_cta = schedule.get("cta_label") or "Read the full story"
        normalized_stories.append({
            "article_id": article_id,
            "headline": clean_text((item or {}).get("headline") or _article_title(article), 180),
            "summary": clean_text((item or {}).get("summary") or (article.get("metadata") or {}).get("summary") or _plain(article.get("content"), 900), 1400),
            "why_it_matters": clean_text((item or {}).get("why_it_matters"), 700),
            "cta_label": clean_text((item or {}).get("cta_label") or default_cta, 60),
            "url": _best_article_url(article, campaign),
            "image_url": _story_image_url(article),
        })
        seen.add(article_id)
    for article in selected_articles:
        article_id = str(article["_id"])
        if article_id in seen:
            continue
        campaign = campaigns.find_one({"_id": article.get("campaign_id")}) or {}
        normalized_stories.append({
            "article_id": article_id,
            "headline": clean_text(_article_title(article), 180),
            "summary": clean_text((article.get("metadata") or {}).get("summary") or _plain(article.get("content"), 900), 1400),
            "why_it_matters": "",
            "cta_label": clean_text(schedule.get("cta_label") or "Read the full story", 60),
            "url": _best_article_url(article, campaign),
            "image_url": _story_image_url(article),
        })
    return {
        "subject": clean_text(payload.get("subject") or f"{schedule.get('name') or 'Newsjacking'} briefing", 140),
        "preheader": clean_text(payload.get("preheader"), 220),
        "intro": clean_text(payload.get("intro"), 1800),
        "stories": normalized_stories,
        "closing": clean_text(payload.get("closing"), 1200),
    }


def compile_newsletter_html(schedule, payload, due_at):
    sender = html.escape(schedule.get("sender_name") or schedule.get("name") or "Newsjacking Briefing")
    title = html.escape(payload.get("subject") or schedule.get("name") or "Newsletter")
    preheader = html.escape(payload.get("preheader") or "")
    intro = html.escape(payload.get("intro") or "").replace("\n", "<br>")
    closing = html.escape(payload.get("closing") or "").replace("\n", "<br>")
    story_blocks = []
    for story in payload.get("stories") or []:
        headline = html.escape(story.get("headline") or "Story")
        summary = html.escape(story.get("summary") or "").replace("\n", "<br>")
        why = html.escape(story.get("why_it_matters") or "").replace("\n", "<br>")
        url = html.escape(story.get("url") or "", quote=True)
        cta = html.escape(story.get("cta_label") or "Read the full story")
        image_url = str(story.get("image_url") or "").strip()
        image_block = ""
        if image_url.startswith(("http://", "https://")):
            safe_image = html.escape(image_url, quote=True)
            image_block = f'<img src="{safe_image}" alt="" width="640" style="display:block;width:100%;height:auto;max-height:360px;object-fit:cover;border-radius:10px;margin:0 0 16px">'
        why_block = f'<p style="margin:12px 0 0;color:#56616f;font-size:14px;line-height:1.55"><strong>Why it matters:</strong> {why}</p>' if why else ""
        cta_block = f'<p style="margin:18px 0 0"><a href="{url}" style="display:inline-block;background:#111827;color:#ffffff;text-decoration:none;padding:10px 14px;border-radius:8px;font-weight:700">{cta}</a></p>' if url else ""
        story_blocks.append(
            '<tr><td style="padding:0 0 18px"><div style="border:1px solid #e5e7eb;border-radius:14px;padding:20px;background:#ffffff">'
            + image_block
            + f'<h2 style="margin:0 0 10px;font-size:21px;line-height:1.3;color:#111827">{headline}</h2>'
            + f'<p style="margin:0;color:#303946;font-size:15px;line-height:1.65">{summary}</p>'
            + why_block + cta_block + '</div></td></tr>'
        )
    due_label = due_at.astimezone(schedule_timezone(schedule)).strftime("%A, %B %d, %Y")
    global_cta_url = str(schedule.get("cta_url") or "").strip()
    global_cta = ""
    if global_cta_url.startswith(("http://", "https://")):
        global_cta = f'<p style="margin:18px 0"><a href="{html.escape(global_cta_url, quote=True)}" style="color:#111827;font-weight:700">Visit the main site →</a></p>'
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title}</title></head><body style="margin:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;color:#111827">'
        f'<div style="display:none;max-height:0;overflow:hidden">{preheader}</div>'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f4f6"><tr><td align="center" style="padding:28px 14px">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px">'
        f'<tr><td style="padding:0 0 18px"><div style="font-size:13px;color:#6b7280;text-transform:uppercase;letter-spacing:.08em">{sender} · {due_label}</div>'
        f'<h1 style="font-size:32px;line-height:1.18;margin:8px 0 10px;color:#111827">{title}</h1>'
        f'<p style="margin:0;color:#4b5563;font-size:16px;line-height:1.65">{intro}</p></td></tr>'
        + ''.join(story_blocks)
        + f'<tr><td style="padding:8px 4px 26px;color:#4b5563;font-size:14px;line-height:1.6">{closing}{global_cta}</td></tr>'
        + "<tr><td style='border-top:1px solid #d1d5db;padding:16px 4px;color:#9ca3af;font-size:12px'>Generated by Newsjacking. Review before distribution. Add your delivery provider's unsubscribe and compliance footer when sending.</td></tr>"
        + '</table></td></tr></table></body></html>'
    )



def sanitize_newsletter_design_html(document):
    """Keep AI-produced email HTML self-contained and remove active/unsafe elements."""
    text = str(document or "").strip()
    match = re.search(r"```(?:html)?\s*(.*?)```", text, re.I | re.S)
    if match:
        text = match.group(1).strip()
    start = re.search(r"<!doctype\s+html|<html\b", text, re.I)
    if start:
        text = text[start.start():]
    text = re.sub(r"<script\b[^>]*>.*?</script>", "", text, flags=re.I | re.S)
    text = re.sub(r"<(?:iframe|object|embed|form)\b[^>]*>.*?</(?:iframe|object|embed|form)>", "", text, flags=re.I | re.S)
    text = re.sub(r"\s+on[a-z]+\s*=\s*([\"']).*?\1", "", text, flags=re.I | re.S)
    text = re.sub(r"\s+on[a-z]+\s*=\s*[^\s>]+", "", text, flags=re.I)
    text = re.sub(r"javascript\s*:", "", text, flags=re.I)
    if "<html" not in text.lower():
        text = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>' + text + '</body></html>'
    return text[:500000]


def apply_newsletter_visual_prompt(schedule, payload, due_at, base_html, instruction):
    """Use a plain-language visual brief to restyle an existing email without changing its editorial facts."""
    instruction = clean_text(instruction, 12000)
    if not instruction:
        return sanitize_newsletter_design_html(base_html)
    story_contract = [
        {
            "headline": clean_text(x.get("headline"), 220),
            "summary": clean_text(x.get("summary"), 1600),
            "why_it_matters": clean_text(x.get("why_it_matters"), 800),
            "cta_label": clean_text(x.get("cta_label"), 80),
            "url": x.get("url") or "",
            "image_url": x.get("image_url") or "",
        }
        for x in (payload.get("stories") or [])
    ]
    due_label = due_at.astimezone(schedule_timezone(schedule)).strftime("%A, %B %d, %Y")
    prompt = f"""
Revise the visual presentation of the supplied production email newsletter according to the DESIGN REQUEST.
Return EXACTLY one fenced HTML block and nothing outside it.

EMAIL-SAFE RULES
- This is an email, not a web page. Prefer table-based layout and inline CSS for critical styling.
- Keep the document responsive and readable on desktop and mobile email clients.
- No JavaScript, forms, iframes, external CSS, external fonts, SVG scripts, video, canvas, or interactive controls.
- Preserve every editorial fact, story, destination URL, image URL, subject meaning, sender identity and compliance/footer intent.
- Do not invent copy, statistics, quotes, claims, links, products, awards or events.
- Do not remove stories. Do not silently change URLs. Visual hierarchy and concise presentational labels may change only when the request clearly calls for it.
- Use only image URLs already present in BASE EMAIL or CONTENT CONTRACT.
- Keep a hidden preheader near the top of the body.
- Keep the maximum email content width sensible (roughly 600-760px unless the design request explicitly requires otherwise).
- Favor broadly supported CSS; avoid CSS grid, complex positioning, animation and browser-only effects.
- Include accessible text contrast, semantic headings where practical, descriptive link text, and image alt attributes.
- Return a complete <!doctype html> document.

DESIGN REQUEST:
{instruction}

NEWSLETTER IDENTITY:
{json.dumps({
    'name': schedule.get('name'),
    'sender_name': schedule.get('sender_name'),
    'subject': payload.get('subject'),
    'preheader': payload.get('preheader'),
    'intro': payload.get('intro'),
    'closing': payload.get('closing'),
    'due_label': due_label,
    'cta_url': schedule.get('cta_url'),
}, ensure_ascii=False, default=str)[:14000]}

CONTENT CONTRACT (must remain faithful):
{json.dumps(story_contract, ensure_ascii=False, default=str)[:50000]}

BASE EMAIL HTML:
{str(base_html or '')[:180000]}
"""
    response = model_client().chat.completions.create(
        model=Config.NEWSLETTER_DESIGN_MODEL,
        messages=[
            {"role": "system", "content": "You are a senior email designer. Apply precise visual changes while preserving newsletter content and email-client compatibility. Return only the requested fenced HTML document."},
            {"role": "user", "content": prompt},
        ],
        temperature=Config.NEWSLETTER_DESIGN_TEMPERATURE,
    )
    revised = sanitize_newsletter_design_html(response.choices[0].message.content)
    if len(revised) < 500 or "<body" not in revised.lower():
        raise RuntimeError("The newsletter design model returned invalid email HTML")
    normalized_document = html.unescape(revised)
    required_urls = []
    for story in payload.get("stories") or []:
        url = str(story.get("url") or "").strip()
        if url.startswith(("http://", "https://")) and url not in required_urls:
            required_urls.append(url)
    global_cta_url = str(schedule.get("cta_url") or "").strip()
    if global_cta_url.startswith(("http://", "https://")) and global_cta_url not in required_urls:
        required_urls.append(global_cta_url)
    missing_urls = [url for url in required_urls if url not in normalized_document]
    if missing_urls:
        raise RuntimeError(f"The redesigned email dropped {len(missing_urls)} required destination URL(s)")
    return revised


def compile_newsletter_designed_html(schedule, payload, due_at, visual_prompt=""):
    base_html = compile_newsletter_html(schedule, payload, due_at)
    prompt = clean_text(visual_prompt or schedule.get("visual_design_prompt") or "", 12000)
    if not prompt:
        return base_html
    return apply_newsletter_visual_prompt(schedule, payload, due_at, base_html, prompt)

def compile_newsletter_text(schedule, payload, due_at):
    lines = [payload.get("subject") or schedule.get("name") or "Newsletter", "", payload.get("intro") or "", ""]
    for story in payload.get("stories") or []:
        lines.extend([story.get("headline") or "Story", story.get("summary") or ""])
        if story.get("why_it_matters"):
            lines.append("Why it matters: " + story["why_it_matters"])
        if story.get("url"):
            lines.append(story["url"])
        lines.append("")
    if payload.get("closing"):
        lines.extend([payload["closing"], ""])
    cta_url = str(schedule.get("cta_url") or "").strip()
    if cta_url.startswith(("http://", "https://")):
        lines.extend([cta_url, ""])
    return "\n".join(lines).strip() + "\n"


def generate_edition(schedule_id, due_at=None, force=False):
    schedule = newsletter_schedules.find_one({"_id": ObjectId(str(schedule_id))})
    if not schedule:
        raise ValueError("Newsletter schedule not found")
    schedule = {**schedule, **normalize_schedule(schedule)}
    schedule = hydrate_blackbook_audience(schedule)
    due_at = due_at or now()
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    local_key = due_local_date_key(schedule, due_at)
    existing = newsletter_editions.find_one({"schedule_id": schedule["_id"], "due_local_date": local_key})
    if existing and not force and existing.get("generation_status") == "completed":
        return existing

    selected = select_articles(schedule, due_at)
    now_value = now()
    base = {
        "user_id": schedule.get("user_id"),
        "organization_id": schedule.get("organization_id"),
        "schedule_id": schedule["_id"],
        "schedule_name": schedule.get("name"),
        "due_at": due_at,
        "due_local_date": local_key,
        "article_ids": [a["_id"] for a in selected],
        "updated_at": now_value,
    }
    if not selected:
        newsletter_editions.update_one(
            {"schedule_id": schedule["_id"], "due_local_date": local_key},
            {"$set": {**base, "status": "needs_material", "generation_status": "completed", "subject": "", "preheader": "", "html": "", "text": "", "story_count": 0}, "$setOnInsert": {"created_at": now_value}},
            upsert=True,
        )
        return newsletter_editions.find_one({"schedule_id": schedule["_id"], "due_local_date": local_key})

    newsletter_editions.update_one(
        {"schedule_id": schedule["_id"], "due_local_date": local_key},
        {"$set": {**base, "status": "generating", "generation_status": "generating", "generation_error": None}, "$setOnInsert": {"created_at": now_value}},
        upsert=True,
    )
    try:
        payload = generate_newsletter_payload(schedule, selected, due_at)
        visual_prompt = clean_text((existing or {}).get("visual_design_prompt") or schedule.get("visual_design_prompt") or "", 12000)
        html_body = compile_newsletter_designed_html(schedule, payload, due_at, visual_prompt=visual_prompt)
        text_body = compile_newsletter_text(schedule, payload, due_at)
        thin = len(selected) < int(schedule.get("min_stories") or 1)
        newsletter_editions.update_one(
            {"schedule_id": schedule["_id"], "due_local_date": local_key},
            {"$set": {
                **base,
                "status": "draft",
                "generation_status": "completed",
                "subject": payload.get("subject"),
                "preheader": payload.get("preheader"),
                "intro": payload.get("intro"),
                "closing": payload.get("closing"),
                "stories": payload.get("stories"),
                "html": html_body,
                "text": text_body,
                "story_count": len(selected),
                "thin_edition": thin,
                "generated_at": now(),
                "generation_model": Config.NEWSLETTER_TEXT_MODEL,
                "design_model": Config.NEWSLETTER_DESIGN_MODEL if visual_prompt else None,
                "visual_design_prompt": visual_prompt,
                "design_revision": int((existing or {}).get("design_revision") or 0) + 1,
            }}
        )
    except Exception as exc:
        newsletter_editions.update_one(
            {"schedule_id": schedule["_id"], "due_local_date": local_key},
            {"$set": {**base, "status": "failed", "generation_status": "failed", "generation_error": str(exc)[:2000]}},
        )
        raise
    return newsletter_editions.find_one({"schedule_id": schedule["_id"], "due_local_date": local_key})
