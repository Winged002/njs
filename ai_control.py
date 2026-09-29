from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from flask import Blueprint, jsonify, request
from slugify import slugify

from config import Config
from db import (
    users, campaigns, products, newsjacking_workers, rss_feeds, articles,
    article_versions, article_change_jobs, social_media_posts, newsletter_schedules,
    newsletter_editions, newsletter_edition_versions, newsletter_design_jobs, collections, collection_sources, collection_items,
    analytics_events, domain_mappings, domain_routes, website_sites, landing_pages, landing_page_versions, landing_page_change_jobs,
    sso_organization_links,
)
from services import clean_text, now, normalize_feed_url, sanitize_manual_landing_html, manual_landing_document
from syntal_sso import discovery, extract_identity, extract_permissions, has_app_access
from tasks import (
    prepare_campaign_strategy_task, newsjacking_scan_worker_task,
    generate_social_media_bundle_task, generate_newsletter_edition_task, apply_newsletter_visual_prompt_task,
    apply_article_review_notes_task, process_collection_source_task,
    generate_landing_page_task, apply_landing_review_notes_task,
)
from newsletter_service import compile_newsletter_html, compile_newsletter_text, compile_newsletter_designed_html
from blackbook_service import (
    public_connector_status as blackbook_connector_status,
    marketing_audience_preview as blackbook_audience_preview,
    distribute_newsletter as blackbook_distribute_newsletter,
    marketing_engagement_overview as blackbook_engagement_overview,
    marketing_engagement_people as blackbook_engagement_people,
    marketing_engagement_refresh_queue as blackbook_engagement_refresh_queue,
    marketing_engagement_bulk_analyze as blackbook_engagement_bulk_analyze,
    marketing_engagement_interests as blackbook_engagement_interests,
    marketing_engagement_batches as blackbook_engagement_batches,
    marketing_engagement_batch as blackbook_engagement_batch,
)
import requests


bp = Blueprint("syntal_ai_control", __name__)


def _schema(properties=None, required=None):
    out = {"type": "object", "properties": properties or {}, "additionalProperties": True}
    if required:
        out["required"] = required
    return out


def _tool(name, description, risk, method, path, properties=None, required=None):
    return {
        "name": name,
        "description": description,
        "permission": Config.SSO_REQUIRED_PERMISSION if risk == "read" else Config.SSO_ADMIN_PERMISSION,
        "risk": risk,
        "method": method,
        "path": path,
        "input_schema": _schema(properties, required),
    }


