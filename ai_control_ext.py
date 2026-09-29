from __future__ import annotations

import ipaddress
import re
import socket
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from bson import ObjectId
from flask import Blueprint, request, render_template
from flask_login import login_required, current_user

from ai_control import (
    TOOLS, _tool, _ok, _error, _guard, _scope, _find_owned, _sanitize_doc,
    _payload, _limit, _jsonable,
)
from config import Config
from db import (
    campaigns, products, collections, collection_sources, collection_items,
    rss_feeds, rss_feed_items, newsjacking_workers, newsjacking_runs,
    articles, article_versions, article_change_jobs, article_submissions,
    social_media_posts, social_generation_jobs, newsletter_schedules,
    newsletter_editions, analytics_events, domain_mappings, domain_routes,
    website_sites, landing_pages, landing_page_versions, landing_page_change_jobs,
    experiments,
)
from services import clean_text, now
from tasks import (
    update_rss_feed, apply_landing_review_notes_task,
)
from learning_service import compute_worker_learning
from experiment_service import PRIMARY_METRICS, experiment_results, experiment_decision
from domain_provisioner import provision_domain, deprovision_domain, DomainProvisioningError
from blackbook_service import (
    public_connector_status as blackbook_connector_status,
    marketing_context as blackbook_marketing_context,
    marketing_people as blackbook_marketing_people,
    marketing_segments as blackbook_marketing_segments,
    marketing_campaigns as blackbook_marketing_campaigns,
    marketing_audience_preview as blackbook_audience_preview,
)


bp = Blueprint("syntal_ai_control_ext", __name__)


EXTRA_TOOLS = [
    _tool("njs.control.capabilities", "Describe the NJS AI control plane, tool groups, risk classes and connector readiness.", "read", "GET", "/api/ai/v1/control/capabilities"),
    _tool("njs.control.health", "Read operational readiness for NJS workers, connectors and generation dependencies without exposing secrets.", "read", "GET", "/api/ai/v1/control/health"),

    _tool("njs.collections.sources.list", "List ingestion sources and processing state for an evidence collection.", "read", "GET", "/api/ai/v1/collections/{collection_id}/sources", {"collection_id": {"type": "string"}, "limit": {"type": "integer"}}, ["collection_id"]),
    _tool("njs.collections.items.list", "List extracted evidence items in a collection.", "read", "GET", "/api/ai/v1/collections/{collection_id}/items", {"collection_id": {"type": "string"}, "active_only": {"type": "boolean"}, "limit": {"type": "integer"}}, ["collection_id"]),
    _tool("njs.collections.items.read", "Read one extracted evidence item.", "read", "GET", "/api/ai/v1/collections/items/{item_id}", {"item_id": {"type": "string"}}, ["item_id"]),

    _tool("njs.feeds.refresh", "Queue an immediate refresh of an RSS/news feed.", "external", "POST", "/api/ai/v1/feeds/{feed_id}/refresh", {"feed_id": {"type": "string"}}, ["feed_id"]),
    _tool("njs.feeds.items.list", "List recent items discovered by a specific feed.", "read", "GET", "/api/ai/v1/feeds/{feed_id}/items", {"feed_id": {"type": "string"}, "limit": {"type": "integer"}}, ["feed_id"]),

    _tool("njs.workers.runs.list", "List recent Newsjack Worker execution history.", "read", "GET", "/api/ai/v1/workers/{worker_id}/runs", {"worker_id": {"type": "string"}, "limit": {"type": "integer"}}, ["worker_id"]),
    _tool("njs.workers.learning.read", "Read the current measured learning report and recommendations for a Worker.", "read", "GET", "/api/ai/v1/workers/{worker_id}/learning", {"worker_id": {"type": "string"}}, ["worker_id"]),
    _tool("njs.workers.learning.refresh", "Recompute a Worker's learning report from current first-party attribution data.", "external", "POST", "/api/ai/v1/workers/{worker_id}/learning/refresh", {"worker_id": {"type": "string"}}, ["worker_id"]),
    _tool("njs.workers.learning.apply", "Apply bounded learning recommendations to a Worker without bypassing evidence/relevance gates.", "write", "POST", "/api/ai/v1/workers/{worker_id}/learning/apply", {"worker_id": {"type": "string"}, "apply_confidence": {"type": "boolean"}, "enable_adaptive": {"type": "boolean"}}, ["worker_id"]),

    _tool("njs.articles.notes.save", "Save review notes against the current article revision or a preserved revision.", "write", "POST", "/api/ai/v1/articles/{article_id}/notes", {"article_id": {"type": "string"}, "version_id": {"type": ["string", "null"]}, "notes": {"type": "string"}}, ["article_id", "notes"]),
    _tool("njs.articles.versions.list", "List preserved article revisions.", "read", "GET", "/api/ai/v1/articles/{article_id}/versions", {"article_id": {"type": "string"}, "limit": {"type": "integer"}}, ["article_id"]),
    _tool("njs.articles.versions.read", "Read a preserved article revision.", "read", "GET", "/api/ai/v1/articles/{article_id}/versions/{version_id}", {"article_id": {"type": "string"}, "version_id": {"type": "string"}}, ["article_id", "version_id"]),
    _tool("njs.articles.versions.restore", "Restore a preserved article revision as a new unsigned working revision.", "write", "POST", "/api/ai/v1/articles/{article_id}/versions/{version_id}/restore", {"article_id": {"type": "string"}, "version_id": {"type": "string"}}, ["article_id", "version_id"]),
    _tool("njs.articles.change_jobs.list", "List recent AI review/change jobs for an article.", "read", "GET", "/api/ai/v1/articles/{article_id}/change-jobs", {"article_id": {"type": "string"}, "limit": {"type": "integer"}}, ["article_id"]),
    _tool("njs.articles.submissions.list", "List recent audience responses captured by an article, excluding secret integration credentials.", "read", "GET", "/api/ai/v1/articles/{article_id}/submissions", {"article_id": {"type": "string"}, "limit": {"type": "integer"}}, ["article_id"]),

    _tool("njs.social.settings.read", "Read social automation settings for a campaign.", "read", "GET", "/api/ai/v1/campaigns/{campaign_id}/social/settings", {"campaign_id": {"type": "string"}}, ["campaign_id"]),
    _tool("njs.social.settings.update", "Update social automation settings for a campaign.", "write", "PATCH", "/api/ai/v1/campaigns/{campaign_id}/social/settings", {"campaign_id": {"type": "string"}, "enabled": {"type": "boolean"}, "platforms": {"type": "array", "items": {"type": "string"}}, "auto_generate_newsjacking": {"type": "boolean"}, "image_enabled": {"type": "boolean"}, "auto_schedule": {"type": "boolean"}, "posting_weekdays": {"type": "array", "items": {"type": "integer"}}, "timezone": {"type": "string"}, "default_post_time": {"type": "string"}, "brand_voice": {"type": "string"}, "image_style": {"type": "string"}}, ["campaign_id"]),
    _tool("njs.social.jobs.list", "List recent social generation jobs.", "read", "GET", "/api/ai/v1/social/jobs", {"article_id": {"type": "string"}, "campaign_id": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("njs.social.jobs.read", "Read one social generation job and its status/result.", "read", "GET", "/api/ai/v1/social/jobs/{job_id}", {"job_id": {"type": "string"}}, ["job_id"]),

    _tool("njs.blackbook.context", "Read BlackBook marketing context, aggregate engagement/enrichment coverage and Mailchimp readiness for the active organization.", "read", "GET", "/api/ai/v1/blackbook/context"),
    _tool("njs.blackbook.people.list", "Search BlackBook people available to the active organization for marketing planning.", "read", "GET", "/api/ai/v1/blackbook/people", {"q": {"type": "string"}, "eligible_only": {"type": "boolean"}, "limit": {"type": "integer"}}),
    _tool("njs.blackbook.segments.list", "List reusable BlackBook audience segments.", "read", "GET", "/api/ai/v1/blackbook/segments"),
    _tool("njs.blackbook.campaigns.list", "List recent BlackBook/Mailchimp campaign performance context.", "read", "GET", "/api/ai/v1/blackbook/campaigns", {"limit": {"type": "integer"}}),
    _tool("njs.newsletters.audience.update", "Configure a dynamic newsletter audience from engagement buckets, canonical interests, BlackBook segments, selected people or all eligible contacts.", "write", "PATCH", "/api/ai/v1/newsletters/schedules/{schedule_id}/audience", {"schedule_id": {"type": "string"}, "segment_ids": {"type": "array", "items": {"type": "string"}}, "person_ids": {"type": "array", "items": {"type": "string"}}, "include_all_eligible": {"type": "boolean"}, "engagement_buckets": {"type": "array", "items": {"type": "string", "enum": ["inactive","low","medium","high"]}}, "interest_ids": {"type": "array", "items": {"type": "string"}}, "interest_match": {"type": "string", "enum": ["any","all"]}}, ["schedule_id"]),
    _tool("njs.newsletters.audience.preview_configured", "Refresh and preview the BlackBook audience configured on a newsletter plan.", "read", "GET", "/api/ai/v1/newsletters/schedules/{schedule_id}/audience/preview", {"schedule_id": {"type": "string"}}, ["schedule_id"]),
    _tool("njs.newsletters.editions.preview", "Read the compiled HTML/plain-text delivery preview for an edition.", "read", "GET", "/api/ai/v1/newsletters/editions/{edition_id}/preview", {"edition_id": {"type": "string"}}, ["edition_id"]),

    _tool("njs.analytics.events.list", "List recent first-party analytics events with optional event/content filters.", "read", "GET", "/api/ai/v1/analytics/events", {"days": {"type": "integer"}, "event_type": {"type": "string"}, "content_type": {"type": "string"}, "content_id": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("njs.analytics.content.summary", "Summarize views, engagement, clicks and conversions for one NJS content object.", "read", "GET", "/api/ai/v1/analytics/content/{content_type}/{content_id}", {"content_type": {"type": "string"}, "content_id": {"type": "string"}, "days": {"type": "integer"}}, ["content_type", "content_id"]),
    _tool("njs.analytics.campaign.summary", "Summarize first-party attribution for a campaign.", "read", "GET", "/api/ai/v1/analytics/campaigns/{campaign_id}", {"campaign_id": {"type": "string"}, "days": {"type": "integer"}}, ["campaign_id"]),

    _tool("njs.domains.create", "Connect a publishing domain and return its DNS instruction.", "write", "POST", "/api/ai/v1/domains", {"domain": {"type": "string"}}, ["domain"]),
    _tool("njs.domains.verify", "Verify domain DNS and activate HTTPS through the configured NJS provisioner when enabled.", "external", "POST", "/api/ai/v1/domains/{domain_id}/verify", {"domain_id": {"type": "string"}}, ["domain_id"]),
    _tool("njs.domains.delete", "Disconnect a publishing domain when it has no active routes.", "destructive", "DELETE", "/api/ai/v1/domains/{domain_id}", {"domain_id": {"type": "string"}}, ["domain_id"]),
    _tool("njs.domains.routes.list", "List page routes mapped to a publishing domain.", "read", "GET", "/api/ai/v1/domains/{domain_id}/routes", {"domain_id": {"type": "string"}}, ["domain_id"]),
    _tool("njs.domains.routes.create", "Map a landing page to a path on a connected publishing domain.", "write", "POST", "/api/ai/v1/domains/{domain_id}/routes", {"domain_id": {"type": "string"}, "page_id": {"type": "string"}, "path": {"type": "string"}, "goal": {"type": "string"}}, ["domain_id", "page_id", "path"]),
    _tool("njs.domains.routes.delete", "Remove a publishing domain route.", "destructive", "DELETE", "/api/ai/v1/domain-routes/{route_id}", {"route_id": {"type": "string"}}, ["route_id"]),
    _tool("njs.domains.dns", "Resolve a connected domain and compare it with the configured NJS publishing IP.", "read", "GET", "/api/ai/v1/domains/{domain_id}/dns", {"domain_id": {"type": "string"}}, ["domain_id"]),

    _tool("njs.sites.read", "Read one website site's shared design/navigation state.", "read", "GET", "/api/ai/v1/sites/{site_id}", {"site_id": {"type": "string"}}, ["site_id"]),
    _tool("njs.landing_pages.request_changes", "Queue AI changes to a landing page using review notes.", "external", "POST", "/api/ai/v1/landing-pages/{page_id}/request-changes", {"page_id": {"type": "string"}, "notes": {"type": "string"}, "version_id": {"type": ["string", "null"]}}, ["page_id", "notes"]),
    _tool("njs.landing_pages.change_jobs.list", "List recent landing-page AI change jobs.", "read", "GET", "/api/ai/v1/landing-pages/{page_id}/change-jobs", {"page_id": {"type": "string"}, "limit": {"type": "integer"}}, ["page_id"]),

    _tool("njs.experiments.list", "List controlled experiments and current status.", "read", "GET", "/api/ai/v1/experiments", {"status": {"type": "string"}, "limit": {"type": "integer"}}),
    _tool("njs.experiments.read", "Read one controlled experiment, results and decision state.", "read", "GET", "/api/ai/v1/experiments/{experiment_id}", {"experiment_id": {"type": "string"}}, ["experiment_id"]),
    _tool("njs.experiments.create", "Create a draft A/B experiment against a published page or signed article.", "write", "POST", "/api/ai/v1/experiments", {"name": {"type": "string"}, "hypothesis": {"type": "string"}, "target_type": {"type": "string", "enum": ["page", "article"]}, "target_id": {"type": "string"}, "primary_metric": {"type": "string"}, "min_sample_size": {"type": "integer"}, "control_weight": {"type": "integer"}, "challenger": {"type": "object"}}, ["target_type", "target_id"]),
    _tool("njs.experiments.update", "Update a draft or paused experiment challenger/settings.", "write", "PATCH", "/api/ai/v1/experiments/{experiment_id}", {"experiment_id": {"type": "string"}, "name": {"type": "string"}, "hypothesis": {"type": "string"}, "primary_metric": {"type": "string"}, "min_sample_size": {"type": "integer"}, "control_weight": {"type": "integer"}, "challenger": {"type": "object"}}, ["experiment_id"]),
    _tool("njs.experiments.start", "Start traffic allocation for a draft/paused experiment after revision/conflict checks.", "external", "POST", "/api/ai/v1/experiments/{experiment_id}/start", {"experiment_id": {"type": "string"}}, ["experiment_id"]),
    _tool("njs.experiments.pause", "Pause experiment traffic allocation.", "write", "POST", "/api/ai/v1/experiments/{experiment_id}/pause", {"experiment_id": {"type": "string"}}, ["experiment_id"]),
    _tool("njs.experiments.complete", "Complete an experiment and lock the selected variant as its recorded winner.", "write", "POST", "/api/ai/v1/experiments/{experiment_id}/complete", {"experiment_id": {"type": "string"}, "winner_variant_id": {"type": "string"}}, ["experiment_id", "winner_variant_id"]),
    _tool("njs.experiments.delete", "Delete a non-running experiment while retaining historical analytics events.", "destructive", "DELETE", "/api/ai/v1/experiments/{experiment_id}", {"experiment_id": {"type": "string"}}, ["experiment_id"]),
    _tool("njs.experiments.results", "Read experiment variant metrics, confidence and current decision guidance.", "read", "GET", "/api/ai/v1/experiments/{experiment_id}/results", {"experiment_id": {"type": "string"}}, ["experiment_id"]),
]

# The base manifest reads ai_control.TOOLS. App imports this module after ai_control,
# so extending here exposes the new capabilities without changing existing route code.
_existing_names = {x.get("name") for x in TOOLS}
TOOLS.extend(x for x in EXTRA_TOOLS if x.get("name") not in _existing_names)


def _safe_int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _resolved_ips(hostname):
    try:
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({item[4][0] for item in infos if item and item[4]})


def _normalize_domain(value):
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Enter a domain name")
    parsed = urlsplit(raw if "://" in raw else "//" + raw)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("Enter a valid domain name")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("The domain name is invalid") from exc
    if len(host) > 253 or "." not in host:
        raise ValueError("Use a complete domain such as example.com")
    if host == Config.PUBLISHING_PRIMARY_HOST:
        raise ValueError("The NJS application hostname cannot be claimed as a publishing domain")
    try:
        ipaddress.ip_address(host)
        raise ValueError("Use a domain name rather than an IP address")
    except ValueError as exc:
        if str(exc) == "Use a domain name rather than an IP address":
            raise
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", host):
        raise ValueError("The domain name is invalid")
    return host


def _normalize_path(value):
    raw = str(value or "/").strip() or "/"
    raw = urlsplit(raw).path or "/"
    raw = re.sub(r"/{2,}", "/", raw)
    if not raw.startswith("/"):
        raw = "/" + raw
    if len(raw) > 240 or any(part in {".", ".."} for part in raw.split("/")):
        raise ValueError("The publishing path is invalid")
    if raw != "/":
        raw = raw.rstrip("/")
    if raw.startswith(("/.well-known/", "/static/", "/api/")):
        raise ValueError("That path is reserved by the publishing service")
    return raw


def _safe_submission(row):
    out = dict(row)
    # NJS can expose the captured response, but never connector credentials/tokens.
    out.pop("integration_secret", None)
    out.pop("token", None)
    return _sanitize_doc(out, content_limit=6000)


def _analytics_summary(query):
    views = analytics_events.count_documents({**query, "event_type": {"$in": ["view", None]}})
    engaged = analytics_events.count_documents({**query, "event_type": {"$in": ["view", None]}, "engaged": True})
    clicks = analytics_events.count_documents({**query, "event_type": {"$in": ["cta_click", "product_click"]}})
    conversions = analytics_events.count_documents({**query, "event_type": {"$in": ["lead", "signup", "purchase"]}})
    return {
        "views": views,
        "engaged_views": engaged,
        "engagement_rate": round(engaged / views * 100, 2) if views else 0,
        "clicks": clicks,
        "click_rate": round(clicks / views * 100, 2) if views else 0,
        "conversions": conversions,
        "conversion_rate": round(conversions / views * 100, 2) if views else 0,
    }


def _experiment_target(actor, target_type, target_id):
    collection = landing_pages if target_type == "page" else articles if target_type == "article" else None
    if collection is None:
        return None
    return _find_owned(collection, actor, target_id, "target_id")


def _target_revision(target):
    return int((target or {}).get("revision") or (target or {}).get("published_revision") or 0)


def _control_snapshot(target_type, target):
    if target_type == "page":
        return {
            "revision": _target_revision(target),
            "html": target.get("published_html") or target.get("html") or "",
            "metadata": target.get("published_metadata") or target.get("metadata") or {},
        }
    return {
        "revision": _target_revision(target),
        "content": target.get("published_content") or target.get("content") or "",
        "metadata": target.get("published_metadata") or target.get("metadata") or {},
    }


def _normalize_challenger(raw):
    raw = raw if isinstance(raw, dict) else {}
    placement = clean_text(raw.get("cta_placement") or "existing", 40)
    if placement not in {"existing", "after_hero", "after_intro", "before_footer"}:
        placement = "existing"
    return {
        "id": "challenger",
        "name": clean_text(raw.get("name") or "Challenger", 100) or "Challenger",
        "overrides": {
            "headline": clean_text(raw.get("headline"), 240),
            "subheadline": clean_text(raw.get("subheadline"), 500),
            "cta_label": clean_text(raw.get("cta_label"), 180),
            "cta_url": clean_text(raw.get("cta_url"), 2000),
            "cta_placement": placement,
        },
    }


@bp.get("/ai-control")
@login_required
def ai_control_page():
    risks = {}
    groups = {}
    for tool in TOOLS:
        risks[tool["risk"]] = risks.get(tool["risk"], 0) + 1
        parts = tool["name"].split(".")
        group = parts[1] if len(parts) > 1 else "other"
        groups.setdefault(group, []).append(tool)
    for tools in groups.values():
        tools.sort(key=lambda x: x["name"])
    return render_template(
        "ai_control.html",
        tool_count=len(TOOLS),
        risk_counts=risks,
        tool_groups=sorted(groups.items()),
        manifest_paths=["/.well-known/syntal-ai-tools", "/api/ai/v1/manifest"],
    )


@bp.get("/api/ai/v1/control/capabilities")
def control_capabilities():
    actor, error = _guard()
    if error: return error
    risks = {}
    groups = {}
    for tool in TOOLS:
        risks[tool["risk"]] = risks.get(tool["risk"], 0) + 1
        parts = tool["name"].split(".")
        group = parts[1] if len(parts) > 1 else "other"
        groups[group] = groups.get(group, 0) + 1
    return _ok(app_version=Config.APP_VERSION, tool_count=len(TOOLS), risks=risks, groups=groups,
               connectors={"blackbook": blackbook_connector_status(actor["local_org_id"])})


@bp.get("/api/ai/v1/control/health")
def control_health():
    actor, error = _guard()
    if error: return error
    from blackbook_service import public_connector_status
    return _ok(
        app_version=Config.APP_VERSION,
        organization_id=actor["identity"].get("syntal_org_id"),
        dependencies={
            "deepseek_configured": bool(Config.DEEPSEEK_API_KEY),
            "openai_images_configured": bool(Config.OPENAI_API_KEY),
            "blackbook": public_connector_status(actor["local_org_id"]),
            "domain_auto_provision": bool(Config.DOMAIN_AUTO_PROVISION),
        },
        queue_counts={
            "article_changes": article_change_jobs.count_documents({**_scope(actor), "status": {"$in": ["queued", "processing", "retrying"]}}),
            "social_generation": social_generation_jobs.count_documents({**_scope(actor), "status": {"$in": ["queued", "processing", "retrying"]}}),
            "landing_changes": landing_page_change_jobs.count_documents({**_scope(actor), "status": {"$in": ["queued", "processing", "retrying"]}}),
        },
    )


@bp.get("/api/ai/v1/collections/<collection_id>/sources")
def collection_sources_list(collection_id):
    actor, error = _guard()
    if error: return error
    collection = _find_owned(collections, actor, collection_id, "collection_id")
    if not collection: return _error("Collection not found", 404, "not_found")
    rows = collection_sources.find({"collection_id": collection["_id"], **_scope(actor)}).sort("created_at", -1).limit(_limit())
    return _ok(items=[_sanitize_doc(x, content_limit=2500) for x in rows])


@bp.get("/api/ai/v1/collections/<collection_id>/items")
def collection_items_list(collection_id):
    actor, error = _guard()
    if error: return error
    collection = _find_owned(collections, actor, collection_id, "collection_id")
    if not collection: return _error("Collection not found", 404, "not_found")
    q = {"collection_id": collection["_id"], **_scope(actor)}
    if str(request.args.get("active_only") or "").lower() in {"1", "true", "yes"}:
        q["active"] = {"$ne": False}
    rows = collection_items.find(q).sort("created_at", -1).limit(_limit(maximum=500))
    return _ok(items=[_sanitize_doc(x, content_limit=6000) for x in rows])


@bp.get("/api/ai/v1/collections/items/<item_id>")
def collection_item_read(item_id):
    actor, error = _guard()
    if error: return error
    row = _find_owned(collection_items, actor, item_id, "item_id")
    return _ok(item=_sanitize_doc(row, content_limit=18000)) if row else _error("Collection item not found", 404, "not_found")


@bp.post("/api/ai/v1/feeds/<feed_id>/refresh")
def feed_refresh(feed_id):
    actor, error = _guard(admin=True)
    if error: return error
    feed = _find_owned(rss_feeds, actor, feed_id, "feed_id")
    if not feed: return _error("Feed not found", 404, "not_found")
    task = update_rss_feed.delay(str(feed["_id"]))
    return _ok(feed_id=feed_id, task_id=task.id, status="queued")


@bp.get("/api/ai/v1/feeds/<feed_id>/items")
def feed_items(feed_id):
    actor, error = _guard()
    if error: return error
    feed = _find_owned(rss_feeds, actor, feed_id, "feed_id")
    if not feed: return _error("Feed not found", 404, "not_found")
    rows = rss_feed_items.find({"feed_id": feed["_id"]}).sort([("published", -1), ("created_at", -1)]).limit(_limit(maximum=500))
    return _ok(items=[_sanitize_doc(x, content_limit=4500) for x in rows])


@bp.get("/api/ai/v1/workers/<worker_id>/runs")
def worker_runs(worker_id):
    actor, error = _guard()
    if error: return error
    worker = _find_owned(newsjacking_workers, actor, worker_id, "worker_id")
    if not worker: return _error("Worker not found", 404, "not_found")
    rows = newsjacking_runs.find({**_scope(actor), "newsjacking_worker_id": worker["_id"]}).sort("started_at", -1).limit(_limit(maximum=200))
    return _ok(items=[_sanitize_doc(x, content_limit=5000) for x in rows])


@bp.get("/api/ai/v1/workers/<worker_id>/learning")
def worker_learning(worker_id):
    actor, error = _guard()
    if error: return error
    worker = _find_owned(newsjacking_workers, actor, worker_id, "worker_id")
    if not worker: return _error("Worker not found", 404, "not_found")
    learning = worker.get("learning_state") or compute_worker_learning(worker, persist=True)
    return _ok(worker_id=worker_id, mode=worker.get("learning_mode") or "observe", learning=learning)


@bp.post("/api/ai/v1/workers/<worker_id>/learning/refresh")
def worker_learning_refresh(worker_id):
    actor, error = _guard(admin=True)
    if error: return error
    worker = _find_owned(newsjacking_workers, actor, worker_id, "worker_id")
    if not worker: return _error("Worker not found", 404, "not_found")
    learning = compute_worker_learning(worker, persist=True)
    return _ok(worker_id=worker_id, learning=learning)


@bp.post("/api/ai/v1/workers/<worker_id>/learning/apply")
def worker_learning_apply(worker_id):
    actor, error = _guard(admin=True)
    if error: return error
    worker = _find_owned(newsjacking_workers, actor, worker_id, "worker_id")
    if not worker: return _error("Worker not found", 404, "not_found")
    body = _payload(); learning = worker.get("learning_state") or compute_worker_learning(worker, persist=True)
    if not learning.get("ready"): return _error("Not enough measured traffic to apply learning recommendations", 409, "insufficient_sample")
    policy = learning.get("policy") or {}; update = {"updated_at": now()}
    adaptive = bool(body.get("enable_adaptive"))
    if bool(body.get("apply_confidence")) and not adaptive and policy.get("recommended_min_confidence") is not None:
        update["min_confidence"] = max(0.0, min(1.0, float(policy["recommended_min_confidence"])))
    if adaptive: update["learning_mode"] = "adaptive"
    newsjacking_workers.update_one({"_id": worker["_id"]}, {"$set": update})
    return _ok(worker_id=worker_id, applied=update)


@bp.post("/api/ai/v1/articles/<article_id>/notes")
def article_notes(article_id):
    actor, error = _guard(admin=True)
    if error: return error
    article = _find_owned(articles, actor, article_id, "article_id")
    if not article: return _error("Article not found", 404, "not_found")
    body = _payload(); notes = clean_text(body.get("notes"), 12000); version_id = str(body.get("version_id") or "").strip()
    if version_id:
        try: vid = ObjectId(version_id)
        except Exception: return _error("Invalid version_id")
        result = article_versions.update_one({"_id": vid, "article_id": article["_id"]}, {"$set": {"review_notes": notes, "review_notes_updated_at": now()}})
        if not result.matched_count: return _error("Article version not found", 404, "not_found")
    else:
        articles.update_one({"_id": article["_id"]}, {"$set": {"review_notes": notes, "review_notes_revision": int(article.get("revision") or 1), "review_notes_updated_at": now(), "updated_at": now()}})
    return _ok(article_id=article_id, version_id=version_id or None, saved=True)


@bp.get("/api/ai/v1/articles/<article_id>/versions")
def article_versions_list(article_id):
    actor, error = _guard()
    if error: return error
    article = _find_owned(articles, actor, article_id, "article_id")
    if not article: return _error("Article not found", 404, "not_found")
    rows = article_versions.find({"article_id": article["_id"]}).sort([("revision", -1), ("created_at", -1)]).limit(_limit(maximum=100))
    return _ok(current_revision=int(article.get("revision") or 1), items=[_sanitize_doc(x, content_limit=3500) for x in rows])


@bp.get("/api/ai/v1/articles/<article_id>/versions/<version_id>")
def article_version_read(article_id, version_id):
    actor, error = _guard()
    if error: return error
    article = _find_owned(articles, actor, article_id, "article_id")
    if not article: return _error("Article not found", 404, "not_found")
    try: vid = ObjectId(version_id)
    except Exception: return _error("Invalid version_id")
    row = article_versions.find_one({"_id": vid, "article_id": article["_id"]})
    return _ok(item=_sanitize_doc(row, content_limit=18000)) if row else _error("Article version not found", 404, "not_found")


@bp.post("/api/ai/v1/articles/<article_id>/versions/<version_id>/restore")
def article_version_restore(article_id, version_id):
    actor, error = _guard(admin=True)
    if error: return error
    article = _find_owned(articles, actor, article_id, "article_id")
    if not article: return _error("Article not found", 404, "not_found")
    try: vid = ObjectId(version_id)
    except Exception: return _error("Invalid version_id")
    version = article_versions.find_one({"_id": vid, "article_id": article["_id"]})
    if not version: return _error("Article version not found", 404, "not_found")
    if article.get("content"):
        article_versions.insert_one({
            "article_id": article["_id"], "revision": int(article.get("revision") or 1),
            "reason": "Before article revision restore via Syntal AI", "content": article.get("content") or "",
            "metadata": article.get("metadata") or {}, "quality": article.get("quality") or {},
            "editorial_plan": article.get("editorial_plan") or {}, "review_notes": article.get("review_notes") or "",
            "review_status": article.get("review_status"), "publication": article.get("publication") or {},
            "published": bool(article.get("published")), "created_at": now(),
        })
    revision = int(article.get("revision") or 1) + 1
    articles.update_one({"_id": article["_id"]}, {"$set": {
        "content": version.get("content") or "", "metadata": version.get("metadata") or {},
        "quality": version.get("quality") or {}, "editorial_plan": version.get("editorial_plan") or article.get("editorial_plan") or {},
        "revision": revision, "review_status": "pending_review", "status": "review", "published": False,
        "review_notes": "", "review_notes_revision": revision, "updated_at": now(),
    }})
    return _ok(article_id=article_id, restored_version_id=version_id, revision=revision)


@bp.get("/api/ai/v1/articles/<article_id>/change-jobs")
def article_change_job_list(article_id):
    actor, error = _guard()
    if error: return error
    article = _find_owned(articles, actor, article_id, "article_id")
    if not article: return _error("Article not found", 404, "not_found")
    rows = article_change_jobs.find({"article_id": article["_id"]}).sort("created_at", -1).limit(_limit(maximum=100))
    return _ok(items=[_sanitize_doc(x, content_limit=4000) for x in rows])


@bp.get("/api/ai/v1/articles/<article_id>/submissions")
def article_submission_list(article_id):
    actor, error = _guard()
    if error: return error
    article = _find_owned(articles, actor, article_id, "article_id")
    if not article: return _error("Article not found", 404, "not_found")
    rows = article_submissions.find({"article_id": article["_id"]}).sort("created_at", -1).limit(_limit(maximum=100))
    return _ok(items=[_safe_submission(x) for x in rows])


@bp.get("/api/ai/v1/campaigns/<campaign_id>/social/settings")
def social_settings_read(campaign_id):
    actor, error = _guard()
    if error: return error
    campaign = _find_owned(campaigns, actor, campaign_id, "campaign_id")
    if not campaign: return _error("Campaign not found", 404, "not_found")
    return _ok(settings=campaign.get("social_calendar") or {})


@bp.patch("/api/ai/v1/campaigns/<campaign_id>/social/settings")
def social_settings_update(campaign_id):
    actor, error = _guard(admin=True)
    if error: return error
    campaign = _find_owned(campaigns, actor, campaign_id, "campaign_id")
    if not campaign: return _error("Campaign not found", 404, "not_found")
    body = _payload(); current = dict(campaign.get("social_calendar") or {})
    allowed_platforms = {"linkedin", "x", "facebook", "instagram", "threads", "bluesky"}
    if "platforms" in body:
        current["platforms"] = [p for p in body.get("platforms") or [] if p in allowed_platforms] or ["linkedin", "x"]
    for key in ("enabled", "auto_generate_newsjacking", "image_enabled", "auto_schedule"):
        if key in body: current[key] = bool(body.get(key))
    if "posting_weekdays" in body:
        current["posting_weekdays"] = sorted({int(x) for x in body.get("posting_weekdays") or [] if str(x).isdigit() and 0 <= int(x) <= 6}) or [0,1,2,3,4]
    for key, limit in (("timezone", 80), ("default_post_time", 20), ("brand_voice", 1200), ("image_style", 1600)):
        if key in body: current[key] = clean_text(body.get(key), limit)
    campaigns.update_one({"_id": campaign["_id"]}, {"$set": {"social_calendar": current, "updated_at": now()}})
    return _ok(settings=current)


@bp.get("/api/ai/v1/social/jobs")
def social_jobs_list():
    actor, error = _guard()
    if error: return error
    q = _scope(actor)
    for field in ("article_id", "campaign_id"):
        raw = request.args.get(field)
        if raw:
            try: q[field] = ObjectId(raw)
            except Exception: return _error(f"Invalid {field}")
    rows = social_generation_jobs.find(q).sort("created_at", -1).limit(_limit(maximum=100))
    return _ok(items=[_sanitize_doc(x, content_limit=6000) for x in rows])


@bp.get("/api/ai/v1/social/jobs/<job_id>")
def social_job_read(job_id):
    actor, error = _guard()
    if error: return error
    row = _find_owned(social_generation_jobs, actor, job_id, "job_id")
    return _ok(item=_sanitize_doc(row, content_limit=9000)) if row else _error("Social generation job not found", 404, "not_found")


def _blackbook_call(fn, actor, *args, **kwargs):
    try:
        return _ok(result=fn(actor["local_org_id"], *args, **kwargs))
    except Exception as exc:
        return _error(str(exc), 502, "blackbook_error")


@bp.get("/api/ai/v1/blackbook/context")
def blackbook_context():
    actor, error = _guard()
    if error: return error
    return _blackbook_call(blackbook_marketing_context, actor)


@bp.get("/api/ai/v1/blackbook/people")
def blackbook_people():
    actor, error = _guard()
    if error: return error
    query = clean_text(request.args.get("q"), 160)
    eligible = str(request.args.get("eligible_only") or "").lower() in {"1", "true", "yes"}
    limit = _safe_int(request.args.get("limit"), 24, 1, 100)
    return _blackbook_call(blackbook_marketing_people, actor, query=query, eligible_only=eligible, limit=limit)


@bp.get("/api/ai/v1/blackbook/segments")
def blackbook_segments():
    actor, error = _guard()
    if error: return error
    return _blackbook_call(blackbook_marketing_segments, actor)


@bp.get("/api/ai/v1/blackbook/campaigns")
def blackbook_campaigns():
    actor, error = _guard()
    if error: return error
    return _blackbook_call(blackbook_marketing_campaigns, actor, limit=_safe_int(request.args.get("limit"), 30, 1, 100))


@bp.patch("/api/ai/v1/newsletters/schedules/<schedule_id>/audience")
def newsletter_audience_update(schedule_id):
    actor, error = _guard(admin=True)
    if error: return error
    schedule = _find_owned(newsletter_schedules, actor, schedule_id, "schedule_id")
    if not schedule: return _error("Newsletter schedule not found", 404, "not_found")
    body = _payload(); segments = [clean_text(x, 160) for x in body.get("segment_ids") or [] if clean_text(x, 160)][:50]
    people = [clean_text(x, 160) for x in body.get("person_ids") or [] if clean_text(x, 160)][:500]
    allowed_buckets = {"inactive", "low", "medium", "high"}
    buckets = list(dict.fromkeys(clean_text(x, 20).lower() for x in body.get("engagement_buckets") or [] if clean_text(x, 20).lower() in allowed_buckets))[:4]
    interests = list(dict.fromkeys(clean_text(x, 160) for x in body.get("interest_ids") or [] if clean_text(x, 160)))[:100]
    interest_match = "all" if clean_text(body.get("interest_match") or "any", 8).lower() == "all" else "any"
    include_all = bool(body.get("include_all_eligible"))
    if include_all:
        segments, people, buckets, interests, interest_match = [], [], [], [], "any"
    if not include_all and not any((segments, people, buckets, interests)):
        return _error("Choose at least one audience criterion", 400, "audience_required")
    try:
        preview = blackbook_audience_preview(
            actor["local_org_id"], segment_ids=segments, person_ids=people, include_all_eligible=include_all,
            engagement_buckets=buckets, interest_ids=interests, interest_match=interest_match,
        )
    except Exception as exc:
        return _error(str(exc), 502, "blackbook_error")
    snapshot = {
        "audience_counts": preview.get("counts") or {},
        "average_relationship_strength": preview.get("average_relationship_strength") or 0,
        "audience_categories": (preview.get("categories") or [])[:15],
        "engagement_distribution": preview.get("engagement_distribution") or {},
        "audience_criteria": preview.get("criteria") or {},
        "selected_engagement_buckets": buckets, "selected_interest_ids": interests,
        "interest_match": interest_match, "refreshed_at": now(),
    }
    newsletter_schedules.update_one({"_id": schedule["_id"]}, {"$set": {
        "blackbook_segment_ids": segments, "blackbook_person_ids": people,
        "blackbook_include_all_eligible": include_all, "blackbook_engagement_buckets": buckets,
        "blackbook_interest_ids": interests, "blackbook_interest_match": interest_match,
        "blackbook_audience_snapshot": snapshot, "blackbook_audience_refreshed_at": now(), "updated_at": now(),
    }})
    return _ok(schedule_id=schedule_id, audience={"segment_ids": segments, "person_ids": people, "include_all_eligible": include_all, "engagement_buckets": buckets, "interest_ids": interests, "interest_match": interest_match}, preview=preview)


@bp.get("/api/ai/v1/newsletters/schedules/<schedule_id>/audience/preview")
def newsletter_audience_preview_configured(schedule_id):
    actor, error = _guard()
    if error: return error
    schedule = _find_owned(newsletter_schedules, actor, schedule_id, "schedule_id")
    if not schedule: return _error("Newsletter schedule not found", 404, "not_found")
    try:
        result = blackbook_audience_preview(
            actor["local_org_id"], segment_ids=schedule.get("blackbook_segment_ids") or [],
            person_ids=schedule.get("blackbook_person_ids") or [], include_all_eligible=bool(schedule.get("blackbook_include_all_eligible")),
            engagement_buckets=schedule.get("blackbook_engagement_buckets") or [], interest_ids=schedule.get("blackbook_interest_ids") or [],
            interest_match=schedule.get("blackbook_interest_match") or "any",
        )
    except Exception as exc:
        return _error(str(exc), 502, "blackbook_error")
    return _ok(schedule_id=schedule_id, result=result)


@bp.get("/api/ai/v1/newsletters/editions/<edition_id>/preview")
def newsletter_edition_preview(edition_id):
    actor, error = _guard()
    if error: return error
    edition = _find_owned(newsletter_editions, actor, edition_id, "edition_id")
    if not edition: return _error("Newsletter edition not found", 404, "not_found")
    return _ok(edition_id=edition_id, status=edition.get("status"), subject=edition.get("subject") or "", preheader=edition.get("preheader") or "", html=(edition.get("html") or "")[:50000], text=(edition.get("text") or "")[:30000])


@bp.get("/api/ai/v1/analytics/events")
def analytics_event_list():
    actor, error = _guard()
    if error: return error
    days = _safe_int(request.args.get("days"), 30, 1, 730); q = {**_scope(actor), "occurred_at": {"$gte": now() - timedelta(days=days)}}
    if request.args.get("event_type"): q["event_type"] = clean_text(request.args.get("event_type"), 60)
    if request.args.get("content_type"): q["content_type"] = clean_text(request.args.get("content_type"), 60)
    if request.args.get("content_id"):
        try: q["content_id"] = ObjectId(request.args["content_id"])
        except Exception: return _error("Invalid content_id")
    fields = {"ip": 0, "user_agent": 0}
    rows = analytics_events.find(q, fields).sort("occurred_at", -1).limit(_limit(maximum=500))
    return _ok(days=days, items=[_sanitize_doc(x, content_limit=2500) for x in rows])


@bp.get("/api/ai/v1/analytics/content/<content_type>/<content_id>")
def analytics_content_summary(content_type, content_id):
    actor, error = _guard()
    if error: return error
    allowed = {"article": articles, "page": landing_pages, "newsletter": newsletter_editions, "social_post": social_media_posts}
    collection = allowed.get(content_type)
    if not collection: return _error("Unsupported content_type")
    item = _find_owned(collection, actor, content_id, "content_id")
    if not item: return _error("Content not found", 404, "not_found")
    days = _safe_int(request.args.get("days"), 30, 1, 730)
    q = {**_scope(actor), "content_type": content_type, "content_id": item["_id"], "occurred_at": {"$gte": now() - timedelta(days=days)}}
    return _ok(days=days, content_type=content_type, content_id=content_id, summary=_analytics_summary(q))


@bp.get("/api/ai/v1/analytics/campaigns/<campaign_id>")
def analytics_campaign_summary(campaign_id):
    actor, error = _guard()
    if error: return error
    campaign = _find_owned(campaigns, actor, campaign_id, "campaign_id")
    if not campaign: return _error("Campaign not found", 404, "not_found")
    days = _safe_int(request.args.get("days"), 30, 1, 730); start = now() - timedelta(days=days)
    q = {**_scope(actor), "$or": [{"campaign_id": campaign["_id"]}, {"campaign_ids": campaign["_id"]}], "occurred_at": {"$gte": start}}
    return _ok(days=days, campaign_id=campaign_id, summary=_analytics_summary(q))


@bp.post("/api/ai/v1/domains")
def domain_create():
    actor, error = _guard(admin=True)
    if error: return error
    try: host = _normalize_domain(_payload().get("domain"))
    except ValueError as exc: return _error(str(exc))
    existing = domain_mappings.find_one({"domain": host})
    if existing:
        if existing.get("organization_id") == actor["local_org_id"]: return _ok(already_owned=True, item=_sanitize_doc(existing, content_limit=2500))
        return _error("This domain is already owned by another workspace", 409, "transfer_required")
    doc = {"user_id": actor["user_id"], **_scope(actor), "domain": host, "status": "pending", "expected_ip": Config.PUBLISHING_IP, "resolved_ips": [], "created_at": now(), "updated_at": now()}
    doc["_id"] = domain_mappings.insert_one(doc).inserted_id
    return _ok(item=_sanitize_doc(doc), dns_instruction={"type": "A", "name": "@", "value": Config.PUBLISHING_IP})


@bp.post("/api/ai/v1/domains/<domain_id>/verify")
def domain_verify(domain_id):
    actor, error = _guard(admin=True)
    if error: return error
    domain = _find_owned(domain_mappings, actor, domain_id, "domain_id")
    if not domain: return _error("Domain not found", 404, "not_found")
    resolved = _resolved_ips(domain["domain"]); stamped = now(); update = {"resolved_ips": resolved, "last_checked_at": stamped, "updated_at": stamped}
    if Config.PUBLISHING_IP not in resolved:
        update.update({"status": "pending", "tls_active": False, "provisioning_error": None}); domain_mappings.update_one({"_id": domain["_id"]}, {"$set": update}); domain.update(update)
        return _error(f"DNS is not pointing to {Config.PUBLISHING_IP} yet", 422, "dns_not_ready")
    update.update({"verified_at": domain.get("verified_at") or stamped})
    if not Config.DOMAIN_AUTO_PROVISION:
        update.update({"status": "verified", "provisioning_error": None}); domain_mappings.update_one({"_id": domain["_id"]}, {"$set": update}); domain.update(update)
        return _ok(item=_sanitize_doc(domain))
    domain_mappings.update_one({"_id": domain["_id"]}, {"$set": {**update, "status": "provisioning", "provision_attempted_at": stamped}})
    try:
        result = provision_domain(domain["domain"])
    except DomainProvisioningError as exc:
        failed = {"status": "provisioning_failed", "tls_active": False, "provisioning_error": str(exc)[:1500], "updated_at": now()}; domain_mappings.update_one({"_id": domain["_id"]}, {"$set": failed})
        return _error(failed["provisioning_error"], 503, "provisioning_failed")
    active = {"status": "active", "tls_active": bool(result.get("tls", True)), "provisioned_at": now(), "provisioning_error": None, "updated_at": now()}
    if result.get("resolved_ips"): active["resolved_ips"] = result["resolved_ips"]
    domain_mappings.update_one({"_id": domain["_id"]}, {"$set": active}); domain.update(update); domain.update(active)
    return _ok(item=_sanitize_doc(domain))


@bp.delete("/api/ai/v1/domains/<domain_id>")
def domain_delete(domain_id):
    actor, error = _guard(admin=True)
    if error: return error
    domain = _find_owned(domain_mappings, actor, domain_id, "domain_id")
    if not domain: return _error("Domain not found", 404, "not_found")
    if domain_routes.count_documents({"domain_id": domain["_id"]}): return _error("Remove domain routes before disconnecting the domain", 409, "routes_exist")
    cleanup_warning = ""
    if Config.DOMAIN_AUTO_PROVISION and domain.get("status") in {"active", "verified", "provisioning_failed"}:
        try: deprovision_domain(domain["domain"])
        except Exception as exc: cleanup_warning = str(exc)[:1000]
    domain_mappings.delete_one({"_id": domain["_id"]})
    return _ok(deleted=True, cleanup_warning=cleanup_warning)


@bp.get("/api/ai/v1/domains/<domain_id>/routes")
def domain_routes_list(domain_id):
    actor, error = _guard()
    if error: return error
    domain = _find_owned(domain_mappings, actor, domain_id, "domain_id")
    if not domain: return _error("Domain not found", 404, "not_found")
    rows = domain_routes.find({"domain_id": domain["_id"], **_scope(actor)}).sort("path_key", 1)
    return _ok(domain=_sanitize_doc(domain, content_limit=2000), items=[_sanitize_doc(x, content_limit=2500) for x in rows])


@bp.post("/api/ai/v1/domains/<domain_id>/routes")
def domain_route_create(domain_id):
    actor, error = _guard(admin=True)
    if error: return error
    domain = _find_owned(domain_mappings, actor, domain_id, "domain_id")
    if not domain: return _error("Domain not found", 404, "not_found")
    body = _payload(); page = _find_owned(landing_pages, actor, body.get("page_id"), "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    try: path = _normalize_path(body.get("path") or "/")
    except ValueError as exc: return _error(str(exc))
    if domain_routes.find_one({"domain_id": domain["_id"], "path_key": path.casefold()}): return _error("This domain and path are already mapped", 409, "path_conflict")
    doc = {"user_id": page.get("user_id") or actor["user_id"], **_scope(actor), "domain_id": domain["_id"], "campaign_id": page.get("campaign_id"), "page_id": page["_id"], "path": path, "path_key": path.casefold(), "goal": clean_text(body.get("goal") or page.get("title"), 500), "status": "draft", "created_at": now(), "updated_at": now()}
    doc["_id"] = domain_routes.insert_one(doc).inserted_id
    return _ok(item=_sanitize_doc(doc))


@bp.delete("/api/ai/v1/domain-routes/<route_id>")
def domain_route_delete(route_id):
    actor, error = _guard(admin=True)
    if error: return error
    route = _find_owned(domain_routes, actor, route_id, "route_id")
    if not route: return _error("Domain route not found", 404, "not_found")
    domain_routes.delete_one({"_id": route["_id"]})
    return _ok(deleted=True)


@bp.get("/api/ai/v1/domains/<domain_id>/dns")
def domain_dns(domain_id):
    actor, error = _guard()
    if error: return error
    domain = _find_owned(domain_mappings, actor, domain_id, "domain_id")
    if not domain: return _error("Domain not found", 404, "not_found")
    resolved = _resolved_ips(domain["domain"])
    return _ok(domain=domain["domain"], ready=Config.PUBLISHING_IP in resolved, resolved_ips=resolved, expected_ip=Config.PUBLISHING_IP, dns_instruction={"type": "A", "name": "@", "value": Config.PUBLISHING_IP})


@bp.get("/api/ai/v1/sites/<site_id>")
def site_read(site_id):
    actor, error = _guard()
    if error: return error
    site = _find_owned(website_sites, actor, site_id, "site_id")
    return _ok(item=_sanitize_doc(site, content_limit=12000)) if site else _error("Site not found", 404, "not_found")


@bp.post("/api/ai/v1/landing-pages/<page_id>/request-changes")
def landing_page_request_changes(page_id):
    actor, error = _guard(admin=True)
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    body = _payload(); notes = clean_text(body.get("notes"), 12000)
    if not notes: return _error("Review notes are required")
    version_id = str(body.get("version_id") or "").strip(); version_oid = None; base_revision = int(page.get("revision") or 0)
    if version_id:
        try: version_oid = ObjectId(version_id)
        except Exception: return _error("Invalid version_id")
        version = landing_page_versions.find_one({"_id": version_oid, "landing_page_id": page["_id"]})
        if not version: return _error("Landing page version not found", 404, "not_found")
        base_revision = int(version.get("revision") or base_revision)
        landing_page_versions.update_one({"_id": version_oid}, {"$set": {"review_notes": notes, "review_notes_updated_at": now()}})
    else:
        landing_pages.update_one({"_id": page["_id"]}, {"$set": {"review_notes": notes, "review_notes_revision": base_revision, "review_notes_updated_at": now()}})
    job = {"user_id": actor["user_id"], **_scope(actor), "landing_page_id": page["_id"], "version_id": version_oid, "base_revision": base_revision, "notes": notes, "status": "queued", "created_at": now(), "updated_at": now()}
    job["_id"] = landing_page_change_jobs.insert_one(job).inserted_id
    task = apply_landing_review_notes_task.delay(str(job["_id"])); landing_page_change_jobs.update_one({"_id": job["_id"]}, {"$set": {"celery_task_id": task.id}})
    return _ok(job_id=str(job["_id"]), task_id=task.id, base_revision=base_revision)


@bp.get("/api/ai/v1/landing-pages/<page_id>/change-jobs")
def landing_page_change_jobs_list(page_id):
    actor, error = _guard()
    if error: return error
    page = _find_owned(landing_pages, actor, page_id, "page_id")
    if not page: return _error("Landing page not found", 404, "not_found")
    rows = landing_page_change_jobs.find({"landing_page_id": page["_id"]}).sort("created_at", -1).limit(_limit(maximum=100))
    return _ok(items=[_sanitize_doc(x, content_limit=5000) for x in rows])


@bp.get("/api/ai/v1/experiments")
def experiments_list():
    actor, error = _guard()
    if error: return error
    q = _scope(actor); status = clean_text(request.args.get("status"), 30)
    if status: q["status"] = status
    rows = experiments.find(q).sort("updated_at", -1).limit(_limit(maximum=200))
    return _ok(items=[_sanitize_doc(x, content_limit=6000) for x in rows])


@bp.get("/api/ai/v1/experiments/<experiment_id>")
def experiment_read(experiment_id):
    actor, error = _guard()
    if error: return error
    exp = _find_owned(experiments, actor, experiment_id, "experiment_id")
    if not exp: return _error("Experiment not found", 404, "not_found")
    results = experiment_results(exp); decision = experiment_decision(exp, results) if results else {"ready": False}
    return _ok(item=_sanitize_doc(exp, content_limit=9000), results=results, decision=decision)


@bp.post("/api/ai/v1/experiments")
def experiment_create():
    actor, error = _guard(admin=True)
    if error: return error
    body = _payload(); target_type = clean_text(body.get("target_type"), 20); target = _experiment_target(actor, target_type, body.get("target_id"))
    if not target: return _error("Experiment target not found", 404, "not_found")
    if target_type == "page" and not target.get("published"): return _error("Publish the page before creating an experiment", 409, "target_not_published")
    if target_type == "article" and not (target.get("published") and target.get("review_status") == "signed"): return _error("Sign and publish the article before creating an experiment", 409, "target_not_published")
    metric = clean_text(body.get("primary_metric"), 40) or "conversion_rate"
    if metric not in PRIMARY_METRICS: metric = "conversion_rate"
    weight = _safe_int(body.get("control_weight"), 50, 5, 95); challenger = _normalize_challenger(body.get("challenger")); challenger["weight"] = 100 - weight
    snapshot = _control_snapshot(target_type, target)
    doc = {**_scope(actor), "created_by_user_id": actor["user_id"], "name": clean_text(body.get("name"), 160) or "Experiment", "hypothesis": clean_text(body.get("hypothesis"), 1200), "target_type": target_type, "target_id": target["_id"], "target_revision": int(snapshot.get("revision") or 0), "control_snapshot": snapshot, "primary_metric": metric, "min_sample_size": _safe_int(body.get("min_sample_size"), 100, 20, 1000000), "variants": [{"id": "control", "name": "Control", "weight": weight, "overrides": {}}, challenger], "status": "draft", "winner_variant_id": "", "created_at": now(), "updated_at": now()}
    doc["_id"] = experiments.insert_one(doc).inserted_id
    return _ok(item=_sanitize_doc(doc, content_limit=9000))


@bp.patch("/api/ai/v1/experiments/<experiment_id>")
def experiment_update(experiment_id):
    actor, error = _guard(admin=True)
    if error: return error
    exp = _find_owned(experiments, actor, experiment_id, "experiment_id")
    if not exp: return _error("Experiment not found", 404, "not_found")
    if exp.get("status") in {"running", "completed"}: return _error("Pause a running experiment before editing; completed experiments are locked", 409, "experiment_locked")
    body = _payload(); update = {"updated_at": now()}
    if "name" in body: update["name"] = clean_text(body.get("name"), 160) or exp.get("name") or "Experiment"
    if "hypothesis" in body: update["hypothesis"] = clean_text(body.get("hypothesis"), 1200)
    metric = clean_text(body.get("primary_metric"), 40) if "primary_metric" in body else exp.get("primary_metric")
    if metric in PRIMARY_METRICS: update["primary_metric"] = metric
    if "min_sample_size" in body: update["min_sample_size"] = _safe_int(body.get("min_sample_size"), int(exp.get("min_sample_size") or 100), 20, 1000000)
    if "control_weight" in body or "challenger" in body:
        weight = _safe_int(body.get("control_weight"), int(((exp.get("variants") or [{}])[0]).get("weight") or 50), 5, 95)
        current_challenger = next((v for v in exp.get("variants") or [] if v.get("id") != "control"), {})
        raw_challenger = body.get("challenger") if "challenger" in body else {**(current_challenger.get("overrides") or {}), "name": current_challenger.get("name")}
        challenger = _normalize_challenger(raw_challenger); challenger["weight"] = 100 - weight
        update["variants"] = [{"id": "control", "name": "Control", "weight": weight, "overrides": {}}, challenger]
    experiments.update_one({"_id": exp["_id"]}, {"$set": update})
    return _ok(item=_sanitize_doc(experiments.find_one({"_id": exp["_id"]}), content_limit=9000))


@bp.post("/api/ai/v1/experiments/<experiment_id>/start")
def experiment_start(experiment_id):
    actor, error = _guard(admin=True)
    if error: return error
    exp = _find_owned(experiments, actor, experiment_id, "experiment_id")
    if not exp: return _error("Experiment not found", 404, "not_found")
    if exp.get("status") == "completed": return _error("Completed experiments cannot be restarted", 409, "experiment_locked")
    target = _experiment_target(actor, exp.get("target_type"), exp.get("target_id"))
    if not target or (exp.get("target_revision") and _target_revision(target) != int(exp.get("target_revision") or 0)):
        experiments.update_one({"_id": exp["_id"]}, {"$set": {"status": "paused", "pause_reason": "target_changed", "paused_at": now(), "updated_at": now()}})
        return _error("The published target changed; create a new experiment for the current revision", 409, "target_changed")
    conflict = experiments.find_one({**_scope(actor), "target_type": exp.get("target_type"), "target_id": exp.get("target_id"), "status": "running", "_id": {"$ne": exp["_id"]}})
    if conflict: return _error("Another experiment is already running on this target", 409, "experiment_conflict")
    experiments.update_one({"_id": exp["_id"]}, {"$set": {"status": "running", "winner_variant_id": "", "started_at": exp.get("started_at") or now(), "updated_at": now()}, "$unset": {"pause_reason": "", "paused_at": ""}})
    return _ok(status="running")


@bp.post("/api/ai/v1/experiments/<experiment_id>/pause")
def experiment_pause(experiment_id):
    actor, error = _guard(admin=True)
    if error: return error
    exp = _find_owned(experiments, actor, experiment_id, "experiment_id")
    if not exp: return _error("Experiment not found", 404, "not_found")
    experiments.update_one({"_id": exp["_id"]}, {"$set": {"status": "paused", "pause_reason": "manual", "paused_at": now(), "updated_at": now()}})
    return _ok(status="paused")


@bp.post("/api/ai/v1/experiments/<experiment_id>/complete")
def experiment_complete(experiment_id):
    actor, error = _guard(admin=True)
    if error: return error
    exp = _find_owned(experiments, actor, experiment_id, "experiment_id")
    if not exp: return _error("Experiment not found", 404, "not_found")
    body = _payload(); winner = clean_text(body.get("winner_variant_id"), 40); valid = {str(v.get("id")) for v in exp.get("variants") or []}
    if winner not in valid: return _error("Choose a valid winner_variant_id")
    target = _experiment_target(actor, exp.get("target_type"), exp.get("target_id"))
    if not target or (exp.get("target_revision") and _target_revision(target) != int(exp.get("target_revision") or 0)):
        return _error("The published target changed; this experiment cannot lock a winner", 409, "target_changed")
    experiments.update_one({"_id": exp["_id"]}, {"$set": {"status": "completed", "winner_variant_id": winner, "completed_at": now(), "updated_at": now()}})
    return _ok(status="completed", winner_variant_id=winner)


@bp.delete("/api/ai/v1/experiments/<experiment_id>")
def experiment_delete(experiment_id):
    actor, error = _guard(admin=True)
    if error: return error
    exp = _find_owned(experiments, actor, experiment_id, "experiment_id")
    if not exp: return _error("Experiment not found", 404, "not_found")
    if exp.get("status") == "running": return _error("Pause the experiment before deleting it", 409, "experiment_running")
    experiments.delete_one({"_id": exp["_id"]})
    return _ok(deleted=True)


@bp.get("/api/ai/v1/experiments/<experiment_id>/results")
def experiment_results_read(experiment_id):
    actor, error = _guard()
    if error: return error
    exp = _find_owned(experiments, actor, experiment_id, "experiment_id")
    if not exp: return _error("Experiment not found", 404, "not_found")
    results = experiment_results(exp); decision = experiment_decision(exp, results) if results else {"ready": False}
    return _ok(results=results, decision=decision)