TOOLS = [
    _tool("njs.workspace.summary", "Summarize the active NJS workspace and its operational counts.", "read", "GET", "/api/ai/v1/workspace/summary"),

    _tool("njs.campaigns.list", "List campaigns in the active organization.", "read", "GET", "/api/ai/v1/campaigns", {"status": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("njs.campaigns.read", "Read one campaign and its strategy state.", "read", "GET", "/api/ai/v1/campaigns/{campaign_id}", {"campaign_id": {"type": "string"}}, ["campaign_id"]),
    _tool("njs.campaigns.create", "Create a campaign from explicit NJS evidence items.", "write", "POST", "/api/ai/v1/campaigns", {"title": {"type": "string"}, "campaign_context": {"type": "string"}, "evidence_item_ids": {"type": "array", "items": {"type": "string"}}, "prepare_strategy": {"type": "boolean"}}, ["title", "evidence_item_ids"]),
    _tool("njs.campaigns.update", "Update campaign title, context, keywords or status.", "write", "PATCH", "/api/ai/v1/campaigns/{campaign_id}", {"campaign_id": {"type": "string"}, "title": {"type": "string"}, "campaign_context": {"type": "string"}, "filter_keywords": {"type": "array", "items": {"type": "string"}}, "status": {"type": "string", "enum": ["active", "draft", "paused"]}}, ["campaign_id"]),
    _tool("njs.campaigns.prepare", "Queue NJS campaign research and strategy generation.", "external", "POST", "/api/ai/v1/campaigns/{campaign_id}/prepare", {"campaign_id": {"type": "string"}}, ["campaign_id"]),

    _tool("njs.products.list", "List products in the active organization.", "read", "GET", "/api/ai/v1/products", {"status": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("njs.products.read", "Read a product configuration.", "read", "GET", "/api/ai/v1/products/{product_id}", {"product_id": {"type": "string"}}, ["product_id"]),
    _tool("njs.products.create", "Create a product for newsjacking campaigns.", "write", "POST", "/api/ai/v1/products", {"name": {"type": "string"}, "product_url": {"type": "string"}, "description": {"type": "string"}, "positioning": {"type": "string"}, "cta_label": {"type": "string"}, "cta_url": {"type": "string"}, "status": {"type": "string"}}, ["name", "product_url"]),
    _tool("njs.products.update", "Update a product configuration.", "write", "PATCH", "/api/ai/v1/products/{product_id}", {"product_id": {"type": "string"}}, ["product_id"]),
    _tool("njs.products.delete", "Delete an unused product.", "destructive", "DELETE", "/api/ai/v1/products/{product_id}", {"product_id": {"type": "string"}}, ["product_id"]),

    _tool("njs.collections.list", "List evidence collections.", "read", "GET", "/api/ai/v1/collections", {"limit": {"type": "integer"}}),
    _tool("njs.collections.read", "Read an evidence collection and recent items.", "read", "GET", "/api/ai/v1/collections/{collection_id}", {"collection_id": {"type": "string"}, "limit": {"type": "integer"}}, ["collection_id"]),
    _tool("njs.collections.create", "Create an evidence collection.", "write", "POST", "/api/ai/v1/collections", {"name": {"type": "string"}, "description": {"type": "string"}}, ["name"]),
    _tool("njs.collections.add_note", "Add a note to an evidence collection and queue extraction.", "write", "POST", "/api/ai/v1/collections/{collection_id}/notes", {"collection_id": {"type": "string"}, "title": {"type": "string"}, "text": {"type": "string"}}, ["collection_id", "text"]),
    _tool("njs.collections.add_url", "Add a web URL to an evidence collection and queue ingestion.", "external", "POST", "/api/ai/v1/collections/{collection_id}/urls", {"collection_id": {"type": "string"}, "url": {"type": "string"}, "title": {"type": "string"}}, ["collection_id", "url"]),

    _tool("njs.feeds.list", "List RSS/news feeds.", "read", "GET", "/api/ai/v1/feeds", {"limit": {"type": "integer"}}),
    _tool("njs.feeds.create", "Add an RSS/news feed.", "write", "POST", "/api/ai/v1/feeds", {"url": {"type": "string"}, "title": {"type": "string"}}, ["url"]),
    _tool("njs.feeds.toggle", "Enable or disable an RSS/news feed.", "write", "POST", "/api/ai/v1/feeds/{feed_id}/toggle", {"feed_id": {"type": "string"}, "enabled": {"type": "boolean"}}, ["feed_id"]),
    _tool("njs.feeds.delete", "Delete an RSS/news feed without deleting historical content.", "destructive", "DELETE", "/api/ai/v1/feeds/{feed_id}", {"feed_id": {"type": "string"}}, ["feed_id"]),

    _tool("njs.workers.list", "List newsjacking workers.", "read", "GET", "/api/ai/v1/workers", {"enabled": {"type": "boolean"}, "limit": {"type": "integer"}}),
    _tool("njs.workers.read", "Read a newsjacking worker configuration.", "read", "GET", "/api/ai/v1/workers/{worker_id}", {"worker_id": {"type": "string"}}, ["worker_id"]),
    _tool("njs.workers.create", "Create a newsjacking worker connected to a campaign, feeds and products.", "write", "POST", "/api/ai/v1/workers", {"name": {"type": "string"}, "campaign_id": {"type": "string"}, "feed_ids": {"type": "array", "items": {"type": "string"}}, "product_ids": {"type": "array", "items": {"type": "string"}}, "enabled": {"type": "boolean"}, "min_confidence": {"type": "number"}, "max_articles_per_run": {"type": "integer"}, "editorial_instructions": {"type": "string"}}, ["name", "campaign_id", "feed_ids", "product_ids"]),
    _tool("njs.workers.update", "Update a newsjacking worker configuration.", "write", "PATCH", "/api/ai/v1/workers/{worker_id}", {"worker_id": {"type": "string"}}, ["worker_id"]),
    _tool("njs.workers.run", "Run a newsjacking worker now.", "external", "POST", "/api/ai/v1/workers/{worker_id}/run", {"worker_id": {"type": "string"}}, ["worker_id"]),
    _tool("njs.workers.toggle", "Enable or disable a newsjacking worker.", "write", "POST", "/api/ai/v1/workers/{worker_id}/toggle", {"worker_id": {"type": "string"}, "enabled": {"type": "boolean"}}, ["worker_id"]),
    _tool("njs.workers.delete", "Delete a newsjacking worker while retaining generated history.", "destructive", "DELETE", "/api/ai/v1/workers/{worker_id}", {"worker_id": {"type": "string"}}, ["worker_id"]),

    _tool("njs.articles.list", "List generated/review articles.", "read", "GET", "/api/ai/v1/articles", {"status": {"type": "string"}, "campaign_id": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("njs.articles.read", "Read an article and review/publication state.", "read", "GET", "/api/ai/v1/articles/{article_id}", {"article_id": {"type": "string"}}, ["article_id"]),
    _tool("njs.articles.request_changes", "Queue AI revision of an article from review notes.", "external", "POST", "/api/ai/v1/articles/{article_id}/request-changes", {"article_id": {"type": "string"}, "notes": {"type": "string"}}, ["article_id", "notes"]),
    _tool("njs.articles.sign_publish", "Sign the current article revision with the operator's configured author profile and publish it.", "external", "POST", "/api/ai/v1/articles/{article_id}/sign", {"article_id": {"type": "string"}}, ["article_id"]),

    _tool("njs.social.posts.list", "List generated social posts.", "read", "GET", "/api/ai/v1/social/posts", {"campaign_id": {"type": "string"}, "status": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("njs.social.posts.read", "Read one social post.", "read", "GET", "/api/ai/v1/social/posts/{post_id}", {"post_id": {"type": "string"}}, ["post_id"]),
    _tool("njs.social.generate", "Generate social copy/media for an article.", "external", "POST", "/api/ai/v1/articles/{article_id}/social/generate", {"article_id": {"type": "string"}, "platforms": {"type": "array", "items": {"type": "string"}}, "regenerate_text": {"type": "boolean"}, "regenerate_image": {"type": "boolean"}}, ["article_id"]),
    _tool("njs.social.posts.update", "Edit generated social post text.", "write", "PATCH", "/api/ai/v1/social/posts/{post_id}", {"post_id": {"type": "string"}, "text": {"type": "string"}}, ["post_id", "text"]),
    _tool("njs.social.posts.schedule", "Schedule or unschedule a social post.", "external", "POST", "/api/ai/v1/social/posts/{post_id}/schedule", {"post_id": {"type": "string"}, "scheduled_at": {"type": ["string", "null"]}}, ["post_id"]),
    _tool("njs.social.posts.archive", "Archive a social post.", "destructive", "POST", "/api/ai/v1/social/posts/{post_id}/archive", {"post_id": {"type": "string"}}, ["post_id"]),

    _tool("njs.newsletters.schedules.list", "List newsletter plans/schedules.", "read", "GET", "/api/ai/v1/newsletters/schedules", {"limit": {"type": "integer"}}),
    _tool("njs.newsletters.schedules.read", "Read a newsletter plan/schedule.", "read", "GET", "/api/ai/v1/newsletters/schedules/{schedule_id}", {"schedule_id": {"type": "string"}}, ["schedule_id"]),
    _tool("njs.newsletters.schedules.create", "Create a newsletter plan/schedule.", "write", "POST", "/api/ai/v1/newsletters/schedules", {"name": {"type": "string"}, "campaign_ids": {"type": "array", "items": {"type": "string"}}, "enabled": {"type": "boolean"}, "timezone": {"type": "string"}, "weekdays": {"type": "array", "items": {"type": "integer"}}, "ready_time": {"type": "string"}, "editorial_voice": {"type": "string"}, "audience_note": {"type": "string"}}, ["name"]),
    _tool("njs.newsletters.schedules.update", "Update a newsletter plan/schedule.", "write", "PATCH", "/api/ai/v1/newsletters/schedules/{schedule_id}", {"schedule_id": {"type": "string"}}, ["schedule_id"]),
    _tool("njs.newsletters.generate", "Generate a newsletter edition now.", "external", "POST", "/api/ai/v1/newsletters/schedules/{schedule_id}/generate", {"schedule_id": {"type": "string"}}, ["schedule_id"]),
    _tool("njs.newsletters.schedules.archive", "Archive a newsletter schedule.", "destructive", "POST", "/api/ai/v1/newsletters/schedules/{schedule_id}/archive", {"schedule_id": {"type": "string"}}, ["schedule_id"]),
    _tool("njs.newsletters.editions.list", "List newsletter editions.", "read", "GET", "/api/ai/v1/newsletters/editions", {"status": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("njs.newsletters.editions.read", "Read a newsletter edition including rendered content metadata.", "read", "GET", "/api/ai/v1/newsletters/editions/{edition_id}", {"edition_id": {"type": "string"}}, ["edition_id"]),
    _tool("njs.newsletters.editions.update", "Edit newsletter copy or revise the visual email design from a plain-language prompt. Visual revisions preserve the current edition and create revision history.", "write", "PATCH", "/api/ai/v1/newsletters/editions/{edition_id}", {"edition_id": {"type": "string"}, "subject": {"type": "string"}, "preheader": {"type": "string"}, "intro": {"type": "string"}, "closing": {"type": "string"}, "visual_prompt": {"type": "string"}, "apply_to_future": {"type": "boolean"}}, ["edition_id"]),
    _tool("njs.newsletters.editions.ready", "Mark a generated newsletter edition ready for distribution.", "write", "POST", "/api/ai/v1/newsletters/editions/{edition_id}/ready", {"edition_id": {"type": "string"}}, ["edition_id"]),
    _tool("njs.newsletters.editions.archive", "Archive a newsletter edition.", "destructive", "POST", "/api/ai/v1/newsletters/editions/{edition_id}/archive", {"edition_id": {"type": "string"}}, ["edition_id"]),
    _tool("njs.newsletters.distribute", "Create a Mailchimp draft or send a ready newsletter through the organization-scoped BlackBook bridge.", "external", "POST", "/api/ai/v1/newsletters/editions/{edition_id}/distribute", {"edition_id": {"type": "string"}, "action": {"type": "string", "enum": ["draft", "send"]}, "reply_to": {"type": "string"}}, ["edition_id", "action", "reply_to"]),

    _tool("njs.analytics.summary", "Read a concise analytics summary for the active workspace.", "read", "GET", "/api/ai/v1/analytics/summary", {"days": {"type": "integer"}}),
    _tool("njs.domains.list", "List publishing domains and status.", "read", "GET", "/api/ai/v1/domains", {"limit": {"type": "integer"}}),
    _tool("njs.sites.list", "List website sites/workspaces.", "read", "GET", "/api/ai/v1/sites", {"limit": {"type": "integer"}}),
    _tool("njs.landing_pages.list", "List landing pages in the active organization.", "read", "GET", "/api/ai/v1/landing-pages", {"campaign_id": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("njs.landing_pages.read", "Read one landing page, including editable HTML and publication state.", "read", "GET", "/api/ai/v1/landing-pages/{page_id}", {"page_id": {"type": "string"}}, ["page_id"]),
    _tool("njs.landing_pages.create_manual", "Create a landing page without AI from explicit HTML or structured starter copy.", "write", "POST", "/api/ai/v1/landing-pages/manual", {"title": {"type": "string"}, "html": {"type": "string"}, "headline": {"type": "string"}, "subheadline": {"type": "string"}, "body": {"type": "string"}, "cta_label": {"type": "string"}, "cta_url": {"type": "string"}, "page_kind": {"type": "string"}, "include_articles": {"type": "boolean"}, "meta_description": {"type": "string"}}, ["title"]),
    _tool("njs.landing_pages.create_ai", "Create and queue an AI-generated landing page from a campaign and/or selected Collection evidence.", "external", "POST", "/api/ai/v1/landing-pages/ai", {"title": {"type": "string"}, "campaign_id": {"type": "string"}, "evidence_item_ids": {"type": "array", "items": {"type": "string"}}, "prompt": {"type": "string"}, "page_kind": {"type": "string"}, "include_articles": {"type": "boolean"}}, ["title"]),
    _tool("njs.landing_pages.update", "Update landing-page identity, metadata and generation instructions without invoking AI.", "write", "PATCH", "/api/ai/v1/landing-pages/{page_id}", {"page_id": {"type": "string"}, "title": {"type": "string"}, "page_kind": {"type": "string"}, "prompt": {"type": "string"}, "include_articles": {"type": "boolean"}, "meta_title": {"type": "string"}, "meta_description": {"type": "string"}, "summary": {"type": "string"}}, ["page_id"]),
    _tool("njs.landing_pages.html.write", "Replace editable landing-page HTML manually and preserve the previous revision.", "write", "PUT", "/api/ai/v1/landing-pages/{page_id}/html", {"page_id": {"type": "string"}, "html": {"type": "string"}, "reason": {"type": "string"}}, ["page_id", "html"]),
    _tool("njs.landing_pages.generate", "Generate or fully regenerate a landing page through the NJS AI worker.", "external", "POST", "/api/ai/v1/landing-pages/{page_id}/generate", {"page_id": {"type": "string"}}, ["page_id"]),
    _tool("njs.landing_pages.ai.revise", "Revise the current landing page with AI from explicit review instructions while preserving the reviewed revision.", "external", "POST", "/api/ai/v1/landing-pages/{page_id}/ai-revise", {"page_id": {"type": "string"}, "notes": {"type": "string"}}, ["page_id", "notes"]),
    _tool("njs.landing_pages.duplicate", "Duplicate a landing page as an unpublished editable copy.", "write", "POST", "/api/ai/v1/landing-pages/{page_id}/duplicate", {"page_id": {"type": "string"}, "title": {"type": "string"}}, ["page_id"]),
    _tool("njs.landing_pages.publish", "Publish the current landing-page revision.", "external", "POST", "/api/ai/v1/landing-pages/{page_id}/publish", {"page_id": {"type": "string"}}, ["page_id"]),
    _tool("njs.landing_pages.unpublish", "Unpublish a landing page while retaining the draft and revision history.", "external", "POST", "/api/ai/v1/landing-pages/{page_id}/unpublish", {"page_id": {"type": "string"}}, ["page_id"]),
    _tool("njs.landing_pages.delete", "Permanently delete a landing page, its page revisions, AI change jobs and routes.", "destructive", "DELETE", "/api/ai/v1/landing-pages/{page_id}", {"page_id": {"type": "string"}, "confirm": {"type": "boolean"}}, ["page_id", "confirm"]),
    _tool("njs.landing_pages.versions.list", "List saved landing-page revisions.", "read", "GET", "/api/ai/v1/landing-pages/{page_id}/versions", {"page_id": {"type": "string"}}, ["page_id"]),
    _tool("njs.landing_pages.versions.restore", "Restore a saved landing-page revision as the new current revision.", "write", "POST", "/api/ai/v1/landing-pages/{page_id}/versions/{version_id}/restore", {"page_id": {"type": "string"}, "version_id": {"type": "string"}}, ["page_id", "version_id"]),
    _tool("njs.landing_pages.navigation.add", "Expose a published landing page in its website navigation.", "external", "POST", "/api/ai/v1/landing-pages/{page_id}/navigation/add", {"page_id": {"type": "string"}, "label": {"type": "string"}, "publish": {"type": "boolean"}}, ["page_id"]),
    _tool("njs.landing_pages.navigation.hide", "Hide a landing page from website navigation without deleting or unpublishing it.", "write", "POST", "/api/ai/v1/landing-pages/{page_id}/navigation/hide", {"page_id": {"type": "string"}}, ["page_id"]),
    _tool("njs.blackbook.status", "Read the organization-scoped BlackBook connector status.", "read", "GET", "/api/ai/v1/blackbook/status"),
    _tool("njs.blackbook.audience.preview", "Preview a BlackBook audience using segments, people, engagement buckets and canonical interests.", "read", "POST", "/api/ai/v1/blackbook/audience/preview", {"segment_ids": {"type": "array", "items": {"type": "string"}}, "person_ids": {"type": "array", "items": {"type": "string"}}, "include_all_eligible": {"type": "boolean"}, "engagement_buckets": {"type": "array", "items": {"type": "string", "enum": ["inactive","low","medium","high"]}}, "interest_ids": {"type": "array", "items": {"type": "string"}}, "interest_match": {"type": "string", "enum": ["any","all"]}}),
    _tool("njs.engagement.overview", "Read Mailchimp engagement queue/classification counts and current interest-catalog size.", "read", "GET", "/api/ai/v1/engagement"),
    _tool("njs.engagement.people.list", "List people by queue, failed, inactive, low, medium, high or all engagement states with canonical interests and failure details.", "read", "GET", "/api/ai/v1/engagement/people", {"bucket": {"type": "string", "enum": ["queue","failed","inactive","low","medium","high","all"]}, "q": {"type": "string"}, "interest": {"type": "string"}, "limit": {"type": "integer"}, "offset": {"type": "integer"}}),
    _tool("njs.engagement.queue.refresh", "Refresh BlackBook's Mailchimp audience so unprocessed people appear in the Engagement Intelligence queue.", "external", "POST", "/api/ai/v1/engagement/queue/refresh"),
    _tool("njs.engagement.bulk_analyze", "Bulk-import Mailchimp engagement and AI-analyze up to 2500 selected people. Per-contact terminal failures move to Failed without stopping the batch.", "external", "POST", "/api/ai/v1/engagement/bulk-analyze", {"person_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 2500}, "days": {"type": "integer"}}, ["person_ids"]),
    _tool("njs.engagement.interests.list", "List the reusable canonical engagement-interest catalog with person counts and engagement-bucket distribution.", "read", "GET", "/api/ai/v1/engagement/interests"),
    _tool("njs.engagement.batches.list", "List recent bulk Mailchimp engagement-analysis batches.", "read", "GET", "/api/ai/v1/engagement/batches", {"limit": {"type": "integer"}}),
    _tool("njs.engagement.batches.read", "Read one Mailchimp engagement-analysis batch and current progress/result.", "read", "GET", "/api/ai/v1/engagement/batches/{batch_id}", {"batch_id": {"type": "string"}}, ["batch_id"]),
]


def _jsonable(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _ok(**payload):
    return jsonify(_jsonable({"ok": True, **payload}))


def _error(message, status=400, code="invalid_request"):
    return jsonify({"ok": False, "error": code, "error_description": str(message)}), status


def _payload():
    return request.get_json(silent=True) or {}


def _oid(value, field="id"):
    try:
        return ObjectId(str(value))
    except Exception as exc:
        raise ValueError(f"Invalid {field}") from exc


def _limit(default=50, maximum=250):
    try:
        return max(1, min(maximum, int(request.args.get("limit") or default)))
    except (TypeError, ValueError):
        return default


def _extract_bearer():
    auth = str(request.headers.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return ""
    return auth.split(None, 1)[1].strip()


def _ensure_org(identity):
    syntal_org_id = str(identity.get("syntal_org_id") or "").strip()
    if not syntal_org_id:
        raise PermissionError("SSO identity has no active organization")
    link = sso_organization_links.find_one({"syntal_org_id": syntal_org_id})
    if link and link.get("local_organization_id"):
        return link["local_organization_id"]
    local_org_id = ObjectId()
    sso_organization_links.update_one(
        {"syntal_org_id": syntal_org_id},
        {"$setOnInsert": {"local_organization_id": local_org_id, "created_at": now()}, "$set": {"organization_name": identity.get("organization_name"), "updated_at": now()}},
        upsert=True,
    )
    link = sso_organization_links.find_one({"syntal_org_id": syntal_org_id}) or {}
    return link.get("local_organization_id") or local_org_id


def _ensure_user(identity, local_org_id):
    syntal_user_id = str(identity.get("syntal_user_id") or "").strip()
    email = str(identity.get("email") or "").strip().lower()
    doc = users.find_one({"syntal_user_id": syntal_user_id}) if syntal_user_id else None
    if not doc and email:
        doc = users.find_one({"email": {"$regex": "^" + __import__("re").escape(email) + "$", "$options": "i"}})
    fields = {
        "syntal_user_id": syntal_user_id,
        "syntal_org_id": identity.get("syntal_org_id"),
        "organization_id": local_org_id,
        "organization_name": identity.get("organization_name"),
        "sso_permissions": identity.get("permissions") or [],
        "auth_provider": "syntal_sso",
        "updated_at": now(),
        "last_sso_ai_access_at": now(),
    }
    if email:
        fields["email"] = email
    if identity.get("name"):
        fields["display_name"] = identity.get("name")
    if identity.get("username"):
        fields["username"] = identity.get("username")
    if doc:
        users.update_one({"_id": doc["_id"]}, {"$set": fields})
        return users.find_one({"_id": doc["_id"]})
    fields.update({"credits": 0, "created_at": now()})
    result = users.insert_one(fields)
    return users.find_one({"_id": result.inserted_id})


def _delegated_permission(token: str, org_id: str, permission: str) -> bool:
    try:
        response = requests.get(
            Config.SSO_AI_DELEGATION_ENDPOINT,
            params={"application": "njs", "permission": permission},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=Config.SSO_HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        return False
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return bool(
        isinstance(payload, dict)
        and payload.get("allowed") is True
        and str(payload.get("syntal_org_id") or "") == str(org_id or "")
        and str(payload.get("application") or "") == "njs"
        and str(payload.get("permission") or "") == str(permission)
    )


def _actor(admin=False):
    if str(request.headers.get("X-Syntal-AI") or "") != "1":
        raise PermissionError("This API is reserved for authenticated Syntal AI tool calls")
    token = _extract_bearer()
    if not token:
        raise PermissionError("Bearer token required")
    cfg = discovery()
    response = requests.get(cfg["userinfo_endpoint"], headers={"Authorization": f"Bearer {token}"}, timeout=Config.SSO_HTTP_TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise PermissionError("SSO rejected the bearer token")
    claims = response.json()
    if not claims.get("sub"):
        raise PermissionError("SSO userinfo is incomplete")
    identity = extract_identity(claims)
    permissions = set(extract_permissions(claims))
    identity["permissions"] = sorted(permissions)
    required = Config.SSO_ADMIN_PERMISSION if admin else Config.SSO_REQUIRED_PERMISSION
    if required and required not in permissions and "*" not in permissions:
        org_id = identity.get("syntal_org_id")
        if _delegated_permission(token, str(org_id or ""), required):
            permissions.add(required)
        else:
            raise PermissionError(f"Missing required permission: {required}")
    identity["permissions"] = sorted(permissions)
    header_user = str(request.headers.get("X-Syntal-Actor-User") or "").strip()
    header_org = str(request.headers.get("X-Syntal-Actor-Org") or "").strip()
    if header_user and header_user != str(identity.get("syntal_user_id") or ""):
        raise PermissionError("AI actor user does not match the SSO token")
    if header_org and header_org != str(identity.get("syntal_org_id") or ""):
        raise PermissionError("AI actor organization does not match the SSO token")
    local_org_id = _ensure_org(identity)
    user_doc = _ensure_user(identity, local_org_id)
    return {
        "identity": identity,
        "permissions": permissions,
        "local_org_id": local_org_id,
        "user_doc": user_doc,
        "user_id": user_doc["_id"],
    }


def _guard(admin=False):
    try:
        return _actor(admin=admin), None
    except PermissionError as exc:
        return None, _error(str(exc), 403, "forbidden")
    except Exception as exc:
        return None, _error(f"SSO authorization failed: {exc}", 503, "sso_unavailable")


def _scope(actor):
    return {"organization_id": actor["local_org_id"]}


def _find_owned(collection, actor, raw_id, field="id"):
    try:
        oid = _oid(raw_id, field)
    except ValueError:
        return None
    return collection.find_one({"_id": oid, **_scope(actor)})


def _owned_ids(collection, actor, raw_values, *, extra=None):
    ids = []
    for raw in raw_values or []:
        try:
            oid = ObjectId(str(raw))
        except Exception:
            continue
        if oid not in ids:
            ids.append(oid)
    if not ids:
        return []
    query = {"_id": {"$in": ids}, **_scope(actor)}
    if extra:
        query.update(extra)
    found = {row["_id"] for row in collection.find(query, {"_id": 1})}
    return [oid for oid in ids if oid in found]


def _sanitize_doc(doc, *, content_limit=18000):
    if not doc:
        return None
    out = dict(doc)
    for key in ("evidence_snapshot", "content", "published_content", "html", "text"):
        if key in out and isinstance(out[key], str) and len(out[key]) > content_limit:
            out[key] = out[key][:content_limit] + "…"
    return _jsonable(out)


@bp.get("/.well-known/syntal-ai-tools")
@bp.get("/api/ai/v1/manifest")
def manifest():
    # Discovery metadata is public. Every executable tool endpoint remains
    # protected by the Syntal AI marker, bearer validation, delegation check
    # and organization-scoped authorization in _guard().
    risk_counts = {}
    groups = {}
    for tool in TOOLS:
        risk_counts[tool["risk"]] = risk_counts.get(tool["risk"], 0) + 1
        group = tool["name"].split(".")[1] if len(tool["name"].split(".")) > 1 else "other"
        groups[group] = groups.get(group, 0) + 1
    return jsonify({
        "version": "1", "application": "njs", "name": "NJS",
        "app_version": Config.APP_VERSION, "tool_count": len(TOOLS),
        "risk_counts": risk_counts, "groups": groups, "tools": TOOLS,
        "authorization": {
            "type": "syntal_sso_delegated_bearer",
            "marker_header": "X-Syntal-AI", "marker_value": "1",
            "delegation_endpoint": "/v1/ai/delegation-check",
        },
    })


@bp.get("/api/ai/v1/workspace/summary")
def workspace_summary():
    actor, error = _guard()
    if error: return error
    scope = _scope(actor)
    return _ok(
        organization={"syntal_org_id": actor["identity"].get("syntal_org_id"), "name": actor["identity"].get("organization_name")},
        counts={
            "campaigns": campaigns.count_documents(scope), "products": products.count_documents(scope),
            "collections": collections.count_documents(scope), "feeds": rss_feeds.count_documents(scope),
            "workers": newsjacking_workers.count_documents(scope), "articles": articles.count_documents(scope),
            "social_posts": social_media_posts.count_documents(scope), "newsletter_schedules": newsletter_schedules.count_documents(scope),
            "newsletter_editions": newsletter_editions.count_documents(scope), "domains": domain_mappings.count_documents(scope),
            "sites": website_sites.count_documents(scope), "landing_pages": landing_pages.count_documents(scope),
        },
        blackbook=blackbook_connector_status(actor["local_org_id"]),
    )


@bp.get("/api/ai/v1/campaigns")
def campaigns_list():
    actor, error = _guard()
    if error: return error
    q = _scope(actor)
    status = clean_text(request.args.get("status") or "", 30)
    if status: q["status"] = status
    rows = list(campaigns.find(q).sort("updated_at", -1).limit(_limit()))
    return _ok(items=[_sanitize_doc(x, content_limit=4000) for x in rows])


@bp.get("/api/ai/v1/campaigns/<campaign_id>")
def campaigns_read(campaign_id):
    actor, error = _guard()
    if error: return error
    row = _find_owned(campaigns, actor, campaign_id, "campaign_id")
    return _ok(item=_sanitize_doc(row)) if row else _error("Campaign not found", 404, "not_found")


@bp.post("/api/ai/v1/campaigns")
def campaigns_create():
    actor, error = _guard(admin=True)
    if error: return error
    body = _payload(); title = clean_text(body.get("title"), 300)
    if len(title) < 3: return _error("Campaign title must be at least three characters")
    evidence_ids = _owned_ids(collection_items, actor, body.get("evidence_item_ids") or [])
    if not evidence_ids: return _error("At least one evidence_item_id from this organization is required")
    docs = list(collection_items.find({"_id": {"$in": evidence_ids}}))
    collection_ids = list(dict.fromkeys(x.get("collection_id") for x in docs if x.get("collection_id")))
    snapshot = "\n\n".join(clean_text(x.get("text") or x.get("content") or x.get("summary") or "", 8000) for x in docs)[:60000]
    doc = {
        "user_id": actor["user_id"], "organization_id": actor["local_org_id"], "title": title,
        "campaign_context": clean_text(body.get("campaign_context"), 2500), "collection_ids": collection_ids,
        "evidence_item_ids": evidence_ids, "evidence_count": len(evidence_ids), "evidence_snapshot": snapshot,
        "target_audience": {}, "campaign_goal": {}, "content_overview": {}, "engagement_engine": {},
        "filter_keywords": [], "status": "draft", "setup_status": "queued" if body.get("prepare_strategy", True) else "draft",
        "setup_version": "1.9", "created_at": now(), "updated_at": now(),
    }
    cid = campaigns.insert_one(doc).inserted_id
    job_id = None
    if body.get("prepare_strategy", True):
        try: job_id = prepare_campaign_strategy_task.delay(str(cid)).id
        except Exception as exc: campaigns.update_one({"_id": cid}, {"$set": {"setup_status": "failed", "setup_error": str(exc)[:1000]}})
    return _ok(item=_sanitize_doc(campaigns.find_one({"_id": cid})), job_id=job_id)


@bp.patch("/api/ai/v1/campaigns/<campaign_id>")
def campaigns_update(campaign_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(campaigns, actor, campaign_id, "campaign_id")
    if not row: return _error("Campaign not found", 404, "not_found")
    body = _payload(); updates = {"updated_at": now()}
    if "title" in body: updates["title"] = clean_text(body.get("title"), 300) or row.get("title")
    if "campaign_context" in body: updates["campaign_context"] = clean_text(body.get("campaign_context"), 2500)
    if "status" in body and body.get("status") in {"active", "draft", "paused"}: updates["status"] = body["status"]
    if "filter_keywords" in body: updates["filter_keywords"] = [clean_text(x, 100) for x in body.get("filter_keywords") or [] if clean_text(x, 100)][:20]
    campaigns.update_one({"_id": row["_id"]}, {"$set": updates})
    return _ok(item=_sanitize_doc(campaigns.find_one({"_id": row["_id"]})))


@bp.post("/api/ai/v1/campaigns/<campaign_id>/prepare")
def campaigns_prepare(campaign_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(campaigns, actor, campaign_id, "campaign_id")
    if not row: return _error("Campaign not found", 404, "not_found")
    campaigns.update_one({"_id": row["_id"]}, {"$set": {"setup_status": "queued", "updated_at": now()}, "$unset": {"setup_error": ""}})
    result = prepare_campaign_strategy_task.delay(str(row["_id"]))
    return _ok(campaign_id=str(row["_id"]), job_id=result.id, status="queued")


@bp.get("/api/ai/v1/products")
def products_list():
    actor, error = _guard()
    if error: return error
    q = _scope(actor); status = clean_text(request.args.get("status") or "", 30)
    if status: q["status"] = status
    return _ok(items=[_sanitize_doc(x, content_limit=4000) for x in products.find(q).sort("updated_at", -1).limit(_limit())])


@bp.get("/api/ai/v1/products/<product_id>")
def products_read(product_id):
    actor, error = _guard()
    if error: return error
    row = _find_owned(products, actor, product_id, "product_id")
    return _ok(item=_sanitize_doc(row)) if row else _error("Product not found", 404, "not_found")


def _product_fields(body, existing=None):
    existing = existing or {}
    name = clean_text(body.get("name") if "name" in body else existing.get("name"), 300)
    product_url = clean_text(body.get("product_url") if "product_url" in body else existing.get("product_url"), 2000)
    if len(name) < 2 or not product_url.startswith(("http://", "https://")):
        raise ValueError("Product name and http(s) product_url are required")
    cta_url = clean_text(body.get("cta_url") if "cta_url" in body else existing.get("cta_url") or product_url, 2000)
    if cta_url and not cta_url.startswith(("http://", "https://")):
        raise ValueError("cta_url must be http(s)")
    status = body.get("status", existing.get("status", "active"))
    if status not in {"active", "draft", "paused"}: status = "active"
    return {
        "name": name, "tagline": clean_text(body.get("tagline", existing.get("tagline")), 600),
        "description": clean_text(body.get("description", existing.get("description")), 5000),
        "positioning": clean_text(body.get("positioning", existing.get("positioning")), 5000),
        "product_url": product_url, "cta_label": clean_text(body.get("cta_label", existing.get("cta_label") or "Learn more"), 120) or "Learn more",
        "cta_url": cta_url or product_url, "status": status, "updated_at": now(),
    }


@bp.post("/api/ai/v1/products")
def products_create():
    actor, error = _guard(admin=True)
    if error: return error
    try: fields = _product_fields(_payload())
    except ValueError as exc: return _error(str(exc))
    fields.update({"user_id": actor["user_id"], "organization_id": actor["local_org_id"], "created_at": now(), "evidence_item_ids": [], "collection_ids": [], "proof_points": [], "claims_to_avoid": []})
    pid = products.insert_one(fields).inserted_id
    return _ok(item=_sanitize_doc(products.find_one({"_id": pid})))


@bp.patch("/api/ai/v1/products/<product_id>")
def products_update(product_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(products, actor, product_id, "product_id")
    if not row: return _error("Product not found", 404, "not_found")
    try: fields = _product_fields(_payload(), row)
    except ValueError as exc: return _error(str(exc))
    products.update_one({"_id": row["_id"]}, {"$set": fields})
    return _ok(item=_sanitize_doc(products.find_one({"_id": row["_id"]})))


@bp.delete("/api/ai/v1/products/<product_id>")
def products_delete(product_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(products, actor, product_id, "product_id")
    if not row: return _error("Product not found", 404, "not_found")
    if newsjacking_workers.count_documents({**_scope(actor), "product_ids": row["_id"]}): return _error("Product is still attached to a newsjacking worker", 409, "conflict")
    products.delete_one({"_id": row["_id"]})
    return _ok(deleted=True, product_id=product_id)


@bp.get("/api/ai/v1/collections")
def collections_list():
    actor, error = _guard()
    if error: return error
    rows = []
    for row in collections.find(_scope(actor)).sort("updated_at", -1).limit(_limit()):
        item = _sanitize_doc(row, content_limit=2000)
        item["source_count"] = collection_sources.count_documents({"collection_id": row["_id"]})
        item["item_count"] = collection_items.count_documents({"collection_id": row["_id"], "active": {"$ne": False}})
        rows.append(item)
    return _ok(items=rows)


@bp.get("/api/ai/v1/collections/<collection_id>")
def collections_read(collection_id):
    actor, error = _guard()
    if error: return error
    row = _find_owned(collections, actor, collection_id, "collection_id")
    if not row: return _error("Collection not found", 404, "not_found")
    limit = _limit(60, 200)
    items = list(collection_items.find({"collection_id": row["_id"], "active": {"$ne": False}}).sort("created_at", -1).limit(limit))
    return _ok(item=_sanitize_doc(row), items=[_sanitize_doc(x, content_limit=6000) for x in items])


@bp.post("/api/ai/v1/collections")
def collections_create():
    actor, error = _guard(admin=True)
    if error: return error
    body = _payload(); name = clean_text(body.get("name"), 220)
    if not name: return _error("Collection name is required")
    doc = {"user_id": actor["user_id"], "organization_id": actor["local_org_id"], "name": name, "description": clean_text(body.get("description"), 1500), "created_at": now(), "updated_at": now()}
    cid = collections.insert_one(doc).inserted_id
    return _ok(item=_sanitize_doc(collections.find_one({"_id": cid})))


def _queue_collection_source(actor, collection, source_type, **fields):
    doc = {"collection_id": collection["_id"], "user_id": actor["user_id"], "organization_id": actor["local_org_id"], "source_type": source_type, "status": "queued", "created_at": now(), "updated_at": now(), **fields}
    sid = collection_sources.insert_one(doc).inserted_id
    collections.update_one({"_id": collection["_id"]}, {"$set": {"updated_at": now()}})
    result = process_collection_source_task.delay(str(sid))
    return sid, result.id


@bp.post("/api/ai/v1/collections/<collection_id>/notes")
def collections_add_note(collection_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(collections, actor, collection_id, "collection_id")
    if not row: return _error("Collection not found", 404, "not_found")
    body = _payload(); text = clean_text(body.get("text"), 50000)
    if not text: return _error("text is required")
    sid, job = _queue_collection_source(actor, row, "note", title=clean_text(body.get("title"), 300) or "AI note", text=text)
    return _ok(source_id=str(sid), job_id=job, status="queued")


@bp.post("/api/ai/v1/collections/<collection_id>/urls")
def collections_add_url(collection_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(collections, actor, collection_id, "collection_id")
    if not row: return _error("Collection not found", 404, "not_found")
    body = _payload(); url = clean_text(body.get("url"), 2000)
    if not url.startswith(("http://", "https://")): return _error("A complete http(s) URL is required")
    existing = collection_sources.find_one({"collection_id": row["_id"], "source_type": "url", "url": url})
    if existing: return _ok(source_id=str(existing["_id"]), status=existing.get("status"), existing=True)
    sid, job = _queue_collection_source(actor, row, "url", url=url, title=clean_text(body.get("title"), 300) or url)
    return _ok(source_id=str(sid), job_id=job, status="queued")


@bp.get("/api/ai/v1/feeds")
def feeds_list():
    actor, error = _guard()
    if error: return error
    return _ok(items=[_sanitize_doc(x) for x in rss_feeds.find(_scope(actor)).sort("title", 1).limit(_limit())])


@bp.post("/api/ai/v1/feeds")
def feeds_create():
    actor, error = _guard(admin=True)
    if error: return error
    body = _payload()
    try: url = normalize_feed_url(body.get("url"))
    except ValueError as exc: return _error(str(exc))
    existing = rss_feeds.find_one({**_scope(actor), "url": url})
    if existing: return _ok(item=_sanitize_doc(existing), existing=True)
    doc = {"user_id": actor["user_id"], "organization_id": actor["local_org_id"], "title": clean_text(body.get("title"), 300) or url, "url": url, "is_active": True, "created_at": now()}
    fid = rss_feeds.insert_one(doc).inserted_id
    return _ok(item=_sanitize_doc(rss_feeds.find_one({"_id": fid})))


@bp.post("/api/ai/v1/feeds/<feed_id>/toggle")
def feeds_toggle(feed_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(rss_feeds, actor, feed_id, "feed_id")
    if not row: return _error("Feed not found", 404, "not_found")
    body = _payload(); enabled = bool(body.get("enabled")) if "enabled" in body else not row.get("is_active", True)
    rss_feeds.update_one({"_id": row["_id"]}, {"$set": {"is_active": enabled}})
    return _ok(feed_id=feed_id, enabled=enabled)


@bp.delete("/api/ai/v1/feeds/<feed_id>")
def feeds_delete(feed_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(rss_feeds, actor, feed_id, "feed_id")
    if not row: return _error("Feed not found", 404, "not_found")
    rss_feeds.delete_one({"_id": row["_id"]})
    return _ok(deleted=True, feed_id=feed_id)


@bp.get("/api/ai/v1/workers")
def workers_list():
    actor, error = _guard()
    if error: return error
    q = _scope(actor)
    if "enabled" in request.args: q["enabled"] = str(request.args.get("enabled")).lower() in {"1", "true", "yes", "on"}
    return _ok(items=[_sanitize_doc(x, content_limit=3000) for x in newsjacking_workers.find(q).sort("updated_at", -1).limit(_limit())])


@bp.get("/api/ai/v1/workers/<worker_id>")
def workers_read(worker_id):
    actor, error = _guard()
    if error: return error
    row = _find_owned(newsjacking_workers, actor, worker_id, "worker_id")
    return _ok(item=_sanitize_doc(row)) if row else _error("Worker not found", 404, "not_found")


def _worker_fields(actor, body, existing=None):
    existing = existing or {}; name = clean_text(body.get("name", existing.get("name")), 200)
    if len(name) < 2: raise ValueError("Worker name is required")
    campaign_id = body.get("campaign_id", str(existing.get("campaign_id") or "")); campaign = _find_owned(campaigns, actor, campaign_id, "campaign_id")
    if not campaign or campaign.get("status") != "active": raise ValueError("An active campaign_id is required")
    raw_feeds = body.get("feed_ids") if "feed_ids" in body else [str(x) for x in existing.get("feed_ids") or []]
    raw_products = body.get("product_ids") if "product_ids" in body else [str(x) for x in existing.get("product_ids") or []]
    feed_ids = _owned_ids(rss_feeds, actor, raw_feeds, extra={"is_active": True}); product_ids = _owned_ids(products, actor, raw_products, extra={"status": "active"})
    if not feed_ids: raise ValueError("At least one active feed is required")
    if not product_ids: raise ValueError("At least one active product is required")
    min_conf = max(0.0, min(1.0, float(body.get("min_confidence", existing.get("min_confidence", 0.68)))))
    max_articles = max(1, min(Config.NEWSJACKING_MAX_ARTICLES_PER_RUN, int(body.get("max_articles_per_run", existing.get("max_articles_per_run", 5)))))
    enabled = bool(body.get("enabled", existing.get("enabled", False)))
    return {"name": name, "description": clean_text(body.get("description", existing.get("description")), 1200), "campaign_id": campaign["_id"], "feed_ids": feed_ids, "product_ids": product_ids, "enabled": enabled, "enabled_at": now() if enabled else existing.get("enabled_at"), "min_confidence": min_conf, "max_articles_per_run": max_articles, "placement_mode": body.get("placement_mode", existing.get("placement_mode", "subtle")) if body.get("placement_mode", existing.get("placement_mode", "subtle")) in {"subtle", "contextual", "conversion"} else "subtle", "editorial_instructions": clean_text(body.get("editorial_instructions", existing.get("editorial_instructions")), 4000), "updated_at": now()}


@bp.post("/api/ai/v1/workers")
def workers_create():
    actor, error = _guard(admin=True)
    if error: return error
    try: fields = _worker_fields(actor, _payload())
    except (ValueError, TypeError) as exc: return _error(str(exc))
    fields.update({"user_id": actor["user_id"], "organization_id": actor["local_org_id"], "evidence_item_ids": [], "evidence_snapshot": "", "learning_mode": "observe", "learning_objective": "conversion_rate", "learning_min_views": 80, "learning_lookback_days": 90, "created_at": now()})
    wid = newsjacking_workers.insert_one(fields).inserted_id
    return _ok(item=_sanitize_doc(newsjacking_workers.find_one({"_id": wid})))


@bp.patch("/api/ai/v1/workers/<worker_id>")
def workers_update(worker_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(newsjacking_workers, actor, worker_id, "worker_id")
    if not row: return _error("Worker not found", 404, "not_found")
    try: fields = _worker_fields(actor, _payload(), row)
    except (ValueError, TypeError) as exc: return _error(str(exc))
    newsjacking_workers.update_one({"_id": row["_id"]}, {"$set": fields})
    return _ok(item=_sanitize_doc(newsjacking_workers.find_one({"_id": row["_id"]})))


@bp.post("/api/ai/v1/workers/<worker_id>/run")
def workers_run(worker_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(newsjacking_workers, actor, worker_id, "worker_id")
    if not row: return _error("Worker not found", 404, "not_found")
    result = newsjacking_scan_worker_task.delay(str(row["_id"]))
    return _ok(worker_id=worker_id, job_id=result.id, status="queued")


@bp.post("/api/ai/v1/workers/<worker_id>/toggle")
def workers_toggle(worker_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(newsjacking_workers, actor, worker_id, "worker_id")
    if not row: return _error("Worker not found", 404, "not_found")
    body = _payload(); enabled = bool(body.get("enabled")) if "enabled" in body else not bool(row.get("enabled"))
    update = {"enabled": enabled, "updated_at": now(), "lock_until": None}
    if enabled: update["enabled_at"] = now()
    newsjacking_workers.update_one({"_id": row["_id"]}, {"$set": update})
    return _ok(worker_id=worker_id, enabled=enabled)


@bp.delete("/api/ai/v1/workers/<worker_id>")
def workers_delete(worker_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(newsjacking_workers, actor, worker_id, "worker_id")
    if not row: return _error("Worker not found", 404, "not_found")
    newsjacking_workers.delete_one({"_id": row["_id"]})
    return _ok(deleted=True, worker_id=worker_id)


@bp.get("/api/ai/v1/articles")
def articles_list():
    actor, error = _guard()
    if error: return error
    q = _scope(actor); status = clean_text(request.args.get("status") or "", 40)
    if status: q["review_status"] = status
    campaign_id = request.args.get("campaign_id")
    if campaign_id:
        try: q["campaign_id"] = ObjectId(campaign_id)
        except Exception: return _error("Invalid campaign_id")
    fields = {"content": 0, "published_content": 0, "evidence_snapshot": 0}
    return _ok(items=[_sanitize_doc(x, content_limit=2000) for x in articles.find(q, fields).sort("created_at", -1).limit(_limit())])


@bp.get("/api/ai/v1/articles/<article_id>")
def articles_read(article_id):
    actor, error = _guard()
    if error: return error
    row = _find_owned(articles, actor, article_id, "article_id")
    return _ok(item=_sanitize_doc(row, content_limit=30000)) if row else _error("Article not found", 404, "not_found")


@bp.post("/api/ai/v1/articles/<article_id>/request-changes")
def articles_request_changes(article_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(articles, actor, article_id, "article_id")
    if not row: return _error("Article not found", 404, "not_found")
    notes = clean_text(_payload().get("notes"), 12000)
    if not notes: return _error("notes are required")
    revision = int(row.get("revision") or 1)
    articles.update_one({"_id": row["_id"]}, {"$set": {"review_notes": notes, "review_notes_revision": revision, "review_notes_updated_at": now(), "review_status": "changes_queued", "status": "review", "updated_at": now()}})
    job = {"user_id": actor["user_id"], "organization_id": actor["local_org_id"], "article_id": row["_id"], "version_id": None, "base_revision": revision, "notes": notes, "status": "queued", "created_at": now(), "updated_at": now()}
    job["_id"] = article_change_jobs.insert_one(job).inserted_id
    result = apply_article_review_notes_task.delay(str(job["_id"])); article_change_jobs.update_one({"_id": job["_id"]}, {"$set": {"celery_task_id": result.id}})
    return _ok(article_id=article_id, change_job_id=str(job["_id"]), celery_job_id=result.id, status="queued")


@bp.post("/api/ai/v1/articles/<article_id>/sign")
def articles_sign(article_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(articles, actor, article_id, "article_id")
    if not row: return _error("Article not found", 404, "not_found")
    user_doc = actor["user_doc"] or {}; profile = user_doc.get("author_profile") or {}
    full_name = clean_text(profile.get("full_name"), 180); image_url = str(profile.get("image_url") or "").strip()
    if not full_name or not image_url: return _error("The Syntal/NJS operator must complete an NJS author profile before signing articles", 409, "author_profile_required")
    if article_change_jobs.count_documents({"article_id": row["_id"], "status": {"$in": ["queued", "processing", "retrying"]}}): return _error("Article changes are still processing", 409, "conflict")
    revision = int(row.get("revision") or 1); signed_at = now(); signature_id = secrets.token_urlsafe(16)
    content_hash = hashlib.sha256((row.get("content") or "").encode("utf-8")).hexdigest()
    material = "|".join([str(row["_id"]), str(revision), str(actor["user_id"]), str(actor["identity"].get("syntal_user_id") or ""), signed_at.isoformat(), content_hash, signature_id])
    publication = {"revision": revision, "signed_at": signed_at, "signed_by_user_id": actor["user_id"], "signed_by_syntal_user_id": actor["identity"].get("syntal_user_id"), "signed_by_syntal_org_id": actor["identity"].get("syntal_org_id"), "organization_name": actor["identity"].get("organization_name"), "signature_id": signature_id, "approval_checksum_sha256": hashlib.sha256(material.encode("utf-8")).hexdigest(), "content_sha256": content_hash, "author": {"full_name": full_name, "image_url": image_url}}
    result = articles.update_one({"_id": row["_id"], "revision": revision}, {"$set": {"review_status": "signed", "status": "published", "published": True, "published_at": signed_at, "published_revision": revision, "published_content": row.get("content") or "", "published_metadata": row.get("metadata") or {}, "published_quality": row.get("quality") or {}, "publication": publication, "updated_at": now()}})
    if not result.matched_count: return _error("Article changed before signature could be applied", 409, "conflict")
    return _ok(article_id=article_id, published=True, revision=revision, publication=publication)


@bp.get("/api/ai/v1/social/posts")
def social_posts_list():
    actor, error = _guard()
    if error: return error
    q = _scope(actor)
    if request.args.get("status"): q["status"] = clean_text(request.args.get("status"), 40)
    if request.args.get("campaign_id"):
        try: q["campaign_id"] = ObjectId(request.args["campaign_id"])
        except Exception: return _error("Invalid campaign_id")
    return _ok(items=[_sanitize_doc(x, content_limit=5000) for x in social_media_posts.find(q).sort("updated_at", -1).limit(_limit())])


@bp.get("/api/ai/v1/social/posts/<post_id>")
def social_posts_read(post_id):
    actor, error = _guard()
    if error: return error
    row = _find_owned(social_media_posts, actor, post_id, "post_id")
    return _ok(item=_sanitize_doc(row)) if row else _error("Social post not found", 404, "not_found")


@bp.post("/api/ai/v1/articles/<article_id>/social/generate")
def social_generate(article_id):
    actor, error = _guard(admin=True)
    if error: return error
    article = _find_owned(articles, actor, article_id, "article_id")
    if not article: return _error("Article not found", 404, "not_found")
    body = _payload(); allowed = {"linkedin", "x", "facebook", "instagram", "threads", "bluesky"}; platforms = [p for p in body.get("platforms") or [] if p in allowed]
    if not platforms: platforms = ["linkedin", "x"]
    result = generate_social_media_bundle_task.delay(str(article["_id"]), platforms=platforms, source_type="syntal-ai", regenerate_text=bool(body.get("regenerate_text")), regenerate_image=bool(body.get("regenerate_image")))
    return _ok(article_id=article_id, platforms=platforms, job_id=result.id, status="queued")


@bp.patch("/api/ai/v1/social/posts/<post_id>")
def social_posts_update(post_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(social_media_posts, actor, post_id, "post_id")
    if not row: return _error("Social post not found", 404, "not_found")
    text = str(_payload().get("text") or "").strip(); limits = {"linkedin": 3000, "x": 280, "facebook": 5000, "instagram": 2200, "threads": 500, "bluesky": 300}; maximum = limits.get(row.get("platform") or "linkedin", 5000)
    if not text or len(text) > maximum: return _error(f"text must contain 1-{maximum} characters")
    social_media_posts.update_one({"_id": row["_id"]}, {"$set": {"text": text, "char_count": len(text), "is_manually_edited": True, "updated_at": now()}})
    return _ok(item=_sanitize_doc(social_media_posts.find_one({"_id": row["_id"]})))


@bp.post("/api/ai/v1/social/posts/<post_id>/schedule")
def social_posts_schedule(post_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(social_media_posts, actor, post_id, "post_id")
    if not row: return _error("Social post not found", 404, "not_found")
    raw = _payload().get("scheduled_at")
    if not raw:
        social_media_posts.update_one({"_id": row["_id"]}, {"$set": {"scheduled_at": None, "status": "draft", "updated_at": now()}}); return _ok(post_id=post_id, status="draft", scheduled_at=None)
    try:
        scheduled = datetime.fromisoformat(str(raw).replace("Z", "+00:00")); scheduled = scheduled.replace(tzinfo=timezone.utc) if scheduled.tzinfo is None else scheduled.astimezone(timezone.utc)
    except ValueError: return _error("scheduled_at must be an ISO 8601 timestamp")
    social_media_posts.update_one({"_id": row["_id"]}, {"$set": {"scheduled_at": scheduled, "status": "scheduled", "updated_at": now()}})
    return _ok(post_id=post_id, status="scheduled", scheduled_at=scheduled)


@bp.post("/api/ai/v1/social/posts/<post_id>/archive")
def social_posts_archive(post_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(social_media_posts, actor, post_id, "post_id")
    if not row: return _error("Social post not found", 404, "not_found")
    social_media_posts.update_one({"_id": row["_id"]}, {"$set": {"status": "archived", "archived_at": now(), "updated_at": now()}})
    return _ok(post_id=post_id, status="archived")


def _newsletter_fields(actor, body, existing=None):
    existing = existing or {}; name = clean_text(body.get("name", existing.get("name") or "Weekly briefing"), 120)
    if not name: raise ValueError("Newsletter name is required")
    raw_campaigns = body.get("campaign_ids") if "campaign_ids" in body else [str(x) for x in existing.get("campaign_ids") or []]
    campaign_ids = _owned_ids(campaigns, actor, raw_campaigns)
    weekdays = []
    for raw in body.get("weekdays", existing.get("weekdays") or [0]):
        try: day = int(raw)
        except (TypeError, ValueError): continue
        if 0 <= day <= 6 and day not in weekdays: weekdays.append(day)
    min_stories = max(1, min(20, int(body.get("min_stories", existing.get("min_stories", 3))))); max_stories = max(min_stories, min(25, int(body.get("max_stories", existing.get("max_stories", 7)))))
    return {"name": name, "enabled": bool(body.get("enabled", existing.get("enabled", False))), "campaign_ids": campaign_ids, "timezone": clean_text(body.get("timezone", existing.get("timezone") or "Europe/Skopje"), 80), "weekdays": weekdays or [0], "ready_time": clean_text(body.get("ready_time", existing.get("ready_time") or "07:30"), 20), "generation_lead_minutes": max(0, min(1440, int(body.get("generation_lead_minutes", existing.get("generation_lead_minutes", 90))))), "lookback_days": max(1, min(30, int(body.get("lookback_days", existing.get("lookback_days", 7))))), "min_stories": min_stories, "max_stories": max_stories, "editorial_voice": clean_text(body.get("editorial_voice", existing.get("editorial_voice") or "Concise, useful, factual, and editorial."), 1600), "audience_note": clean_text(body.get("audience_note", existing.get("audience_note") or "Busy readers who want the most important developments and why they matter."), 1200), "subject_style": clean_text(body.get("subject_style", existing.get("subject_style") or "Specific and informative; avoid clickbait."), 900), "sender_name": clean_text(body.get("sender_name", existing.get("sender_name") or name), 120), "cta_label": clean_text(body.get("cta_label", existing.get("cta_label") or "Read the full story"), 60), "cta_url": clean_text(body.get("cta_url", existing.get("cta_url") or ""), 1000), "updated_at": now()}


@bp.get("/api/ai/v1/newsletters/schedules")
def newsletter_schedules_list():
    actor, error = _guard()
    if error: return error
    return _ok(items=[_sanitize_doc(x) for x in newsletter_schedules.find(_scope(actor)).sort("updated_at", -1).limit(_limit())])


@bp.get("/api/ai/v1/newsletters/schedules/<schedule_id>")
def newsletter_schedules_read(schedule_id):
    actor, error = _guard()
    if error: return error
    row = _find_owned(newsletter_schedules, actor, schedule_id, "schedule_id")
    return _ok(item=_sanitize_doc(row)) if row else _error("Newsletter schedule not found", 404, "not_found")


@bp.post("/api/ai/v1/newsletters/schedules")
def newsletter_schedules_create():
    actor, error = _guard(admin=True)
    if error: return error
    try: fields = _newsletter_fields(actor, _payload())
    except (ValueError, TypeError) as exc: return _error(str(exc))
    fields.update({"user_id": actor["user_id"], "organization_id": actor["local_org_id"], "created_at": now()})
    sid = newsletter_schedules.insert_one(fields).inserted_id
    return _ok(item=_sanitize_doc(newsletter_schedules.find_one({"_id": sid})))


@bp.patch("/api/ai/v1/newsletters/schedules/<schedule_id>")
def newsletter_schedules_update(schedule_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(newsletter_schedules, actor, schedule_id, "schedule_id")
    if not row: return _error("Newsletter schedule not found", 404, "not_found")
    try: fields = _newsletter_fields(actor, _payload(), row)
    except (ValueError, TypeError) as exc: return _error(str(exc))
    newsletter_schedules.update_one({"_id": row["_id"]}, {"$set": fields})
    return _ok(item=_sanitize_doc(newsletter_schedules.find_one({"_id": row["_id"]})))


@bp.post("/api/ai/v1/newsletters/schedules/<schedule_id>/generate")
def newsletter_generate(schedule_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(newsletter_schedules, actor, schedule_id, "schedule_id")
    if not row: return _error("Newsletter schedule not found", 404, "not_found")
    result = generate_newsletter_edition_task.delay(str(row["_id"]), now().isoformat(), True)
    return _ok(schedule_id=schedule_id, job_id=result.id, status="queued")


@bp.post("/api/ai/v1/newsletters/schedules/<schedule_id>/archive")
def newsletter_schedules_archive(schedule_id):
    actor, error = _guard(admin=True)
    if error: return error
    row = _find_owned(newsletter_schedules, actor, schedule_id, "schedule_id")
    if not row: return _error("Newsletter schedule not found", 404, "not_found")
    newsletter_schedules.update_one({"_id": row["_id"]}, {"$set": {"enabled": False, "archived_at": now(), "updated_at": now()}})
    return _ok(schedule_id=schedule_id, archived=True)


@bp.get("/api/ai/v1/newsletters/editions")
def newsletter_editions_list():
    actor, error = _guard()
    if error: return error
    q = _scope(actor)
    if request.args.get("status"): q["status"] = clean_text(request.args.get("status"), 30)
    fields = {"html": 0, "text": 0}
    return _ok(items=[_sanitize_doc(x) for x in newsletter_editions.find(q, fields).sort("due_at", -1).limit(_limit())])


@bp.get("/api/ai/v1/newsletters/editions/<edition_id>")
def newsletter_editions_read(edition_id):
    actor, error = _guard()
    if error: return error
    row = _find_owned(newsletter_editions, actor, edition_id, "edition_id")
    return _ok(item=_sanitize_doc(row, content_limit=30000)) if row else _error("Newsletter edition not found", 404, "not_found")


@bp.patch("/api/ai/v1/newsletters/editions/<edition_id>")
def newsletter_editions_update(edition_id):
    actor, error = _guard(admin=True)
    if error: return error
    edition = _find_owned(newsletter_editions, actor, edition_id, "edition_id")
    if not edition: return _error("Newsletter edition not found", 404, "not_found")
    schedule = newsletter_schedules.find_one({"_id": edition.get("schedule_id"), **_scope(actor)}) or {}; body = _payload()
    visual_prompt = clean_text(body.get("visual_prompt") or "", 12000)
    if visual_prompt:
        stamped = now(); base_revision = int(edition.get("design_revision") or 1)
        job = {"user_id": actor["user_id"], "organization_id": actor["local_org_id"], "edition_id": edition["_id"], "schedule_id": edition.get("schedule_id"), "version_id": None, "base_revision": base_revision, "notes": visual_prompt, "apply_to_future": bool(body.get("apply_to_future")), "status": "queued", "created_at": stamped, "updated_at": stamped}
        job["_id"] = newsletter_design_jobs.insert_one(job).inserted_id
        result = apply_newsletter_visual_prompt_task.delay(str(job["_id"]))
        newsletter_design_jobs.update_one({"_id": job["_id"]}, {"$set": {"celery_task_id": result.id}})
        return _ok(edition_id=edition_id, design_job_id=str(job["_id"]), job_id=result.id, status="queued", base_revision=base_revision)
    payload = {"subject": clean_text(body.get("subject", edition.get("subject")), 140), "preheader": clean_text(body.get("preheader", edition.get("preheader")), 220), "intro": clean_text(body.get("intro", edition.get("intro")), 1800), "stories": edition.get("stories") or [], "closing": clean_text(body.get("closing", edition.get("closing")), 1200)}
    due_at = edition.get("due_at") or now(); design_prompt = clean_text(edition.get("visual_design_prompt") or schedule.get("visual_design_prompt") or "", 12000)
    html_body = compile_newsletter_designed_html(schedule, payload, due_at, visual_prompt=design_prompt) if design_prompt else compile_newsletter_html(schedule, payload, due_at)
    if edition.get("html"):
        newsletter_edition_versions.insert_one({"edition_id": edition["_id"], "schedule_id": edition.get("schedule_id"), "user_id": edition.get("user_id"), "organization_id": edition.get("organization_id"), "revision": int(edition.get("design_revision") or 1), "reason": "Before Syntal AI editorial edit", "html": edition.get("html") or "", "subject": edition.get("subject") or "", "preheader": edition.get("preheader") or "", "visual_design_prompt": edition.get("visual_design_prompt") or "", "created_at": now()})
    update = {**payload, "html": html_body, "text": compile_newsletter_text(schedule, payload, due_at), "is_manually_edited": True, "design_revision": int(edition.get("design_revision") or 1) + 1, "status": "draft", "updated_at": now()}
    newsletter_editions.update_one({"_id": edition["_id"]}, {"$set": update, "$unset": {"distribution": "", "distributed_at": "", "blackbook_mailchimp_draft": ""}})
    return _ok(item=_sanitize_doc(newsletter_editions.find_one({"_id": edition["_id"]}), content_limit=30000))


@bp.post("/api/ai/v1/newsletters/editions/<edition_id>/ready")
def newsletter_editions_ready(edition_id):
    actor, error = _guard(admin=True)
    if error: return error
    edition = _find_owned(newsletter_editions, actor, edition_id, "edition_id")
    if not edition: return _error("Newsletter edition not found", 404, "not_found")
    if not edition.get("html") or not edition.get("stories"): return _error("Generate the edition before marking it ready", 409, "conflict")
    newsletter_editions.update_one({"_id": edition["_id"]}, {"$set": {"status": "ready", "ready_at": now(), "updated_at": now()}})
    return _ok(edition_id=edition_id, status="ready")


@bp.post("/api/ai/v1/newsletters/editions/<edition_id>/archive")
def newsletter_editions_archive(edition_id):
    actor, error = _guard(admin=True)
    if error: return error
    edition = _find_owned(newsletter_editions, actor, edition_id, "edition_id")
    if not edition: return _error("Newsletter edition not found", 404, "not_found")
    newsletter_editions.update_one({"_id": edition["_id"]}, {"$set": {"status": "archived", "archived_at": now(), "updated_at": now()}})
    return _ok(edition_id=edition_id, status="archived")


@bp.post("/api/ai/v1/newsletters/editions/<edition_id>/distribute")
def newsletter_distribute(edition_id):
    actor, error = _guard(admin=True)
    if error: return error
    edition = _find_owned(newsletter_editions, actor, edition_id, "edition_id")
    if not edition: return _error("Newsletter edition not found", 404, "not_found")
    if edition.get("status") not in {"ready", "distributed"}: return _error("Edition must be ready before distribution", 409, "conflict")
    body = _payload(); action = "send" if body.get("action") == "send" else "draft"; reply_to = clean_text(body.get("reply_to"), 320)
    if "@" not in reply_to: return _error("A valid reply_to email is required")
    schedule = newsletter_schedules.find_one({"_id": edition.get("schedule_id"), **_scope(actor)}) or {}
    segment_ids = schedule.get("blackbook_segment_ids") or []; person_ids = schedule.get("blackbook_person_ids") or []; include_all = bool(schedule.get("blackbook_include_all_eligible"))
    engagement_buckets = schedule.get("blackbook_engagement_buckets") or []; interest_ids = schedule.get("blackbook_interest_ids") or []; interest_match = schedule.get("blackbook_interest_match") or "any"
    if not segment_ids and not person_ids and not include_all and not engagement_buckets and not interest_ids: return _error("Configure the BlackBook audience before distribution", 409, "audience_required")
    result = blackbook_distribute_newsletter(actor["local_org_id"], edition_id=edition["_id"], schedule_id=edition.get("schedule_id"), subject=edition.get("subject") or schedule.get("name") or "Newsletter", preheader=edition.get("preheader") or "", html_body=edition.get("html") or "", text_body=edition.get("text") or "", sender_name=schedule.get("sender_name") or schedule.get("name") or "Newsjacking", reply_to=reply_to, segment_ids=segment_ids, person_ids=person_ids, include_all_eligible=include_all, engagement_buckets=engagement_buckets, interest_ids=interest_ids, interest_match=interest_match, action=action)
    stamped = now()
    if action == "send":
        distribution = {"provider": "BlackBook / Mailchimp", "audience": "BlackBook audience", "recipient_count": int(result.get("recipient_count") or 0), "external_reference": clean_text(result.get("campaign_id") or "", 160), "notes": "Sent through Syntal AI via the organization-scoped BlackBook Marketing Bridge.", "delivered_at": stamped, "recorded_at": stamped, "recorded_by": actor["user_id"], "blackbook": result}
        newsletter_editions.update_one({"_id": edition["_id"]}, {"$set": {"status": "distributed", "distribution": distribution, "distributed_at": stamped, "updated_at": stamped}})
    else:
        newsletter_editions.update_one({"_id": edition["_id"]}, {"$set": {"blackbook_mailchimp_draft": {"campaign_id": clean_text(result.get("campaign_id") or "", 160), "recipient_count": int(result.get("recipient_count") or 0), "created_at": stamped, "result": result}, "updated_at": stamped}})
    return _ok(action=action, result=result)


@bp.get("/api/ai/v1/analytics/summary")
def analytics_summary():
    actor, error = _guard()
    if error: return error
    try: days = max(1, min(365, int(request.args.get("days") or 30)))
    except ValueError: days = 30
    start = now() - timedelta(days=days); q = {**_scope(actor), "occurred_at": {"$gte": start}}
    views = analytics_events.count_documents({**q, "event_type": {"$in": ["view", None]}}); interactions = analytics_events.count_documents({**q, "event_type": {"$nin": ["view", None]}}); leads = analytics_events.count_documents({**q, "event_type": {"$in": ["lead", "signup", "purchase"]}}); clicks = analytics_events.count_documents({**q, "event_type": {"$in": ["cta_click", "product_click"]}})
    return _ok(days=days, views=views, interactions=interactions, clicks=clicks, conversions=leads, conversion_rate=round(leads / views * 100, 2) if views else 0)


@bp.get("/api/ai/v1/domains")
def domains_list():
    actor, error = _guard()
    if error: return error
    return _ok(items=[_sanitize_doc(x, content_limit=2000) for x in domain_mappings.find(_scope(actor)).sort("updated_at", -1).limit(_limit())])


@bp.get("/api/ai/v1/sites")
def sites_list():
    actor, error = _guard()
    if error: return error
    return _ok(items=[_sanitize_doc(x, content_limit=3000) for x in website_sites.find(_scope(actor)).sort("updated_at", -1).limit(_limit())])


def _landing_internal_site(actor):
    site = website_sites.find_one({"site_key": "internal:primary", **_scope(actor)})
    if site:
        return site
    doc = {
        "user_id": actor["user_id"], "organization_id": actor["local_org_id"],
        "site_key": "internal:primary", "domain_id": None,
        "name": "Primary website", "brand_name": actor["identity"].get("organization_name") or "Website",
        "design_system": {}, "style_source_page_id": None, "homepage_page_id": None,
        "navigation": [], "navigation_version": 1, "created_at": now(), "updated_at": now(),
    }
    doc["_id"] = website_sites.insert_one(doc).inserted_id
    return doc


def _landing_save_version(page, reason):
    if not page or not page.get("html"):
        return None
    doc = {
        "landing_page_id": page["_id"], "revision": int(page.get("revision") or 0),
        "reason": clean_text(reason, 240) or "Saved revision", "html": page.get("html") or "",
        "metadata": page.get("metadata") or {}, "design_plan": page.get("design_plan") or {},
        "visual_assets": page.get("visual_assets") or [], "quality": page.get("quality") or {},
        "article_ids": page.get("article_ids") or [], "review_notes": page.get("review_notes") or "",
        "review_notes_updated_at": page.get("review_notes_updated_at"), "created_at": now(),
    }
    return landing_page_versions.insert_one(doc).inserted_id


def _landing_register_site_page(site, page, label=None):
    nav = list(site.get("navigation") or [])
    path = page.get("site_path") or f"/p/{page.get('public_id')}"
    if not any(item.get("page_id") == page["_id"] for item in nav):
        nav.append({
            "page_id": page["_id"], "label": clean_text(label or page.get("title"), 80) or "Page",
            "path": path, "enabled": False, "order": len(nav) * 10,
            "added_at": now(), "updated_at": now(),
        })
        website_sites.update_one({"_id": site["_id"]}, {"$set": {"navigation": nav, "updated_at": now()}})
    landing_pages.update_one({"_id": page["_id"]}, {"$set": {"site_id": site["_id"], "site_path": path, "navigation_enabled": False, "navigation_pending": True, "updated_at": now()}})


def _landing_publish(page, actor):
    if not page.get("html"):
        raise ValueError("Landing page has no HTML to publish")
    current_revision = int(page.get("revision") or 0)
    stamp = now()
    landing_pages.update_one({"_id": page["_id"], **_scope(actor)}, {"$set": {
        "published": True, "published_at": stamp, "published_revision": current_revision,
        "published_html": page.get("html"), "published_metadata": page.get("metadata") or {},
        "published_article_ids": page.get("article_ids") or [], "updated_at": stamp,
    }})
    domain_routes.update_many({"page_id": page["_id"], **_scope(actor)}, {"$set": {"status": "published", "updated_at": stamp}})


def _landing_detach(page, actor):
    if page.get("site_id"):
        site = website_sites.find_one({"_id": page.get("site_id"), **_scope(actor)})
        if site:
            nav = [item for item in (site.get("navigation") or []) if item.get("page_id") != page["_id"]]
            update = {"navigation": nav, "navigation_version": int(site.get("navigation_version") or 1) + 1, "updated_at": now()}
            if site.get("homepage_page_id") == page["_id"]:
                update["homepage_page_id"] = None
            if site.get("style_source_page_id") == page["_id"]:
                update["style_source_page_id"] = None
            website_sites.update_one({"_id": site["_id"]}, {"$set": update})
    domain_routes.delete_many({"page_id": page["_id"], **_scope(actor)})


@bp.get("/api/ai/v1/landing-pages")
def landing_pages_list():
    actor, error = _guard()
    if error: return error
    q = _scope(actor)
    if request.args.get("campaign_id"):
        try: q["campaign_id"] = ObjectId(request.args["campaign_id"])
        except Exception: return _error("Invalid campaign_id")
    fields = {"html": 0, "generated_html": 0, "published_html": 0}
    return _ok(items=[_sanitize_doc(x, content_limit=3000) for x in landing_pages.find(q, fields).sort("updated_at", -1).limit(_limit())])


@bp.get("/api/ai/v1/landing-pages/<page_id>")
def landing_pages_read(page_id):
    actor, error = _guard()
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    route = domain_routes.find_one({"page_id": page["_id"], **_scope(actor)})
    return _ok(page=_sanitize_doc(page, content_limit=80000), route=_sanitize_doc(route, content_limit=3000) if route else None)


@bp.post("/api/ai/v1/landing-pages/manual")
def landing_pages_create_manual():
    actor, error = _guard(admin=True)
    if error: return error
    body = _payload(); title = clean_text(body.get("title"), 300)
    if not title: return _error("title is required")
    document = sanitize_manual_landing_html(body.get("html") or "")
    if not document:
        document = manual_landing_document(title, body.get("headline") or title, body.get("subheadline") or "", body.get("body") or "", body.get("cta_label") or "Learn more", body.get("cta_url") or "#")
    site = _landing_internal_site(actor); public_id = secrets.token_urlsafe(9); stamp = now()
    kind = clean_text(body.get("page_kind"), 40).lower() or "custom"
    if kind not in {"home", "about", "services", "contact", "resources", "news", "custom"}: kind = "custom"
    description = clean_text(body.get("meta_description"), 170)
    doc = {
        "user_id": actor["user_id"], "organization_id": actor["local_org_id"], "site_id": site["_id"],
        "site_path": f"/p/{public_id}", "page_kind": kind, "include_articles": bool(body.get("include_articles")),
        "campaign_id": None, "campaign_ids": [], "evidence_item_ids": [], "evidence_snapshot": [],
        "title": title, "slug": slugify(title)[:100], "public_id": public_id, "article_mode": "selected", "article_ids": [],
        "prompt": "", "source_website_url": "", "generation_status": "completed", "generation_profile": "manual_v3_8",
        "manual_mode": True, "html": document, "metadata": {"title": title, "meta_description": description, "summary": description, "language": "en"},
        "design_plan": {}, "visual_assets": [], "quality": {}, "review_notes": "", "revision": 1, "published": False,
        "created_at": stamp, "updated_at": stamp,
    }
    doc["_id"] = landing_pages.insert_one(doc).inserted_id
    _landing_register_site_page(site, doc, title)
    page = landing_pages.find_one({"_id": doc["_id"]}) or doc
    return _ok(page=_sanitize_doc(page, content_limit=80000))


@bp.post("/api/ai/v1/landing-pages/ai")
def landing_pages_create_ai():
    actor, error = _guard(admin=True)
    if error: return error
    body = _payload(); title = clean_text(body.get("title"), 300)
    if not title: return _error("title is required")
    campaign = None
    if body.get("campaign_id"):
        campaign = _find_owned(campaigns, actor, body.get("campaign_id"), "campaign_id")
        if not campaign: return _error("Campaign not found", 404, "not_found")
    evidence_ids = _owned_ids(collection_items, actor, body.get("evidence_item_ids") or [])
    if not campaign and not evidence_ids: return _error("campaign_id or evidence_item_ids is required")
    recent = list(articles.find({"campaign_id": campaign["_id"], **_scope(actor)}).sort("created_at", -1).limit(Config.LANDING_MAX_ARTICLES)) if campaign else []
    site = _landing_internal_site(actor); public_id = secrets.token_urlsafe(9); stamp = now()
    kind = clean_text(body.get("page_kind"), 40).lower() or "custom"
    if kind not in {"home", "about", "services", "contact", "resources", "news", "custom"}: kind = "custom"
    doc = {
        "user_id": actor["user_id"], "organization_id": actor["local_org_id"], "site_id": site["_id"],
        "site_path": f"/p/{public_id}", "page_kind": kind, "include_articles": bool(body.get("include_articles", True)),
        "campaign_id": campaign.get("_id") if campaign else None, "campaign_ids": [campaign["_id"]] if campaign else [],
        "evidence_item_ids": evidence_ids, "evidence_snapshot": [], "title": title, "slug": slugify(title)[:100], "public_id": public_id,
        "article_mode": "all" if campaign else "selected", "article_ids": [row["_id"] for row in recent],
        "prompt": clean_text(body.get("prompt"), 4000), "source_website_url": (campaign or {}).get("website_url") or "",
        "generation_status": "queued", "generation_profile": "ai_control_v3_8", "manual_mode": False,
        "metadata": {}, "design_plan": {}, "visual_assets": [], "quality": {}, "review_notes": "", "revision": 0, "published": False,
        "created_at": stamp, "updated_at": stamp,
    }
    doc["_id"] = landing_pages.insert_one(doc).inserted_id
    _landing_register_site_page(site, doc, title)
    result = generate_landing_page_task.delay(str(doc["_id"]))
    return _ok(page_id=str(doc["_id"]), job_id=result.id, status="queued")


@bp.patch("/api/ai/v1/landing-pages/<page_id>")
def landing_pages_update(page_id):
    actor, error = _guard(admin=True)
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    body = _payload(); updates = {"updated_at": now()}; metadata = dict(page.get("metadata") or {})
    if "title" in body:
        title = clean_text(body.get("title"), 300)
        if not title: return _error("title cannot be empty")
        updates.update({"title": title, "slug": slugify(title)[:100]})
    if "page_kind" in body:
        kind = clean_text(body.get("page_kind"), 40).lower()
        if kind not in {"home", "about", "services", "contact", "resources", "news", "custom"}: return _error("Invalid page_kind")
        updates["page_kind"] = kind
    if "prompt" in body: updates["prompt"] = clean_text(body.get("prompt"), 4000)
    if "include_articles" in body: updates["include_articles"] = bool(body.get("include_articles"))
    if "meta_title" in body: metadata["title"] = clean_text(body.get("meta_title"), 90)
    if "meta_description" in body: metadata["meta_description"] = clean_text(body.get("meta_description"), 170)
    if "summary" in body: metadata["summary"] = clean_text(body.get("summary"), 320)
    updates["metadata"] = metadata
    landing_pages.update_one({"_id": page["_id"], **_scope(actor)}, {"$set": updates})
    return _ok(page=_sanitize_doc(landing_pages.find_one({"_id": page["_id"]}), content_limit=4000))


@bp.put("/api/ai/v1/landing-pages/<page_id>/html")
def landing_pages_html_write(page_id):
    actor, error = _guard(admin=True)
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    body = _payload(); document = sanitize_manual_landing_html(body.get("html") or "")
    if not document: return _error("html cannot be empty")
    _landing_save_version(page, clean_text(body.get("reason"), 240) or "Before manual HTML write")
    landing_pages.update_one({"_id": page["_id"], **_scope(actor)}, {"$set": {
        "html": document, "manual_mode": True, "generation_status": "completed",
        "revision": int(page.get("revision") or 0) + 1, "updated_at": now(),
    }})
    return _ok(page_id=page_id, revision=int(page.get("revision") or 0) + 1)


@bp.post("/api/ai/v1/landing-pages/<page_id>/generate")
def landing_pages_generate(page_id):
    actor, error = _guard(admin=True)
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    landing_pages.update_one({"_id": page["_id"], **_scope(actor)}, {"$set": {"generation_status": "queued", "manual_mode": False, "updated_at": now()}, "$unset": {"generation_error": ""}})
    result = generate_landing_page_task.delay(str(page["_id"]))
    return _ok(page_id=page_id, job_id=result.id, status="queued")


@bp.post("/api/ai/v1/landing-pages/<page_id>/ai-revise")
def landing_pages_ai_revise(page_id):
    actor, error = _guard(admin=True)
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    notes = clean_text(_payload().get("notes"), 12000)
    if not notes: return _error("notes are required")
    base_revision = int(page.get("revision") or 0)
    landing_pages.update_one({"_id": page["_id"]}, {"$set": {"review_notes": notes, "review_notes_revision": base_revision, "review_notes_updated_at": now()}})
    job = {"user_id": actor["user_id"], "organization_id": actor["local_org_id"], "landing_page_id": page["_id"], "version_id": None, "base_revision": base_revision, "notes": notes, "status": "queued", "created_at": now(), "updated_at": now()}
    job["_id"] = landing_page_change_jobs.insert_one(job).inserted_id
    result = apply_landing_review_notes_task.delay(str(job["_id"]))
    landing_page_change_jobs.update_one({"_id": job["_id"]}, {"$set": {"celery_task_id": result.id}})
    return _ok(page_id=page_id, change_job_id=str(job["_id"]), job_id=result.id, status="queued")


@bp.post("/api/ai/v1/landing-pages/<page_id>/duplicate")
def landing_pages_duplicate(page_id):
    actor, error = _guard(admin=True)
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    body = _payload(); public_id = secrets.token_urlsafe(9)
    clone = {k: v for k, v in page.items() if k not in {"_id", "published", "published_at", "published_revision", "published_html", "published_metadata", "created_at", "updated_at", "navigation_enabled", "navigation_pending"}}
    clone_title = clean_text(body.get("title"), 300) or f"{page.get('title') or 'Landing page'} copy"
    site = _landing_internal_site(actor)
    clone.update({"user_id": actor["user_id"], "organization_id": actor["local_org_id"], "site_id": site["_id"], "title": clone_title, "slug": slugify(clone_title)[:100], "public_id": public_id, "site_path": f"/p/{public_id}", "published": False, "generation_status": "completed" if page.get("html") else "draft", "revision": 1, "manual_mode": True, "created_at": now(), "updated_at": now()})
    clone["_id"] = landing_pages.insert_one(clone).inserted_id
    _landing_register_site_page(site, clone, clone_title)
    return _ok(page=_sanitize_doc(landing_pages.find_one({"_id": clone["_id"]}), content_limit=4000))


@bp.post("/api/ai/v1/landing-pages/<page_id>/publish")
def landing_pages_publish(page_id):
    actor, error = _guard(admin=True)
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    try: _landing_publish(page, actor)
    except ValueError as exc: return _error(str(exc), 409, "conflict")
    return _ok(page_id=page_id, published=True, revision=int(page.get("revision") or 0))


@bp.post("/api/ai/v1/landing-pages/<page_id>/unpublish")
def landing_pages_unpublish(page_id):
    actor, error = _guard(admin=True)
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    stamp = now(); landing_pages.update_one({"_id": page["_id"], **_scope(actor)}, {"$set": {"published": False, "updated_at": stamp}}); domain_routes.update_many({"page_id": page["_id"], **_scope(actor)}, {"$set": {"status": "draft", "updated_at": stamp}})
    return _ok(page_id=page_id, published=False)


@bp.delete("/api/ai/v1/landing-pages/<page_id>")
def landing_pages_delete(page_id):
    actor, error = _guard(admin=True)
    if error: return error
    if _payload().get("confirm") is not True: return _error("confirm=true is required for permanent deletion", 409, "confirmation_required")
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    _landing_detach(page, actor); landing_page_versions.delete_many({"landing_page_id": page["_id"]}); landing_page_change_jobs.delete_many({"landing_page_id": page["_id"]}); landing_pages.delete_one({"_id": page["_id"], **_scope(actor)})
    return _ok(page_id=page_id, deleted=True)


@bp.get("/api/ai/v1/landing-pages/<page_id>/versions")
def landing_pages_versions_list(page_id):
    actor, error = _guard()
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    rows = landing_page_versions.find({"landing_page_id": page["_id"]}).sort([("revision", -1), ("created_at", -1)]).limit(_limit(default=50, maximum=100))
    return _ok(items=[_sanitize_doc(row, content_limit=1200) for row in rows])


@bp.post("/api/ai/v1/landing-pages/<page_id>/versions/<version_id>/restore")
def landing_pages_versions_restore(page_id, version_id):
    actor, error = _guard(admin=True)
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    try: version_oid = ObjectId(str(version_id))
    except Exception: return _error("Invalid version_id")
    version = landing_page_versions.find_one({"_id": version_oid, "landing_page_id": page["_id"]})
    if not version: return _error("Landing-page revision not found", 404, "not_found")
    _landing_save_version(page, "Before revision restore")
    new_revision = int(page.get("revision") or 0) + 1
    landing_pages.update_one({"_id": page["_id"], **_scope(actor)}, {"$set": {"html": version.get("html") or "", "metadata": version.get("metadata") or {}, "design_plan": version.get("design_plan") or {}, "visual_assets": version.get("visual_assets") or [], "quality": version.get("quality") or {}, "article_ids": version.get("article_ids") or [], "revision": new_revision, "generation_status": "completed", "manual_mode": True, "updated_at": now()}})
    return _ok(page_id=page_id, revision=new_revision, restored_from_revision=int(version.get("revision") or 0))


@bp.post("/api/ai/v1/landing-pages/<page_id>/navigation/add")
def landing_pages_navigation_add(page_id):
    actor, error = _guard(admin=True)
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    body = _payload()
    if body.get("publish") is True and not page.get("published"):
        try: _landing_publish(page, actor)
        except ValueError as exc: return _error(str(exc), 409, "conflict")
        page = landing_pages.find_one({"_id": page["_id"]}) or page
    if not page.get("published"): return _error("Publish the page before exposing it in navigation", 409, "conflict")
    site = website_sites.find_one({"_id": page.get("site_id"), **_scope(actor)}) if page.get("site_id") else _landing_internal_site(actor)
    nav = list(site.get("navigation") or []); label = clean_text(body.get("label"), 80) or page.get("title") or "Page"; found = False
    for item in nav:
        if item.get("page_id") == page["_id"]:
            item.update({"enabled": True, "label": label, "updated_at": now()}); found = True; break
    if not found:
        nav.append({"page_id": page["_id"], "label": label, "path": page.get("site_path") or f"/p/{page.get('public_id')}", "enabled": True, "order": len(nav) * 10, "added_at": now(), "updated_at": now()})
    website_sites.update_one({"_id": site["_id"]}, {"$set": {"navigation": nav, "navigation_version": int(site.get("navigation_version") or 1) + 1, "updated_at": now()}}); landing_pages.update_one({"_id": page["_id"]}, {"$set": {"site_id": site["_id"], "navigation_enabled": True, "navigation_pending": False, "updated_at": now()}})
    return _ok(page_id=page_id, navigation_enabled=True)


@bp.post("/api/ai/v1/landing-pages/<page_id>/navigation/hide")
def landing_pages_navigation_hide(page_id):
    actor, error = _guard(admin=True)
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    site = website_sites.find_one({"_id": page.get("site_id"), **_scope(actor)}) if page.get("site_id") else None
    if site:
        nav = list(site.get("navigation") or [])
        for item in nav:
            if item.get("page_id") == page["_id"]: item["enabled"] = False; item["updated_at"] = now()
        website_sites.update_one({"_id": site["_id"]}, {"$set": {"navigation": nav, "navigation_version": int(site.get("navigation_version") or 1) + 1, "updated_at": now()}})
    landing_pages.update_one({"_id": page["_id"]}, {"$set": {"navigation_enabled": False, "navigation_pending": True, "updated_at": now()}})
    return _ok(page_id=page_id, navigation_enabled=False)


@bp.get("/api/ai/v1/engagement")
def engagement_overview():
    actor, error = _guard()
    if error: return error
    try:
        return _ok(result=blackbook_engagement_overview(actor["local_org_id"]))
    except Exception as exc:
        return _error(str(exc), 502, "blackbook_error")


@bp.get("/api/ai/v1/engagement/people")
def engagement_people_list():
    actor, error = _guard()
    if error: return error
    bucket = clean_text(request.args.get("bucket") or "queue", 20).lower()
    if bucket not in {"queue","failed","inactive","low","medium","high","all"}: bucket = "queue"
    try:
        result = blackbook_engagement_people(
            actor["local_org_id"], bucket=bucket, query=clean_text(request.args.get("q") or "", 160),
            interest=clean_text(request.args.get("interest") or "", 180), limit=_limit(default=100, maximum=2500),
            offset=max(0, int(request.args.get("offset") or 0)),
        )
        return _ok(result=result)
    except Exception as exc:
        return _error(str(exc), 502, "blackbook_error")


@bp.post("/api/ai/v1/engagement/queue/refresh")
def engagement_queue_refresh():
    actor, error = _guard(admin=True)
    if error: return error
    try:
        return _ok(result=blackbook_engagement_refresh_queue(actor["local_org_id"]))
    except Exception as exc:
        return _error(str(exc), 502, "blackbook_error")


@bp.post("/api/ai/v1/engagement/bulk-analyze")
def engagement_bulk_analyze():
    actor, error = _guard(admin=True)
    if error: return error
    body = _payload(); person_ids = [str(x) for x in (body.get("person_ids") or []) if str(x).strip()]
    person_ids = list(dict.fromkeys(person_ids))
    if not person_ids: return _error("person_ids is required")
    if len(person_ids) > 2500: return _error("A maximum of 2500 people can be analyzed in one batch", 413, "too_many_people")
    try: days = max(30, min(36500, int(body.get("days") or 3650)))
    except Exception: days = 3650
    try:
        return _ok(result=blackbook_engagement_bulk_analyze(actor["local_org_id"], person_ids=person_ids, days=days))
    except Exception as exc:
        return _error(str(exc), 502, "blackbook_error")


@bp.get("/api/ai/v1/engagement/interests")
def engagement_interests_list():
    actor, error = _guard()
    if error: return error
    try: return _ok(result=blackbook_engagement_interests(actor["local_org_id"]))
    except Exception as exc: return _error(str(exc), 502, "blackbook_error")


@bp.get("/api/ai/v1/engagement/batches")
def engagement_batches_list():
    actor, error = _guard()
    if error: return error
    try: return _ok(result=blackbook_engagement_batches(actor["local_org_id"], limit=_limit(default=12, maximum=100)))
    except Exception as exc: return _error(str(exc), 502, "blackbook_error")


@bp.get("/api/ai/v1/engagement/batches/<batch_id>")
def engagement_batch_read(batch_id):
    actor, error = _guard()
    if error: return error
    try: return _ok(result=blackbook_engagement_batch(actor["local_org_id"], batch_id))
    except Exception as exc: return _error(str(exc), 502, "blackbook_error")


@bp.get("/api/ai/v1/blackbook/status")
def blackbook_status():
    actor, error = _guard()
    if error: return error
    return _ok(status=blackbook_connector_status(actor["local_org_id"]))


@bp.post("/api/ai/v1/blackbook/audience/preview")
def blackbook_audience():
    actor, error = _guard()
    if error: return error
    body = _payload()
    try:
        result = blackbook_audience_preview(
            actor["local_org_id"], segment_ids=body.get("segment_ids") or [],
            person_ids=body.get("person_ids") or [], include_all_eligible=bool(body.get("include_all_eligible")),
            engagement_buckets=body.get("engagement_buckets") or [], interest_ids=body.get("interest_ids") or [],
            interest_match=body.get("interest_match") or "any",
        )
    except Exception as exc:
        return _error(str(exc), 502, "blackbook_error")
    return _ok(result=result)
