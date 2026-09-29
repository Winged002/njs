import json
import hashlib
import hmac
import secrets
import csv
import socket
import ipaddress
import re
import os
import zipfile
from pathlib import Path
from io import BytesIO
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlsplit

from bson import ObjectId
from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request, session,
    url_for, Response, make_response
)
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_wtf import CSRFProtect
from slugify import slugify
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps, UnidentifiedImageError

from config import Config
from db import (
    users, campaigns, products, newsjacking_workers, rss_feeds, rss_feed_items, hooks, articles, article_versions, article_change_jobs,
    article_generation_queue, newsjacking_runs, landing_pages, landing_page_versions, website_sites,
    domain_mappings, domain_routes, social_media_posts, social_generation_jobs,
    newsletter_schedules, newsletter_editions, newsletter_edition_versions, newsletter_design_jobs, landing_page_change_jobs, collections, collection_sources, collection_items, analytics_events, experiments, sso_organization_links, article_submissions, ensure_indexes,
)
from services import (
    clean_text, normalize_feed_url, normalize_website_url, now, strategy_option, compile_landing_html,
    sanitize_manual_landing_html, manual_landing_document,
    model_client, parse_json_object,
)
from newsletter_service import compile_newsletter_html, compile_newsletter_text, compile_newsletter_designed_html, sanitize_newsletter_design_html
from collection_service import ALLOWED_DOCUMENT_EXTENSIONS, evidence_context
from analytics_service import (
    record_view, inject_tracking, tracking_script, visitor_token_from_request, parse_date_range, analytics_report,
    child_event_from_view, record_conversion_from_request, analytics_settings_for, _hash as analytics_hash,
)
from experiment_service import (
    PRIMARY_METRICS, experiment_context_for_public, apply_page_variant, article_variant_view,
    experiment_results, experiment_decision,
)
from learning_service import LEARNING_MODES, LEARNING_OBJECTIVES, compute_worker_learning
from syntal_sso import (
    begin_login as syntal_begin_login, complete_login as syntal_complete_login,
    extract_identity as syntal_extract_identity, has_app_access as syntal_has_app_access,
    logout_url as syntal_logout_url, encrypt_token as syntal_encrypt_token,
    decrypt_token as syntal_decrypt_token, access_token_expiry as syntal_access_token_expiry,
    refresh_tokens as syntal_refresh_tokens, available_organizations as syntal_available_organizations,
)
from blackbook_service import (
    encrypt_secret as encrypt_blackbook_secret, public_connector_status as blackbook_connector_status,
    connector_for_local_org as blackbook_connector_for_org, marketing_context as blackbook_marketing_context,
    marketing_people as blackbook_marketing_people, marketing_segments as blackbook_marketing_segments,
    marketing_campaigns as blackbook_marketing_campaigns, marketing_audience_preview as blackbook_audience_preview,
    distribute_newsletter as blackbook_distribute_newsletter,
    marketing_engagement_overview as blackbook_engagement_overview,
    marketing_engagement_people as blackbook_engagement_people,
    marketing_engagement_refresh_queue as blackbook_engagement_refresh_queue,
    marketing_engagement_bulk_analyze as blackbook_engagement_bulk_analyze,
    marketing_engagement_interests as blackbook_engagement_interests,
    marketing_engagement_batches as blackbook_engagement_batches,
    marketing_engagement_batch as blackbook_engagement_batch,
)
from domain_provisioner import DomainProvisioningError, provision_domain, deprovision_domain
from tasks import (
    newsjacking_scan_user_task, newsjacking_scan_worker_task, generate_landing_page_task,
    prepare_campaign_strategy_task, generate_social_media_bundle_task, generate_newsletter_edition_task, apply_newsletter_visual_prompt_task, apply_landing_review_notes_task, apply_article_review_notes_task, process_collection_source_task, sync_article_submission_to_blackbook_task,
)

Config.validate()
ensure_indexes()

app = Flask(__name__)
app.config.from_object(Config)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
csrf = CSRFProtect(app)

# v3.7.0 first-party Syntal AI control plane. The Blueprint authenticates every
# request against Syntal SSO and scopes all data access to the token organization.
from ai_control import bp as syntal_ai_control_bp
csrf.exempt(syntal_ai_control_bp)
app.register_blueprint(syntal_ai_control_bp)
from ai_control_ext import bp as syntal_ai_control_ext_bp
csrf.exempt(syntal_ai_control_ext_bp)
app.register_blueprint(syntal_ai_control_ext_bp)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"


class User(UserMixin):
    def __init__(self, doc):
        self.doc = doc
        self.id = str(doc["_id"])
        self.username = doc.get("username", "")
        self.email = doc.get("email")
        self.organization_id = doc.get("organization_id")
        self.organization_name = doc.get("organization_name")
        self.syntal_user_id = doc.get("syntal_user_id")
        self.syntal_org_id = doc.get("syntal_org_id")
        self.sso_permissions = doc.get("sso_permissions") or []
        self.is_syntal_admin = Config.SSO_ADMIN_PERMISSION in set(self.sso_permissions)
        self.credits = doc.get("credits", 0)
        self.display_name = doc.get("display_name")
        self.author_profile = doc.get("author_profile") or {}


@login_manager.user_loader
def load_user(user_id):
    try:
        doc = users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        return None
    return User(doc) if doc else None



def _find_legacy_user_for_sso(identity):
    syntal_user_id = identity.get("syntal_user_id")
    if syntal_user_id:
        doc = users.find_one({"syntal_user_id": syntal_user_id})
        if doc:
            return doc
    email = (identity.get("email") or "").strip()
    if email:
        doc = users.find_one({"email": {"$regex": "^" + re.escape(email) + "$", "$options": "i"}})
        if doc:
            return doc
    username = (identity.get("username") or "").strip().lower()
    if username:
        return users.find_one({"username": username})
    return None


def _resolve_local_org(identity, existing_user=None):
    syntal_org_id = identity.get("syntal_org_id")
    if not syntal_org_id:
        return (existing_user or {}).get("organization_id")
    link = sso_organization_links.find_one({"syntal_org_id": syntal_org_id})
    if link and link.get("local_organization_id"):
        return link["local_organization_id"]

    # A Syntal user can belong to several organizations. Never reuse the local
    # workspace id from a different SSO organization or data can bleed across
    # workspaces after an organization switch.
    existing_syntal_org = str((existing_user or {}).get("syntal_org_id") or "").strip()
    existing_local_org = (existing_user or {}).get("organization_id")
    local_id = existing_local_org if existing_local_org and existing_syntal_org == str(syntal_org_id) else ObjectId()
    now_value = now()
    sso_organization_links.update_one(
        {"syntal_org_id": syntal_org_id},
        {"$setOnInsert": {"local_organization_id": local_id, "created_at": now_value}, "$set": {
            "organization_name": identity.get("organization_name"), "updated_at": now_value,
        }},
        upsert=True,
    )
    link = sso_organization_links.find_one({"syntal_org_id": syntal_org_id})
    return (link or {}).get("local_organization_id") or local_id


def _provision_syntal_user(identity):
    existing = _find_legacy_user_for_sso(identity)
    local_org_id = _resolve_local_org(identity, existing_user=existing)
    now_value = now()
    username = (identity.get("username") or identity.get("email") or identity.get("syntal_user_id") or "syntal-user").strip().lower()[:200]
    set_fields = {
        "syntal_user_id": identity.get("syntal_user_id"),
        "syntal_org_id": identity.get("syntal_org_id"),
        "organization_id": local_org_id,
        "username": username,
        "sso_permissions": identity.get("permissions") or [],
        "auth_provider": "syntal_sso",
        "last_sso_login_at": now_value,
        "updated_at": now_value,
    }
    if identity.get("organization_name"):
        set_fields["organization_name"] = identity.get("organization_name")
    if identity.get("email"):
        set_fields["email"] = identity.get("email")
    if identity.get("name"):
        set_fields["display_name"] = identity.get("name")
    if existing:
        users.update_one({"_id": existing["_id"]}, {"$set": set_fields})
        return users.find_one({"_id": existing["_id"]})
    doc = dict(set_fields)
    doc.update({"credits": 0, "created_at": now_value})
    result = users.insert_one(doc)
    return users.find_one({"_id": result.inserted_id})



def _store_sso_tokens(user_id, token_payload):
    token_payload = token_payload or {}
    access_token = str(token_payload.get("access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip()
    if not access_token:
        return
    existing = users.find_one({"_id": user_id}, {"sso_credentials": 1}) or {}
    existing_credentials = existing.get("sso_credentials") or {}
    update = {
        "sso_credentials.access_token_encrypted": syntal_encrypt_token(access_token),
        "sso_credentials.access_token_expires_at": syntal_access_token_expiry(token_payload),
        "sso_credentials.token_type": str(token_payload.get("token_type") or "Bearer")[:40],
        "sso_credentials.updated_at": now(),
    }
    if refresh_token:
        update["sso_credentials.refresh_token_encrypted"] = syntal_encrypt_token(refresh_token)
    elif existing_credentials.get("refresh_token_encrypted"):
        update["sso_credentials.refresh_token_encrypted"] = existing_credentials.get("refresh_token_encrypted")
    users.update_one({"_id": user_id}, {"$set": update})


def _sso_access_token(user_doc, *, allow_refresh=True):
    credentials = (user_doc or {}).get("sso_credentials") or {}
    access_token = syntal_decrypt_token(credentials.get("access_token_encrypted"))
    try:
        expires_at = int(credentials.get("access_token_expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    current_epoch = int(datetime.now(timezone.utc).timestamp())
    if access_token and (not expires_at or expires_at > current_epoch + 45):
        return access_token
    if not allow_refresh:
        return access_token
    refresh_token = syntal_decrypt_token(credentials.get("refresh_token_encrypted"))
    if not refresh_token:
        return ""
    token_payload = syntal_refresh_tokens(refresh_token)
    _store_sso_tokens(user_doc["_id"], token_payload)
    return str(token_payload.get("access_token") or "").strip()


def _fallback_sso_organizations(user_doc):
    current_id = str((user_doc or {}).get("syntal_org_id") or "").strip()
    current_name = str((user_doc or {}).get("organization_name") or current_id).strip()
    cached = (user_doc or {}).get("sso_organizations_cache") or []
    rows = []
    seen = set()
    for raw in cached:
        if not isinstance(raw, dict):
            continue
        org_id = str(raw.get("id") or raw.get("syntal_org_id") or raw.get("org_id") or "").strip()
        if not org_id or org_id in seen:
            continue
        seen.add(org_id)
        rows.append({
            "id": org_id,
            "name": str(raw.get("name") or raw.get("organization_name") or org_id).strip(),
            "role": str(raw.get("role") or "").strip() or None,
        })
    if current_id and current_id not in seen:
        rows.insert(0, {"id": current_id, "name": current_name or current_id, "role": None})
    rows.sort(key=lambda row: (0 if row["id"] == current_id else 1, row["name"].casefold()))
    return rows


def _sso_organizations_for_user(user_doc, *, force=False):
    if not user_doc or user_doc.get("auth_provider") != "syntal_sso":
        return [], False
    cached = _fallback_sso_organizations(user_doc)
    try:
        cached_at = int(user_doc.get("sso_organizations_cache_epoch") or 0)
    except (TypeError, ValueError):
        cached_at = 0
    age = int(datetime.now(timezone.utc).timestamp()) - cached_at if cached_at else 10**9
    if not force and cached and age < Config.SSO_ORGANIZATIONS_CACHE_SECONDS:
        return cached, True
    try:
        access_token = _sso_access_token(user_doc, allow_refresh=True)
        if not access_token:
            return cached, False
        rows = syntal_available_organizations(
            access_token,
            current_org_id=user_doc.get("syntal_org_id"),
            current_org_name=user_doc.get("organization_name"),
        )
        if rows:
            users.update_one(
                {"_id": user_doc["_id"]},
                {"$set": {
                    "sso_organizations_cache": rows,
                    "sso_organizations_cache_epoch": int(datetime.now(timezone.utc).timestamp()),
                    "sso_organizations_cache_updated_at": now(),
                }},
            )
            return rows, True
    except Exception as exc:
        app.logger.warning("Could not refresh Syntal organization directory for user %s: %s", user_doc.get("_id"), exc)
    return cached, False

def _safe_next_url(value):
    value = str(value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return "/"


def workspace_scope():
    """Return the authoritative data scope for the currently selected workspace."""
    if current_user.is_authenticated and current_user.organization_id:
        return {"organization_id": current_user.organization_id}
    return {"user_id": ObjectId(current_user.id)}


def document_in_workspace(doc):
    if not doc:
        return False
    if current_user.organization_id:
        return doc.get("organization_id") == current_user.organization_id
    return doc.get("user_id") == ObjectId(current_user.id)


def _claim_legacy_workspace_records(user_doc):
    """Attach pre-multi-org user-owned records to the first selected SSO workspace.

    Earlier releases stored a few record types only by user id. Once a local
    organization exists, claim those unscoped records exactly once so the new
    organization boundary remains strict without hiding pre-upgrade data.
    """
    org_id = (user_doc or {}).get("organization_id")
    user_id = (user_doc or {}).get("_id")
    if not org_id or not user_id or (user_doc or {}).get("workspace_scope_migrated_at"):
        return
    for collection in (
        campaigns, products, newsjacking_workers, rss_feeds, hooks, articles, article_change_jobs, article_generation_queue,
        landing_pages, landing_page_change_jobs, domain_mappings, domain_routes,
        social_media_posts, social_generation_jobs, newsletter_schedules, newsletter_editions,
        collections, collection_sources, collection_items, newsjacking_runs, experiments,
    ):
        collection.update_many(
            {"user_id": user_id, "organization_id": {"$exists": False}},
            {"$set": {"organization_id": org_id}},
        )
    users.update_one({"_id": user_id}, {"$set": {"workspace_scope_migrated_at": now()}})


def _newsjacking_workspace_key(org_id=None):
    org_id = org_id or (current_user.organization_id if current_user.is_authenticated else None)
    return str(org_id) if org_id else None


def _newsjacking_settings(user_doc):
    user_doc = user_doc or {}
    key = _newsjacking_workspace_key()
    if key:
        workspaces = user_doc.get("newsjacking_workspaces") or {}
        if isinstance(workspaces.get(key), dict):
            return workspaces.get(key) or {}
    return user_doc.get("newsjacking", {}) or {}


def owned_campaign(campaign_id):
    try:
        oid = ObjectId(campaign_id)
    except Exception:
        return None
    return campaigns.find_one({"_id": oid, **workspace_scope()})


def owned_product(product_id):
    try:
        oid = ObjectId(product_id)
    except Exception:
        return None
    return products.find_one({"_id": oid, **workspace_scope()})


def owned_newsjacking_worker(worker_id):
    try:
        oid = ObjectId(worker_id)
    except Exception:
        return None
    return newsjacking_workers.find_one({"_id": oid, **workspace_scope()})


def _object_ids(values):
    output = []
    for value in values or []:
        try:
            oid = ObjectId(str(value))
        except Exception:
            continue
        if oid not in output:
            output.append(oid)
    return output


def _simple_http_url(value, field_name="URL"):
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a complete http(s) URL.")
    return value


def _collection_picker_cards(limit=500):
    rows = list(collections.find(workspace_scope()).sort("updated_at", -1))
    cards = []
    for collection in rows:
        items = list(collection_items.find({"collection_id": collection["_id"], "active": {"$ne": False}}).sort("created_at", 1).limit(limit))
        cards.append({"collection": collection, "items": items})
    return cards


def author_profile_complete(user_doc):
    profile = (user_doc or {}).get("author_profile") or {}
    return bool(clean_text(profile.get("full_name"), 180) and str(profile.get("image_url") or "").strip())


def current_author_profile():
    if not current_user.is_authenticated:
        return {}
    doc = users.find_one({"_id": ObjectId(current_user.id)}) or {}
    profile = doc.get("author_profile") or {}
    return {
        "full_name": clean_text(profile.get("full_name"), 180),
        "image_url": str(profile.get("image_url") or "").strip(),
        "completed": author_profile_complete(doc),
        "updated_at": profile.get("updated_at"),
    }


def can_sign_article(article, user_doc=None):
    if not article or not current_user.is_authenticated:
        return False
    user_doc = user_doc or users.find_one({"_id": ObjectId(current_user.id)})
    if not author_profile_complete(user_doc):
        return False
    article_org = article.get("organization_id")
    if article_org:
        return bool(current_user.organization_id and current_user.organization_id == article_org)
    return article.get("user_id") == ObjectId(current_user.id)


def article_is_public(article):
    publication = (article or {}).get("publication") or {}
    author = publication.get("author") or {}
    published_revision = int((article or {}).get("published_revision") or 0)
    return bool(
        article
        and article.get("published") is True
        and published_revision > 0
        and publication.get("signed_at")
        and int(publication.get("revision") or 0) == published_revision
        and article.get("published_content")
        and clean_text(author.get("full_name"), 180)
        and author.get("image_url")
    )


def published_article_view(article):
    if not article_is_public(article):
        return None
    view = dict(article)
    view["content"] = article.get("published_content") or article.get("content") or ""
    view["metadata"] = article.get("published_metadata") or article.get("metadata") or {}
    view["quality"] = article.get("published_quality") or article.get("quality") or {}
    view["revision"] = int(article.get("published_revision") or 0)
    view["review_status"] = "signed"
    view["status"] = "published"
    return view


def signed_article_filter():
    return {"published": True, "published_revision": {"$gt": 0}, "publication.signed_at": {"$exists": True}, "published_content": {"$exists": True}}


def article_engagement_enabled(article):
    cfg = (article or {}).get("engagement") or {}
    return bool(cfg.get("email_capture_enabled") or cfg.get("survey_enabled"))


def _capture_token(article):
    ts = int(datetime.now(timezone.utc).timestamp())
    nonce = secrets.token_urlsafe(10)
    revision = int((article or {}).get("published_revision") or 0)
    payload = f"{article['_id']}|{revision}|{ts}|{nonce}"
    sig = hmac.new(Config.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{ts}.{nonce}.{sig}"


def _valid_capture_token(article, token):
    try:
        ts_text, nonce, supplied = str(token or "").split(".", 2)
        ts = int(ts_text)
    except Exception:
        return False
    age = int(datetime.now(timezone.utc).timestamp()) - ts
    if age < 0 or age > Config.AUDIENCE_CAPTURE_TOKEN_MAX_AGE_SECONDS:
        return False
    revision = int((article or {}).get("published_revision") or 0)
    payload = f"{article['_id']}|{revision}|{ts}|{nonce}"
    expected = hmac.new(Config.SECRET_KEY.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied, expected)


def _article_syntal_org_id(article):
    local_org = (article or {}).get("organization_id")
    if not local_org:
        return None
    link = sso_organization_links.find_one({"local_organization_id": local_org}, {"syntal_org_id": 1})
    return (link or {}).get("syntal_org_id")


def _article_submission_stats(article_id):
    base = {"article_id": article_id}
    return {
        "total": article_submissions.count_documents(base),
        "emails": article_submissions.count_documents({**base, "email_normalized": {"$ne": ""}}),
        "surveys": article_submissions.count_documents({**base, "survey.0": {"$exists": True}}),
        "synced": article_submissions.count_documents({**base, "blackbook.status": "synced"}),
        "sync_issues": article_submissions.count_documents({**base, "blackbook.status": {"$in": ["failed", "retrying"]}}),
    }


def owned_article(article_id):
    try:
        oid = ObjectId(article_id)
    except Exception:
        return None
    return articles.find_one({"_id": oid, **workspace_scope()})


def owned_page(page_id):
    try:
        oid = ObjectId(page_id)
    except Exception:
        return None
    return landing_pages.find_one({"_id": oid, **workspace_scope()})


def owned_experiment(experiment_id):
    try:
        oid = ObjectId(experiment_id)
    except Exception:
        return None
    return experiments.find_one({"_id": oid, **workspace_scope()})


def _set_experiment_cookie(response, context):
    if context and context.get("cookie_new") and context.get("cookie_name") and context.get("variant_id"):
        response.set_cookie(
            context["cookie_name"], str(context["variant_id"]), max_age=30 * 86400,
            secure=request.is_secure, httponly=False, samesite="Lax",
        )
    return response


def owned_site(site_id):
    try:
        oid = ObjectId(site_id)
    except Exception:
        return None
    return website_sites.find_one({"_id": oid, **workspace_scope()})


def _site_key(domain=None):
    return f"domain:{domain['_id']}" if domain else "internal:primary"


def ensure_website_site(domain=None, name=None):
    """Return the shared website workspace for a domain (or the primary internal site)."""
    key = _site_key(domain)
    scope = workspace_scope()
    site = website_sites.find_one({"site_key": key, **scope})
    if site:
        if domain and domain.get("site_id") != site.get("_id"):
            domain_mappings.update_one({"_id": domain["_id"]}, {"$set": {"site_id": site["_id"], "updated_at": now()}})
        return site
    now_value = now()
    doc = {
        "user_id": ObjectId(current_user.id),
        "organization_id": current_user.organization_id,
        "site_key": key,
        "domain_id": domain.get("_id") if domain else None,
        "name": clean_text(name, 160) or ((domain or {}).get("domain") if domain else "Primary website"),
        "brand_name": clean_text(name, 160) or ((domain or {}).get("domain") if domain else (current_user.organization_name or "Website")),
        "design_system": {},
        "style_source_page_id": None,
        "homepage_page_id": None,
        "navigation": [],
        "navigation_version": 1,
        "created_at": now_value,
        "updated_at": now_value,
    }
    doc["_id"] = website_sites.insert_one(doc).inserted_id
    if domain:
        domain_mappings.update_one({"_id": domain["_id"]}, {"$set": {"site_id": doc["_id"], "updated_at": now_value}})
    return doc


def _site_page_path(page, domain=None):
    if domain:
        route = domain_routes.find_one({"domain_id": domain["_id"], "page_id": page["_id"]})
        if route:
            return route.get("path") or "/"
    return page.get("site_path") or f"/p/{page.get('public_id')}"


def register_site_page(site, page, path, label=None):
    """Register a page in the site's route manifest. New secondary pages start hidden from nav."""
    site = website_sites.find_one({"_id": site["_id"]}) or site
    nav = list(site.get("navigation") or [])
    page_id = page["_id"]
    normalized_path = path or f"/p/{page.get('public_id')}"
    existing_index = next((i for i, item in enumerate(nav) if item.get("page_id") == page_id), None)
    has_visible = any(bool(item.get("enabled")) for item in nav)
    auto_enable = normalized_path == "/" or not has_visible or bool(page.get("published"))
    item = {
        "page_id": page_id,
        "label": clean_text(label or page.get("title"), 80) or "Page",
        "path": normalized_path,
        "enabled": auto_enable,
        "order": (nav[existing_index].get("order") if existing_index is not None else len(nav) * 10),
        "added_at": (nav[existing_index].get("added_at") if existing_index is not None else now()),
        "updated_at": now(),
    }
    if existing_index is None:
        nav.append(item)
    else:
        nav[existing_index] = {**nav[existing_index], **item}
    update = {"navigation": nav, "updated_at": now()}
    if normalized_path == "/":
        update["homepage_page_id"] = page_id
    if not site.get("style_source_page_id") and page.get("html"):
        update["style_source_page_id"] = page_id
    website_sites.update_one({"_id": site["_id"]}, {"$set": update})
    page_updates = {
        "site_id": site["_id"], "site_path": normalized_path, "navigation_enabled": auto_enable,
        "navigation_pending": not auto_enable, "updated_at": now(),
    }
    if not page.get("page_kind"):
        route_hint = normalized_path.strip("/").lower()
        if normalized_path == "/":
            page_updates["page_kind"] = "home"
        else:
            page_updates["page_kind"] = next((kind for kind in ("about", "services", "contact", "resources", "news") if kind in route_hint), "custom")
    effective_kind = page.get("page_kind") or page_updates.get("page_kind") or "custom"
    if "include_articles" not in page:
        page_updates["include_articles"] = effective_kind in {"home", "resources", "news"}
    landing_pages.update_one({"_id": page_id}, {"$set": page_updates})
    return website_sites.find_one({"_id": site["_id"]})


def site_render_context(page, domain=None, preview=False):
    """Build render-time navigation so every page receives the same working site chrome."""
    site_id = (page or {}).get("site_id")
    if not site_id:
        return None, [], _site_page_path(page or {}, domain=domain)
    site = website_sites.find_one({"_id": site_id})
    if not site:
        return None, [], _site_page_path(page or {}, domain=domain)
    rows = []
    for item in sorted(site.get("navigation") or [], key=lambda x: (int(x.get("order") or 0), str(x.get("label") or ""))):
        if not item.get("enabled"):
            continue
        target = landing_pages.find_one({"_id": item.get("page_id")}) if item.get("page_id") else None
        if not target:
            continue
        if domain:
            path = item.get("path") or _site_page_path(target, domain=domain)
            href = f"https://{domain.get('domain')}{path}" if preview else path
        else:
            path = f"/p/{target.get('public_id')}"
            href = f"{Config.PUBLIC_BASE_URL}{path}" if preview else path
        rows.append({
            "page_id": str(target["_id"]), "label": item.get("label") or target.get("title") or "Page",
            "path": path, "href": href,
        })
    return site, rows, _site_page_path(page, domain=domain)


def ownership_or():
    # Keep the existing call sites concise while enforcing a single workspace
    # boundary. Local fallback users continue to be scoped by user id.
    return [workspace_scope()]




def owned_collection(collection_id):
    try:
        oid = ObjectId(collection_id)
    except Exception:
        return None
    return collections.find_one({"_id": oid, "$or": ownership_or()})


def owned_collection_source(source_id):
    try:
        oid = ObjectId(source_id)
    except Exception:
        return None
    return collection_sources.find_one({"_id": oid, "$or": ownership_or()})


def owned_collection_item_ids(raw_ids):
    ids = []
    for raw in raw_ids or []:
        try:
            ids.append(ObjectId(str(raw)))
        except Exception:
            continue
    if not ids:
        return []
    return [row["_id"] for row in collection_items.find({"_id": {"$in": ids}, "$or": ownership_or()}, {"_id": 1})]

def normalize_domain(value):
    raw = (value or "").strip()
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
        raise ValueError("Use a complete domain such as example.com or news.example.com")
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


def normalize_domain_path(value):
    raw = (value or "/").strip()
    if not raw:
        raw = "/"
    raw = urlsplit(raw).path or "/"
    raw = re.sub(r"/{2,}", "/", raw)
    if not raw.startswith("/"):
        raw = "/" + raw
    if len(raw) > 240 or any(part in {".", ".."} for part in raw.split("/")):
        raise ValueError("The publishing path is invalid")
    if raw != "/":
        raw = raw.rstrip("/")
    if raw.startswith("/.well-known/") or raw.startswith("/static/") or raw.startswith("/api/"):
        raise ValueError("That path is reserved by the publishing service")
    return raw


def resolved_domain_ips(hostname):
    try:
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return sorted({item[4][0] for item in infos if item and item[4]})


def activate_domain_after_dns(domain, resolved=None):
    """Verify DNS and, when enabled, atomically activate Nginx + TLS on the host."""
    resolved = resolved if resolved is not None else resolved_domain_ips(domain["domain"])
    checked_at = now()
    base = {
        "resolved_ips": resolved,
        "last_checked_at": checked_at,
        "updated_at": checked_at,
    }
    if Config.PUBLISHING_IP not in resolved:
        update = {
            **base,
            "status": "pending",
            "tls_active": False,
            "provisioning_error": None,
        }
        domain_mappings.update_one({"_id": domain["_id"]}, {"$set": update})
        domain.update(update)
        found = ", ".join(resolved) if resolved else "no A record yet"
        return False, f"DNS is not pointing to {Config.PUBLISHING_IP} yet. Current result: {found}.", domain

    verified_at = domain.get("verified_at") or checked_at
    if not Config.DOMAIN_AUTO_PROVISION:
        update = {
            **base,
            "status": "verified",
            "verified_at": verified_at,
            "provisioning_error": None,
        }
        domain_mappings.update_one({"_id": domain["_id"]}, {"$set": update})
        domain.update(update)
        return True, f"DNS verified for {domain['domain']}. Automatic activation is disabled.", domain

    provisioning = {
        **base,
        "status": "provisioning",
        "verified_at": verified_at,
        "provision_attempted_at": checked_at,
        "provisioning_error": None,
    }
    domain_mappings.update_one({"_id": domain["_id"]}, {"$set": provisioning})
    domain.update(provisioning)
    try:
        result = provision_domain(domain["domain"])
    except DomainProvisioningError as exc:
        error = str(exc)[:1500]
        failed = {
            "status": "provisioning_failed",
            "tls_active": False,
            "provisioning_error": error,
            "updated_at": now(),
        }
        domain_mappings.update_one({"_id": domain["_id"]}, {"$set": failed})
        domain.update(failed)
        return False, f"DNS verified, but automatic activation failed: {error}", domain

    active_at = now()
    update = {
        "status": "active",
        "tls_active": bool(result.get("tls", True)),
        "provisioned_at": active_at,
        "provisioning_error": None,
        "updated_at": active_at,
    }
    if result.get("resolved_ips"):
        update["resolved_ips"] = result["resolved_ips"]
    domain_mappings.update_one({"_id": domain["_id"]}, {"$set": update})
    domain.update(update)
    return True, f"{domain['domain']} is active with HTTPS. Map a published page to / or another path to serve content.", domain


def owned_domain(domain_id):
    try:
        oid = ObjectId(domain_id)
    except Exception:
        return None
    return domain_mappings.find_one({"_id": oid, "$or": ownership_or()})


def owned_domain_route(route_id):
    try:
        oid = ObjectId(route_id)
    except Exception:
        return None
    return domain_routes.find_one({"_id": oid, "$or": ownership_or()})


def domain_payload(domain):
    if not domain:
        return None
    return {
        "id": str(domain["_id"]),
        "domain": domain.get("domain"),
        "status": domain.get("status", "pending"),
        "expected_ip": domain.get("expected_ip", Config.PUBLISHING_IP),
        "resolved_ips": domain.get("resolved_ips") or [],
        "last_checked_at": domain.get("last_checked_at"),
        "verified_at": domain.get("verified_at"),
        "provisioned_at": domain.get("provisioned_at"),
        "tls_active": bool(domain.get("tls_active")),
        "provisioning_error": domain.get("provisioning_error"),
        "routes_count": domain_routes.count_documents({"domain_id": domain["_id"]}),
    }


def route_payload(route, domain=None, page=None):
    if not route:
        return None
    if domain is None:
        domain = domain_mappings.find_one({"_id": route.get("domain_id")})
    if page is None and route.get("page_id"):
        page = landing_pages.find_one({"_id": route.get("page_id")})
    host = domain.get("domain") if domain else ""
    path = route.get("path") or "/"
    return {
        "id": str(route["_id"]),
        "domain_id": str(route.get("domain_id")) if route.get("domain_id") else None,
        "domain": host,
        "path": path,
        "campaign_id": str(route.get("campaign_id")) if route.get("campaign_id") else None,
        "page_id": str(route.get("page_id")) if route.get("page_id") else None,
        "page_title": (page or {}).get("title"),
        "status": route.get("status", "draft"),
        "domain_status": (domain or {}).get("status", "pending"),
        "domain_live": (domain or {}).get("status") in {"active", "verified"},
        "goal": route.get("goal", ""),
        "public_url": f"https://{host}{path if path != '/' else '/'}" if host else None,
    }


def create_domain_route(domain, page, path, goal=""):
    normalized = normalize_domain_path(path)
    existing = domain_routes.find_one({"domain_id": domain["_id"], "path_key": normalized.casefold()})
    if existing:
        raise ValueError("This domain and path are already mapped to a page")
    now_value = now()
    doc = {
        "user_id": page.get("user_id"),
        "organization_id": page.get("organization_id"),
        "domain_id": domain["_id"],
        "campaign_id": page.get("campaign_id"),
        "page_id": page["_id"],
        "path": normalized,
        "path_key": normalized.casefold(),
        "goal": clean_text(goal or page.get("title"), 500),
        "status": "draft",
        "created_at": now_value,
        "updated_at": now_value,
    }
    doc["_id"] = domain_routes.insert_one(doc).inserted_id
    return doc


def page_campaign_ids(page):
    ids = []
    for raw in page.get("campaign_ids") or ([page.get("campaign_id")] if page.get("campaign_id") else []):
        try:
            oid = raw if isinstance(raw, ObjectId) else ObjectId(str(raw))
        except Exception:
            continue
        if oid not in ids:
            ids.append(oid)
    return ids


def _custom_domain_page_articles(page):
    public_filter = signed_article_filter()
    if page.get("article_mode", "all") == "all":
        campaign_ids = page_campaign_ids(page)
        query = {**public_filter, "campaign_id": {"$in": campaign_ids}}
        return [published_article_view(row) for row in articles.find(query).sort("created_at", -1).limit(Config.LANDING_MAX_ARTICLES) if published_article_view(row)] if campaign_ids else []
    ids = page.get("published_article_ids") or page.get("article_ids") or []
    return [published_article_view(row) for row in articles.find({**public_filter, "_id": {"$in": ids}}).sort("created_at", -1).limit(Config.LANDING_MAX_ARTICLES) if published_article_view(row)]


def _custom_domain_article_allowed(page, article):
    if not article_is_public(article) or article.get("campaign_id") not in page_campaign_ids(page):
        return False
    if page.get("article_mode", "all") == "all":
        return True
    ids = page.get("published_article_ids") or page.get("article_ids") or []
    return article.get("_id") in ids


def page_article_docs(page, public_only=False):
    public_filter = signed_article_filter() if public_only else {}
    if page.get("article_mode", "all") == "all":
        campaign_ids = page_campaign_ids(page)
        query = {**public_filter, "campaign_id": {"$in": campaign_ids}}
        rows = list(articles.find(query).sort("created_at", -1).limit(Config.LANDING_MAX_ARTICLES)) if campaign_ids else []
    else:
        article_ids = page.get("article_ids") or []
        rows = list(articles.find({**public_filter, "_id": {"$in": article_ids}}).sort("created_at", -1).limit(Config.LANDING_MAX_ARTICLES))
    if public_only:
        return [view for view in (published_article_view(row) for row in rows) if view]
    return rows


def option_features(option):
    for key in ("key_points", "benefits", "tactics", "techniques"):
        if option.get(key):
            return option[key]
    return []


@app.template_filter("dt")
def format_dt(value):
    if not value:
        return "—"
    if isinstance(value, str):
        return value
    try:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(value)


@app.template_filter("field")
def format_field(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return value or ""


@app.template_filter("features")
def format_features(value):
    return option_features(value or {})


@app.context_processor
def inject_globals():
    fresh = None
    if current_user.is_authenticated:
        fresh = users.find_one({"_id": ObjectId(current_user.id)})
    website_domains = []
    navigation_campaigns = []
    navigation_products = []
    navigation_workers = []
    analytics_nav_pages = []
    analytics_nav_articles = []
    article_review_count = 0
    if current_user.is_authenticated:
        try:
            website_domains = list(domain_mappings.find({"$or": ownership_or()}).sort("domain", 1))
        except Exception:
            website_domains = []
        try:
            navigation_campaigns = list(campaigns.find({"$or": ownership_or()}, {"title": 1, "status": 1, "setup_status": 1, "updated_at": 1}).sort("updated_at", -1).limit(30))
        except Exception:
            navigation_campaigns = []
        try:
            navigation_products = list(products.find({"$or": ownership_or()}, {"name": 1, "status": 1, "updated_at": 1}).sort("updated_at", -1).limit(30))
        except Exception:
            navigation_products = []
        try:
            navigation_workers = list(newsjacking_workers.find({"$or": ownership_or()}, {"name": 1, "enabled": 1, "last_run_status": 1, "learning_mode": 1, "learning_state.ready": 1, "updated_at": 1}).sort("updated_at", -1).limit(30))
        except Exception:
            navigation_workers = []
        try:
            analytics_nav_pages = list(landing_pages.find({"$or": ownership_or()}, {"title": 1, "campaign_id": 1, "published": 1, "updated_at": 1}).sort("updated_at", -1).limit(30))
        except Exception:
            analytics_nav_pages = []
        try:
            analytics_nav_articles = list(articles.find({"$or": ownership_or()}, {"metadata.title": 1, "published_metadata.title": 1, "title": 1, "campaign_id": 1, "created_at": 1}).sort("created_at", -1).limit(30))
        except Exception:
            analytics_nav_articles = []
        try:
            article_review_count = articles.count_documents({"$and": [{"$or": ownership_or()}, {"review_status": {"$ne": "signed"}}]})
        except Exception:
            article_review_count = 0
    return {
        "app_name": "Newsjacking",
        "app_version": Config.APP_VERSION,
        "current_credits": (fresh or {}).get("credits", 0) if fresh else 0,
        "credits_enabled": Config.ENABLE_CREDITS,
        "website_domains": website_domains,
        "navigation_campaigns": navigation_campaigns,
        "navigation_products": navigation_products,
        "navigation_workers": navigation_workers,
        "analytics_nav_pages": analytics_nav_pages,
        "analytics_nav_articles": analytics_nav_articles,
        "internal_website_host": urlsplit(Config.PUBLIC_BASE_URL).hostname or Config.PUBLISHING_PRIMARY_HOST,
        "analytics_tracking_script": tracking_script,
        "sso_enabled": Config.SSO_ENABLED,
        "sso_account_url": Config.SSO_ACCOUNT_URL,
        "sso_organizations": _fallback_sso_organizations(fresh) if fresh else [],
        "sso_organizations_connected": bool(((fresh or {}).get("sso_credentials") or {}).get("access_token_encrypted")),
        "author_profile": current_author_profile() if current_user.is_authenticated else {},
        "article_review_count": article_review_count,
    }


@app.before_request
def dispatch_verified_custom_domain():
    """Serve verified domain routes before the NJS administrative routes win."""
    if not Config.CUSTOM_DOMAIN_ROUTING:
        return None
    if request.path.startswith("/.well-known/acme-challenge/") or request.path == "/api/analytics/client" or (request.path.startswith("/api/public/articles/") and request.path.endswith("/respond")):
        return None
    host = (request.host.split(":", 1)[0] or "").strip().lower().rstrip(".")
    public_host = (urlsplit(Config.PUBLIC_BASE_URL).hostname or "").lower().rstrip(".")
    admin_hosts = {
        Config.PUBLISHING_PRIMARY_HOST,
        public_host,
        Config.PUBLISHING_IP,
        "127.0.0.1",
        "localhost",
        "mongo",
        "redis",
        "web",
    }
    if not host or host in admin_hosts:
        return None
    mapping = domain_mappings.find_one({"domain": host})
    if not mapping:
        abort(404)
    if mapping.get("status") not in {"active", "verified"}:
        abort(404)
    if request.method not in {"GET", "HEAD"}:
        abort(405)

    if request.path == "/robots.txt":
        body = "User-agent: *\nAllow: /\nSitemap: https://%s/pages.xml\n" % host
        return Response(body, mimetype="text/plain")

    routes = list(domain_routes.find({"domain_id": mapping["_id"]}).sort("path_key", 1))
    if request.path == "/pages.xml":
        urls = []
        for route in routes:
            page = landing_pages.find_one({"_id": route.get("page_id"), "published": True})
            if page:
                path = route.get("path") or "/"
                urls.append(f"https://{host}{path if path != '/' else '/'}")
        rows = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + rows + '</urlset>'
        return Response(xml, mimetype="application/xml")

    try:
        normalized = normalize_domain_path(request.path)
    except ValueError:
        abort(404)

    route = domain_routes.find_one({"domain_id": mapping["_id"], "path_key": normalized.casefold()})
    if route:
        page = landing_pages.find_one({"_id": route.get("page_id"), "published": True})
        if not page:
            abort(404)
        published_page = dict(page)
        if page.get("published_html"):
            published_page["html"] = page.get("published_html")
            published_page["metadata"] = page.get("published_metadata") or page.get("metadata") or {}
        experiment_ctx = experiment_context_for_public("page", page["_id"], page, request)
        published_page = _experiment_snapshot_page(published_page, experiment_ctx)
        article_docs = _custom_domain_page_articles(published_page)
        route_path = route.get("path") or "/"
        page_url = f"https://{host}{route_path if route_path != '/' else '/'}"
        article_base = f"https://{host}{'' if route_path == '/' else route_path}"
        event_id, visitor_token, is_new_visitor = record_view(
            request, owner_doc=page, content_type="page", content_id=page["_id"],
            campaign_id=page.get("campaign_id"), campaign_ids=page_campaign_ids(page), domain=host, path=route_path,
            experiment_id=(experiment_ctx or {}).get("experiment_id"), variant_id=(experiment_ctx or {}).get("variant_id"),
        )
        site, site_navigation, current_site_path = site_render_context(published_page, domain=mapping, preview=False)
        rendered = compile_landing_html(
            published_page, article_docs, public=True, public_url=page_url, article_base_url=article_base,
            site=site, navigation=site_navigation, current_path=current_site_path,
        )
        if experiment_ctx:
            rendered = apply_page_variant(rendered, experiment_ctx.get("variant"))
        response = Response(inject_tracking(rendered, event_id), mimetype="text/html")
        if visitor_token:
            response.set_cookie("njs_vid", visitor_token, max_age=31536000, secure=request.is_secure, httponly=False, samesite="Lax")
        _set_experiment_cookie(response, experiment_ctx)
        return response

    # Article paths are derived from a mapped page path:
    # /articles/<id> for /, or /research/articles/<id> for /research.
    match = re.fullmatch(r"(.*/)?articles/([0-9a-fA-F]{24})/?", normalized.lstrip("/"))
    if match:
        prefix = (match.group(1) or "").strip("/")
        page_path = "/" + prefix if prefix else "/"
        route = domain_routes.find_one({"domain_id": mapping["_id"], "path_key": page_path.casefold()})
        page = landing_pages.find_one({"_id": route.get("page_id"), "published": True}) if route else None
        if not page:
            abort(404)
        try:
            article = articles.find_one({"_id": ObjectId(match.group(2))})
        except Exception:
            article = None
        if not _custom_domain_article_allowed(page, article):
            abort(404)
        public_article_doc = published_article_view(article)
        if not public_article_doc:
            abort(404)
        campaign = campaigns.find_one({"_id": article.get("campaign_id")})
        hook = hooks.find_one({"_id": article.get("hook_id")}) if article.get("hook_id") else None
        experiment_ctx = experiment_context_for_public("article", article["_id"], article, request)
        if experiment_ctx:
            public_article_doc = _experiment_snapshot_article(public_article_doc, experiment_ctx)
            public_article_doc = article_variant_view(public_article_doc, experiment_ctx.get("variant"))
        event_id, visitor_token, _ = record_view(
            request, owner_doc=article, content_type="article", content_id=article["_id"],
            campaign_id=article.get("campaign_id"), domain=host, path=normalized,
            experiment_id=(experiment_ctx or {}).get("experiment_id"), variant_id=(experiment_ctx or {}).get("variant_id"),
        )
        response = make_response(render_template(
            "public_article.html", article=public_article_doc, campaign=campaign, hook=hook, back_url=page_path, custom_domain=True, analytics_event_id=event_id,
            experiment_variant=(experiment_ctx or {}).get("variant"),
            audience_capture_token=_capture_token(article) if article_engagement_enabled(article) else None,
        ))
        if visitor_token:
            response.set_cookie("njs_vid", visitor_token, max_age=31536000, secure=request.is_secure, httponly=False, samesite="Lax")
        _set_experiment_cookie(response, experiment_ctx)
        return response

    abort(404)


@app.route("/health")
def health():
    return {"status": "ok", "version": Config.APP_VERSION}


@app.get("/api/auth/me")
@login_required
def auth_me():
    return jsonify({
        "authenticated": True,
        "provider": current_user.doc.get("auth_provider") or session.get("auth_provider") or "local",
        "syntal_user_id": current_user.syntal_user_id,
        "syntal_org_id": current_user.syntal_org_id,
        "organization_name": current_user.organization_name,
        "username": current_user.username,
        "email": current_user.email,
        "permissions": current_user.sso_permissions,
        "is_njs_admin": current_user.is_syntal_admin,
    })


@app.route("/api/live-status")
@login_required
def live_status():
    """Small polling surface used by v2.0 to keep async work visibly current.

    The endpoint intentionally returns only status metadata and a compact state token.
    The browser polls at a cadence chosen for the workload: extraction/scraping 2s,
    normal AI generation 4s, and large page/newsletter generation 8s.
    """
    scope = clean_text(request.args.get("scope"), 40).lower()
    object_id = clean_text(request.args.get("id"), 80)
    payload = {"scope": scope, "busy": False, "stage": "Ready", "next_poll_ms": 8000, "items": []}

    def token(value):
        encoded = json.dumps(value, default=str, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha1(encoded).hexdigest()[:16]

    if scope == "campaigns":
        rows = list(campaigns.find({"$or": ownership_or()}, {"setup_status": 1, "status": 1, "updated_at": 1}).sort("updated_at", -1).limit(100))
        active = [r for r in rows if r.get("setup_status") in {"queued", "researching"}]
        payload.update({
            "busy": bool(active),
            "stage": ("Building %d campaign strateg%s" % (len(active), "ies" if len(active) != 1 else "y")) if active else "Campaigns up to date",
            "next_poll_ms": 4000,
        })
        state = [(str(r["_id"]), r.get("setup_status"), r.get("status")) for r in rows]
    elif scope == "collection":
        collection = owned_collection(object_id)
        if not collection:
            abort(404)
        rows = list(collection_sources.find(
            {"collection_id": collection["_id"]},
            {"status": 1, "source_type": 1, "title": 1, "item_count": 1, "error": 1}
        ).sort("created_at", -1).limit(100))
        active = [r for r in rows if r.get("status") in {"queued", "processing"}]
        payload.update({
            "busy": bool(active),
            "stage": ("Scraping / extracting %d source%s" % (len(active), "s" if len(active) != 1 else "")) if active else "Collection up to date",
            "next_poll_ms": 2000,
            "items": [{"id": str(r["_id"]), "status": r.get("status"), "type": r.get("source_type"), "count": r.get("item_count", 0)} for r in rows],
        })
        state = [(str(r["_id"]), r.get("status"), int(r.get("item_count", 0) or 0), bool(r.get("error"))) for r in rows]
    elif scope == "campaign":
        campaign = owned_campaign(object_id)
        if not campaign:
            abort(404)
        status = campaign.get("setup_status", "complete")
        payload.update({
            "busy": status in {"queued", "researching"},
            "stage": {"queued": "Campaign strategy queued", "researching": "Building campaign strategy", "suggestions_ready": "Strategy ready for review", "review": "Awaiting approval", "complete": "Campaign ready", "failed": "Campaign generation failed"}.get(status, status.replace("_", " ").title()),
            "next_poll_ms": 4000,
            "status": status,
        })
        state = [status, bool(campaign.get("strategy_suggestions")), bool(campaign.get("setup_error"))]
    elif scope == "page":
        page = owned_page(object_id)
        if not page:
            abort(404)
        jobs = list(landing_page_change_jobs.find(
            {"landing_page_id": page["_id"]}, {"status": 1, "result_revision": 1, "error": 1}
        ).sort("created_at", -1).limit(30))
        page_status = page.get("generation_status", "completed")
        active_jobs = [j for j in jobs if j.get("status") in {"queued", "processing", "retrying"}]
        busy = page_status in {"queued", "processing"} or bool(active_jobs)
        stage = "Generating website page" if page_status in {"queued", "processing"} else ("Applying review notes" if active_jobs else "Website page ready")
        payload.update({"busy": busy, "stage": stage, "next_poll_ms": 8000, "status": page_status, "revision": page.get("revision", 0)})
        state = [page_status, int(page.get("revision", 0) or 0), [(str(j["_id"]), j.get("status"), j.get("result_revision"), bool(j.get("error"))) for j in jobs]]
    elif scope == "article":
        article = owned_article(object_id)
        if not article:
            abort(404)
        jobs = list(article_change_jobs.find(
            {"article_id": article["_id"]}, {"status": 1, "result_revision": 1, "error": 1}
        ).sort("created_at", -1).limit(30))
        active = [j for j in jobs if j.get("status") in {"queued", "processing", "retrying"}]
        busy = bool(active)
        stage = "Applying article review notes" if active else ("Awaiting signature" if article.get("review_status") != "signed" else "Article signed")
        payload.update({"busy": busy, "stage": stage, "next_poll_ms": 4000, "status": article.get("review_status"), "revision": int(article.get("revision") or 1)})
        state = [article.get("review_status"), int(article.get("revision") or 1), int(article.get("published_revision") or 0), [(str(j["_id"]), j.get("status"), j.get("result_revision"), bool(j.get("error"))) for j in jobs]]
    elif scope == "social":
        jobs = list(social_generation_jobs.find({"$or": ownership_or()}, {"status": 1, "result": 1, "error": 1}).sort("created_at", -1).limit(30))
        active = [j for j in jobs if j.get("status") in {"queued", "generating", "retrying"}]
        payload.update({"busy": bool(active), "stage": ("Generating social bundle%s" % ("s" if len(active) != 1 else "")) if active else "Social output up to date", "next_poll_ms": 4000})
        state = [(str(j["_id"]), j.get("status"), bool(j.get("error"))) for j in jobs]
    elif scope == "newsletter":
        rows = list(newsletter_editions.find({"$or": ownership_or()}, {"generation_status": 1, "status": 1, "story_count": 1, "generation_error": 1}).sort("updated_at", -1).limit(30))
        active = [r for r in rows if r.get("generation_status") in {"queued", "generating", "processing"} or r.get("status") == "generating"]
        payload.update({"busy": bool(active), "stage": ("Building newsletter edition%s" % ("s" if len(active) != 1 else "")) if active else "Newsletter drafts up to date", "next_poll_ms": 8000})
        state = [(str(r["_id"]), r.get("generation_status"), r.get("status"), int(r.get("story_count", 0) or 0), bool(r.get("generation_error"))) for r in rows]
    elif scope == "newsjacking":
        run = newsjacking_runs.find_one(workspace_scope(), sort=[("started_at", -1)])
        status = (run or {}).get("status", "idle")
        payload.update({"busy": status in {"queued", "running", "processing"}, "stage": "Scanning monitored sources" if status in {"queued", "running", "processing"} else "Newsjacking monitor ready", "next_poll_ms": 4000, "status": status})
        state = [str((run or {}).get("_id", "")), status, (run or {}).get("completed_at"), (run or {}).get("stats")]
    else:
        return jsonify({"error": "Unsupported live-status scope"}), 400

    payload["state_token"] = token(state)
    return jsonify(payload)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if Config.SSO_ENABLED:
        return render_template("login.html", sso_enabled=True, local_fallback=Config.SSO_LOCAL_FALLBACK)
    return redirect(url_for("local_login"))


@app.get("/auth/login")
def auth_login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if not Config.SSO_ENABLED:
        return redirect(url_for("local_login"))
    try:
        return redirect(syntal_begin_login(_safe_next_url(request.args.get("next"))))
    except Exception as exc:
        app.logger.error("Could not begin Syntal SSO login: %s", exc, exc_info=True)
        flash("Syntal SSO is temporarily unavailable.", "danger")
        return redirect(url_for("login"))


@app.get("/auth/switch-organization")
@login_required
def auth_switch_organization():
    """Compatibility route: refresh SSO context and ask Syntal to choose an org."""
    if not Config.SSO_ENABLED or session.get("auth_provider") != "syntal_sso":
        flash("Organization switching is available through Syntal SSO.", "warning")
        return redirect(_safe_next_url(request.args.get("next")))
    session["switch_from_org"] = current_user.syntal_org_id or ""
    try:
        return redirect(syntal_begin_login(_safe_next_url(request.args.get("next")), prompt="select_account"))
    except Exception as exc:
        session.pop("switch_from_org", None)
        app.logger.error("Could not begin Syntal organization selection: %s", exc, exc_info=True)
        flash("The organization selector is temporarily unavailable.", "danger")
        return redirect(_safe_next_url(request.args.get("next")))


@app.post("/auth/switch-organization/<organization_id>")
@login_required
def auth_switch_organization_to(organization_id):
    if not Config.SSO_ENABLED or session.get("auth_provider") != "syntal_sso":
        abort(403)
    organization_id = clean_text(organization_id, 180)
    if not organization_id:
        abort(400)
    return_to = _safe_next_url(request.form.get("next") or request.args.get("next"))
    user_doc = users.find_one({"_id": ObjectId(current_user.id)}) or {}
    organizations, directory_ok = _sso_organizations_for_user(user_doc, force=True)
    allowed = {row.get("id") for row in organizations}
    if organization_id not in allowed:
        if not directory_ok:
            flash("NJS could not refresh your organization list from Syntal. Reconnect the selector and try again.", "warning")
        else:
            flash("That organization is not available to your Syntal account.", "danger")
        return redirect(return_to)
    if organization_id == str(current_user.syntal_org_id or ""):
        return redirect(return_to)
    session["switch_from_org"] = current_user.syntal_org_id or ""
    try:
        # SSO remains authoritative: selecting a row starts a new authorization
        # transaction pinned to that organization, so membership, NJS access and
        # current permissions are checked again before the local workspace changes.
        return redirect(syntal_begin_login(return_to, organization_id=organization_id))
    except Exception as exc:
        session.pop("switch_from_org", None)
        app.logger.error("Could not switch Syntal organization: %s", exc, exc_info=True)
        flash("Syntal could not start the organization switch.", "danger")
        return redirect(return_to)


@app.get("/api/auth/organizations")
@login_required
def auth_organizations():
    if not Config.SSO_ENABLED or session.get("auth_provider") != "syntal_sso":
        return jsonify({"organizations": [], "connected": False, "reauth_required": False}), 200
    user_doc = users.find_one({"_id": ObjectId(current_user.id)}) or {}
    organizations, connected = _sso_organizations_for_user(user_doc, force=True)
    return jsonify({
        "organizations": organizations,
        "current_org_id": current_user.syntal_org_id,
        "connected": connected,
        "reauth_required": not bool(((user_doc.get("sso_credentials") or {}).get("access_token_encrypted"))),
    })


@app.get("/auth/callback")
def auth_callback():
    if request.args.get("error"):
        session.pop("switch_from_org", None)
        detail = request.args.get("error_description") or request.args.get("error")
        flash("Syntal SSO rejected the sign-in: " + clean_text(detail, 300), "danger")
        return redirect(url_for("login"))
    code = request.args.get("code")
    state = request.args.get("state")
    if not code:
        session.pop("switch_from_org", None)
        flash("Syntal SSO returned no authorization code.", "danger")
        return redirect(url_for("login"))
    try:
        claims, next_url, token_payload = syntal_complete_login(code, state)
        if not syntal_has_app_access(claims):
            app.logger.warning("Syntal user %s lacks %s", claims.get("sub"), Config.SSO_REQUIRED_PERMISSION)
            flash("Your Syntal account does not have access to Newsjacking.", "danger")
            return redirect(url_for("login"))
        identity = syntal_extract_identity(claims)
        if not identity.get("syntal_user_id"):
            raise RuntimeError("Syntal identity is missing a user id")
        doc = _provision_syntal_user(identity)
        _store_sso_tokens(doc["_id"], token_payload)
        doc = users.find_one({"_id": doc["_id"]}) or doc
        _sso_organizations_for_user(doc, force=True)
        doc = users.find_one({"_id": doc["_id"]}) or doc
        _claim_legacy_workspace_records(doc)
        previous_org = session.pop("switch_from_org", None)
        login_user(User(doc), remember=False, fresh=True)
        session["auth_provider"] = "syntal_sso"
        session.permanent = True
        if previous_org is not None:
            if previous_org and previous_org != identity.get("syntal_org_id"):
                flash(f"Switched to {identity.get('organization_name') or identity.get('syntal_org_id') or 'the selected organization'}.", "success")
            else:
                flash(f"Using {identity.get('organization_name') or identity.get('syntal_org_id') or 'the selected organization'}.", "success")
        return redirect(_safe_next_url(next_url))
    except Exception as exc:
        session.pop("switch_from_org", None)
        app.logger.error("Syntal SSO callback failed: %s", exc, exc_info=True)
        flash("Sign-in could not be completed. Please try again.", "danger")
        return redirect(url_for("login"))


@app.route("/auth/local", methods=["GET", "POST"])
def local_login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if Config.SSO_ENABLED and not Config.SSO_LOCAL_FALLBACK:
        abort(404)
    if request.method == "POST":
        username = clean_text(request.form.get("username"), 200).lower()
        password = request.form.get("password") or ""
        doc = users.find_one({"username": username})
        if not doc or not doc.get("password_hash") or not check_password_hash(doc["password_hash"], password):
            flash("Invalid username or password.", "danger")
        else:
            login_user(User(doc), remember=False)
            session["auth_provider"] = "local"
            return redirect(url_for("dashboard"))
    return render_template("local_login.html")


@app.post("/logout")
@login_required
def logout():
    provider = session.get("auth_provider")
    logout_user()
    session.clear()
    if Config.SSO_ENABLED and provider == "syntal_sso":
        try:
            return redirect(syntal_logout_url(url_for("login", _external=True, _scheme="https")))
        except Exception:
            return redirect(Config.SSO_ISSUER.rstrip("/") + "/")
    return redirect(url_for("login"))


@app.route("/author-profile", methods=["GET", "POST"])
@login_required
def author_profile_settings():
    user_doc = users.find_one({"_id": ObjectId(current_user.id)}) or {}
    existing = user_doc.get("author_profile") or {}
    if request.method == "POST":
        full_name = clean_text(request.form.get("full_name"), 180)
        if len(full_name) < 2:
            flash("Enter the full name that should appear on signed articles.", "danger")
            return redirect(url_for("author_profile_settings", next=(_safe_next_url(request.form.get("next")) if request.form.get("next") else "")))
        image_url = str(existing.get("image_url") or "").strip()
        upload = request.files.get("picture")
        if upload and upload.filename:
            raw = upload.read(Config.AUTHOR_IMAGE_MAX_BYTES + 1)
            if len(raw) > Config.AUTHOR_IMAGE_MAX_BYTES:
                flash("Author picture is too large.", "danger")
                return redirect(url_for("author_profile_settings", next=(_safe_next_url(request.form.get("next")) if request.form.get("next") else "")))
            try:
                image = Image.open(BytesIO(raw))
                if image.width * image.height > 40_000_000:
                    raise ValueError("Image dimensions are too large")
                image = ImageOps.exif_transpose(image).convert("RGB")
                image = ImageOps.fit(image, (640, 640), method=Image.Resampling.LANCZOS)
            except (UnidentifiedImageError, OSError, ValueError):
                flash("Upload a valid JPG, PNG or WebP image.", "danger")
                return redirect(url_for("author_profile_settings", next=(_safe_next_url(request.form.get("next")) if request.form.get("next") else "")))
            output_dir = Path(Config.AUTHOR_IMAGE_OUTPUT_DIR)
            output_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(raw).hexdigest()[:14]
            filename = f"author-{current_user.id}-{digest}.jpg"
            output_path = output_dir / filename
            image.save(output_path, format="JPEG", quality=90, optimize=True)
            image_url = f"{Config.AUTHOR_IMAGE_URL_PREFIX}/{filename}"
        if not image_url:
            flash("An author picture is required before the profile can be completed.", "danger")
            return redirect(url_for("author_profile_settings", next=(_safe_next_url(request.form.get("next")) if request.form.get("next") else "")))
        profile = {
            "full_name": full_name,
            "image_url": image_url,
            "completed": True,
            "updated_at": now(),
        }
        users.update_one({"_id": ObjectId(current_user.id)}, {"$set": {"author_profile": profile, "updated_at": now()}})
        flash("Author profile saved. You can now sign articles for your organization.", "success")
        return redirect(_safe_next_url(request.form.get("next")) if request.form.get("next") else url_for("article_review_queue"))
    return render_template(
        "author_profile.html",
        profile=existing,
        suggested_name=existing.get("full_name") or user_doc.get("display_name") or current_user.username,
        next_url=_safe_next_url(request.args.get("next")) if request.args.get("next") else "",
    )


@app.route("/integrations/blackbook", methods=["GET", "POST"])
@login_required
def blackbook_integration():
    if not current_user.organization_id:
        abort(400, description="An organization is required to configure BlackBook.")
    link = sso_organization_links.find_one({"local_organization_id": current_user.organization_id}) or {}
    if request.method == "POST":
        if Config.SSO_ENABLED and not current_user.is_syntal_admin:
            abort(403, description="njs.admin is required to configure organization integrations.")
        enabled = request.form.get("enabled") == "on"
        auto_mailchimp = request.form.get("auto_mailchimp") == "on"
        base_url = str(request.form.get("base_url") or Config.BLACKBOOK_BASE_URL).strip().rstrip("/")
        org_identifier = clean_text(request.form.get("organization_identifier"), 200) or current_user.syntal_org_id or link.get("syntal_org_id") or ""
        if enabled and not re.match(r"^https?://", base_url, re.I):
            flash("BlackBook URL must start with http:// or https://.", "danger")
            return redirect(url_for("blackbook_integration"))
        existing = (link.get("blackbook") or {})
        raw_key = str(request.form.get("api_key") or "").strip()
        encrypted = encrypt_blackbook_secret(raw_key) if raw_key else existing.get("api_key_encrypted", "")
        if enabled and not encrypted:
            flash("Enter the BlackBook prospect intake API key before enabling the connector.", "danger")
            return redirect(url_for("blackbook_integration"))
        if enabled and not org_identifier:
            flash("BlackBook organization identifier is required.", "danger")
            return redirect(url_for("blackbook_integration"))
        now_value = now()
        update = {
            "blackbook.enabled": enabled,
            "blackbook.auto_mailchimp": auto_mailchimp,
            "blackbook.base_url": base_url,
            "blackbook.organization_identifier": org_identifier,
            "blackbook.api_key_encrypted": encrypted,
            "blackbook.updated_at": now_value,
            "blackbook.updated_by": ObjectId(current_user.id),
            "updated_at": now_value,
        }
        query = {"local_organization_id": current_user.organization_id}
        set_on_insert = {"local_organization_id": current_user.organization_id, "created_at": now_value}
        if current_user.syntal_org_id:
            set_on_insert["syntal_org_id"] = current_user.syntal_org_id
        sso_organization_links.update_one(query, {"$set": update, "$setOnInsert": set_on_insert}, upsert=True)
        flash("BlackBook integration settings saved.", "success")
        return redirect(url_for("blackbook_integration"))
    status = blackbook_connector_status(current_user.organization_id)
    recent = list(article_submissions.find({"organization_id": current_user.organization_id, "blackbook.status": {"$exists": True}}).sort("created_at", -1).limit(12))
    return render_template("integration_blackbook.html", connector=status, recent_sync=recent, can_manage=(current_user.is_syntal_admin or not Config.SSO_ENABLED))


@app.route("/")
@login_required
def dashboard():
    scope = workspace_scope()
    active_campaigns = campaigns.count_documents({**scope, "status": "active"})
    active_feeds = rss_feeds.count_documents({**scope, "is_active": True})
    generated_articles = articles.count_documents(scope)
    last_run = newsjacking_runs.find_one(scope, sort=[("started_at", -1)])
    recent_articles = list(articles.find(scope).sort("created_at", -1).limit(6))
    setup_campaigns = list(campaigns.find({
        **scope,
        "setup_status": {"$in": ["queued", "researching", "suggestions_ready", "review", "failed"]},
    }).sort("updated_at", -1).limit(4))
    return render_template(
        "dashboard.html",
        active_campaigns=active_campaigns,
        active_feeds=active_feeds,
        generated_articles=generated_articles,
        last_run=last_run,
        recent_articles=recent_articles,
        setup_campaigns=setup_campaigns,
    )



@app.route("/collections", methods=["GET", "POST"])
@login_required
def collection_list():
    if request.method == "POST":
        name = clean_text(request.form.get("name"), 180)
        if len(name) < 2:
            flash("Give the collection a name.", "danger")
            return redirect(url_for("collection_list"))
        doc = {
            "user_id": ObjectId(current_user.id),
            "organization_id": current_user.organization_id,
            "name": name,
            "description": clean_text(request.form.get("description"), 1500),
            "created_at": now(), "updated_at": now(),
        }
        cid = collections.insert_one(doc).inserted_id
        flash("Collection created. Add URLs, files, videos or notes.", "success")
        return redirect(url_for("collection_detail", collection_id=str(cid)))
    rows = list(collections.find({"$or": ownership_or()}).sort("updated_at", -1))
    for row in rows:
        row["source_count"] = collection_sources.count_documents({"collection_id": row["_id"]})
        row["item_count"] = collection_items.count_documents({"collection_id": row["_id"], "active": {"$ne": False}})
    return render_template("collections.html", collections=rows)


@app.route("/collections/<collection_id>")
@login_required
def collection_detail(collection_id):
    collection = owned_collection(collection_id)
    if not collection:
        abort(404)
    sources = list(collection_sources.find({"collection_id": collection["_id"]}).sort("created_at", -1))
    items = list(collection_items.find({"collection_id": collection["_id"], "active": {"$ne": False}}).sort([("source_id", 1), ("created_at", 1)]).limit(1200))
    by_source = {}
    for item in items:
        by_source.setdefault(str(item.get("source_id")), []).append(item)
    collected_urls = {str(source.get("url")) for source in sources if source.get("source_type") == "url" and source.get("url")}
    return render_template("collection_detail.html", collection=collection, sources=sources, items=items, items_by_source=by_source, collected_urls=collected_urls, allowed_extensions=sorted(ALLOWED_DOCUMENT_EXTENSIONS))


def _queue_collection_source(collection, source_type, **fields):
    doc = {
        "collection_id": collection["_id"],
        "user_id": ObjectId(current_user.id),
        "organization_id": current_user.organization_id,
        "source_type": source_type,
        "status": "queued",
        "created_at": now(), "updated_at": now(),
        **fields,
    }
    sid = collection_sources.insert_one(doc).inserted_id
    collections.update_one({"_id": collection["_id"]}, {"$set": {"updated_at": now()}})
    try:
        process_collection_source_task.delay(str(sid))
    except Exception as exc:
        collection_sources.update_one({"_id": sid}, {"$set": {"status": "failed", "error": str(exc)[:1200], "updated_at": now()}})
    return sid


@app.post("/collections/<collection_id>/urls")
@login_required
def collection_add_url(collection_id):
    collection = owned_collection(collection_id)
    if not collection:
        abort(404)
    raw = request.form.get("urls") or request.form.get("url") or ""
    urls = [clean_text(line, 2000) for line in re.split(r"[\r\n]+", raw) if clean_text(line, 2000)]
    if not urls:
        flash("Enter at least one URL.", "danger")
        return redirect(url_for("collection_detail", collection_id=collection_id))
    queued = skipped = 0
    for url in urls[:25]:
        existing = collection_sources.find_one({"collection_id": collection["_id"], "source_type": "url", "url": url})
        if existing:
            skipped += 1; continue
        _queue_collection_source(collection, "url", url=url, title=url); queued += 1
    if queued:
        flash(f"Queued {queued} web page{'s' if queued != 1 else ''} for scraping and link discovery.", "success")
    if skipped:
        flash(f"Skipped {skipped} URL{'s' if skipped != 1 else ''} already in the collection.", "warning")
    return redirect(url_for("collection_detail", collection_id=collection_id))


@app.post("/collections/<collection_id>/youtube")
@login_required
def collection_add_youtube(collection_id):
    collection = owned_collection(collection_id)
    if not collection:
        abort(404)
    raw = request.form.get("urls") or request.form.get("url") or ""
    urls = [clean_text(line, 2000) for line in re.split(r"[\r\n]+", raw) if clean_text(line, 2000)]
    if not urls:
        flash("Enter at least one YouTube URL.", "danger")
        return redirect(url_for("collection_detail", collection_id=collection_id))
    queued = 0
    for index, url in enumerate(urls[:20]):
        existing = collection_sources.find_one({"collection_id": collection["_id"], "source_type": "youtube", "url": url})
        if existing:
            continue
        label = clean_text(request.form.get("title"), 300)
        _queue_collection_source(collection, "youtube", url=url, title=(label if len(urls) == 1 and label else f"YouTube video {index + 1}")); queued += 1
    flash(f"Queued {queued} video{'s' if queued != 1 else ''} for Supadata transcript retrieval." if queued else "Those videos are already in this collection.", "success" if queued else "warning")
    return redirect(url_for("collection_detail", collection_id=collection_id))


@app.post("/collections/<collection_id>/notes")
@login_required
def collection_add_note(collection_id):
    collection = owned_collection(collection_id)
    if not collection:
        abort(404)
    text = clean_text(request.form.get("text"), 50000)
    if not text:
        flash("Enter some information for the note.", "danger")
        return redirect(url_for("collection_detail", collection_id=collection_id))
    _queue_collection_source(collection, "note", title=clean_text(request.form.get("title"), 300) or "Manual note", text=text)
    flash("Note added to the collection.", "success")
    return redirect(url_for("collection_detail", collection_id=collection_id))


@app.post("/collections/<collection_id>/upload")
@login_required
def collection_upload(collection_id):
    collection = owned_collection(collection_id)
    if not collection:
        abort(404)
    files = request.files.getlist("files")
    if not files:
        flash("Choose at least one document.", "danger")
        return redirect(url_for("collection_detail", collection_id=collection_id))
    base_dir = Path(Config.COLLECTION_FILE_DIR) / str(current_user.id) / str(collection["_id"])
    base_dir.mkdir(parents=True, exist_ok=True)
    queued = 0
    for uploaded in files[:20]:
        original = uploaded.filename or "document"
        ext = Path(original).suffix.lower()
        if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
            flash(f"Skipped {original}: unsupported file type.", "warning")
            continue
        safe = secure_filename(Path(original).name) or f"document{ext}"
        final_name = f"{secrets.token_hex(6)}-{safe}"
        path = base_dir / final_name
        uploaded.save(path)
        if path.stat().st_size > Config.COLLECTION_MAX_UPLOAD_BYTES:
            path.unlink(missing_ok=True)
            flash(f"Skipped {original}: file is larger than the configured limit.", "warning")
            continue
        _queue_collection_source(collection, "document", title=original, original_name=original, file_path=str(path), file_size=path.stat().st_size, extension=ext)
        queued += 1
    if queued:
        flash(f"Queued {queued} document{'s' if queued != 1 else ''} for extraction.", "success")
    return redirect(url_for("collection_detail", collection_id=collection_id))


@app.post("/collections/<collection_id>/links/scrape")
@login_required
def collection_scrape_link(collection_id):
    collection = owned_collection(collection_id)
    if not collection:
        abort(404)
    parent = owned_collection_source(request.form.get("source_id") or "")
    if not parent or parent.get("collection_id") != collection["_id"]:
        abort(404)
    url = clean_text(request.form.get("url"), 2000)
    label = clean_text(request.form.get("label"), 300)
    kind = request.form.get("kind") if request.form.get("kind") in {"internal", "external"} else "linked"
    existing = collection_sources.find_one({"collection_id": collection["_id"], "source_type": "url", "url": url})
    if existing:
        flash("That linked page is already collected.", "warning")
    else:
        _queue_collection_source(collection, "url", url=url, title=label or url, parent_source_id=parent["_id"], discovery_kind=kind)
        flash("Linked page queued for scraping.", "success")
    return redirect(url_for("collection_detail", collection_id=collection_id))


@app.post("/collections/<collection_id>/sources/<source_id>/retry")
@login_required
def collection_retry_source(collection_id, source_id):
    collection = owned_collection(collection_id)
    source = owned_collection_source(source_id)
    if not collection or not source or source.get("collection_id") != collection["_id"]:
        abort(404)
    collection_sources.update_one({"_id": source["_id"]}, {"$set": {"status": "queued", "error": None, "updated_at": now()}})
    process_collection_source_task.delay(str(source["_id"]))
    flash("Source queued again.", "success")
    return redirect(url_for("collection_detail", collection_id=collection_id))


@app.get("/api/collections/<collection_id>/items")
@login_required
def collection_items_api(collection_id):
    collection = owned_collection(collection_id)
    if not collection:
        return jsonify({"error": "Collection not found"}), 404
    rows = list(collection_items.find({"collection_id": collection["_id"], "active": {"$ne": False}}).sort("created_at", 1).limit(1500))
    return jsonify({"items": [{
        "id": str(row["_id"]), "source_id": str(row.get("source_id")), "type": row.get("item_type"),
        "title": row.get("title"), "url": row.get("url"), "image_url": row.get("image_url"),
        "text_preview": clean_text(row.get("text"), 360),
    } for row in rows]})


@app.route("/campaigns")
@login_required
def campaign_list():
    rows = list(campaigns.find({"$or": ownership_or()}).sort("created_at", -1))
    return render_template("campaigns.html", campaigns=rows)


@app.route("/campaigns/new", methods=["GET", "POST"])
@login_required
def campaign_new():
    collection_rows = list(collections.find({"$or": ownership_or()}).sort("updated_at", -1))
    collection_cards = []
    for collection in collection_rows:
        items = list(collection_items.find({"collection_id": collection["_id"], "active": {"$ne": False}}).sort("created_at", 1).limit(400))
        collection_cards.append({"collection": collection, "items": items})

    if request.method == "POST":
        title = clean_text(request.form.get("title"), 300)
        if len(title) < 3:
            flash("Use a campaign title of at least three characters.", "danger")
            return render_template("campaign_new.html", values=request.form, collection_cards=collection_cards), 400
        evidence_ids = owned_collection_item_ids(request.form.getlist("evidence_item_ids"))
        if not evidence_ids:
            flash("Select at least one item from a Collection. Campaigns are generated from explicit Collection evidence.", "danger")
            return render_template("campaign_new.html", values=request.form, collection_cards=collection_cards), 400
        evidence_docs = list(collection_items.find({"_id": {"$in": evidence_ids}}))
        collection_ids = list(dict.fromkeys(item.get("collection_id") for item in evidence_docs if item.get("collection_id")))
        # Preserve a useful website hint for legacy article/page helpers when selected evidence came from the web.
        website_url = next((str(item.get("url")) for item in evidence_docs if item.get("url") and item.get("item_type") in {"description", "page_text"}), "")
        context = clean_text(request.form.get("campaign_context"), 2500)
        doc = {
            "user_id": ObjectId(current_user.id),
            "organization_id": current_user.organization_id,
            "title": title,
            "website_url": website_url,
            "campaign_context": context,
            "collection_ids": collection_ids,
            "evidence_item_ids": evidence_ids,
            "evidence_count": len(evidence_ids),
            "evidence_snapshot": evidence_context(evidence_docs, max_chars=60000),
            "target_audience": {},
            "campaign_goal": {},
            "content_overview": {},
            "engagement_engine": {},
            "filter_keywords": [],
            "status": "draft",
            "setup_status": "queued",
            "setup_version": "1.9",
            "created_at": now(),
            "updated_at": now(),
        }
        campaign_id = campaigns.insert_one(doc).inserted_id
        try:
            prepare_campaign_strategy_task.delay(str(campaign_id))
        except Exception as exc:
            campaigns.update_one({"_id": campaign_id}, {"$set": {"setup_status": "failed", "setup_error": str(exc)[:1000]}})
        return redirect(url_for("campaign_setup", campaign_id=str(campaign_id)))
    return render_template("campaign_new.html", values={}, collection_cards=collection_cards)


@app.route("/campaign/<campaign_id>/setup")
@login_required
def campaign_setup(campaign_id):
    campaign = owned_campaign(campaign_id)
    if not campaign:
        abort(404)
    if campaign.get("setup_status") == "review":
        return redirect(url_for("campaign_review", campaign_id=campaign_id))
    if campaign.get("setup_status") == "complete":
        return redirect(url_for("campaign_detail", campaign_id=campaign_id))
    return render_template("campaign_setup.html", campaign=campaign)


@app.route("/api/campaign/<campaign_id>/setup-status")
@login_required
def campaign_setup_status(campaign_id):
    campaign = owned_campaign(campaign_id)
    if not campaign:
        abort(404)
    return jsonify({
        "status": campaign.get("setup_status", "draft"),
        "error": campaign.get("setup_error"),
        "has_suggestions": bool(campaign.get("strategy_suggestions")),
        "review_url": url_for("campaign_review", campaign_id=campaign_id),
    })


@app.post("/campaign/<campaign_id>/prepare")
@login_required
def campaign_prepare(campaign_id):
    campaign = owned_campaign(campaign_id)
    if not campaign:
        abort(404)
    campaigns.update_one({"_id": campaign["_id"]}, {"$set": {"setup_status": "queued", "updated_at": now()}, "$unset": {"setup_error": ""}})
    prepare_campaign_strategy_task.delay(campaign_id)
    flash("Campaign research and suggestions were queued again.", "success")
    return redirect(url_for("campaign_setup", campaign_id=campaign_id))


@app.post("/campaign/<campaign_id>/strategy")
@login_required
def campaign_strategy(campaign_id):
    campaign = owned_campaign(campaign_id)
    if not campaign:
        abort(404)
    suggestions = campaign.get("strategy_suggestions") or {}
    if not suggestions:
        flash("Strategy suggestions are not ready yet.", "warning")
        return redirect(url_for("campaign_setup", campaign_id=campaign_id))

    selected_ids = {
        "audience": request.form.get("audience") or (suggestions.get("recommended") or {}).get("audience"),
        "objective": request.form.get("objective") or (suggestions.get("recommended") or {}).get("objective"),
        "blueprint": request.form.get("blueprint") or (suggestions.get("recommended") or {}).get("blueprint"),
        "engagement": request.form.get("engagement") or (suggestions.get("recommended") or {}).get("engagement"),
    }
    selected = {kind: strategy_option(suggestions, kind, option_id) for kind, option_id in selected_ids.items()}
    if not all(selected.values()):
        flash("Select one option in each strategy section.", "danger")
        return redirect(url_for("campaign_setup", campaign_id=campaign_id))

    updates = {
        "strategy_selected": selected_ids,
        "target_audience": selected["audience"],
        "campaign_goal": selected["objective"],
        "content_overview": selected["blueprint"],
        "engagement_engine": selected["engagement"],
        "filter_keywords": suggestions.get("keywords", []),
        "campaign_summary": suggestions.get("campaign_summary", ""),
        "setup_status": "review",
        "updated_at": now(),
    }
    campaigns.update_one({"_id": campaign["_id"]}, {"$set": updates})
    return redirect(url_for("campaign_review", campaign_id=campaign_id))


@app.route("/campaign/<campaign_id>/review", methods=["GET", "POST"])
@login_required
def campaign_review(campaign_id):
    campaign = owned_campaign(campaign_id)
    if not campaign:
        abort(404)
    if request.method == "POST":
        title = clean_text(request.form.get("title"), 300) or campaign.get("title")
        try:
            website_url = normalize_website_url(request.form.get("website_url"))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("campaign_review", campaign_id=campaign_id))
        keywords = [clean_text(x, 100) for x in (request.form.get("filter_keywords") or "").split(",") if clean_text(x, 100)][:20]
        status = request.form.get("status") if request.form.get("status") in {"active", "draft", "paused"} else "active"
        updates = {
            "title": title,
            "website_url": website_url,
            "filter_keywords": keywords,
            "campaign_context": clean_text(request.form.get("campaign_context"), 2500),
            "status": status,
            "setup_status": "complete",
            "reviewed_at": now(),
            "updated_at": now(),
        }
        campaigns.update_one({"_id": campaign["_id"]}, {"$set": updates})
        flash("Campaign approved. Newsjacking can now use this operating brief.", "success")
        return redirect(url_for("campaign_detail", campaign_id=campaign_id))
    evidence_ids = [x for x in (campaign.get("evidence_item_ids") or []) if isinstance(x, ObjectId)]
    evidence_rows = list(collection_items.find({"_id": {"$in": evidence_ids}})) if evidence_ids else []
    evidence_map = {row["_id"]: row for row in evidence_rows}
    evidence_rows = [evidence_map[x] for x in evidence_ids if x in evidence_map]
    return render_template("campaign_review.html", campaign=campaign, evidence=evidence_rows)


@app.route("/campaign/<campaign_id>")
@login_required
def campaign_detail(campaign_id):
    campaign = owned_campaign(campaign_id)
    if not campaign:
        abort(404)
    hook_rows = list(hooks.find({"campaign_id": campaign["_id"]}).sort("created_at", -1).limit(50))
    article_rows = list(articles.find({"campaign_id": campaign["_id"]}).sort("created_at", -1).limit(50))
    evidence_ids = [x for x in (campaign.get("evidence_item_ids") or []) if isinstance(x, ObjectId)]
    evidence_rows = list(collection_items.find({"_id": {"$in": evidence_ids}})) if evidence_ids else []
    evidence_map = {row["_id"]: row for row in evidence_rows}
    evidence_rows = [evidence_map[x] for x in evidence_ids if x in evidence_map]
    worker_rows = list(newsjacking_workers.find({**workspace_scope(), "campaign_id": campaign["_id"]}).sort("updated_at", -1))
    return render_template("campaign.html", campaign=campaign, hooks=hook_rows, articles=article_rows, evidence=evidence_rows, workers=worker_rows)



@app.get("/products")
@login_required
def product_list():
    rows = list(products.find(workspace_scope()).sort("updated_at", -1))
    for row in rows:
        row["evidence_count"] = len(row.get("evidence_item_ids") or [])
        row["worker_count"] = newsjacking_workers.count_documents({**workspace_scope(), "product_ids": row["_id"]})
    return render_template("products.html", products=rows)


@app.route("/products/new", methods=["GET", "POST"])
@login_required
def product_new():
    cards = _collection_picker_cards()
    if request.method == "POST":
        name = clean_text(request.form.get("name"), 300)
        if len(name) < 2:
            flash("Give the product a clear name.", "danger")
            return render_template("product_new.html", values=request.form, collection_cards=cards), 400
        try:
            product_url = _simple_http_url(request.form.get("product_url"), "Product link")
            cta_url = _simple_http_url(request.form.get("cta_url") or product_url, "CTA link")
        except ValueError as exc:
            flash(str(exc), "danger")
            return render_template("product_new.html", values=request.form, collection_cards=cards), 400
        if not product_url:
            flash("Add the product link that readers should ultimately be able to visit.", "danger")
            return render_template("product_new.html", values=request.form, collection_cards=cards), 400
        evidence_ids = owned_collection_item_ids(request.form.getlist("evidence_item_ids"))
        evidence_docs = list(collection_items.find({"_id": {"$in": evidence_ids}})) if evidence_ids else []
        collection_ids = list(dict.fromkeys(item.get("collection_id") for item in evidence_docs if item.get("collection_id")))
        proof_points = [clean_text(x, 500) for x in re.split(r"[\r\n]+", request.form.get("proof_points") or "") if clean_text(x, 500)][:20]
        claims_to_avoid = [clean_text(x, 500) for x in re.split(r"[\r\n]+", request.form.get("claims_to_avoid") or "") if clean_text(x, 500)][:20]
        status = request.form.get("status") if request.form.get("status") in {"active", "draft", "paused"} else "active"
        doc = {
            "user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id,
            "name": name, "tagline": clean_text(request.form.get("tagline"), 600),
            "description": clean_text(request.form.get("description"), 5000),
            "positioning": clean_text(request.form.get("positioning"), 5000),
            "product_url": product_url, "cta_label": clean_text(request.form.get("cta_label"), 120) or "Learn more",
            "cta_url": cta_url or product_url, "proof_points": proof_points, "claims_to_avoid": claims_to_avoid,
            "collection_ids": collection_ids, "evidence_item_ids": evidence_ids,
            "evidence_snapshot": evidence_context(evidence_docs, max_chars=50000) if evidence_docs else "",
            "status": status, "created_at": now(), "updated_at": now(),
        }
        product_id = products.insert_one(doc).inserted_id
        flash("Product created. You can now attach it to one or more Newsjack Workers.", "success")
        return redirect(url_for("product_detail", product_id=str(product_id)))
    return render_template("product_new.html", values={}, collection_cards=cards)


@app.route("/products/<product_id>", methods=["GET", "POST"])
@login_required
def product_detail(product_id):
    product = owned_product(product_id)
    if not product:
        abort(404)
    if request.method == "POST":
        try:
            product_url = _simple_http_url(request.form.get("product_url"), "Product link")
            cta_url = _simple_http_url(request.form.get("cta_url") or product_url, "CTA link")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("product_detail", product_id=product_id))
        name = clean_text(request.form.get("name"), 300)
        if len(name) < 2 or not product_url:
            flash("Product name and product link are required.", "danger")
            return redirect(url_for("product_detail", product_id=product_id))
        evidence_ids = owned_collection_item_ids(request.form.getlist("evidence_item_ids"))
        evidence_docs = list(collection_items.find({"_id": {"$in": evidence_ids}})) if evidence_ids else []
        updates = {
            "name": name, "tagline": clean_text(request.form.get("tagline"), 600),
            "description": clean_text(request.form.get("description"), 5000),
            "positioning": clean_text(request.form.get("positioning"), 5000),
            "product_url": product_url, "cta_label": clean_text(request.form.get("cta_label"), 120) or "Learn more",
            "cta_url": cta_url or product_url,
            "proof_points": [clean_text(x, 500) for x in re.split(r"[\r\n]+", request.form.get("proof_points") or "") if clean_text(x, 500)][:20],
            "claims_to_avoid": [clean_text(x, 500) for x in re.split(r"[\r\n]+", request.form.get("claims_to_avoid") or "") if clean_text(x, 500)][:20],
            "status": request.form.get("status") if request.form.get("status") in {"active", "draft", "paused"} else "active",
            "collection_ids": list(dict.fromkeys(item.get("collection_id") for item in evidence_docs if item.get("collection_id"))),
            "evidence_item_ids": evidence_ids,
            "evidence_snapshot": evidence_context(evidence_docs, max_chars=50000) if evidence_docs else "",
            "updated_at": now(),
        }
        products.update_one({"_id": product["_id"]}, {"$set": updates})
        flash("Product updated.", "success")
        return redirect(url_for("product_detail", product_id=product_id))
    evidence_ids = [x for x in (product.get("evidence_item_ids") or []) if isinstance(x, ObjectId)]
    evidence_rows = list(collection_items.find({"_id": {"$in": evidence_ids}})) if evidence_ids else []
    evidence_map = {row["_id"]: row for row in evidence_rows}
    evidence_rows = [evidence_map[x] for x in evidence_ids if x in evidence_map]
    worker_rows = list(newsjacking_workers.find({**workspace_scope(), "product_ids": product["_id"]}).sort("updated_at", -1))
    recent_articles = list(articles.find({**workspace_scope(), "product_ids": product["_id"]}).sort("created_at", -1).limit(20))
    return render_template("product_detail.html", product=product, collection_cards=_collection_picker_cards(), evidence=evidence_rows, workers=worker_rows, articles=recent_articles)


@app.post("/products/<product_id>/delete")
@login_required
def product_delete(product_id):
    product = owned_product(product_id)
    if not product:
        abort(404)
    if newsjacking_workers.count_documents({**workspace_scope(), "product_ids": product["_id"]}):
        flash("This product is used by a Newsjack Worker. Remove it from the worker before deleting it.", "warning")
        return redirect(url_for("product_detail", product_id=product_id))
    products.delete_one({"_id": product["_id"]})
    flash("Product deleted. Existing articles keep their historical product context.", "success")
    return redirect(url_for("product_list"))


@app.route("/feeds", methods=["GET", "POST"])
@login_required
def feeds():
    uid = ObjectId(current_user.id)
    scope = workspace_scope()
    if request.method == "POST":
        try:
            url = normalize_feed_url(request.form.get("url"))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("feeds"))
        if rss_feeds.find_one({**scope, "url": url}, {"_id": 1}):
            flash("That feed is already in this workspace.", "warning")
        else:
            rss_feeds.insert_one({
                "user_id": uid,
                "organization_id": current_user.organization_id,
                "title": clean_text(request.form.get("title"), 300) or url,
                "url": url,
                "is_active": True,
                "created_at": now(),
            })
            flash("Feed added.", "success")
        return redirect(url_for("feeds"))
    rows = list(rss_feeds.find(scope).sort("title", 1))
    return render_template("feeds.html", feeds=rows)


@app.post("/feeds/<feed_id>/toggle")
@login_required
def feed_toggle(feed_id):
    uid = ObjectId(current_user.id)
    try:
        oid = ObjectId(feed_id)
    except Exception:
        abort(404)
    feed = rss_feeds.find_one({"_id": oid, **workspace_scope()})
    if not feed:
        abort(404)
    rss_feeds.update_one({"_id": oid}, {"$set": {"is_active": not feed.get("is_active", True)}})
    return redirect(url_for("feeds"))


@app.post("/feeds/<feed_id>/delete")
@login_required
def feed_delete(feed_id):
    uid = ObjectId(current_user.id)
    try:
        oid = ObjectId(feed_id)
    except Exception:
        abort(404)
    result = rss_feeds.delete_one({"_id": oid, **workspace_scope()})
    if result.deleted_count:
        flash("Feed removed. Existing feed items and generated content were left intact.", "success")
    return redirect(url_for("feeds"))


def _worker_learning_fields(form, worker=None):
    worker = worker or {}
    mode = form.get("learning_mode") or worker.get("learning_mode") or "observe"
    if mode not in LEARNING_MODES:
        mode = "observe"
    objective = form.get("learning_objective") or worker.get("learning_objective") or "conversion_rate"
    if objective not in LEARNING_OBJECTIVES:
        objective = "conversion_rate"
    try:
        min_views = max(20, min(5000, int(form.get("learning_min_views") or worker.get("learning_min_views") or 80)))
    except (TypeError, ValueError):
        min_views = 80
    try:
        lookback = max(14, min(365, int(form.get("learning_lookback_days") or worker.get("learning_lookback_days") or 90)))
    except (TypeError, ValueError):
        lookback = 90
    return {
        "learning_mode": mode,
        "learning_objective": objective,
        "learning_min_views": min_views,
        "learning_lookback_days": lookback,
    }


def _worker_form_context(worker=None):
    scope = workspace_scope()
    feed_rows = list(rss_feeds.find(scope).sort("title", 1))
    campaign_rows = list(campaigns.find(scope).sort("updated_at", -1))
    product_rows = list(products.find(scope).sort("updated_at", -1))
    return {"feeds": feed_rows, "campaigns": campaign_rows, "products": product_rows, "collection_cards": _collection_picker_cards(300), "worker": worker or {}}


@app.get("/newsjacking")
@login_required
def newsjacking():
    scope = workspace_scope()
    worker_rows = list(newsjacking_workers.find(scope).sort("updated_at", -1))
    campaign_map = {row["_id"]: row for row in campaigns.find({**scope, "_id": {"$in": [w.get("campaign_id") for w in worker_rows if w.get("campaign_id")]}})} if worker_rows else {}
    product_ids = list({pid for w in worker_rows for pid in (w.get("product_ids") or []) if isinstance(pid, ObjectId)})
    product_map = {row["_id"]: row for row in products.find({**scope, "_id": {"$in": product_ids}})} if product_ids else {}
    for worker in worker_rows:
        worker["campaign"] = campaign_map.get(worker.get("campaign_id"))
        worker["product_names"] = [product_map[pid].get("name") for pid in (worker.get("product_ids") or []) if pid in product_map]
        worker["feed_count"] = len(worker.get("feed_ids") or [])
        worker["supplemental_evidence_count"] = len(worker.get("evidence_item_ids") or [])
    runs = list(newsjacking_runs.find(scope).sort("started_at", -1).limit(30))
    user = users.find_one({"_id": ObjectId(current_user.id)}) or {}
    legacy = _newsjacking_settings(user)
    return render_template("newsjacking.html", workers=worker_rows, runs=runs, legacy_settings=legacy)


@app.route("/newsjacking/workers/new", methods=["GET", "POST"])
@login_required
def newsjacking_worker_new():
    context = _worker_form_context()
    if request.method == "POST":
        name = clean_text(request.form.get("name"), 200)
        campaign = owned_campaign(request.form.get("campaign_id") or "")
        feed_ids = _object_ids(request.form.getlist("feed_ids"))
        product_ids = _object_ids(request.form.getlist("product_ids"))
        evidence_ids = owned_collection_item_ids(request.form.getlist("evidence_item_ids"))[:30]
        evidence_docs = list(collection_items.find({"_id": {"$in": evidence_ids}})) if evidence_ids else []
        owned_feeds = list(rss_feeds.find({**workspace_scope(), "_id": {"$in": feed_ids}, "is_active": True})) if feed_ids else []
        owned_products = list(products.find({**workspace_scope(), "_id": {"$in": product_ids}, "status": "active"})) if product_ids else []
        if len(name) < 2:
            flash("Give the worker a clear name.", "danger")
        elif not campaign or campaign.get("status") != "active":
            flash("Choose an active campaign. Campaigns define the audience and editorial direction.", "danger")
        elif not owned_feeds:
            flash("Choose at least one active source for this worker.", "danger")
        elif not owned_products:
            flash("Choose at least one active product for this worker.", "danger")
        elif len(owned_products) > 8:
            flash("Choose no more than eight eligible products per Worker. Smaller product pools produce clearer relevance decisions.", "danger")
        else:
            try:
                min_confidence = max(0.0, min(1.0, float(request.form.get("min_confidence", 0.68))))
                max_articles = max(1, min(Config.NEWSJACKING_MAX_ARTICLES_PER_RUN, int(request.form.get("max_articles_per_run", 5))))
            except ValueError:
                min_confidence, max_articles = 0.68, 5
            enabled = request.form.get("enabled") == "on"
            placement_mode = request.form.get("placement_mode") if request.form.get("placement_mode") in {"subtle", "contextual", "conversion"} else "subtle"
            doc = {
                "user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id,
                "name": name, "description": clean_text(request.form.get("description"), 1200),
                "campaign_id": campaign["_id"], "feed_ids": [f["_id"] for f in owned_feeds],
                "product_ids": [p["_id"] for p in owned_products],
                "evidence_item_ids": evidence_ids, "evidence_snapshot": evidence_context(evidence_docs, max_chars=30000) if evidence_docs else "",
                "enabled": enabled, "enabled_at": now() if enabled else None, "min_confidence": min_confidence,
                "max_articles_per_run": max_articles, "placement_mode": placement_mode,
                "editorial_instructions": clean_text(request.form.get("editorial_instructions"), 4000),
                **_worker_learning_fields(request.form),
                "created_at": now(), "updated_at": now(),
            }
            worker_id = newsjacking_workers.insert_one(doc).inserted_id
            flash("Newsjack Worker created. Its source, campaign and product recipe is now reusable.", "success")
            return redirect(url_for("newsjacking_worker_detail", worker_id=str(worker_id)))
        context["values"] = request.form
        return render_template("newsjacking_worker_form.html", **context), 400
    context["values"] = {}
    return render_template("newsjacking_worker_form.html", **context)


@app.route("/newsjacking/workers/<worker_id>", methods=["GET", "POST"])
@login_required
def newsjacking_worker_detail(worker_id):
    worker = owned_newsjacking_worker(worker_id)
    if not worker:
        abort(404)
    if request.method == "POST":
        campaign = owned_campaign(request.form.get("campaign_id") or "")
        feed_ids = _object_ids(request.form.getlist("feed_ids"))
        product_ids = _object_ids(request.form.getlist("product_ids"))
        evidence_ids = owned_collection_item_ids(request.form.getlist("evidence_item_ids"))[:30]
        evidence_docs = list(collection_items.find({"_id": {"$in": evidence_ids}})) if evidence_ids else []
        owned_feeds = list(rss_feeds.find({**workspace_scope(), "_id": {"$in": feed_ids}, "is_active": True})) if feed_ids else []
        owned_products = list(products.find({**workspace_scope(), "_id": {"$in": product_ids}, "status": "active"})) if product_ids else []
        name = clean_text(request.form.get("name"), 200)
        if len(name) < 2 or not campaign or campaign.get("status") != "active" or not owned_feeds or not owned_products:
            flash("A worker needs a name, one active campaign, at least one active source, and at least one active product.", "danger")
            return redirect(url_for("newsjacking_worker_detail", worker_id=worker_id))
        if len(owned_products) > 8:
            flash("Choose no more than eight eligible products per Worker.", "danger")
            return redirect(url_for("newsjacking_worker_detail", worker_id=worker_id))
        try:
            min_confidence = max(0.0, min(1.0, float(request.form.get("min_confidence", 0.68))))
            max_articles = max(1, min(Config.NEWSJACKING_MAX_ARTICLES_PER_RUN, int(request.form.get("max_articles_per_run", 5))))
        except ValueError:
            min_confidence, max_articles = 0.68, 5
        enabled = request.form.get("enabled") == "on"
        placement_mode = request.form.get("placement_mode") if request.form.get("placement_mode") in {"subtle", "contextual", "conversion"} else "subtle"
        updates = {
            "name": name, "description": clean_text(request.form.get("description"), 1200),
            "campaign_id": campaign["_id"], "feed_ids": [f["_id"] for f in owned_feeds],
            "product_ids": [p["_id"] for p in owned_products],
            "evidence_item_ids": evidence_ids, "evidence_snapshot": evidence_context(evidence_docs, max_chars=30000) if evidence_docs else "",
            "enabled": enabled, "min_confidence": min_confidence, "max_articles_per_run": max_articles,
            "placement_mode": placement_mode, "editorial_instructions": clean_text(request.form.get("editorial_instructions"), 4000),
            **_worker_learning_fields(request.form, worker),
            "updated_at": now(),
        }
        if enabled and not worker.get("enabled"):
            updates["enabled_at"] = now()
        newsjacking_workers.update_one({"_id": worker["_id"]}, {"$set": updates})
        flash("Worker recipe updated.", "success")
        return redirect(url_for("newsjacking_worker_detail", worker_id=worker_id))
    context = _worker_form_context(worker)
    context["values"] = None
    context["runs"] = list(newsjacking_runs.find({**workspace_scope(), "newsjacking_worker_id": worker["_id"]}).sort("started_at", -1).limit(20))
    context["selected_campaign"] = campaigns.find_one({"_id": worker.get("campaign_id")})
    context["selected_products"] = list(products.find({"_id": {"$in": worker.get("product_ids") or []}}))
    return render_template("newsjacking_worker_form.html", **context)


@app.get("/newsjacking/workers/<worker_id>/learning")
@login_required
def newsjacking_worker_learning(worker_id):
    worker = owned_newsjacking_worker(worker_id)
    if not worker:
        abort(404)
    learning = worker.get("learning_state") or compute_worker_learning(worker, persist=True)
    return render_template("newsjacking_worker_learning.html", worker=worker, learning=learning)


@app.post("/newsjacking/workers/<worker_id>/learning/refresh")
@login_required
def newsjacking_worker_learning_refresh(worker_id):
    worker = owned_newsjacking_worker(worker_id)
    if not worker:
        abort(404)
    learning = compute_worker_learning(worker, persist=True)
    if learning.get("ready"):
        flash("Worker learning report refreshed from current attribution data.", "success")
    else:
        flash("Learning report refreshed. More public traffic is needed before adaptive recommendations are ready.", "info")
    return redirect(url_for("newsjacking_worker_learning", worker_id=worker_id))


@app.post("/newsjacking/workers/<worker_id>/learning/apply")
@login_required
def newsjacking_worker_learning_apply(worker_id):
    worker = owned_newsjacking_worker(worker_id)
    if not worker:
        abort(404)
    learning = worker.get("learning_state") or compute_worker_learning(worker, persist=True)
    if not learning.get("ready"):
        flash("There is not enough measured traffic to apply learning recommendations yet.", "warning")
        return redirect(url_for("newsjacking_worker_learning", worker_id=worker_id))
    policy = learning.get("policy") or {}
    update = {"updated_at": now()}
    enable_adaptive = request.form.get("enable_adaptive") == "on"
    # Adaptive mode applies the learned confidence delta dynamically. Do not also bake
    # that same delta into the configured base threshold in the same action.
    if request.form.get("apply_confidence") == "on" and not enable_adaptive and policy.get("recommended_min_confidence") is not None:
        update["min_confidence"] = max(0.0, min(1.0, float(policy.get("recommended_min_confidence"))))
    if enable_adaptive:
        update["learning_mode"] = "adaptive"
    newsjacking_workers.update_one({"_id": worker["_id"]}, {"$set": update})
    flash("Selected learning recommendations applied. The original relevance and evidence gates remain active.", "success")
    return redirect(url_for("newsjacking_worker_learning", worker_id=worker_id))


@app.post("/newsjacking/workers/<worker_id>/run")
@login_required
def newsjacking_worker_run(worker_id):
    worker = owned_newsjacking_worker(worker_id)
    if not worker:
        abort(404)
    if not worker.get("enabled"):
        flash("Enable this worker before running it.", "warning")
    else:
        newsjacking_scan_worker_task.delay(worker_id)
        flash(f"{worker.get('name') or 'Worker'} scan queued.", "success")
    return redirect(url_for("newsjacking_worker_detail", worker_id=worker_id))


@app.post("/newsjacking/workers/<worker_id>/toggle")
@login_required
def newsjacking_worker_toggle(worker_id):
    worker = owned_newsjacking_worker(worker_id)
    if not worker:
        abort(404)
    enabled = not bool(worker.get("enabled"))
    update = {"enabled": enabled, "updated_at": now()}
    if enabled:
        update["enabled_at"] = now()
    else:
        update["lock_until"] = None
    newsjacking_workers.update_one({"_id": worker["_id"]}, {"$set": update})
    flash(f"Worker {'enabled' if enabled else 'paused'}.", "success")
    return redirect(request.referrer or url_for("newsjacking"))


@app.post("/newsjacking/workers/<worker_id>/delete")
@login_required
def newsjacking_worker_delete(worker_id):
    worker = owned_newsjacking_worker(worker_id)
    if not worker:
        abort(404)
    newsjacking_workers.delete_one({"_id": worker["_id"]})
    flash("Worker deleted. Existing articles and runs remain in history.", "success")
    return redirect(url_for("newsjacking"))


@app.post("/newsjacking/run")
@login_required
def newsjacking_run():
    worker_rows = list(newsjacking_workers.find({**workspace_scope(), "enabled": True}, {"_id": 1}))
    if worker_rows:
        for worker in worker_rows:
            newsjacking_scan_worker_task.delay(str(worker["_id"]))
        flash(f"Queued {len(worker_rows)} enabled Newsjack Worker{'s' if len(worker_rows) != 1 else ''}.", "success")
    else:
        user = users.find_one({"_id": ObjectId(current_user.id)}) or {}
        settings = _newsjacking_settings(user)
        if settings.get("enabled"):
            newsjacking_scan_user_task.delay(str(current_user.id), str(current_user.organization_id) if current_user.organization_id else None)
            flash("Legacy Newsjacking scan queued. Create a Worker to use campaign + product recipes.", "warning")
        else:
            flash("Create and enable a Newsjack Worker first.", "warning")
    return redirect(url_for("newsjacking"))


@app.route("/articles")
@login_required
def article_list():
    rows = list(articles.find({"$or": ownership_or()}).sort("created_at", -1).limit(300))
    return render_template("articles.html", articles=rows)


@app.route("/articles/review")
@login_required
def article_review_queue():
    rows = list(articles.find({"$and": [
        {"$or": ownership_or()},
        {"review_status": {"$ne": "signed"}},
    ]}).sort("created_at", -1).limit(300))
    return render_template("article_review_queue.html", articles=rows, profile=current_author_profile())


def _article_versions_context(article, version_key="current"):
    raw_snapshots = list(article_versions.find({"article_id": article["_id"]}).sort([("revision", -1), ("created_at", -1)]).limit(100))
    snapshots, seen = [], set()
    for snapshot in raw_snapshots:
        number = int(snapshot.get("revision") or 0)
        if number in seen:
            continue
        seen.add(number)
        snapshots.append(snapshot)
        if len(snapshots) >= 50:
            break
    versions = [{
        "key": "current", "id": None, "revision": int(article.get("revision") or 1),
        "reason": "Current working version", "created_at": article.get("updated_at") or article.get("completed_at") or article.get("created_at"),
        "is_current": True,
    }] + [{
        "key": str(v["_id"]), "id": v["_id"], "revision": int(v.get("revision") or 0),
        "reason": v.get("reason") or "Saved revision", "created_at": v.get("created_at"), "is_current": False,
    } for v in snapshots]
    selected = None
    notes = ""
    if version_key == "current":
        selected = {
            **versions[0], "content": article.get("content") or "", "metadata": article.get("metadata") or {},
            "quality": article.get("quality") or {}, "publication": article.get("publication") or {},
            "review_status": article.get("review_status") or "pending_review",
        }
        notes = article.get("review_notes") or ""
    else:
        try:
            snapshot = article_versions.find_one({"_id": ObjectId(version_key), "article_id": article["_id"]})
        except Exception:
            snapshot = None
        if snapshot:
            selected = {
                "key": str(snapshot["_id"]), "id": snapshot["_id"], "revision": int(snapshot.get("revision") or 0),
                "reason": snapshot.get("reason") or "Saved revision", "created_at": snapshot.get("created_at"), "is_current": False,
                "content": snapshot.get("content") or "", "metadata": snapshot.get("metadata") or {},
                "quality": snapshot.get("quality") or {}, "publication": snapshot.get("publication") or {},
                "review_status": snapshot.get("review_status") or "pending_review",
            }
            notes = snapshot.get("review_notes") or ""
        else:
            version_key = "current"
            selected = {
                **versions[0], "content": article.get("content") or "", "metadata": article.get("metadata") or {},
                "quality": article.get("quality") or {}, "publication": article.get("publication") or {},
                "review_status": article.get("review_status") or "pending_review",
            }
            notes = article.get("review_notes") or ""
    return versions, selected, version_key, notes


@app.route("/articles/<article_id>")
@login_required
def article_detail(article_id):
    article = owned_article(article_id)
    if not article:
        abort(404)
    campaign = campaigns.find_one({"_id": article.get("campaign_id")})
    hook = hooks.find_one({"_id": article.get("hook_id")})
    product_ids = [x for x in (article.get("product_ids") or []) if isinstance(x, ObjectId)]
    article_products = list(products.find({"_id": {"$in": product_ids}})) if product_ids else []
    product_map = {p["_id"]: p for p in article_products}
    article_products = [product_map[x] for x in product_ids if x in product_map]
    article_worker = newsjacking_workers.find_one({"_id": article.get("newsjacking_worker_id")}) if article.get("newsjacking_worker_id") else None
    social_posts = list(social_media_posts.find({"article_id": article["_id"]}).sort("platform", 1))
    latest_social_job = social_generation_jobs.find_one({"article_id": article["_id"]}, sort=[("created_at", -1)])
    version_key = request.args.get("version") or "current"
    versions, selected_version, selected_version_key, selected_notes = _article_versions_context(article, version_key)
    change_jobs = list(article_change_jobs.find({"article_id": article["_id"]}).sort("created_at", -1).limit(8))
    active_change_jobs = [job for job in change_jobs if job.get("status") in {"queued", "processing", "retrying"}]
    user_doc = users.find_one({"_id": ObjectId(current_user.id)}) or {}
    recent_submissions = list(article_submissions.find({"article_id": article["_id"]}).sort("created_at", -1).limit(12))
    return render_template(
        "article.html", article=article, campaign=campaign, hook=hook, article_products=article_products, article_worker=article_worker, social_posts=social_posts,
        latest_social_job=latest_social_job, openai_images_configured=bool(Config.OPENAI_API_KEY),
        versions=versions, selected_version=selected_version, selected_version_key=selected_version_key,
        selected_notes=selected_notes, change_jobs=change_jobs, can_sign=can_sign_article(article, user_doc) and not active_change_jobs,
        author_profile=current_author_profile(), public_available=article_is_public(article),
        engagement=article.get("engagement") or {}, recent_submissions=recent_submissions,
        submission_stats=_article_submission_stats(article["_id"]), blackbook_connector=blackbook_connector_status(article.get("organization_id")),
    )


@app.route("/articles/<article_id>/preview")
@login_required
def article_preview(article_id):
    article = owned_article(article_id)
    if not article:
        abort(404)
    _, selected, version_key, _ = _article_versions_context(article, request.args.get("version") or "current")
    preview = dict(article)
    preview["content"] = selected.get("content") or ""
    preview["metadata"] = selected.get("metadata") or {}
    preview["quality"] = selected.get("quality") or {}
    preview["revision"] = selected.get("revision")
    selected_publication = selected.get("publication") or {}
    preview["publication"] = selected_publication if int(selected_publication.get("revision") or 0) == int(selected.get("revision") or 0) else {}
    campaign = campaigns.find_one({"_id": article.get("campaign_id")})
    hook = hooks.find_one({"_id": article.get("hook_id")}) if article.get("hook_id") else None
    return render_template(
        "public_article.html", article=preview, campaign=campaign, hook=hook,
        analytics_event_id=None, preview=True, preview_author=current_author_profile(), version_key=version_key,
        audience_capture_token=None,
    )


@app.post("/articles/<article_id>/engagement")
@login_required
def article_engagement_settings(article_id):
    article = owned_article(article_id)
    if not article:
        abort(404)
    email_enabled = request.form.get("email_capture_enabled") == "on"
    survey_enabled = request.form.get("survey_enabled") == "on"
    blackbook_enabled = request.form.get("blackbook_sync_enabled") == "on" and email_enabled
    mailchimp_opt_in = request.form.get("mailchimp_opt_in_enabled") == "on" and blackbook_enabled
    questions = []
    allowed_types = {"text", "textarea", "choice", "rating"}
    for index in range(1, 9):
        label = clean_text(request.form.get(f"survey_q{index}_text"), 300)
        if not label:
            continue
        qtype = clean_text(request.form.get(f"survey_q{index}_type"), 30).lower()
        if qtype not in allowed_types:
            qtype = "text"
        options = []
        if qtype == "choice":
            raw_options = str(request.form.get(f"survey_q{index}_options") or "")
            options = [clean_text(x, 120) for x in re.split(r"[\r\n,]+", raw_options) if clean_text(x, 120)][:10]
            if len(options) < 2:
                qtype = "text"
                options = []
        questions.append({
            "id": f"q{index}", "question": label, "type": qtype,
            "required": request.form.get(f"survey_q{index}_required") == "on",
            "options": options,
        })
    if survey_enabled and not questions:
        questions = [{"id": "q1", "question": "Was this article useful?", "type": "rating", "required": True, "options": []}]
    consent_text = clean_text(request.form.get("consent_text"), 1200)
    if mailchimp_opt_in and not consent_text:
        consent_text = f"I agree to receive relevant email updates from {current_user.organization_name or 'this organization'}. I can unsubscribe at any time."
    engagement = {
        "email_capture_enabled": email_enabled,
        "email_title": clean_text(request.form.get("email_title"), 180) or "Stay informed",
        "email_description": clean_text(request.form.get("email_description"), 800) or "Receive relevant updates related to this topic.",
        "ask_name": request.form.get("ask_name") == "on",
        "survey_enabled": survey_enabled,
        "survey_title": clean_text(request.form.get("survey_title"), 180) or "Quick survey",
        "survey_description": clean_text(request.form.get("survey_description"), 800) or "Tell us what you thought about this article.",
        "survey_questions": questions,
        "blackbook_sync_enabled": blackbook_enabled,
        "mailchimp_opt_in_enabled": mailchimp_opt_in,
        "consent_text": consent_text,
        "button_label": clean_text(request.form.get("button_label"), 80) or ("Send response" if survey_enabled else "Subscribe"),
        "updated_at": now(), "updated_by": ObjectId(current_user.id),
    }
    articles.update_one({"_id": article["_id"]}, {"$set": {"engagement": engagement, "updated_at": now()}})
    if blackbook_enabled and not blackbook_connector_status(article.get("organization_id")).get("configured"):
        flash("Audience capture saved, but BlackBook is not configured for this organization yet. Responses will remain safely in NJS until the connector is configured.", "warning")
    else:
        flash("Audience capture settings saved.", "success")
    return redirect(url_for("article_detail", article_id=article_id) + "#audience-capture")


@app.get("/articles/<article_id>/responses.csv")
@login_required
def article_responses_export(article_id):
    article = owned_article(article_id)
    if not article:
        abort(404)
    rows = list(article_submissions.find({"article_id": article["_id"]}).sort("created_at", -1).limit(10000))
    from io import StringIO
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["submitted_at", "name", "email", "marketing_consent", "survey", "domain", "path", "blackbook_status", "blackbook_person_id"])
    for row in rows:
        survey_text = " | ".join(f"{x.get('question','')}: {x.get('answer','')}" for x in (row.get("survey") or []))
        writer.writerow([
            row.get("created_at").isoformat() if hasattr(row.get("created_at"), "isoformat") else row.get("created_at"),
            row.get("name") or "", row.get("email") or "", bool((row.get("marketing_consent") or {}).get("granted")),
            survey_text, row.get("domain") or "", row.get("path") or "", (row.get("blackbook") or {}).get("status") or "", (row.get("blackbook") or {}).get("person_id") or "",
        ])
    response = Response(buf.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="article-{article_id}-responses.csv"'
    return response


@app.post("/articles/<article_id>/submissions/<submission_id>/retry-blackbook")
@login_required
def article_submission_retry_blackbook(article_id, submission_id):
    article = owned_article(article_id)
    if not article:
        abort(404)
    try:
        sid = ObjectId(submission_id)
    except Exception:
        abort(404)
    row = article_submissions.find_one({"_id": sid, "article_id": article["_id"]})
    if not row:
        abort(404)
    article_submissions.update_one({"_id": sid}, {"$set": {"blackbook.status": "queued", "blackbook.error": None, "blackbook.updated_at": now()}})
    sync_article_submission_to_blackbook_task.delay(str(sid))
    flash("BlackBook sync queued again.", "success")
    return redirect(url_for("article_detail", article_id=article_id) + "#audience-responses")


@app.post("/articles/<article_id>/notes")
@login_required
def article_save_notes(article_id):
    article = owned_article(article_id)
    if not article:
        abort(404)
    notes = clean_text(request.form.get("notes"), 12000)
    version_key = request.form.get("version") or "current"
    if version_key == "current":
        articles.update_one({"_id": article["_id"]}, {"$set": {
            "review_notes": notes, "review_notes_revision": int(article.get("revision") or 1),
            "review_notes_updated_at": now(), "updated_at": now(),
        }})
    else:
        try:
            version_oid = ObjectId(version_key)
        except Exception:
            abort(400)
        result = article_versions.update_one({"_id": version_oid, "article_id": article["_id"]}, {"$set": {"review_notes": notes, "review_notes_updated_at": now()}})
        if not result.matched_count:
            abort(404)
    flash("Review notes saved for this article revision.", "success")
    return redirect(request.form.get("return_to") or url_for("article_detail", article_id=article_id, version=version_key))


@app.post("/articles/<article_id>/request-changes")
@login_required
def article_request_changes(article_id):
    article = owned_article(article_id)
    if not article:
        abort(404)
    notes = clean_text(request.form.get("notes"), 12000)
    if not notes:
        flash("Add review notes before requesting AI changes.", "warning")
        return redirect(request.form.get("return_to") or url_for("article_detail", article_id=article_id))
    version_key = request.form.get("version") or "current"
    version_oid = None
    base_revision = int(article.get("revision") or 1)
    if version_key != "current":
        try:
            version_oid = ObjectId(version_key)
        except Exception:
            abort(400)
        version = article_versions.find_one({"_id": version_oid, "article_id": article["_id"]})
        if not version:
            abort(404)
        base_revision = int(version.get("revision") or base_revision)
        article_versions.update_one({"_id": version_oid}, {"$set": {"review_notes": notes, "review_notes_updated_at": now()}})
    else:
        articles.update_one({"_id": article["_id"]}, {"$set": {"review_notes": notes, "review_notes_revision": base_revision, "review_notes_updated_at": now()}})
    job = {
        "user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id,
        "article_id": article["_id"], "version_id": version_oid, "base_revision": base_revision,
        "notes": notes, "status": "queued", "created_at": now(), "updated_at": now(),
    }
    job["_id"] = article_change_jobs.insert_one(job).inserted_id
    result = apply_article_review_notes_task.delay(str(job["_id"]))
    article_change_jobs.update_one({"_id": job["_id"]}, {"$set": {"celery_task_id": result.id}})
    articles.update_one({"_id": article["_id"]}, {"$set": {"review_status": "changes_queued", "status": "review", "updated_at": now()}})
    flash(f"AI changes queued from article revision {base_revision}. The reviewed version remains preserved.", "success")
    return redirect(request.form.get("return_to") or url_for("article_detail", article_id=article_id, version=version_key))


@app.post("/articles/<article_id>/versions/<version_id>/restore")
@login_required
def article_restore_version(article_id, version_id):
    article = owned_article(article_id)
    if not article:
        abort(404)
    try:
        version = article_versions.find_one({"_id": ObjectId(version_id), "article_id": article["_id"]})
    except Exception:
        version = None
    if not version:
        abort(404)
    if article.get("content"):
        snapshot = {
            "article_id": article["_id"], "revision": int(article.get("revision") or 1),
            "reason": "Before article revision restore", "content": article.get("content") or "",
            "metadata": article.get("metadata") or {}, "quality": article.get("quality") or {},
            "editorial_plan": article.get("editorial_plan") or {}, "review_notes": article.get("review_notes") or "",
            "review_notes_updated_at": article.get("review_notes_updated_at"), "review_status": article.get("review_status"),
            "publication": article.get("publication") or {}, "published": bool(article.get("published")), "created_at": now(),
        }
        article_versions.insert_one(snapshot)
    revision = int(article.get("revision") or 1) + 1
    articles.update_one({"_id": article["_id"]}, {"$set": {
        "content": version.get("content") or "", "metadata": version.get("metadata") or {},
        "quality": version.get("quality") or {}, "editorial_plan": version.get("editorial_plan") or article.get("editorial_plan") or {},
        "revision": revision, "review_status": "pending_review", "status": "review",
        "review_notes": "", "review_notes_revision": revision, "updated_at": now(),
    }})
    flash("Previous article revision restored as a new unsigned revision.", "success")
    return redirect(url_for("article_detail", article_id=article_id))


@app.post("/articles/<article_id>/sign")
@login_required
def article_sign(article_id):
    article = owned_article(article_id)
    if not article:
        abort(404)
    user_doc = users.find_one({"_id": ObjectId(current_user.id)}) or {}
    if not author_profile_complete(user_doc):
        flash("Complete your author profile with a full name and picture before signing an article.", "warning")
        return redirect(url_for("author_profile_settings", next=url_for("article_detail", article_id=article_id)))
    if not can_sign_article(article, user_doc):
        abort(403)
    if article_change_jobs.count_documents({"article_id": article["_id"], "status": {"$in": ["queued", "processing", "retrying"]}}):
        flash("Wait for the requested article changes to finish before signing the current revision.", "warning")
        return redirect(url_for("article_detail", article_id=article_id))
    revision = int(article.get("revision") or 1)
    if article.get("review_status") == "signed" and int(article.get("published_revision") or 0) == revision:
        flash(f"Revision {revision} is already signed and public.", "info")
        return redirect(url_for("article_detail", article_id=article_id))
    profile = user_doc.get("author_profile") or {}
    signed_at = now()
    signature_id = secrets.token_urlsafe(16)
    content_hash = hashlib.sha256((article.get("content") or "").encode("utf-8")).hexdigest()
    material = "|".join([
        str(article["_id"]), str(revision), str(current_user.id), str(current_user.syntal_user_id or ""),
        signed_at.isoformat(), content_hash, signature_id,
    ])
    checksum = hashlib.sha256(material.encode("utf-8")).hexdigest()
    publication = {
        "revision": revision,
        "signed_at": signed_at,
        "signed_by_user_id": ObjectId(current_user.id),
        "signed_by_syntal_user_id": current_user.syntal_user_id,
        "signed_by_syntal_org_id": current_user.syntal_org_id,
        "organization_name": current_user.organization_name,
        "signature_id": signature_id,
        "approval_checksum_sha256": checksum,
        "content_sha256": content_hash,
        "author": {
            "full_name": clean_text(profile.get("full_name"), 180),
            "image_url": str(profile.get("image_url") or "").strip(),
        },
    }
    result = articles.update_one({"_id": article["_id"], "revision": revision, "$or": [{"review_status": {"$ne": "signed"}}, {"published_revision": {"$ne": revision}}]}, {"$set": {
        "review_status": "signed", "status": "published", "published": True,
        "published_at": signed_at, "published_revision": revision,
        "published_content": article.get("content") or "", "published_metadata": article.get("metadata") or {},
        "published_quality": article.get("quality") or {}, "publication": publication, "updated_at": now(),
    }})
    if not result.modified_count:
        flash("The article changed or was signed by another reviewer before this approval completed. Refresh and review the current revision.", "warning")
        return redirect(url_for("article_detail", article_id=article_id))
    flash(f"Revision {revision} signed by {publication['author']['full_name']} and published.", "success")
    return redirect(url_for("article_detail", article_id=article_id))


@app.post("/api/public/articles/<article_id>/respond")
@csrf.exempt
def public_article_respond(article_id):
    try:
        article = articles.find_one({"_id": ObjectId(article_id)})
    except Exception:
        article = None
    if not article_is_public(article) or not article_engagement_enabled(article):
        abort(404)
    engagement = article.get("engagement") or {}
    return_path = str(request.form.get("return_path") or f"/public/articles/{article_id}").strip()
    if not return_path.startswith("/") or article_id not in return_path:
        return_path = f"/public/articles/{article_id}"
    if str(request.form.get("company_website") or "").strip():
        return redirect(return_path + "?submitted=1#audience-response", code=303)
    if not _valid_capture_token(article, request.form.get("capture_token")):
        return redirect(return_path + "?submitted=expired#audience-response", code=303)

    forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    ip_hash = analytics_hash(forwarded or request.remote_addr or "")
    ten_minutes_ago = now() - timedelta(minutes=10)
    recent_count = article_submissions.count_documents({"article_id": article["_id"], "ip_hash": ip_hash, "created_at": {"$gte": ten_minutes_ago}})
    if recent_count >= Config.AUDIENCE_CAPTURE_MAX_SUBMISSIONS_PER_10_MIN:
        return redirect(return_path + "?submitted=rate#audience-response", code=303)

    email = str(request.form.get("email") or "").strip().lower()
    if engagement.get("email_capture_enabled"):
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 320:
            return redirect(return_path + "?submitted=invalid#audience-response", code=303)
    else:
        email = ""
    name = clean_text(request.form.get("name"), 180) if engagement.get("ask_name") else ""

    survey_answers = []
    if engagement.get("survey_enabled"):
        for question in (engagement.get("survey_questions") or [])[:8]:
            qid = str(question.get("id") or "")
            if not qid:
                continue
            raw = request.form.get(f"survey_{qid}")
            answer = clean_text(raw, 2000)
            qtype = question.get("type") or "text"
            if qtype == "rating" and answer not in {"1", "2", "3", "4", "5"}:
                answer = ""
            if qtype == "choice" and answer not in set(question.get("options") or []):
                answer = ""
            if question.get("required") and not answer:
                return redirect(return_path + "?submitted=invalid#audience-response", code=303)
            if answer:
                survey_answers.append({"id": qid, "question": question.get("question") or qid, "type": qtype, "answer": answer})

    consent_granted = request.form.get("marketing_consent") == "on"
    if engagement.get("mailchimp_opt_in_enabled") and not consent_granted:
        return redirect(return_path + "?submitted=consent#audience-response", code=303)
    marketing_opt_in = bool(consent_granted and engagement.get("mailchimp_opt_in_enabled"))
    consent = {
        "granted": marketing_opt_in,
        "text": (engagement.get("consent_text") or "") if marketing_opt_in else "",
        "captured_at": now() if marketing_opt_in else None,
    }
    visitor_token = str(request.cookies.get("njs_vid") or "").strip()
    submission_public_id = "sub_" + secrets.token_urlsafe(14)
    host = (request.host.split(":", 1)[0] or "").lower()
    source_url = f"https://{request.host}{return_path}"
    utm = {key: clean_text(request.form.get(key), 300) for key in ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"] if clean_text(request.form.get(key), 300)}
    connector = blackbook_connector_status(article.get("organization_id"))
    sync_requested = bool(engagement.get("blackbook_sync_enabled") and email)
    blackbook_state = "queued" if sync_requested and connector.get("configured") else ("not_configured" if sync_requested else "disabled")
    submission_experiment_ctx = experiment_context_for_public("article", article["_id"], article, request)
    doc = {
        "submission_id": submission_public_id,
        "article_id": article["_id"], "campaign_id": article.get("campaign_id"),
        "user_id": article.get("user_id"), "organization_id": article.get("organization_id"), "syntal_org_id": _article_syntal_org_id(article),
        "name": name, "email": email, "email_normalized": email,
        "survey": survey_answers, "marketing_consent": consent,
        "mailchimp_sync_requested": bool(consent.get("granted") and engagement.get("mailchimp_opt_in_enabled")),
        "domain": host, "path": return_path, "source_url": source_url,
        "original_referrer": clean_text(request.form.get("original_referrer"), 1500), "utm": utm,
        "visitor_hash": analytics_hash(visitor_token) if visitor_token else "", "ip_hash": ip_hash,
        "experiment_id": (submission_experiment_ctx or {}).get("experiment_id"), "variant_id": (submission_experiment_ctx or {}).get("variant_id") or "",
        "user_agent": clean_text(request.headers.get("User-Agent"), 500),
        "blackbook": {"requested": sync_requested, "status": blackbook_state, "updated_at": now()},
        "created_at": now(), "updated_at": now(),
    }
    sid = article_submissions.insert_one(doc).inserted_id
    try:
        record_conversion_from_request(
            request, owner_doc=article, content_type="article", content_id=article["_id"],
            event_type="lead", label="Audience response", conversion_name="article_response",
            experiment_id=(submission_experiment_ctx or {}).get("experiment_id"), variant_id=(submission_experiment_ctx or {}).get("variant_id"),
        )
    except Exception:
        app.logger.exception("Unable to record article response attribution")
    if blackbook_state == "queued":
        try:
            task = sync_article_submission_to_blackbook_task.delay(str(sid))
            article_submissions.update_one({"_id": sid}, {"$set": {"blackbook.celery_task_id": task.id}})
        except Exception as exc:
            article_submissions.update_one({"_id": sid}, {"$set": {"blackbook.status": "failed", "blackbook.error": str(exc)[:1200], "blackbook.updated_at": now()}})
    return redirect(return_path + "?submitted=1#audience-response", code=303)


@app.route("/public/articles/<article_id>")
def public_article(article_id):
    try:
        article = articles.find_one({"_id": ObjectId(article_id)})
    except Exception:
        article = None
    public_doc = published_article_view(article)
    if not public_doc:
        abort(404)
    campaign = campaigns.find_one({"_id": article.get("campaign_id")})
    hook = hooks.find_one({"_id": article.get("hook_id")}) if article.get("hook_id") else None
    host = (request.host.split(":", 1)[0] or "").lower()
    experiment_ctx = experiment_context_for_public("article", article["_id"], article, request)
    if experiment_ctx:
        public_doc = _experiment_snapshot_article(public_doc, experiment_ctx)
        public_doc = article_variant_view(public_doc, experiment_ctx.get("variant"))
    event_id, visitor_token, _ = record_view(
        request, owner_doc=article, content_type="article", content_id=article["_id"],
        campaign_id=article.get("campaign_id"), domain=host, path=request.path, internal_view=False,
        experiment_id=(experiment_ctx or {}).get("experiment_id"), variant_id=(experiment_ctx or {}).get("variant_id"),
    )
    response = make_response(render_template(
        "public_article.html", article=public_doc, campaign=campaign, hook=hook, analytics_event_id=event_id,
        experiment_variant=(experiment_ctx or {}).get("variant"),
        audience_capture_token=_capture_token(article) if article_engagement_enabled(article) else None,
    ))
    if visitor_token:
        response.set_cookie("njs_vid", visitor_token, max_age=31536000, secure=request.is_secure, httponly=False, samesite="Lax")
    _set_experiment_cookie(response, experiment_ctx)
    return response


def owned_social_post(post_id):
    try:
        oid = ObjectId(post_id)
    except Exception:
        return None
    return social_media_posts.find_one({"_id": oid, "$or": ownership_or()})


def owned_newsletter_schedule(schedule_id):
    try:
        oid = ObjectId(schedule_id)
    except Exception:
        return None
    return newsletter_schedules.find_one({"_id": oid, "$or": ownership_or()})


def owned_newsletter_edition(edition_id):
    try:
        oid = ObjectId(edition_id)
    except Exception:
        return None
    return newsletter_editions.find_one({"_id": oid, "$or": ownership_or()})


def _selected_social_campaign():
    campaign_id = request.args.get("campaign_id")
    if campaign_id == "all":
        return None
    selected = owned_campaign(campaign_id) if campaign_id else None
    if not selected:
        selected = campaigns.find_one({"status": "active", "$or": ownership_or()}, sort=[("updated_at", -1)])
    return selected


def _campaign_rows():
    return list(campaigns.find({"$or": ownership_or()}).sort("updated_at", -1).limit(100))


def _social_timezone(campaign=None):
    value = ((campaign or {}).get("social_calendar") or {}).get("timezone") or "Europe/Skopje"
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError:
        return timezone.utc


@app.route("/social")
@login_required
def social_workspace():
    args = {k: v for k, v in request.args.items() if v}
    return redirect(url_for("social_calendar_view", **args))


@app.get("/social/calendar")
@login_required
def social_calendar_view():
    selected_campaign = _selected_social_campaign()
    tz = _social_timezone(selected_campaign)
    week_raw = request.args.get("week")
    try:
        anchor = datetime.strptime(week_raw, "%Y-%m-%d").date() if week_raw else now().astimezone(tz).date()
    except ValueError:
        anchor = now().astimezone(tz).date()
    week_start_date = anchor - timedelta(days=anchor.weekday())
    week_start_local = datetime(week_start_date.year, week_start_date.month, week_start_date.day, tzinfo=tz)
    week_end_local = week_start_local + timedelta(days=7)
    week_start = week_start_local.astimezone(timezone.utc)
    week_end = week_end_local.astimezone(timezone.utc)

    post_query = {"$or": ownership_or(), "scheduled_at": {"$gte": week_start, "$lt": week_end}, "status": {"$ne": "archived"}}
    if selected_campaign:
        post_query["campaign_id"] = selected_campaign["_id"]
    posts = list(social_media_posts.find(post_query).sort("scheduled_at", 1))
    editions = list(newsletter_editions.find({
        "$or": ownership_or(), "due_at": {"$gte": week_start, "$lt": week_end}, "status": {"$ne": "archived"}
    }).sort("due_at", 1))
    days = []
    for offset in range(7):
        day_date = week_start_date + timedelta(days=offset)
        day_posts = []
        for post in posts:
            scheduled_at = post.get("scheduled_at")
            if scheduled_at and scheduled_at.astimezone(tz).date() == day_date:
                item = dict(post)
                item["scheduled_local"] = scheduled_at.astimezone(tz)
                day_posts.append(item)
        day_editions = []
        for edition in editions:
            due_at = edition.get("due_at")
            if due_at and due_at.astimezone(tz).date() == day_date:
                item = dict(edition)
                item["due_local"] = due_at.astimezone(tz)
                day_editions.append(item)
        days.append({"date": day_date, "posts": day_posts, "editions": day_editions})
    prev_week = (week_start_date - timedelta(days=7)).isoformat()
    next_week = (week_start_date + timedelta(days=7)).isoformat()
    unscheduled_query = {"$and": [
        {"$or": ownership_or()},
        {"status": "draft"},
        {"$or": [{"scheduled_at": None}, {"scheduled_at": {"$exists": False}}]},
    ]}
    if selected_campaign:
        unscheduled_query["$and"].append({"campaign_id": selected_campaign["_id"]})
    unscheduled_count = social_media_posts.count_documents(unscheduled_query)
    return render_template(
        "social_calendar.html", campaigns=_campaign_rows(), selected_campaign=selected_campaign,
        days=days, week_start=week_start_date, prev_week=prev_week, next_week=next_week,
        timezone_name=str(tz), unscheduled_count=unscheduled_count, today=now().astimezone(tz).date(),
    )


@app.get("/social/queue")
@login_required
def social_queue_view():
    selected_campaign = _selected_social_campaign()
    query = {"$or": ownership_or(), "status": {"$in": ["draft", "scheduled"]}}
    if selected_campaign:
        query["campaign_id"] = selected_campaign["_id"]
    rows = list(social_media_posts.find(query).sort([("scheduled_at", 1), ("updated_at", -1)]).limit(300))
    tz = _social_timezone(selected_campaign)
    for row in rows:
        if row.get("scheduled_at"):
            row["scheduled_local"] = row["scheduled_at"].astimezone(tz)
    article_ids = list({row.get("article_id") for row in rows if row.get("article_id")})
    article_map = {a["_id"]: a for a in articles.find({"_id": {"$in": article_ids}})} if article_ids else {}
    unscheduled = [row for row in rows if not row.get("scheduled_at")]
    scheduled = [row for row in rows if row.get("scheduled_at")]
    return render_template(
        "social_queue.html", campaigns=_campaign_rows(), selected_campaign=selected_campaign,
        unscheduled=unscheduled, scheduled=scheduled, article_map=article_map,
        timezone_name=str(tz),
    )


@app.get("/social/posts")
@login_required
def social_posts_view():
    selected_campaign = _selected_social_campaign()
    query = {"$or": ownership_or(), "status": {"$ne": "archived"}}
    if selected_campaign:
        query["campaign_id"] = selected_campaign["_id"]
    post_rows = list(social_media_posts.find(query).sort("updated_at", -1).limit(300))
    article_ids = list({p.get("article_id") for p in post_rows if p.get("article_id")})
    article_map = {a["_id"]: a for a in articles.find({"_id": {"$in": article_ids}})} if article_ids else {}
    job_query = {"$or": ownership_or()}
    if selected_campaign:
        job_query["campaign_id"] = selected_campaign["_id"]
    jobs = list(social_generation_jobs.find(job_query).sort("created_at", -1).limit(30))
    return render_template(
        "social_posts.html", campaigns=_campaign_rows(), selected_campaign=selected_campaign,
        posts=post_rows, jobs=jobs, article_map=article_map,
        openai_images_configured=bool(Config.OPENAI_API_KEY),
    )


@app.get("/social/settings")
@login_required
def social_settings_view():
    selected_campaign = _selected_social_campaign()
    return render_template(
        "social_settings.html", campaigns=_campaign_rows(), selected_campaign=selected_campaign,
        social_settings=(selected_campaign or {}).get("social_calendar") or {},
        social_platforms=["linkedin", "x", "facebook", "instagram", "threads", "bluesky"],
        weekday_options=[(0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun")],
        openai_images_configured=bool(Config.OPENAI_API_KEY),
    )


@app.post("/social/settings/<campaign_id>")
@login_required
def social_settings_save(campaign_id):
    campaign = owned_campaign(campaign_id)
    if not campaign:
        abort(404)
    allowed = ["linkedin", "x", "facebook", "instagram", "threads", "bluesky"]
    platforms = [p for p in request.form.getlist("platforms") if p in allowed] or ["linkedin", "x"]
    weekdays = []
    for raw in request.form.getlist("posting_weekdays"):
        try:
            day = int(raw)
        except ValueError:
            continue
        if 0 <= day <= 6 and day not in weekdays:
            weekdays.append(day)
    if not weekdays:
        weekdays = [0, 1, 2, 3, 4]
    try:
        delay = max(0, min(10080, int(request.form.get("schedule_delay_minutes", 15))))
        stagger = max(0, min(1440, int(request.form.get("platform_stagger_minutes", 5))))
        ratio = max(0.28, min(0.46, float(request.form.get("image_text_area_ratio", 0.36))))
    except (TypeError, ValueError):
        flash("Scheduling or image layout values are invalid.", "danger")
        return redirect(url_for("social_settings_view", campaign_id=campaign_id))
    settings = {
        "enabled": request.form.get("enabled") == "on",
        "platforms": platforms,
        "auto_generate_newsjacking": request.form.get("auto_generate_newsjacking") == "on",
        "image_enabled": request.form.get("image_enabled") == "on",
        "auto_schedule": request.form.get("auto_schedule") == "on",
        "posting_weekdays": weekdays,
        "timezone": clean_text(request.form.get("timezone") or "Europe/Skopje", 80),
        "default_post_time": clean_text(request.form.get("default_post_time") or "09:00", 20),
        "schedule_delay_minutes": delay,
        "platform_stagger_minutes": stagger,
        "brand_voice": clean_text(request.form.get("brand_voice") or "Clear, useful, confident, and factual.", 1200),
        "image_style": clean_text(request.form.get("image_style") or "Premium contemporary editorial photography", 1600),
        "image_text_enabled": request.form.get("image_text_enabled") == "on",
        "image_footer": clean_text(request.form.get("image_footer") or campaign.get("title") or "", 80),
        "image_accent_color": clean_text(request.form.get("image_accent_color") or "#149FE8", 20),
        "image_text_area_ratio": ratio,
        "image_headline_max_chars": 105,
    }
    campaigns.update_one({"_id": campaign["_id"]}, {"$set": {"social_calendar": settings, "updated_at": now()}})
    flash("Social automation settings saved.", "success")
    return redirect(url_for("social_settings_view", campaign_id=campaign_id))


@app.post("/social/articles/<article_id>/generate")
@login_required
def social_generate_article(article_id):
    article = owned_article(article_id)
    if not article:
        abort(404)
    campaign = campaigns.find_one({"_id": article.get("campaign_id")})
    allowed = ["linkedin", "x", "facebook", "instagram", "threads", "bluesky"]
    selected = [p for p in request.form.getlist("platforms") if p in allowed]
    if not selected:
        selected = [p for p in ((campaign or {}).get("social_calendar") or {}).get("platforms", []) if p in allowed]
    if not selected:
        selected = ["linkedin", "x"]
    result = generate_social_media_bundle_task.delay(
        str(article["_id"]), platforms=selected, source_type="manual",
        regenerate_text=request.form.get("regenerate_text") == "on",
        regenerate_image=request.form.get("regenerate_image") == "on",
    )
    flash("Social bundle queued. Copy and image generation run in the worker.", "success")
    if request.form.get("from_article") == "1":
        return redirect(url_for("article_detail", article_id=article_id))
    return redirect(url_for("social_posts_view", campaign_id=str(article.get("campaign_id")), job=result.id))


@app.post("/social/posts/<post_id>/save")
@login_required
def social_post_save(post_id):
    post = owned_social_post(post_id)
    if not post:
        abort(404)
    text = str(request.form.get("text") or "").strip()
    platform = post.get("platform") or "linkedin"
    limits = {"linkedin": 3000, "x": 280, "facebook": 5000, "instagram": 2200, "threads": 500, "bluesky": 300}
    if not text:
        flash("Post text cannot be empty.", "danger")
    elif len(text) > limits.get(platform, 5000):
        flash(f"Post exceeds the {limits.get(platform)} character limit for {platform}.", "danger")
    else:
        social_media_posts.update_one({"_id": post["_id"]}, {"$set": {
            "text": text, "char_count": len(text), "is_manually_edited": True, "updated_at": now(),
        }})
        flash("Social post saved.", "success")
    return redirect(url_for("social_posts_view", campaign_id=str(post.get("campaign_id"))))


@app.post("/social/posts/<post_id>/schedule")
@login_required
def social_post_schedule(post_id):
    post = owned_social_post(post_id)
    if not post:
        abort(404)
    campaign = campaigns.find_one({"_id": post.get("campaign_id")}) or {}
    tz = _social_timezone(campaign)
    raw = str(request.form.get("scheduled_at") or "").strip()
    if not raw:
        social_media_posts.update_one({"_id": post["_id"]}, {"$set": {"scheduled_at": None, "status": "draft", "updated_at": now()}})
        flash("Post returned to the unscheduled queue.", "success")
    else:
        try:
            local = datetime.strptime(raw, "%Y-%m-%dT%H:%M").replace(tzinfo=tz)
            scheduled_at = local.astimezone(timezone.utc)
        except ValueError:
            flash("Enter a valid schedule date and time.", "danger")
            return redirect(url_for("social_queue_view", campaign_id=str(post.get("campaign_id"))))
        social_media_posts.update_one({"_id": post["_id"]}, {"$set": {"scheduled_at": scheduled_at, "status": "scheduled", "updated_at": now()}})
        flash("Post schedule updated.", "success")
    return redirect(url_for("social_queue_view", campaign_id=str(post.get("campaign_id"))))


@app.post("/social/posts/<post_id>/archive")
@login_required
def social_post_archive(post_id):
    post = owned_social_post(post_id)
    if not post:
        abort(404)
    social_media_posts.update_one({"_id": post["_id"]}, {"$set": {"status": "archived", "archived_at": now(), "updated_at": now()}})
    flash("Social post archived.", "success")
    return redirect(url_for("social_posts_view", campaign_id=str(post.get("campaign_id"))))


@app.get("/api/social/jobs/<job_id>")
@login_required
def social_job_status(job_id):
    try:
        oid = ObjectId(job_id)
    except Exception:
        abort(404)
    job = social_generation_jobs.find_one({"_id": oid, "$or": ownership_or()})
    if not job:
        abort(404)
    return jsonify({
        "id": str(job["_id"]), "status": job.get("status"), "error": job.get("error"),
        "result": job.get("result"), "created_at": job.get("created_at"), "completed_at": job.get("completed_at"),
    })


def _newsletter_schedule_form(existing=None):
    existing = existing or {}
    owned_ids = {str(row["_id"]): row["_id"] for row in campaigns.find({"$or": ownership_or()}, {"_id": 1})}
    campaign_ids = [owned_ids[value] for value in request.form.getlist("campaign_ids") if value in owned_ids]
    weekdays = []
    for raw in request.form.getlist("weekdays"):
        try:
            value = int(raw)
        except ValueError:
            continue
        if 0 <= value <= 6 and value not in weekdays:
            weekdays.append(value)
    try:
        lead = max(0, min(1440, int(request.form.get("generation_lead_minutes", 90))))
        lookback = max(1, min(30, int(request.form.get("lookback_days", 7))))
        min_stories = max(1, min(20, int(request.form.get("min_stories", 3))))
        max_stories = max(min_stories, min(25, int(request.form.get("max_stories", 7))))
    except (TypeError, ValueError) as exc:
        raise ValueError("Newsletter numeric settings are invalid") from exc
    name = clean_text(request.form.get("name") or existing.get("name") or "Weekly briefing", 120)
    if not name:
        raise ValueError("Newsletter name is required")
    return {
        "name": name,
        "enabled": request.form.get("enabled") == "on",
        "campaign_ids": campaign_ids,
        "timezone": clean_text(request.form.get("timezone") or existing.get("timezone") or "Europe/Skopje", 80),
        "weekdays": weekdays or [0],
        "ready_time": clean_text(request.form.get("ready_time") or existing.get("ready_time") or "07:30", 20),
        "generation_lead_minutes": lead,
        "lookback_days": lookback,
        "min_stories": min_stories,
        "max_stories": max_stories,
        "editorial_voice": clean_text(request.form.get("editorial_voice") or "Concise, useful, factual, and editorial.", 1600),
        "audience_note": clean_text(request.form.get("audience_note") or "Busy readers who want the most important developments and why they matter.", 1200),
        "subject_style": clean_text(request.form.get("subject_style") or "Specific and informative; avoid clickbait.", 900),
        "sender_name": clean_text(request.form.get("sender_name") or name, 120),
        "cta_label": clean_text(request.form.get("cta_label") or "Read the full story", 60),
        "cta_url": clean_text(request.form.get("cta_url") or "", 1000),
        "visual_design_prompt": clean_text(request.form.get("visual_design_prompt") if "visual_design_prompt" in request.form else existing.get("visual_design_prompt") or "", 12000),
        "updated_at": now(),
    }


@app.route("/social/newsletters", methods=["GET", "POST"])
@login_required
def newsletter_workspace():
    """Backward-compatible entry point retained for v3.4 links/bookmarks."""
    if request.method == "POST":
        try:
            payload = _newsletter_schedule_form()
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("newsletter_create"))
        payload.update({"user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id, "created_at": now()})
        newsletter_schedules.insert_one(payload)
        flash("Newsletter schedule created.", "success")
        return redirect(url_for("newsletter_manage"))
    return redirect(url_for("newsletter_manage"))


def _newsletter_workspace_data():
    schedules = list(newsletter_schedules.find({"$or": ownership_or()}).sort("updated_at", -1))
    editions = list(newsletter_editions.find({"$or": ownership_or()}).sort("due_at", -1).limit(200))
    campaign_rows = _campaign_rows()
    return schedules, editions, campaign_rows


def _newsletter_blackbook_state(schedule=None, *, people_query=""):
    connector = blackbook_connector_status(current_user.organization_id)
    state = {
        "connector": connector, "connected": False, "error": "", "context": {}, "people": [],
        "segments": [], "campaigns": [], "preview": {}, "engagement": {}, "interests": [],
    }
    if not connector.get("configured"):
        state["error"] = "Connect BlackBook to this Syntal organization to use CRM audience intelligence and Mailchimp delivery."
        return state
    try:
        context = blackbook_marketing_context(current_user.organization_id)
        state["context"] = context
        state["segments"] = context.get("segments") or []
        state["campaigns"] = context.get("recent_campaigns") or []
        people = blackbook_marketing_people(current_user.organization_id, query=people_query, limit=30, eligible_only=False)
        state["people"] = people.get("people") or []
        state["connected"] = True
    except Exception as exc:
        state["error"] = clean_text(str(exc), 1000)
        return state

    # Engagement Intelligence is additive. A temporary problem with its richer
    # endpoints must not hide the ordinary BlackBook segment/person controls.
    try:
        state["engagement"] = blackbook_engagement_overview(current_user.organization_id) or {}
        interest_result = blackbook_engagement_interests(current_user.organization_id) or {}
        state["interests"] = interest_result.get("interests") or []
    except Exception as exc:
        state["engagement_error"] = clean_text(str(exc), 800)

    if schedule:
        try:
            state["preview"] = blackbook_audience_preview(
                current_user.organization_id,
                segment_ids=schedule.get("blackbook_segment_ids") or [],
                person_ids=schedule.get("blackbook_person_ids") or [],
                include_all_eligible=bool(schedule.get("blackbook_include_all_eligible")),
                engagement_buckets=schedule.get("blackbook_engagement_buckets") or [],
                interest_ids=schedule.get("blackbook_interest_ids") or [],
                interest_match=schedule.get("blackbook_interest_match") or "any",
            )
        except Exception as exc:
            state["preview_error"] = clean_text(str(exc), 800)
    return state


def _newsletter_workflow_stats(schedules, editions):
    return {
        "schedules": len(schedules),
        "active_schedules": sum(1 for row in schedules if row.get("enabled") and not row.get("archived_at")),
        "draft": sum(1 for row in editions if row.get("status") == "draft"),
        "ready": sum(1 for row in editions if row.get("status") == "ready"),
        "distributed": sum(1 for row in editions if row.get("status") == "distributed"),
        "failed": sum(1 for row in editions if row.get("status") == "failed" or row.get("generation_error")),
    }


@app.get("/newsletters")
@login_required
def newsletter_home():
    schedules, editions, campaign_rows = _newsletter_workspace_data()
    bb = _newsletter_blackbook_state(schedules[0] if schedules else None)
    return render_template(
        "newsletter_home.html", schedules=schedules, editions=editions[:10], campaigns=campaign_rows,
        stats=_newsletter_workflow_stats(schedules, editions), blackbook=bb,
    )


_NEWSLETTER_ENGAGEMENT_BUCKETS = {"inactive", "low", "medium", "high"}


def _newsletter_audience_criteria(source):
    getlist = source.getlist if hasattr(source, "getlist") else lambda key: source.get(key) or []
    include_all = str(source.get("include_all_eligible") or "").lower() in {"1", "true", "yes", "on"}
    segment_ids = list(dict.fromkeys(clean_text(x, 64) for x in getlist("segment_ids") if clean_text(x, 64)))[:50]
    person_ids = list(dict.fromkeys(clean_text(x, 64) for x in getlist("person_ids") if clean_text(x, 64)))[:500]
    buckets = []
    for raw in getlist("engagement_buckets"):
        value = clean_text(raw, 20).lower()
        if value in _NEWSLETTER_ENGAGEMENT_BUCKETS and value not in buckets:
            buckets.append(value)
    interest_ids = list(dict.fromkeys(clean_text(x, 64) for x in getlist("interest_ids") if clean_text(x, 64)))[:100]
    interest_match = "all" if clean_text(source.get("interest_match") or "any", 8).lower() == "all" else "any"
    if include_all:
        # "All eligible" is intentionally an exclusive shortcut; retaining hidden
        # filters here would surprise operators when they later turn it off.
        segment_ids, person_ids, buckets, interest_ids = [], [], [], []
        interest_match = "any"
    return {
        "include_all_eligible": include_all, "segment_ids": segment_ids, "person_ids": person_ids,
        "engagement_buckets": buckets, "interest_ids": interest_ids, "interest_match": interest_match,
    }


@app.route("/newsletters/audience", methods=["GET", "POST"])
@login_required
def newsletter_audience():
    schedules, editions, campaign_rows = _newsletter_workspace_data()
    schedule_id = clean_text(request.values.get("schedule") or request.values.get("schedule_id") or "", 64)
    selected = owned_newsletter_schedule(schedule_id) if schedule_id else (schedules[0] if schedules else None)
    if request.method == "POST":
        if not selected:
            flash("Choose a newsletter plan before configuring its audience.", "danger")
            return redirect(url_for("newsletter_audience"))
        criteria = _newsletter_audience_criteria(request.form)
        if not criteria["include_all_eligible"] and not any((criteria["segment_ids"], criteria["person_ids"], criteria["engagement_buckets"], criteria["interest_ids"])):
            flash("Choose at least one engagement bucket, interest, BlackBook segment, person, or all eligible contacts.", "danger")
            return redirect(url_for("newsletter_audience", schedule=str(selected["_id"])))
        try:
            preview = blackbook_audience_preview(current_user.organization_id, **criteria)
        except Exception as exc:
            flash(f"BlackBook could not validate this audience: {clean_text(str(exc), 700)}", "danger")
            return redirect(url_for("newsletter_audience", schedule=str(selected["_id"])))

        context = {}
        interest_rows = []
        try:
            context = blackbook_marketing_context(current_user.organization_id)
        except Exception:
            pass
        try:
            interest_rows = (blackbook_engagement_interests(current_user.organization_id) or {}).get("interests") or []
        except Exception:
            pass
        selected_segments = [x for x in (context.get("segments") or []) if str(x.get("id")) in set(criteria["segment_ids"])]
        selected_interests = [x for x in interest_rows if str(x.get("id")) in set(criteria["interest_ids"])]
        snapshot = {
            "audience_counts": preview.get("counts") or {},
            "average_relationship_strength": preview.get("average_relationship_strength") or 0,
            "audience_categories": (preview.get("categories") or [])[:15],
            "engagement_distribution": preview.get("engagement_distribution") or {},
            "audience_criteria": preview.get("criteria") or {},
            "selected_segments": [{"id": x.get("id"), "name": x.get("name"), "count": x.get("count")} for x in selected_segments],
            "selected_interests": [{"id": x.get("id"), "name": x.get("name"), "person_count": x.get("person_count")} for x in selected_interests],
            "selected_engagement_buckets": criteria["engagement_buckets"],
            "interest_match": criteria["interest_match"],
            "refreshed_at": now(),
        }
        newsletter_schedules.update_one({"_id": selected["_id"]}, {"$set": {
            "blackbook_segment_ids": criteria["segment_ids"],
            "blackbook_person_ids": criteria["person_ids"],
            "blackbook_include_all_eligible": criteria["include_all_eligible"],
            "blackbook_engagement_buckets": criteria["engagement_buckets"],
            "blackbook_interest_ids": criteria["interest_ids"],
            "blackbook_interest_match": criteria["interest_match"],
            "blackbook_audience_snapshot": snapshot,
            "blackbook_audience_refreshed_at": now(),
            "updated_at": now(),
        }})
        flash("Audience saved. Interest and engagement rules will be re-evaluated against BlackBook at delivery time.", "success")
        return redirect(url_for("newsletter_audience", schedule=str(selected["_id"])))
    q = clean_text(request.args.get("q") or "", 160)
    bb = _newsletter_blackbook_state(selected, people_query=q)
    return render_template(
        "newsletter_audience.html", schedules=schedules, selected_schedule=selected, blackbook=bb,
        people_query=q, editions=editions[:6], campaigns=campaign_rows,
    )


@app.post("/newsletters/audience/preview")
@login_required
def newsletter_audience_live_preview():
    criteria = _newsletter_audience_criteria(request.form)
    if not criteria["include_all_eligible"] and not any((criteria["segment_ids"], criteria["person_ids"], criteria["engagement_buckets"], criteria["interest_ids"])):
        return jsonify({"ok": True, "preview": {"counts": {"selected": 0, "eligible": 0}, "categories": [], "engagement_distribution": {}}})
    try:
        preview = blackbook_audience_preview(current_user.organization_id, **criteria)
        return jsonify({"ok": True, "preview": preview})
    except Exception as exc:
        return jsonify({"ok": False, "error": clean_text(str(exc), 700)}), 502


@app.get("/newsletters/engagement")
@login_required
def newsletter_engagement():
    bucket = clean_text(request.args.get("bucket") or "queue", 20).lower()
    if bucket not in {"queue", "failed", "inactive", "low", "medium", "high", "all"}:
        bucket = "queue"
    q = clean_text(request.args.get("q") or "", 160)
    interest = clean_text(request.args.get("interest") or "", 180)
    overview = {}; people = []; interests = []; batches = []; bridge_error = ""
    try:
        overview = blackbook_engagement_overview(current_user.organization_id)
        people_limit = max(1, min(2500, int((overview or {}).get("max_select") or 2500)))
        people_result = blackbook_engagement_people(
            current_user.organization_id, bucket=bucket, query=q, interest=interest, limit=people_limit,
        )
        interests_result = blackbook_engagement_interests(current_user.organization_id)
        batches_result = blackbook_engagement_batches(current_user.organization_id, limit=12)
        people = people_result.get("people") or []
        interests = interests_result.get("interests") or []
        batches = batches_result.get("batches") or []
    except Exception as exc:
        bridge_error = clean_text(str(exc), 1200)
    return render_template(
        "newsletter_engagement.html", bucket=bucket, q=q, interest_filter=interest,
        overview=overview, people=people, interests=interests, batches=batches,
        bridge_error=bridge_error, max_select=int((overview or {}).get("max_select") or 2500),
    )


@app.post("/newsletters/engagement/queue/refresh")
@login_required
def newsletter_engagement_refresh():
    try:
        result = blackbook_engagement_refresh_queue(current_user.organization_id)
        queue_count = result.get("queue_count") or 0
        if result.get("status") == "queued":
            flash(
                f"Mailchimp audience refresh started in BlackBook. "
                f"{queue_count} people are currently waiting for engagement analysis. "
                f"Job {result.get('job_id') or ''}.",
                "success",
            )
        else:
            pulled = ((result.get("result") or {}).get("pulled") or 0)
            flash(f"Mailchimp queue refreshed: {pulled} audience records checked; {queue_count} currently waiting for engagement analysis.", "success")
    except Exception as exc:
        flash(f"Could not refresh the Mailchimp queue: {clean_text(str(exc), 900)}", "danger")
    return redirect(url_for("newsletter_engagement", bucket="queue"))


@app.post("/newsletters/engagement/analyze")
@login_required
def newsletter_engagement_analyze():
    person_ids = []
    seen = set()
    for value in request.form.getlist("person_ids"):
        value = clean_text(value, 80)
        if value and value not in seen:
            seen.add(value); person_ids.append(value)
    return_bucket = clean_text(request.form.get("return_bucket") or "queue", 20).lower()
    if return_bucket not in {"queue", "failed"}:
        return_bucket = "queue"
    if not person_ids:
        flash("Select at least one person to analyze.", "danger")
        return redirect(url_for("newsletter_engagement", bucket=return_bucket))
    if len(person_ids) > 2500:
        flash("A maximum of 2,500 people can be analyzed in one batch.", "danger")
        return redirect(url_for("newsletter_engagement", bucket=return_bucket))
    try:
        days = max(30, min(36500, int(request.form.get("days") or 3650)))
    except ValueError:
        days = 3650
    try:
        result = blackbook_engagement_bulk_analyze(current_user.organization_id, person_ids=person_ids, days=days)
        flash(f"Engagement import + AI analysis queued for {result.get('selected') or len(person_ids)} people. Batch {result.get('batch_id') or ''} is running in BlackBook.", "success")
    except Exception as exc:
        flash(f"Could not start engagement analysis: {clean_text(str(exc), 900)}", "danger")
    return redirect(url_for("newsletter_engagement", bucket=return_bucket))


@app.get("/newsletters/learn")
@login_required
def newsletter_learn():
    schedules, editions, campaign_rows = _newsletter_workspace_data()
    bb = _newsletter_blackbook_state(schedules[0] if schedules else None)
    distributed = [row for row in editions if row.get("status") == "distributed"]
    return render_template(
        "newsletter_learn.html", schedules=schedules, editions=editions, distributed_editions=distributed[:30],
        blackbook=bb, stats=_newsletter_workflow_stats(schedules, editions), campaigns=campaign_rows,
    )


@app.route("/newsletters/create", methods=["GET", "POST"])
@login_required
def newsletter_create():
    if request.method == "POST":
        try:
            payload = _newsletter_schedule_form()
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("newsletter_create"))
        payload.update({"user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id, "created_at": now()})
        inserted = newsletter_schedules.insert_one(payload)
        flash("Newsletter plan created. Now choose its BlackBook audience.", "success")
        return redirect(url_for("newsletter_audience", schedule=str(inserted.inserted_id)))
    schedules, editions, campaign_rows = _newsletter_workspace_data()
    return render_template(
        "newsletter_create.html",
        schedules=schedules,
        editions=editions[:6],
        campaigns=campaign_rows,
        weekday_options=[(0,"Mon"),(1,"Tue"),(2,"Wed"),(3,"Thu"),(4,"Fri"),(5,"Sat"),(6,"Sun")],
    )


@app.get("/newsletters/manage")
@login_required
def newsletter_manage():
    schedules, editions, campaign_rows = _newsletter_workspace_data()
    campaign_map = {row["_id"]: row for row in campaign_rows}
    return render_template(
        "newsletter_manage.html",
        schedules=schedules,
        editions=editions,
        campaigns=campaign_rows,
        campaign_map=campaign_map,
        weekday_options=[(0,"Mon"),(1,"Tue"),(2,"Wed"),(3,"Thu"),(4,"Fri"),(5,"Sat"),(6,"Sun")],
    )


@app.get("/newsletters/view")
@login_required
def newsletter_view():
    schedules, editions, campaign_rows = _newsletter_workspace_data()
    requested_status = clean_text(request.args.get("status") or "", 32).lower()
    if requested_status in {"draft", "ready", "distributed", "archived", "generating", "failed"}:
        visible_editions = [row for row in editions if str(row.get("status") or "draft").lower() == requested_status]
    else:
        requested_status = ""
        visible_editions = editions
    stats = {
        "all": len(editions),
        "draft": sum(1 for row in editions if row.get("status") == "draft"),
        "ready": sum(1 for row in editions if row.get("status") == "ready"),
        "distributed": sum(1 for row in editions if row.get("status") == "distributed"),
    }
    schedule_map = {row["_id"]: row for row in schedules}
    return render_template(
        "newsletter_view.html",
        editions=visible_editions,
        all_editions=editions,
        schedules=schedules,
        schedule_map=schedule_map,
        stats=stats,
        status_filter=requested_status,
    )


@app.get("/newsletters/distribute")
@login_required
def newsletter_distribute():
    schedules, editions, campaign_rows = _newsletter_workspace_data()
    ready_editions = [row for row in editions if row.get("status") == "ready"]
    distributed_editions = [row for row in editions if row.get("status") == "distributed"]
    selected_id = clean_text(request.args.get("edition") or "", 64)
    selected = None
    if selected_id:
        selected = owned_newsletter_edition(selected_id)
    if not selected and ready_editions:
        selected = ready_editions[0]
    schedule_map = {row["_id"]: row for row in schedules}
    selected_schedule = schedule_map.get(selected.get("schedule_id")) if selected else None
    bb = _newsletter_blackbook_state(selected_schedule)
    return render_template(
        "newsletter_distribute.html",
        ready_editions=ready_editions,
        distributed_editions=distributed_editions[:40],
        selected=selected,
        schedule_map=schedule_map,
        selected_schedule=selected_schedule,
        blackbook=bb,
    )


@app.post("/social/newsletters/schedules/<schedule_id>/save")
@login_required
def newsletter_schedule_save(schedule_id):
    schedule = owned_newsletter_schedule(schedule_id)
    if not schedule:
        abort(404)
    try:
        payload = _newsletter_schedule_form(schedule)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("newsletter_manage"))
    newsletter_schedules.update_one({"_id": schedule["_id"]}, {"$set": payload})
    flash("Newsletter schedule updated.", "success")
    return redirect(url_for("newsletter_manage"))


@app.post("/social/newsletters/schedules/<schedule_id>/delete")
@login_required
def newsletter_schedule_delete(schedule_id):
    schedule = owned_newsletter_schedule(schedule_id)
    if not schedule:
        abort(404)
    if newsletter_editions.count_documents({"schedule_id": schedule["_id"]}) > 0:
        newsletter_schedules.update_one({"_id": schedule["_id"]}, {"$set": {"enabled": False, "archived_at": now(), "updated_at": now()}})
        flash("Newsletter schedule archived because it already has editions.", "success")
    else:
        newsletter_schedules.delete_one({"_id": schedule["_id"]})
        flash("Newsletter schedule removed.", "success")
    return redirect(url_for("newsletter_manage"))


@app.post("/social/newsletters/schedules/<schedule_id>/generate")
@login_required
def newsletter_generate_now(schedule_id):
    schedule = owned_newsletter_schedule(schedule_id)
    if not schedule:
        abort(404)
    result = generate_newsletter_edition_task.delay(str(schedule["_id"]), now().isoformat(), True)
    flash("Newsletter draft queued. NJS will select the strongest recent stories and build the edition.", "success")
    return redirect(url_for("newsletter_view", job=result.id))


@app.get("/social/newsletters/editions/<edition_id>")
@login_required
def newsletter_edition_detail(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    schedule = newsletter_schedules.find_one({"_id": edition.get("schedule_id")}) or {}
    versions = list(newsletter_edition_versions.find({"edition_id": edition["_id"]}).sort("created_at", -1).limit(20))
    design_jobs = list(newsletter_design_jobs.find({"edition_id": edition["_id"]}).sort("created_at", -1).limit(12))
    return render_template("newsletter_edition.html", edition=edition, schedule=schedule, design_versions=versions, design_jobs=design_jobs)


@app.post("/social/newsletters/editions/<edition_id>/save")
@login_required
def newsletter_edition_save(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    schedule = newsletter_schedules.find_one({"_id": edition.get("schedule_id")}) or {}
    payload = {
        "subject": clean_text(request.form.get("subject") or edition.get("subject"), 140),
        "preheader": clean_text(request.form.get("preheader") or edition.get("preheader"), 220),
        "intro": clean_text(request.form.get("intro") or edition.get("intro"), 1800),
        "stories": edition.get("stories") or [],
        "closing": clean_text(request.form.get("closing") or edition.get("closing"), 1200),
    }
    due_at = edition.get("due_at") or now()
    current_html = edition.get("html") or ""
    visual_prompt = clean_text(edition.get("visual_design_prompt") or schedule.get("visual_design_prompt") or "", 12000)
    try:
        rendered_html = compile_newsletter_designed_html(schedule, payload, due_at, visual_prompt=visual_prompt)
    except Exception as exc:
        rendered_html = compile_newsletter_html(schedule, payload, due_at)
        flash(f"Editorial copy was saved, but the visual design could not be reapplied: {clean_text(str(exc), 500)}", "warning")
    if current_html:
        newsletter_edition_versions.insert_one({
            "edition_id": edition["_id"], "schedule_id": edition.get("schedule_id"),
            "user_id": edition.get("user_id"), "organization_id": edition.get("organization_id"),
            "revision": int(edition.get("design_revision") or 1), "reason": "Before editorial edit",
            "html": current_html, "subject": edition.get("subject") or "", "preheader": edition.get("preheader") or "",
            "visual_design_prompt": edition.get("visual_design_prompt") or "", "created_at": now(),
        })
    newsletter_editions.update_one({"_id": edition["_id"]}, {
        "$set": {
            **payload,
            "html": rendered_html,
            "text": compile_newsletter_text(schedule, payload, due_at),
            "is_manually_edited": True,
            "design_revision": int(edition.get("design_revision") or 1) + 1,
            "status": "draft",
            "updated_at": now(),
        },
        "$unset": {"distribution": "", "distributed_at": "", "blackbook_mailchimp_draft": ""},
    })
    flash("Newsletter edits saved.", "success")
    return redirect(url_for("newsletter_edition_detail", edition_id=edition_id))


@app.post("/social/newsletters/editions/<edition_id>/design/ai")
@login_required
def newsletter_edition_design_ai(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    notes = clean_text(request.form.get("notes") or "", 12000)
    if not notes:
        flash("Describe the visual change you want NJS to make.", "warning")
        return redirect(url_for("newsletter_edition_detail", edition_id=edition_id) + "#visual-design")
    if not edition.get("html"):
        flash("Generate the newsletter before redesigning it.", "danger")
        return redirect(url_for("newsletter_edition_detail", edition_id=edition_id))
    base_revision = int(edition.get("design_revision") or 1)
    stamped = now()
    job = {
        "user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id,
        "edition_id": edition["_id"], "schedule_id": edition.get("schedule_id"),
        "version_id": None, "base_revision": base_revision, "notes": notes,
        "apply_to_future": request.form.get("apply_to_future") == "1",
        "status": "queued", "created_at": stamped, "updated_at": stamped,
    }
    job["_id"] = newsletter_design_jobs.insert_one(job).inserted_id
    result = apply_newsletter_visual_prompt_task.delay(str(job["_id"]))
    newsletter_design_jobs.update_one({"_id": job["_id"]}, {"$set": {"celery_task_id": result.id}})
    flash("Visual redesign queued. The current email remains available until the new design revision is complete.", "success")
    return redirect(url_for("newsletter_edition_detail", edition_id=edition_id) + "#visual-design")


@app.get("/social/newsletters/editions/<edition_id>/design/jobs/<job_id>")
@login_required
def newsletter_edition_design_job_status(edition_id, job_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    try:
        job = newsletter_design_jobs.find_one({"_id": ObjectId(job_id), "edition_id": edition["_id"]})
    except Exception:
        job = None
    if not job:
        abort(404)
    return jsonify({
        "id": str(job["_id"]), "status": job.get("status") or "queued",
        "error": job.get("error"), "result_revision": job.get("result_revision"),
        "updated_at": job.get("updated_at"),
    })


@app.post("/social/newsletters/editions/<edition_id>/design/html")
@login_required
def newsletter_edition_design_html_save(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    raw_html = request.form.get("html")
    if raw_html is None:
        flash("HTML is required.", "danger")
        return redirect(url_for("newsletter_edition_detail", edition_id=edition_id) + "#visual-design")
    document = sanitize_newsletter_design_html(raw_html)
    if len(document) < 300 or "<body" not in document.lower():
        flash("The supplied email HTML is incomplete.", "danger")
        return redirect(url_for("newsletter_edition_detail", edition_id=edition_id) + "#visual-design")
    if edition.get("html"):
        newsletter_edition_versions.insert_one({
            "edition_id": edition["_id"], "schedule_id": edition.get("schedule_id"),
            "user_id": edition.get("user_id"), "organization_id": edition.get("organization_id"),
            "revision": int(edition.get("design_revision") or 1), "reason": "Before manual HTML edit",
            "html": edition.get("html") or "", "subject": edition.get("subject") or "", "preheader": edition.get("preheader") or "",
            "visual_design_prompt": edition.get("visual_design_prompt") or "", "created_at": now(),
        })
    revision = int(edition.get("design_revision") or 1) + 1
    newsletter_editions.update_one({"_id": edition["_id"]}, {"$set": {
        "html": document, "design_revision": revision, "is_visually_edited": True,
        "manual_design_html": True, "status": "draft", "design_updated_at": now(), "updated_at": now(),
    }, "$unset": {"distribution": "", "distributed_at": "", "blackbook_mailchimp_draft": ""}})
    flash("Email HTML saved as a new visual revision.", "success")
    return redirect(url_for("newsletter_edition_detail", edition_id=edition_id) + "#visual-design")


@app.post("/social/newsletters/editions/<edition_id>/design/versions/<version_id>/restore")
@login_required
def newsletter_edition_design_restore(edition_id, version_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    try:
        version = newsletter_edition_versions.find_one({"_id": ObjectId(version_id), "edition_id": edition["_id"]})
    except Exception:
        version = None
    if not version:
        abort(404)
    if edition.get("html"):
        newsletter_edition_versions.insert_one({
            "edition_id": edition["_id"], "schedule_id": edition.get("schedule_id"),
            "user_id": edition.get("user_id"), "organization_id": edition.get("organization_id"),
            "revision": int(edition.get("design_revision") or 1), "reason": "Before design revision restore",
            "html": edition.get("html") or "", "subject": edition.get("subject") or "", "preheader": edition.get("preheader") or "",
            "visual_design_prompt": edition.get("visual_design_prompt") or "", "created_at": now(),
        })
    revision = int(edition.get("design_revision") or 1) + 1
    newsletter_editions.update_one({"_id": edition["_id"]}, {"$set": {
        "html": version.get("html") or "", "visual_design_prompt": version.get("visual_design_prompt") or "",
        "design_revision": revision, "is_visually_edited": True, "status": "draft", "design_updated_at": now(), "updated_at": now(),
    }, "$unset": {"distribution": "", "distributed_at": "", "blackbook_mailchimp_draft": ""}})
    flash(f"Restored visual design from revision {version.get('revision') or 'saved' } as a new revision.", "success")
    return redirect(url_for("newsletter_edition_detail", edition_id=edition_id) + "#visual-design")


@app.post("/social/newsletters/editions/<edition_id>/ready")
@login_required
def newsletter_edition_ready(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    if not edition.get("html") or not edition.get("stories"):
        flash("Generate the edition before marking it ready.", "danger")
    else:
        newsletter_editions.update_one({"_id": edition["_id"]}, {"$set": {"status": "ready", "ready_at": now(), "updated_at": now()}})
        flash("Newsletter marked ready for delivery.", "success")
    return redirect(url_for("newsletter_edition_detail", edition_id=edition_id))


@app.post("/social/newsletters/editions/<edition_id>/regenerate")
@login_required
def newsletter_edition_regenerate(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    if edition.get("html"):
        newsletter_edition_versions.insert_one({
            "edition_id": edition["_id"], "schedule_id": edition.get("schedule_id"),
            "user_id": edition.get("user_id"), "organization_id": edition.get("organization_id"),
            "revision": int(edition.get("design_revision") or 1), "reason": "Before full edition regeneration",
            "html": edition.get("html") or "", "subject": edition.get("subject") or "", "preheader": edition.get("preheader") or "",
            "visual_design_prompt": edition.get("visual_design_prompt") or "", "created_at": now(),
        })
    newsletter_editions.update_one({"_id": edition["_id"]}, {"$unset": {"distribution": "", "distributed_at": "", "blackbook_mailchimp_draft": ""}})
    generate_newsletter_edition_task.delay(str(edition["schedule_id"]), (edition.get("due_at") or now()).isoformat(), True)
    flash("Newsletter regeneration queued. Any previous distribution record was cleared because the content is changing.", "success")
    return redirect(url_for("newsletter_edition_detail", edition_id=edition_id))


@app.post("/social/newsletters/editions/<edition_id>/archive")
@login_required
def newsletter_edition_archive(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    newsletter_editions.update_one({"_id": edition["_id"]}, {"$set": {"status": "archived", "archived_at": now(), "updated_at": now()}})
    flash("Newsletter edition archived.", "success")
    return redirect(url_for("newsletter_view"))


@app.get("/social/newsletters/editions/<edition_id>/preview")
@login_required
def newsletter_edition_preview(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition or not edition.get("html"):
        abort(404)
    return Response(edition["html"], mimetype="text/html", headers={"X-Robots-Tag": "noindex, nofollow"})


@app.get("/social/newsletters/editions/<edition_id>/download/<format_name>")
@login_required
def newsletter_edition_download(edition_id, format_name):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    if format_name == "html":
        body, mimetype, ext = edition.get("html") or "", "text/html", "html"
    elif format_name == "txt":
        body, mimetype, ext = edition.get("text") or "", "text/plain", "txt"
    else:
        abort(404)
    filename = re.sub(r"[^A-Za-z0-9_.-]+", "-", edition.get("subject") or "newsletter").strip("-")[:80] or "newsletter"
    return Response(body, mimetype=mimetype, headers={"Content-Disposition": f'attachment; filename="{filename}.{ext}"'})



@app.get("/newsletters/editions/<edition_id>/distribution-package")
@login_required
def newsletter_distribution_package(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    if not edition.get("html"):
        flash("Generate the newsletter before exporting a delivery package.", "danger")
        return redirect(url_for("newsletter_edition_detail", edition_id=edition_id))
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", edition.get("subject") or "newsletter").strip("-")[:80] or "newsletter"
    package = BytesIO()
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{safe_name}.html", edition.get("html") or "")
        zf.writestr(f"{safe_name}.txt", edition.get("text") or "")
        zf.writestr("subject.txt", (edition.get("subject") or "") + "\n")
        zf.writestr("preheader.txt", (edition.get("preheader") or "") + "\n")
        manifest = {
            "edition_id": str(edition.get("_id")),
            "schedule_id": str(edition.get("schedule_id") or ""),
            "subject": edition.get("subject") or "",
            "preheader": edition.get("preheader") or "",
            "story_count": edition.get("story_count") or 0,
            "status": edition.get("status") or "draft",
            "due_at": edition.get("due_at").isoformat() if hasattr(edition.get("due_at"), "isoformat") else str(edition.get("due_at") or ""),
            "exported_at": now().isoformat(),
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    package.seek(0)
    return Response(
        package.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}-delivery-package.zip"'},
    )


@app.post("/newsletters/editions/<edition_id>/distribution")
@login_required
def newsletter_distribution_record(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    if edition.get("status") != "ready":
        flash("Mark the newsletter ready before recording distribution.", "danger")
        return redirect(url_for("newsletter_distribute", edition=edition_id))
    provider = clean_text(request.form.get("provider") or "Manual export", 120)
    audience = clean_text(request.form.get("audience") or "", 220)
    external_reference = clean_text(request.form.get("external_reference") or "", 240)
    notes = clean_text(request.form.get("notes") or "", 1600)
    try:
        recipient_count = max(0, min(100000000, int(request.form.get("recipient_count") or 0)))
    except (TypeError, ValueError):
        recipient_count = 0
    delivered_at_raw = clean_text(request.form.get("delivered_at") or "", 40)
    delivered_at = now()
    if delivered_at_raw:
        try:
            delivered_at = datetime.fromisoformat(delivered_at_raw)
            if delivered_at.tzinfo is None:
                delivered_at = delivered_at.replace(tzinfo=timezone.utc)
        except ValueError:
            flash("Delivery timestamp was invalid, so the current time was used.", "warning")
    distribution = {
        "provider": provider,
        "audience": audience,
        "recipient_count": recipient_count,
        "external_reference": external_reference,
        "notes": notes,
        "delivered_at": delivered_at,
        "recorded_at": now(),
        "recorded_by": ObjectId(current_user.id),
    }
    newsletter_editions.update_one(
        {"_id": edition["_id"]},
        {"$set": {"distribution": distribution, "status": "distributed", "distributed_at": delivered_at, "updated_at": now()}},
    )
    flash("Distribution recorded for this newsletter edition.", "success")
    return redirect(url_for("newsletter_distribute", edition=edition_id))


@app.post("/newsletters/editions/<edition_id>/blackbook-distribute")
@login_required
def newsletter_blackbook_distribute(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    if edition.get("status") not in {"ready", "distributed"}:
        flash("Mark the edition ready before handing it to BlackBook / Mailchimp.", "danger")
        return redirect(url_for("newsletter_edition_detail", edition_id=edition_id))
    schedule = newsletter_schedules.find_one({"_id": edition.get("schedule_id"), "$or": ownership_or()}) or {}
    action = "send" if request.form.get("action") == "send" else "draft"
    reply_to = clean_text(request.form.get("reply_to") or "", 320)
    if "@" not in reply_to:
        flash("A valid reply-to email is required for a Mailchimp campaign.", "danger")
        return redirect(url_for("newsletter_distribute", edition=edition_id))
    segment_ids = schedule.get("blackbook_segment_ids") or []
    person_ids = schedule.get("blackbook_person_ids") or []
    include_all = bool(schedule.get("blackbook_include_all_eligible"))
    engagement_buckets = schedule.get("blackbook_engagement_buckets") or []
    interest_ids = schedule.get("blackbook_interest_ids") or []
    interest_match = schedule.get("blackbook_interest_match") or "any"
    if not segment_ids and not person_ids and not include_all and not engagement_buckets and not interest_ids:
        flash("Configure the BlackBook audience before distributing this newsletter.", "danger")
        return redirect(url_for("newsletter_audience", schedule=str(schedule.get("_id") or "")))
    try:
        result = blackbook_distribute_newsletter(
            current_user.organization_id,
            edition_id=edition["_id"], schedule_id=edition.get("schedule_id"),
            subject=edition.get("subject") or schedule.get("name") or "Newsletter",
            preheader=edition.get("preheader") or "", html_body=edition.get("html") or "",
            text_body=edition.get("text") or "", sender_name=schedule.get("sender_name") or schedule.get("name") or "Newsjacking",
            reply_to=reply_to, segment_ids=segment_ids, person_ids=person_ids,
            include_all_eligible=include_all, engagement_buckets=engagement_buckets,
            interest_ids=interest_ids, interest_match=interest_match, action=action,
        )
    except Exception as exc:
        flash(f"BlackBook / Mailchimp handoff failed: {clean_text(str(exc), 900)}", "danger")
        return redirect(url_for("newsletter_distribute", edition=edition_id))
    now_value = now()
    campaign_id = clean_text(result.get("campaign_id") or "", 160)
    if action == "send":
        distribution = {
            "provider": "BlackBook / Mailchimp", "audience": "BlackBook audience",
            "recipient_count": int(result.get("recipient_count") or 0), "external_reference": campaign_id,
            "notes": "Sent through the organization-scoped BlackBook Marketing Bridge.",
            "delivered_at": now_value, "recorded_at": now_value, "recorded_by": ObjectId(current_user.id),
            "blackbook": result,
        }
        newsletter_editions.update_one({"_id": edition["_id"]}, {"$set": {"status": "distributed", "distribution": distribution, "updated_at": now_value}})
        flash(f"BlackBook sent the Mailchimp campaign to {int(result.get('recipient_count') or 0)} eligible recipients.", "success")
    else:
        newsletter_editions.update_one({"_id": edition["_id"]}, {"$set": {
            "blackbook_mailchimp_draft": {"campaign_id": campaign_id, "recipient_count": int(result.get("recipient_count") or 0), "created_at": now_value, "result": result},
            "updated_at": now_value,
        }})
        flash("Mailchimp draft created through BlackBook. The NJS edition remains Ready until it is actually sent.", "success")
    return redirect(url_for("newsletter_distribute", edition=edition_id))


@app.post("/newsletters/editions/<edition_id>/distribution/reset")
@login_required
def newsletter_distribution_reset(edition_id):
    edition = owned_newsletter_edition(edition_id)
    if not edition:
        abort(404)
    newsletter_editions.update_one(
        {"_id": edition["_id"]},
        {"$unset": {"distribution": "", "distributed_at": ""}, "$set": {"status": "ready", "updated_at": now()}},
    )
    flash("Distribution record cleared. The edition is ready for delivery again.", "success")
    return redirect(url_for("newsletter_distribute", edition=edition_id))


@app.route("/domains", methods=["GET", "POST"])
@login_required
def domains_workspace():
    uid = ObjectId(current_user.id)
    if request.method == "POST":
        try:
            hostname = normalize_domain(request.form.get("domain"))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("domains_workspace"))
        existing = domain_mappings.find_one({"domain": hostname})
        if existing:
            if document_in_workspace(existing):
                flash("That domain is already connected to this workspace.", "warning")
                return redirect(url_for("domains_workspace"))
            flash("That domain has already been claimed by another NJS workspace.", "danger")
            return redirect(url_for("domains_workspace"))
        doc = {
            "user_id": uid,
            "organization_id": current_user.organization_id,
            "domain": hostname,
            "status": "pending",
            "expected_ip": Config.PUBLISHING_IP,
            "resolved_ips": [],
            "created_at": now(),
            "updated_at": now(),
        }
        domain_mappings.insert_one(doc)
        flash(f"Domain added. Create an A record pointing {hostname} to {Config.PUBLISHING_IP}, then verify it here.", "success")
        return redirect(url_for("domains_workspace"))

    rows = list(domain_mappings.find({"$or": ownership_or()}).sort("domain", 1))
    payloads = []
    for domain in rows:
        item = domain_payload(domain)
        item["routes"] = [
            route_payload(route, domain=domain)
            for route in domain_routes.find({"domain_id": domain["_id"]}).sort("path_key", 1)
        ]
        payloads.append(item)
    return render_template(
        "domains.html",
        domains=payloads,
        publishing_ip=Config.PUBLISHING_IP,
        campaigns=list(campaigns.find({"status": "active", "$or": ownership_or()}).sort("title", 1)),
    )


@app.post("/domains/<domain_id>/verify")
@login_required
def domain_verify(domain_id):
    domain = owned_domain(domain_id)
    if not domain:
        abort(404)
    success, message, updated = activate_domain_after_dns(domain)
    category = "success" if updated.get("status") in {"active", "verified"} else ("danger" if updated.get("status") == "provisioning_failed" else "warning")
    flash(message, category)
    return redirect(url_for("domains_workspace"))


@app.post("/domains/<domain_id>/delete")
@login_required
def domain_delete(domain_id):
    domain = owned_domain(domain_id)
    if not domain:
        abort(404)
    if domain_routes.count_documents({"domain_id": domain["_id"]}):
        flash("Remove the domain's page routes before disconnecting it.", "warning")
        return redirect(url_for("domains_workspace"))
    cleanup_warning = None
    if Config.DOMAIN_AUTO_PROVISION and domain.get("status") in {"active", "verified", "provisioning_failed"}:
        try:
            deprovision_domain(domain["domain"])
        except DomainProvisioningError as exc:
            cleanup_warning = str(exc)[:800]
    domain_mappings.delete_one({"_id": domain["_id"]})
    if cleanup_warning:
        flash(f"Domain disconnected from NJS, but host proxy cleanup needs attention: {cleanup_warning}", "warning")
    else:
        flash("Domain disconnected and publishing proxy removed.", "success")
    return redirect(url_for("domains_workspace"))


@app.post("/domain-routes/<route_id>/delete")
@login_required
def domain_route_delete(route_id):
    route = owned_domain_route(route_id)
    if not route:
        abort(404)
    domain_routes.delete_one({"_id": route["_id"]})
    flash("Publishing route removed.", "success")
    return redirect(request.referrer or url_for("domains_workspace"))


@app.post("/pages/<page_id>/routes")
@login_required
def page_add_route(page_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    domain = owned_domain(request.form.get("domain_id") or "")
    if not domain:
        flash("Domain not found.", "danger")
        return redirect(url_for("page_detail", page_id=page_id))
    try:
        create_domain_route(domain, page, request.form.get("path") or "/", request.form.get("goal") or "")
        flash("Domain path mapped to this page.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("page_detail", page_id=page_id))


@app.route("/api/domains", methods=["GET", "POST"])
@login_required
def api_domains():
    if request.method == "GET":
        rows = list(domain_mappings.find({"$or": ownership_or()}).sort("domain", 1))
        return jsonify({"success": True, "domains": [domain_payload(row) for row in rows], "dns_instruction": {"type": "A", "name": "@", "value": Config.PUBLISHING_IP}})
    data = request.get_json(silent=True) or {}
    try:
        hostname = normalize_domain(data.get("domain"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    existing = domain_mappings.find_one({"domain": hostname})
    if existing:
        if document_in_workspace(existing):
            return jsonify({"success": True, "already_owned": True, "domain": domain_payload(existing)})
        return jsonify({"error": "This domain is already owned by another workspace", "transfer_required": True}), 409
    doc = {"user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id, "domain": hostname, "status": "pending", "expected_ip": Config.PUBLISHING_IP, "resolved_ips": [], "created_at": now(), "updated_at": now()}
    doc["_id"] = domain_mappings.insert_one(doc).inserted_id
    return jsonify({"success": True, "domain": domain_payload(doc), "dns_instruction": {"type": "A", "name": "@", "value": Config.PUBLISHING_IP}}), 201


@app.post("/api/domains/check")
@login_required
def api_domain_check():
    data = request.get_json(silent=True) or {}
    try:
        hostname = normalize_domain(data.get("domain"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    existing = domain_mappings.find_one({"domain": hostname})
    if not existing:
        return jsonify({"available": True, "domain": hostname})
    if document_in_workspace(existing):
        return jsonify({"available": True, "already_owned": True, "domain": domain_payload(existing)})
    return jsonify({"available": False, "domain": hostname, "transfer_required": True}), 409


@app.post("/api/domains/<domain_id>/verify")
@login_required
def api_verify_domain(domain_id):
    domain = owned_domain(domain_id)
    if not domain:
        return jsonify({"error": "Domain not found"}), 404
    success, message, domain = activate_domain_after_dns(domain)
    status_code = 200 if success else (503 if domain.get("status") == "provisioning_failed" else 422)
    return jsonify({"success": success, "message": message, "domain": domain_payload(domain), "resolved_ips": domain.get("resolved_ips") or [], "expected_ip": Config.PUBLISHING_IP}), status_code


@app.route("/api/domain-routes", methods=["GET", "POST"])
@login_required
def api_domain_route_collection():
    if request.method == "GET":
        rows = list(domain_routes.find({"$or": ownership_or()}).sort([("domain_id", 1), ("path_key", 1)]))
        return jsonify({"success": True, "routes": [route_payload(row) for row in rows]})
    data = request.get_json(silent=True) or {}
    domain = owned_domain(data.get("domain_id") or "")
    page = owned_page(data.get("page_id") or "")
    if not domain or not page:
        return jsonify({"error": "Domain or page not found"}), 404
    try:
        route = create_domain_route(domain, page, data.get("path") or "/", data.get("goal") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc), "code": "domain_path_conflict"}), 409
    return jsonify({"success": True, "route": route_payload(route, domain=domain, page=page)}), 201


@app.delete("/api/domain-routes/<route_id>")
@login_required
def api_delete_domain_route(route_id):
    route = owned_domain_route(route_id)
    if not route:
        return jsonify({"error": "Route not found"}), 404
    domain_routes.delete_one({"_id": route["_id"]})
    return "", 204


@app.route("/api/domains/<domain_id>/dns", methods=["GET"])
@login_required
def api_domain_dns(domain_id):
    domain = owned_domain(domain_id)
    if not domain:
        return jsonify({"error": "Domain not found"}), 404
    resolved = resolved_domain_ips(domain["domain"])
    return jsonify({
        "success": Config.PUBLISHING_IP in resolved,
        "domain": domain["domain"],
        "status": domain.get("status", "pending"),
        "resolved_ips": resolved,
        "dns_instruction": {"type": "A", "name": "@", "value": Config.PUBLISHING_IP},
    })


@app.route("/api/domains/<domain_id>/routes", methods=["GET"])
@login_required
def api_domain_routes(domain_id):
    domain = owned_domain(domain_id)
    if not domain:
        return jsonify({"error": "Domain not found"}), 404
    rows = list(domain_routes.find({"domain_id": domain["_id"]}).sort("path_key", 1))
    return jsonify({"success": True, "domain": domain_payload(domain), "routes": [route_payload(row, domain=domain) for row in rows]})


@app.route("/api/domains/<domain_id>/resolve", methods=["GET"])
@login_required
def api_domain_resolve(domain_id):
    domain = owned_domain(domain_id)
    if not domain:
        return jsonify({"error": "Domain not found"}), 404
    try:
        path = normalize_domain_path(request.args.get("path") or "/")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    route = domain_routes.find_one({"domain_id": domain["_id"], "path_key": path.casefold()})
    page = landing_pages.find_one({"_id": route.get("page_id")}) if route else None
    return jsonify({"resolved": bool(page), "live": bool(page and page.get("published") and domain.get("status") == "verified"), "domain": domain["domain"], "path": path, "route": route_payload(route, domain=domain, page=page) if route else None})


def _save_landing_snapshot(page, reason="Before manual edit"):
    if not page or not page.get("html"):
        return None
    doc = {
        "landing_page_id": page["_id"],
        "revision": int(page.get("revision") or 0),
        "reason": clean_text(reason, 240) or "Saved revision",
        "html": page.get("html") or "",
        "metadata": page.get("metadata") or {},
        "design_plan": page.get("design_plan") or {},
        "visual_assets": page.get("visual_assets") or [],
        "quality": page.get("quality") or {},
        "article_ids": page.get("article_ids") or [],
        "review_notes": page.get("review_notes") or "",
        "review_notes_updated_at": page.get("review_notes_updated_at"),
        "created_at": now(),
    }
    return landing_page_versions.insert_one(doc).inserted_id


def _landing_page_versions(page):
    rows = list(landing_page_versions.find({"landing_page_id": page["_id"]}).sort([("revision", -1), ("created_at", -1)]).limit(60))
    return [{
        "id": row["_id"], "revision": int(row.get("revision") or 0),
        "reason": row.get("reason") or "Saved revision", "created_at": row.get("created_at"),
    } for row in rows]


def _detach_landing_page(page):
    site_id = page.get("site_id")
    if site_id:
        site = website_sites.find_one({"_id": site_id, **workspace_scope()})
        if site:
            nav = [item for item in (site.get("navigation") or []) if item.get("page_id") != page["_id"]]
            update = {"navigation": nav, "navigation_version": int(site.get("navigation_version") or 1) + 1, "updated_at": now()}
            if site.get("homepage_page_id") == page["_id"]:
                update["homepage_page_id"] = None
            if site.get("style_source_page_id") == page["_id"]:
                update["style_source_page_id"] = None
            website_sites.update_one({"_id": site["_id"]}, {"$set": update})
    domain_routes.delete_many({"page_id": page["_id"], **workspace_scope()})


@app.get("/landing-pages")
@login_required
def landing_page_studio():
    scope = workspace_scope()
    pages = list(landing_pages.find(scope).sort("updated_at", -1).limit(300))
    campaigns_rows = list(campaigns.find({**scope, "status": {"$in": ["active", "draft"]}}).sort("title", 1).limit(250))
    domains_rows = list(domain_mappings.find(scope).sort("domain", 1).limit(100))
    return render_template(
        "landing_pages_studio.html",
        pages=pages,
        campaigns=campaigns_rows,
        domains=domains_rows,
        counts={
            "total": len(pages),
            "published": sum(1 for page in pages if page.get("published")),
            "ai": sum(1 for page in pages if str(page.get("generation_profile") or "").startswith(("site_builder", "ai_"))),
            "manual": sum(1 for page in pages if page.get("manual_mode") or str(page.get("generation_profile") or "").startswith("manual")),
        },
    )


@app.post("/landing-pages/manual")
@login_required
def landing_page_manual_create():
    title = clean_text(request.form.get("title"), 300)
    if not title:
        flash("Enter a page title.", "danger")
        return redirect(url_for("landing_page_studio"))
    domain_id = clean_text(request.form.get("domain_id"), 80)
    domain = owned_domain(domain_id) if domain_id else None
    if domain_id and not domain:
        flash("The selected domain was not found.", "danger")
        return redirect(url_for("landing_page_studio"))
    requested_path = ""
    if domain:
        try:
            requested_path = normalize_domain_path(request.form.get("path") or "/")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("landing_page_studio"))
        if domain_routes.find_one({"domain_id": domain["_id"], "path_key": requested_path.casefold()}):
            flash(f"{requested_path} is already assigned on {domain.get('domain')}.", "danger")
            return redirect(url_for("landing_page_studio"))
    raw_html = request.form.get("html") or ""
    document = sanitize_manual_landing_html(raw_html)
    if not document:
        document = manual_landing_document(
            title,
            request.form.get("headline") or title,
            request.form.get("subheadline") or "",
            request.form.get("body") or "",
            request.form.get("cta_label") or "Learn more",
            request.form.get("cta_url") or "#",
        )
    public_id = secrets.token_urlsafe(9)
    site = ensure_website_site(domain=domain, name=domain.get("domain") if domain else "Primary website")
    page_kind = clean_text(request.form.get("page_kind"), 40).lower() or ("home" if requested_path == "/" else "custom")
    if page_kind not in {"home", "about", "services", "contact", "resources", "news", "custom"}:
        page_kind = "custom"
    description = clean_text(request.form.get("meta_description") or request.form.get("subheadline"), 170)
    now_value = now()
    doc = {
        "user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id,
        "site_id": site["_id"], "site_path": requested_path if domain else f"/p/{public_id}",
        "page_kind": page_kind, "include_articles": request.form.get("include_articles") == "1",
        "campaign_id": None, "campaign_ids": [], "evidence_item_ids": [], "evidence_snapshot": [],
        "title": title, "slug": slugify(title)[:100], "public_id": public_id,
        "article_mode": "selected", "article_ids": [], "prompt": "", "source_website_url": "",
        "generation_status": "completed", "generation_profile": "manual_v3_8", "manual_mode": True,
        "html": document,
        "metadata": {"title": title, "meta_description": description, "summary": description, "language": "en"},
        "design_plan": {}, "visual_assets": [], "quality": {}, "review_notes": "",
        "revision": 1, "published": False, "created_at": now_value, "updated_at": now_value,
    }
    page_id = landing_pages.insert_one(doc).inserted_id
    page = landing_pages.find_one({"_id": page_id})
    if domain:
        create_domain_route(domain, page, requested_path, request.form.get("route_goal") or title)
    register_site_page(site, page, requested_path if domain else f"/p/{public_id}", label=title)
    flash("Manual landing page created. You can edit the HTML directly or ask AI to revise it.", "success")
    return redirect(url_for("landing_page_editor", page_id=page_id))



@app.post("/landing-pages/ai")
@login_required
def landing_page_ai_create():
    title = clean_text(request.form.get("title"), 300)
    campaign = owned_campaign(request.form.get("campaign_id") or "")
    if not title or not campaign:
        flash("Choose a campaign and enter a page title.", "danger")
        return redirect(url_for("landing_page_studio"))
    domain_id = clean_text(request.form.get("domain_id"), 80)
    domain = owned_domain(domain_id) if domain_id else None
    if domain_id and not domain:
        flash("The selected domain was not found.", "danger")
        return redirect(url_for("landing_page_studio"))
    requested_path = ""
    if domain:
        try:
            requested_path = normalize_domain_path(request.form.get("path") or "/")
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("landing_page_studio"))
        if domain_routes.find_one({"domain_id": domain["_id"], "path_key": requested_path.casefold()}):
            flash(f"{requested_path} is already assigned on {domain.get('domain')}.", "danger")
            return redirect(url_for("landing_page_studio"))
    recent = list(articles.find({"campaign_id": campaign["_id"], **workspace_scope()}).sort("created_at", -1).limit(Config.LANDING_MAX_ARTICLES))
    public_id = secrets.token_urlsafe(9)
    site = ensure_website_site(domain=domain, name=domain.get("domain") if domain else campaign.get("title") or title)
    page_kind = clean_text(request.form.get("page_kind"), 40).lower() or ("home" if requested_path == "/" else "custom")
    if page_kind not in {"home", "about", "services", "contact", "resources", "news", "custom"}:
        page_kind = "custom"
    stamp = now()
    doc = {
        "user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id,
        "site_id": site["_id"], "site_path": requested_path if domain else f"/p/{public_id}",
        "page_kind": page_kind, "include_articles": request.form.get("include_articles") == "1",
        "campaign_id": campaign["_id"], "campaign_ids": [campaign["_id"]],
        "evidence_item_ids": [], "evidence_snapshot": [], "title": title, "slug": slugify(title)[:100],
        "public_id": public_id, "article_mode": "all", "article_ids": [row["_id"] for row in recent],
        "prompt": clean_text(request.form.get("prompt"), 4000), "source_website_url": campaign.get("website_url") or "",
        "generation_status": "queued", "generation_profile": "landing_studio_ai_v3_8", "manual_mode": False,
        "metadata": {}, "design_plan": {}, "visual_assets": [], "quality": {}, "review_notes": "",
        "revision": 0, "published": False, "created_at": stamp, "updated_at": stamp,
    }
    page_id = landing_pages.insert_one(doc).inserted_id
    page = landing_pages.find_one({"_id": page_id})
    if domain:
        create_domain_route(domain, page, requested_path, title)
    register_site_page(site, page, requested_path if domain else f"/p/{public_id}", label=("Home" if page_kind == "home" else title))
    result = generate_landing_page_task.delay(str(page_id))
    flash(f"AI page generation queued ({result.id}). You can keep working from Landing Page Studio while it renders.", "success")
    return redirect(url_for("landing_page_editor", page_id=page_id))


@app.get("/landing-pages/<page_id>/edit")
@login_required
def landing_page_editor(page_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    route = domain_routes.find_one({"page_id": page["_id"], **workspace_scope()})
    domain = domain_mappings.find_one({"_id": route.get("domain_id"), **workspace_scope()}) if route else None
    site = website_sites.find_one({"_id": page.get("site_id"), **workspace_scope()}) if page.get("site_id") else None
    jobs = list(landing_page_change_jobs.find({"landing_page_id": page["_id"]}).sort("created_at", -1).limit(12))
    return render_template(
        "landing_page_editor.html", page=page, route=route, domain=domain, site=site,
        versions=_landing_page_versions(page), change_jobs=jobs,
    )


@app.post("/landing-pages/<page_id>/save")
@login_required
def landing_page_manual_save(page_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    title = clean_text(request.form.get("title"), 300) or page.get("title") or "Landing page"
    raw_html = request.form.get("html")
    if raw_html is None:
        flash("HTML content is required.", "danger")
        return redirect(url_for("landing_page_editor", page_id=page_id))
    document = sanitize_manual_landing_html(raw_html)
    if not document:
        flash("The page cannot be empty.", "danger")
        return redirect(url_for("landing_page_editor", page_id=page_id))
    _save_landing_snapshot(page, "Before manual edit")
    metadata = dict(page.get("metadata") or {})
    metadata["title"] = clean_text(request.form.get("meta_title"), 90) or title
    metadata["meta_description"] = clean_text(request.form.get("meta_description"), 170)
    metadata["summary"] = clean_text(request.form.get("meta_summary"), 320) or metadata.get("meta_description")
    page_kind = clean_text(request.form.get("page_kind"), 40).lower() or page.get("page_kind") or "custom"
    if page_kind not in {"home", "about", "services", "contact", "resources", "news", "custom"}:
        page_kind = "custom"
    landing_pages.update_one({"_id": page["_id"]}, {"$set": {
        "title": title, "slug": slugify(title)[:100], "html": document, "metadata": metadata,
        "page_kind": page_kind, "include_articles": request.form.get("include_articles") == "1",
        "manual_mode": True, "generation_status": "completed",
        "revision": int(page.get("revision") or 0) + 1, "updated_at": now(),
    }})
    flash("Landing page saved as a new revision.", "success")
    return redirect(url_for("landing_page_editor", page_id=page_id))


@app.post("/landing-pages/<page_id>/duplicate")
@login_required
def landing_page_duplicate(page_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    public_id = secrets.token_urlsafe(9)
    clone = {k: v for k, v in page.items() if k not in {"_id", "published", "published_at", "published_revision", "published_html", "published_metadata", "created_at", "updated_at", "navigation_enabled", "navigation_pending"}}
    internal_site = ensure_website_site(name="Primary website")
    clone.update({
        "user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id,
        "site_id": internal_site["_id"],
        "title": clean_text(f"{page.get('title') or 'Landing page'} copy", 300),
        "slug": slugify(f"{page.get('title') or 'landing-page'} copy")[:100],
        "public_id": public_id, "site_path": f"/p/{public_id}", "published": False,
        "generation_status": "completed" if page.get("html") else "draft",
        "revision": 1, "manual_mode": True, "created_at": now(), "updated_at": now(),
    })
    result = landing_pages.insert_one(clone)
    clone_page = landing_pages.find_one({"_id": result.inserted_id})
    register_site_page(internal_site, clone_page, clone_page["site_path"], label=clone_page["title"])
    flash("Landing page duplicated as an unpublished manual copy.", "success")
    return redirect(url_for("landing_page_editor", page_id=result.inserted_id))


@app.post("/landing-pages/<page_id>/delete")
@login_required
def landing_page_delete(page_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    _detach_landing_page(page)
    landing_page_versions.delete_many({"landing_page_id": page["_id"]})
    landing_page_change_jobs.delete_many({"landing_page_id": page["_id"]})
    landing_pages.delete_one({"_id": page["_id"], **workspace_scope()})
    flash("Landing page and its saved page revisions were deleted.", "success")
    return redirect(url_for("landing_page_studio"))


@app.route("/pages", methods=["GET", "POST"])
@app.route("/websites", methods=["GET", "POST"])
@login_required
def websites():
    uid = ObjectId(current_user.id)
    if request.method == "POST":
        raw_campaign_ids = request.form.getlist("campaign_ids") or ([request.form.get("campaign_id")] if request.form.get("campaign_id") else [])
        campaign_docs = []
        seen_campaigns = set()
        for raw in raw_campaign_ids:
            campaign = owned_campaign(raw or "")
            if campaign and campaign["_id"] not in seen_campaigns:
                campaign_docs.append(campaign); seen_campaigns.add(campaign["_id"])
        evidence_ids = owned_collection_item_ids(request.form.getlist("evidence_item_ids"))
        evidence_docs = list(collection_items.find({"_id": {"$in": evidence_ids}})) if evidence_ids else []
        if not campaign_docs and not evidence_docs:
            flash("Select at least one campaign or Collection item for this page.", "danger")
            return redirect(url_for("websites", domain=request.args.get("domain") or "internal"))
        primary_campaign = campaign_docs[0] if campaign_docs else None
        campaign_oids = [c["_id"] for c in campaign_docs]
        recent = list(articles.find({"campaign_id": {"$in": campaign_oids}}).sort("created_at", -1).limit(Config.LANDING_MAX_ARTICLES)) if campaign_oids else []
        default_name = primary_campaign.get("title") if primary_campaign else ((evidence_docs[0].get("title") if evidence_docs else None) or "Evidence")
        title = clean_text(request.form.get("title"), 300) or f"{default_name} page"
        try:
            source_website_url = normalize_website_url(request.form.get("website_url"))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("websites"))
        if not source_website_url:
            source_website_url = (primary_campaign or {}).get("website_url") or next((str(item.get("url")) for item in evidence_docs if item.get("url") and item.get("item_type") in {"page_text", "description"}), "")
        domain_id = request.form.get("domain_id") or ""
        domain = owned_domain(domain_id) if domain_id else None
        if domain_id and not domain:
            flash("The selected publishing domain was not found.", "danger")
            return redirect(url_for("websites", domain="internal"))

        requested_path = ""
        if domain:
            try:
                requested_path = normalize_domain_path(request.form.get("path") or "/")
            except ValueError as exc:
                flash(str(exc), "danger")
                return redirect(url_for("websites", domain=str(domain["_id"])))
            if domain_routes.find_one({"domain_id": domain["_id"], "path_key": requested_path.casefold()}):
                flash(f"{requested_path} already belongs to another page on this website.", "danger")
                return redirect(url_for("websites", domain=str(domain["_id"])))

        public_id = secrets.token_urlsafe(9)
        site = ensure_website_site(domain=domain, name=(primary_campaign or {}).get("title") or title)
        site_path = requested_path if domain else f"/p/{public_id}"
        allowed_page_kinds = {"home", "about", "services", "contact", "resources", "news", "custom"}
        page_kind = clean_text(request.form.get("page_kind"), 40).lower()
        if page_kind not in allowed_page_kinds:
            if requested_path == "/":
                page_kind = "home"
            else:
                guess = (requested_path.strip("/") or title).lower()
                page_kind = next((kind for kind in ("about", "services", "contact", "resources", "news") if kind in guess), "custom")
        include_articles = request.form.get("include_articles") == "1"
        if "include_articles" not in request.form:
            include_articles = page_kind in {"home", "resources", "news"}

        doc = {
            "user_id": uid,
            "organization_id": (primary_campaign or {}).get("organization_id") or current_user.organization_id,
            "site_id": site["_id"],
            "site_path": site_path,
            "page_kind": page_kind,
            "include_articles": include_articles,
            "campaign_id": (primary_campaign or {}).get("_id"),
            "campaign_ids": campaign_oids,
            "evidence_item_ids": evidence_ids,
            "evidence_snapshot": evidence_context(evidence_docs, max_chars=70000),
            "title": title,
            "slug": slugify(title)[:100],
            "public_id": public_id,
            "article_mode": "all",
            "article_ids": [a["_id"] for a in recent],
            "prompt": clean_text(request.form.get("prompt"), 4000),
            "source_website_url": source_website_url or "",
            "generation_status": "queued",
            "generation_profile": "site_builder_v3_0_3_shared_design",
            "metadata": {}, "design_plan": {}, "visual_assets": [], "quality": {},
            "review_notes": "", "revision": 0, "published": False,
            "created_at": now(), "updated_at": now(),
        }
        page_id = landing_pages.insert_one(doc).inserted_id
        page = landing_pages.find_one({"_id": page_id})
        redirect_args = {"domain": "internal", "page": str(page_id)}
        if domain:
            route = create_domain_route(domain, page, requested_path, request.form.get("route_goal") or title)
            redirect_args = {"domain": str(domain["_id"]), "route": str(route["_id"])}
        site = register_site_page(site, page, site_path, label=("Home" if page_kind == "home" else title))
        generate_landing_page_task.delay(str(page_id))
        if page_kind == "home":
            flash("Homepage generation queued. Its design will become the shared visual system for this website.", "success")
        elif (site.get("design_system") or {}):
            flash(f"{title} is generating with this website's established design system. Review it, then publish and add it to navigation when ready.", "success")
        else:
            flash("Page generation queued. This first completed page will establish the reusable website design system.", "success")
        return redirect(url_for("websites", **redirect_args))

    page_rows = list(landing_pages.find({"$or": ownership_or()}).sort("created_at", -1).limit(200))
    campaign_rows = list(campaigns.find({"status": "active", "$or": ownership_or()}).sort("title", 1))
    collection_rows = list(collections.find({"$or": ownership_or()}).sort("updated_at", -1))
    page_collection_cards = [{"collection": c, "items": list(collection_items.find({"collection_id": c["_id"], "active": {"$ne": False}}).sort("created_at", 1).limit(300))} for c in collection_rows]
    domain_rows = list(domain_mappings.find({"$or": ownership_or()}).sort("domain", 1))
    domain_key = str(request.args.get("domain") or "internal")
    selected_domain = None
    route_rows = []
    route_items = []
    selected_route = None
    selected_page = None
    selected_site = None
    selected_nav_item = None

    if domain_key == "internal":
        for page in page_rows:
            route_items.append({
                "id": None,
                "page_id": str(page["_id"]),
                "path": f"/p/{page.get('public_id')}",
                "page_title": page.get("title") or "Website",
                "status": "published" if page.get("published") else page.get("generation_status", "draft"),
                "public_url": f"{Config.PUBLIC_BASE_URL}/p/{page.get('public_id')}",
            })
        requested_page = request.args.get("page")
        if requested_page:
            selected_page = owned_page(requested_page)
        elif page_rows:
            selected_page = page_rows[0]
    else:
        selected_domain = owned_domain(domain_key)
        if not selected_domain:
            flash("Connected domain not found.", "warning")
            return redirect(url_for("websites", domain="internal"))
        route_rows = list(domain_routes.find({"domain_id": selected_domain["_id"]}).sort("path_key", 1))
        route_items = [route_payload(route, domain=selected_domain) for route in route_rows]
        requested_route = request.args.get("route")
        if requested_route:
            selected_route = next((r for r in route_rows if str(r["_id"]) == str(requested_route)), None)
        elif route_rows:
            selected_route = route_rows[0]
        if selected_route:
            selected_page = landing_pages.find_one({"_id": selected_route.get("page_id"), "$or": ownership_or()})

    # v3.0.3 migration/website grouping: pages on the same domain inherit one site identity.
    if selected_domain:
        selected_site = ensure_website_site(domain=selected_domain, name=selected_domain.get("domain"))
        for route in route_rows:
            route_page = landing_pages.find_one({"_id": route.get("page_id"), "$or": ownership_or()})
            if route_page and route_page.get("site_id") != selected_site.get("_id"):
                selected_site = register_site_page(selected_site, route_page, route.get("path") or "/", label=("Home" if (route.get("path") or "/") == "/" else route_page.get("title")))
    elif domain_key == "internal":
        selected_site = ensure_website_site(name="Primary website")
        for route_page in page_rows:
            if not route_page.get("site_id"):
                selected_site = register_site_page(selected_site, route_page, f"/p/{route_page.get('public_id')}", label=route_page.get("title"))

    if selected_page:
        selected_page = landing_pages.find_one({"_id": selected_page["_id"]}) or selected_page
        if selected_page.get("site_id"):
            selected_site = website_sites.find_one({"_id": selected_page.get("site_id")}) or selected_site
        if selected_site:
            selected_nav_item = next((item for item in (selected_site.get("navigation") or []) if item.get("page_id") == selected_page.get("_id")), None)

    versions = []
    selected_version = None
    selected_version_key = request.args.get("version") or "current"
    selected_notes = ""
    change_jobs = []
    if selected_page:
        raw_snapshots = list(landing_page_versions.find({"landing_page_id": selected_page["_id"]}).sort([("revision", -1), ("created_at", -1)]).limit(100))
        snapshots = []
        seen_revisions = set()
        for snapshot in raw_snapshots:
            revision_number = int(snapshot.get("revision") or 0)
            if revision_number in seen_revisions:
                continue
            seen_revisions.add(revision_number)
            snapshots.append(snapshot)
            if len(snapshots) >= 50:
                break
        versions = [{
            "key": "current", "id": None, "revision": int(selected_page.get("revision") or 0),
            "reason": "Current working version", "created_at": selected_page.get("generated_at") or selected_page.get("updated_at"),
            "is_current": True,
        }] + [{
            "key": str(v["_id"]), "id": v["_id"], "revision": int(v.get("revision") or 0),
            "reason": v.get("reason") or "Saved revision", "created_at": v.get("created_at"), "is_current": False,
        } for v in snapshots]
        if selected_version_key == "current":
            selected_version = {**versions[0], "html": selected_page.get("html") or "", "metadata": selected_page.get("metadata") or {}, "quality": selected_page.get("quality") or {}}
            selected_notes = selected_page.get("review_notes") or ""
        else:
            try:
                snapshot = landing_page_versions.find_one({"_id": ObjectId(selected_version_key), "landing_page_id": selected_page["_id"]})
            except Exception:
                snapshot = None
            if snapshot:
                selected_version = {
                    "key": str(snapshot["_id"]), "id": snapshot["_id"], "revision": int(snapshot.get("revision") or 0),
                    "reason": snapshot.get("reason") or "Saved revision", "created_at": snapshot.get("created_at"),
                    "is_current": False, "html": snapshot.get("html") or "", "metadata": snapshot.get("metadata") or {}, "quality": snapshot.get("quality") or {},
                }
                selected_notes = snapshot.get("review_notes") or ""
            else:
                selected_version = {**versions[0], "html": selected_page.get("html") or "", "metadata": selected_page.get("metadata") or {}, "quality": selected_page.get("quality") or {}}
                selected_version_key = "current"
                selected_notes = selected_page.get("review_notes") or ""
        change_jobs = list(landing_page_change_jobs.find({"landing_page_id": selected_page["_id"]}).sort("created_at", -1).limit(8))

    return render_template(
        "websites.html",
        pages=page_rows, campaigns=campaign_rows, collection_cards=page_collection_cards, domains=domain_rows, publishing_ip=Config.PUBLISHING_IP,
        domain_key=domain_key, selected_domain=selected_domain, route_items=route_items, selected_route=selected_route,
        selected_page=selected_page, selected_site=selected_site, selected_nav_item=selected_nav_item, versions=versions, selected_version=selected_version, selected_version_key=selected_version_key,
        selected_notes=selected_notes, change_jobs=change_jobs,
    )

@app.route("/pages/<page_id>")
@login_required
def page_detail(page_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    route = domain_routes.find_one({"page_id": page["_id"], "$or": ownership_or()})
    if route:
        return redirect(url_for("websites", domain=str(route.get("domain_id")), route=str(route["_id"])))
    return redirect(url_for("websites", domain="internal", page=page_id))


@app.post("/pages/<page_id>/regenerate")
@login_required
def page_regenerate(page_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    landing_pages.update_one(
        {"_id": page["_id"]},
        {"$set": {"generation_status": "queued", "updated_at": now()}, "$unset": {"generation_error": ""}},
    )
    generate_landing_page_task.delay(str(page["_id"]))
    flash("Page regeneration queued.", "success")
    return redirect(url_for("page_detail", page_id=page_id))


@app.post("/pages/<page_id>/publish")
@login_required
def page_publish(page_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    if not page.get("html"):
        flash("Generate the page before publishing it.", "warning")
    else:
        current_revision = int(page.get("revision") or 0)
        published_revision = int(page.get("published_revision") or 0)
        if page.get("published") and published_revision == current_revision:
            landing_pages.update_one({"_id": page["_id"]}, {"$set": {"published": False, "updated_at": now()}})
            domain_routes.update_many({"page_id": page["_id"]}, {"$set": {"status": "draft", "updated_at": now()}})
            flash("Page unpublished on NJS and all mapped domains.", "success")
        else:
            landing_pages.update_one(
                {"_id": page["_id"]},
                {"$set": {
                    "published": True,
                    "published_at": now(),
                    "published_revision": current_revision,
                    "published_html": page.get("html"),
                    "published_metadata": page.get("metadata") or {},
                    "published_article_ids": page.get("article_ids") or [],
                    "updated_at": now(),
                }},
            )
            domain_routes.update_many({"page_id": page["_id"]}, {"$set": {"status": "published", "updated_at": now()}})
            flash("Page published. Verified domain routes are now live." if not page.get("published") else "Published page and domain routes updated.", "success")
    return redirect(url_for("page_detail", page_id=page_id))


@app.post("/websites/sites/<site_id>/navigation/pages/<page_id>/add")
@login_required
def website_add_page_to_navigation(site_id, page_id):
    site = owned_site(site_id)
    page = owned_page(page_id)
    if not site or not page or page.get("site_id") != site.get("_id"):
        abort(404)
    if not page.get("html"):
        flash("Wait for page generation to finish before adding it to the live website navigation.", "warning")
        return redirect(url_for("page_detail", page_id=page_id))

    publish_now = request.form.get("publish") == "1"
    if publish_now and not page.get("published"):
        current_revision = int(page.get("revision") or 0)
        landing_pages.update_one(
            {"_id": page["_id"]},
            {"$set": {
                "published": True, "published_at": now(), "published_revision": current_revision,
                "published_html": page.get("html"), "published_metadata": page.get("metadata") or {},
                "published_article_ids": page.get("article_ids") or [], "updated_at": now(),
            }},
        )
        domain_routes.update_many({"page_id": page["_id"]}, {"$set": {"status": "published", "updated_at": now()}})
        page = landing_pages.find_one({"_id": page["_id"]}) or page
    elif not page.get("published"):
        flash("Publish the page before exposing it in website navigation, or use “Publish + add to navigation”.", "warning")
        return redirect(url_for("page_detail", page_id=page_id))

    nav = list(site.get("navigation") or [])
    found = False
    for item in nav:
        if item.get("page_id") == page["_id"]:
            item["enabled"] = True
            item["label"] = clean_text(request.form.get("label"), 80) or item.get("label") or page.get("title") or "Page"
            item["updated_at"] = now()
            found = True
            break
    if not found:
        route = domain_routes.find_one({"page_id": page["_id"]})
        nav.append({
            "page_id": page["_id"], "label": clean_text(request.form.get("label"), 80) or page.get("title") or "Page",
            "path": (route or {}).get("path") or page.get("site_path") or f"/p/{page.get('public_id')}",
            "enabled": True, "order": len(nav) * 10, "added_at": now(), "updated_at": now(),
        })
    version = int(site.get("navigation_version") or 1) + 1
    website_sites.update_one({"_id": site["_id"]}, {"$set": {"navigation": nav, "navigation_version": version, "updated_at": now()}})
    landing_pages.update_one({"_id": page["_id"]}, {"$set": {"navigation_enabled": True, "navigation_pending": False, "updated_at": now()}})
    flash(f"{page.get('title') or 'Page'} is now visible in the shared website navigation. Existing pages update automatically.", "success")
    return redirect(url_for("page_detail", page_id=page_id))


@app.post("/websites/sites/<site_id>/navigation/pages/<page_id>/hide")
@login_required
def website_hide_page_from_navigation(site_id, page_id):
    site = owned_site(site_id)
    page = owned_page(page_id)
    if not site or not page or page.get("site_id") != site.get("_id"):
        abort(404)
    nav = list(site.get("navigation") or [])
    for item in nav:
        if item.get("page_id") == page["_id"]:
            item["enabled"] = False
            item["updated_at"] = now()
    website_sites.update_one({"_id": site["_id"]}, {"$set": {"navigation": nav, "navigation_version": int(site.get("navigation_version") or 1) + 1, "updated_at": now()}})
    landing_pages.update_one({"_id": page["_id"]}, {"$set": {"navigation_enabled": False, "navigation_pending": True, "updated_at": now()}})
    flash(f"{page.get('title') or 'Page'} is hidden from the shared website navigation. The page itself remains published if it was already live.", "success")
    return redirect(url_for("page_detail", page_id=page_id))


@app.route("/pages/<page_id>/preview")
@login_required
def page_preview(page_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    version_key = request.args.get("version") or "current"
    preview_page = dict(page)
    if version_key != "current":
        try:
            version = landing_page_versions.find_one({"_id": ObjectId(version_key), "landing_page_id": page["_id"]})
        except Exception:
            version = None
        if not version:
            abort(404)
        preview_page["html"] = version.get("html") or ""
        preview_page["metadata"] = version.get("metadata") or page.get("metadata") or {}
        preview_page["article_ids"] = version.get("article_ids") or page.get("article_ids") or []
    if not preview_page.get("html"):
        abort(404)
    article_docs = page_article_docs(preview_page)
    preview_route = domain_routes.find_one({"page_id": page["_id"], "$or": ownership_or()})
    preview_domain = domain_mappings.find_one({"_id": preview_route.get("domain_id")}) if preview_route else None
    site, site_navigation, current_site_path = site_render_context(preview_page, domain=preview_domain, preview=True)
    return Response(compile_landing_html(
        preview_page, article_docs, public=False, site=site, navigation=site_navigation, current_path=current_site_path
    ), mimetype="text/html")


@app.post("/pages/<page_id>/versions/<version_id>/restore")
@login_required
def page_restore_version(page_id, version_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    try:
        version = landing_page_versions.find_one({"_id": ObjectId(version_id), "landing_page_id": page["_id"]})
    except Exception:
        version = None
    if not version:
        abort(404)
    # Save the current state before restoring an older revision.
    if page.get("html"):
        landing_page_versions.insert_one({
            "landing_page_id": page["_id"],
            "revision": int(page.get("revision") or 0),
            "reason": "Before revision restore",
            "html": page.get("html"),
            "metadata": page.get("metadata") or {},
            "design_plan": page.get("design_plan") or {},
            "visual_assets": page.get("visual_assets") or [],
            "quality": page.get("quality") or {},
            "article_ids": page.get("article_ids") or [],
            "review_notes": page.get("review_notes") or "",
            "review_notes_updated_at": page.get("review_notes_updated_at"),
            "created_at": now(),
        })
    landing_pages.update_one(
        {"_id": page["_id"]},
        {"$set": {
            "html": version.get("html", ""),
            "metadata": version.get("metadata") or {},
            "design_plan": version.get("design_plan") or {},
            "visual_assets": version.get("visual_assets") or [],
            "quality": version.get("quality") or {},
            "revision": int(page.get("revision") or 0) + 1,
            "generation_status": "completed",
            "updated_at": now(),
        }},
    )
    flash("Previous website-page revision restored as a new current version.", "success")
    return redirect(url_for("page_detail", page_id=page_id))


@app.post("/websites/domains/connect")
@login_required
def website_connect_domain():
    try:
        hostname = normalize_domain(request.form.get("domain"))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("websites", connect="1"))
    existing = domain_mappings.find_one({"domain": hostname})
    if existing:
        if document_in_workspace(existing):
            flash("That domain is already connected to this workspace.", "warning")
            return redirect(url_for("websites", domain=str(existing["_id"])))
        flash("That domain has already been claimed by another NJS workspace.", "danger")
        return redirect(url_for("websites", connect="1"))
    doc = {
        "user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id,
        "domain": hostname, "status": "pending", "expected_ip": Config.PUBLISHING_IP,
        "resolved_ips": [], "created_at": now(), "updated_at": now(),
    }
    doc["_id"] = domain_mappings.insert_one(doc).inserted_id
    ensure_website_site(domain=doc, name=hostname)
    flash(f"Domain connected. Add an A record pointing {hostname} to {Config.PUBLISHING_IP}, then verify DNS.", "success")
    return redirect(url_for("websites", domain=str(doc["_id"])))


@app.post("/websites/pages/<page_id>/notes")
@login_required
def website_save_notes(page_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    notes = clean_text(request.form.get("notes"), 12000)
    version_key = request.form.get("version") or "current"
    if version_key == "current":
        landing_pages.update_one({"_id": page["_id"]}, {"$set": {"review_notes": notes, "review_notes_revision": int(page.get("revision") or 0), "review_notes_updated_at": now(), "updated_at": now()}})
    else:
        try:
            version_oid = ObjectId(version_key)
        except Exception:
            abort(400)
        result = landing_page_versions.update_one({"_id": version_oid, "landing_page_id": page["_id"]}, {"$set": {"review_notes": notes, "review_notes_updated_at": now()}})
        if not result.matched_count:
            abort(404)
    flash("Review notes saved for this version.", "success")
    return redirect(request.form.get("return_to") or url_for("websites", domain="internal", page=page_id, version=version_key))


@app.post("/websites/pages/<page_id>/request-changes")
@login_required
def website_request_changes(page_id):
    page = owned_page(page_id)
    if not page:
        abort(404)
    notes = clean_text(request.form.get("notes"), 12000)
    if not notes:
        flash("Add review notes before requesting AI changes.", "warning")
        return redirect(request.form.get("return_to") or url_for("websites", domain="internal", page=page_id))
    version_key = request.form.get("version") or "current"
    version_oid = None
    base_revision = int(page.get("revision") or 0)
    if version_key != "current":
        try:
            version_oid = ObjectId(version_key)
        except Exception:
            abort(400)
        version = landing_page_versions.find_one({"_id": version_oid, "landing_page_id": page["_id"]})
        if not version:
            abort(404)
        base_revision = int(version.get("revision") or 0)
        landing_page_versions.update_one({"_id": version_oid}, {"$set": {"review_notes": notes, "review_notes_updated_at": now()}})
    else:
        landing_pages.update_one({"_id": page["_id"]}, {"$set": {"review_notes": notes, "review_notes_revision": base_revision, "review_notes_updated_at": now()}})
    job = {
        "user_id": ObjectId(current_user.id), "organization_id": current_user.organization_id,
        "landing_page_id": page["_id"], "version_id": version_oid, "base_revision": base_revision,
        "notes": notes, "status": "queued", "created_at": now(), "updated_at": now(),
    }
    job["_id"] = landing_page_change_jobs.insert_one(job).inserted_id
    result = apply_landing_review_notes_task.delay(str(job["_id"]))
    landing_page_change_jobs.update_one({"_id": job["_id"]}, {"$set": {"celery_task_id": result.id}})
    flash(f"AI changes queued from revision {base_revision}. The reviewed version remains preserved.", "success")
    return redirect(request.form.get("return_to") or url_for("websites", domain="internal", page=page_id, version=version_key))


@app.post("/websites/domains/<domain_id>/verify")
@login_required
def website_domain_verify(domain_id):
    domain = owned_domain(domain_id)
    if not domain:
        abort(404)
    success, message, updated = activate_domain_after_dns(domain)
    category = "success" if updated.get("status") in {"active", "verified"} else ("danger" if updated.get("status") == "provisioning_failed" else "warning")
    flash(message, category)
    return redirect(url_for("websites", domain=domain_id))


@app.route("/p/<public_id>")
def published_page(public_id):
    page = landing_pages.find_one({"public_id": public_id, "published": True})
    if not page or not page.get("html"):
        abort(404)
    published_page = dict(page)
    if page.get("published_html"):
        published_page["html"] = page.get("published_html")
        published_page["metadata"] = page.get("published_metadata") or page.get("metadata") or {}
    host = (request.host.split(":", 1)[0] or "").lower()
    experiment_ctx = experiment_context_for_public("page", page["_id"], page, request)
    published_page = _experiment_snapshot_page(published_page, experiment_ctx)
    article_docs = page_article_docs(published_page, public_only=True)
    event_id, visitor_token, _ = record_view(
        request, owner_doc=page, content_type="page", content_id=page["_id"],
        campaign_id=page.get("campaign_id"), campaign_ids=page_campaign_ids(page), domain=host, path=request.path, internal_view=False,
        experiment_id=(experiment_ctx or {}).get("experiment_id"), variant_id=(experiment_ctx or {}).get("variant_id"),
    )
    site, site_navigation, current_site_path = site_render_context(published_page, domain=None, preview=False)
    rendered = compile_landing_html(
        published_page, article_docs, public=True, site=site, navigation=site_navigation, current_path=current_site_path
    )
    if experiment_ctx:
        rendered = apply_page_variant(rendered, experiment_ctx.get("variant"))
    response = Response(inject_tracking(rendered, event_id), mimetype="text/html")
    if visitor_token:
        response.set_cookie("njs_vid", visitor_token, max_age=31536000, secure=request.is_secure, httponly=False, samesite="Lax")
    _set_experiment_cookie(response, experiment_ctx)
    return response


@app.route("/api/analytics/client", methods=["POST"])
@csrf.exempt
def analytics_client_update():
    data = request.get_json(silent=True) or {}
    event_id = str(data.get("event_id") or "")
    try:
        oid = ObjectId(event_id)
    except Exception:
        return {"ok": False}, 400
    visitor_token, _ = visitor_token_from_request(request, create=False)
    visitor_hash = analytics_hash(visitor_token) if visitor_token else ""
    parent = analytics_events.find_one({"_id": oid})
    if not parent or not parent.get("tracking_allowed") or not visitor_hash or parent.get("visitor_hash") != visitor_hash:
        return {"ok": False}, 403

    kind = clean_text(data.get("kind"), 40) or "engagement"
    if kind == "engagement":
        try:
            duration = max(0, min(int(data.get("duration_ms") or 0), 8 * 60 * 60 * 1000))
            scroll = max(0, min(int(data.get("max_scroll_pct") or 0), 100))
            screen_w = max(0, min(int(data.get("screen_width") or 0), 20000))
            screen_h = max(0, min(int(data.get("screen_height") or 0), 20000))
            viewport_w = max(0, min(int(data.get("viewport_width") or 0), 20000))
            viewport_h = max(0, min(int(data.get("viewport_height") or 0), 20000))
        except Exception:
            return {"ok": False}, 400
        update = {
            "duration_ms": duration,
            "max_scroll_pct": scroll,
            "screen_width": screen_w,
            "screen_height": screen_h,
            "viewport_width": viewport_w,
            "viewport_height": viewport_h,
            "timezone": clean_text(data.get("timezone"), 80),
            "client_language": clean_text(data.get("language"), 40),
            "engaged": bool(duration >= 10000 or scroll >= 50),
            "updated_at": now(),
        }
        analytics_events.update_one({"_id": oid, "visitor_hash": visitor_hash}, {"$set": update})
        return {"ok": True}

    if kind == "conversion":
        conversion_type = clean_text(data.get("conversion_type"), 40)
        if conversion_type not in {"lead", "signup", "purchase"}:
            conversion_type = "lead"
        conversion_name = clean_text(data.get("conversion_name"), 120) or "conversion"
        label = clean_text(data.get("label"), 180) or conversion_name
        value = None
        try:
            raw_value = data.get("value")
            if raw_value not in (None, ""):
                value = max(0.0, min(float(raw_value), 1000000000.0))
        except Exception:
            value = None
        product_id = None
        latest_product_click = analytics_events.find_one({
            "event_type": "product_click", "content_type": parent.get("content_type"), "content_id": parent.get("content_id"),
            "visitor_hash": visitor_hash, "occurred_at": {"$gte": now() - timedelta(hours=8)},
        }, sort=[("occurred_at", -1)])
        if latest_product_click and isinstance(latest_product_click.get("product_id"), ObjectId):
            product_id = latest_product_click.get("product_id")
        analytics_events.insert_one(child_event_from_view(
            parent, event_type=conversion_type, label=label, product_id=product_id, value=value,
            currency=clean_text(data.get("currency"), 12), conversion_name=conversion_name,
        ))
        return {"ok": True, "event_type": conversion_type}

    if kind != "interaction":
        return {"ok": False}, 400
    interaction = clean_text(data.get("interaction"), 40)
    if interaction not in {"click", "form_start", "form_submit"}:
        return {"ok": False}, 400
    label = clean_text(data.get("label"), 180)
    target_url = clean_text(data.get("target_url"), 2000)
    event_type = interaction
    product_id = None
    if interaction == "click":
        event_type = "click"
        target_host = ""
        target_path = ""
        try:
            parsed_target = urlsplit(target_url)
            target_host = (parsed_target.hostname or "").lower()
            target_path = (parsed_target.path or "/").rstrip("/") or "/"
        except Exception:
            pass
        parent_product_ids = [x for x in (parent.get("product_ids") or []) if isinstance(x, ObjectId)]
        product_rows = list(products.find({"_id": {"$in": parent_product_ids}}, {"product_url": 1, "cta_url": 1})) if parent_product_ids else []
        for product in product_rows:
            for candidate in (product.get("cta_url"), product.get("product_url")):
                try:
                    parsed = urlsplit(candidate or "")
                    host = (parsed.hostname or "").lower()
                    path = (parsed.path or "/").rstrip("/") or "/"
                except Exception:
                    continue
                if host and host == target_host and path == target_path:
                    event_type = "product_click"
                    product_id = product["_id"]
                    break
            if product_id:
                break
        if event_type != "product_click":
            cta_hint = bool(data.get("cta_hint"))
            if cta_hint:
                event_type = "cta_click"
            elif target_host and target_host != str(parent.get("domain") or "").lower():
                event_type = "outbound_click"
    analytics_events.insert_one(child_event_from_view(
        parent, event_type=event_type, label=label, target_url=target_url, product_id=product_id,
    ))
    return {"ok": True, "event_type": event_type}



def _experiment_target_options():
    scope = {"$or": ownership_or()}
    page_rows = list(landing_pages.find({**scope, "published": True, "html": {"$exists": True}}, {
        "title": 1, "published_metadata": 1, "metadata": 1, "public_id": 1, "updated_at": 1,
    }).sort("updated_at", -1).limit(500))
    article_rows = list(articles.find({**scope, "published": True, "published_revision": {"$gt": 0}}, {
        "title": 1, "published_metadata": 1, "metadata": 1, "created_at": 1,
    }).sort("created_at", -1).limit(1000))
    pages_out = []
    for row in page_rows:
        meta = row.get("published_metadata") or row.get("metadata") or {}
        pages_out.append({"id": str(row["_id"]), "type": "page", "label": row.get("title") or meta.get("title") or "Untitled page"})
    articles_out = []
    for row in article_rows:
        meta = row.get("published_metadata") or row.get("metadata") or {}
        articles_out.append({"id": str(row["_id"]), "type": "article", "label": meta.get("title") or row.get("title") or "Untitled article"})
    return pages_out, articles_out


def _experiment_target_doc(target_type, target_id):
    try:
        oid = target_id if isinstance(target_id, ObjectId) else ObjectId(str(target_id))
    except Exception:
        return None
    if target_type == "page":
        return landing_pages.find_one({"_id": oid, **workspace_scope()})
    if target_type == "article":
        return articles.find_one({"_id": oid, **workspace_scope()})
    return None


def _experiment_target_label(exp):
    target = _experiment_target_doc(exp.get("target_type"), exp.get("target_id"))
    if not target:
        return "Target unavailable"
    if exp.get("target_type") == "page":
        return target.get("title") or (target.get("published_metadata") or target.get("metadata") or {}).get("title") or "Untitled page"
    return (target.get("published_metadata") or target.get("metadata") or {}).get("title") or target.get("title") or "Untitled article"


def _experiment_target_revision(target):
    return int((target or {}).get("published_revision") or 0)


def _experiment_control_snapshot(target_type, target):
    """Freeze the exact published target used as the experiment Control."""
    revision = _experiment_target_revision(target)
    if target_type == "page":
        published_page = dict(target or {})
        published_page["html"] = target.get("published_html") or target.get("html") or ""
        published_page["metadata"] = target.get("published_metadata") or target.get("metadata") or {}
        if target.get("published_article_ids") is not None:
            published_page["article_ids"] = list(target.get("published_article_ids") or [])
        visible_articles = page_article_docs(published_page, public_only=True)
        return {
            "revision": revision,
            "html": published_page.get("html") or "",
            "metadata": published_page.get("metadata") or {},
            "article_ids": [row.get("_id") for row in visible_articles if row.get("_id")],
            "article_mode": "selected",
            "campaign_id": target.get("campaign_id"),
            "campaign_ids": target.get("campaign_ids") or [],
            "title": target.get("title") or "",
        }
    public_doc = published_article_view(target) or {}
    return {
        "revision": revision,
        "content": public_doc.get("content") or "",
        "metadata": public_doc.get("metadata") or {},
        "quality": public_doc.get("quality") or {},
        "publication": target.get("publication") or {},
        "title": target.get("title") or "",
    }


def _experiment_snapshot_page(page, experiment_ctx):
    snap = (((experiment_ctx or {}).get("experiment") or {}).get("control_snapshot") or {})
    if not snap:
        return page
    view = dict(page or {})
    view["html"] = snap.get("html") or view.get("html") or ""
    view["metadata"] = snap.get("metadata") or view.get("metadata") or {}
    view["article_mode"] = snap.get("article_mode") or "selected"
    view["article_ids"] = list(snap.get("article_ids") or [])
    view["published_article_ids"] = list(snap.get("article_ids") or [])
    if snap.get("campaign_id"):
        view["campaign_id"] = snap.get("campaign_id")
    if snap.get("campaign_ids") is not None:
        view["campaign_ids"] = snap.get("campaign_ids") or []
    return view


def _experiment_snapshot_article(article_view, experiment_ctx):
    snap = (((experiment_ctx or {}).get("experiment") or {}).get("control_snapshot") or {})
    if not snap:
        return article_view
    view = dict(article_view or {})
    view["content"] = snap.get("content") or view.get("content") or ""
    view["metadata"] = snap.get("metadata") or view.get("metadata") or {}
    view["quality"] = snap.get("quality") or view.get("quality") or {}
    view["revision"] = int(snap.get("revision") or view.get("revision") or 0)
    if snap.get("publication"):
        view["publication"] = snap.get("publication")
    return view


def _experiment_variant_from_form(prefix="challenger"):
    placement = clean_text(request.form.get(f"{prefix}_cta_placement"), 40) or "after_content"
    if placement not in {"existing", "after_hero", "after_content", "sticky_bottom"}:
        placement = "after_content"
    raw_cta_url = clean_text(request.form.get(f"{prefix}_cta_url"), 2000)
    try:
        cta_url = _simple_http_url(raw_cta_url, "CTA destination") if raw_cta_url else ""
    except ValueError:
        cta_url = ""
    return {
        "id": prefix,
        "name": clean_text(request.form.get(f"{prefix}_name"), 80) or "Challenger",
        "weight": 50,
        "overrides": {
            "headline": clean_text(request.form.get(f"{prefix}_headline"), 180),
            "subheadline": clean_text(request.form.get(f"{prefix}_subheadline"), 500),
            "cta_label": clean_text(request.form.get(f"{prefix}_cta_label"), 180),
            "cta_url": cta_url,
            "cta_placement": placement,
        },
    }


@app.route("/experiments")
@login_required
def experiment_list():
    rows = list(experiments.find(workspace_scope()).sort("updated_at", -1))
    cards = []
    for exp in rows:
        results = experiment_results(exp) if exp.get("status") in {"running", "paused", "completed"} else []
        decision = experiment_decision(exp, results) if results else {"ready": False}
        cards.append({"experiment": exp, "target_label": _experiment_target_label(exp), "results": results, "decision": decision})
    return render_template("experiments.html", cards=cards)


@app.route("/experiments/new", methods=["GET", "POST"])
@login_required
def experiment_new():
    pages_out, articles_out = _experiment_target_options()
    if request.method == "POST":
        raw_target = clean_text(request.form.get("target"), 120)
        if ":" not in raw_target:
            flash("Choose a published page or article to test.", "warning")
            return render_template("experiment_form.html", pages=pages_out, articles=articles_out, values=request.form)
        target_type, target_id = raw_target.split(":", 1)
        if target_type not in {"page", "article"} or not ObjectId.is_valid(target_id):
            flash("The selected experiment target is invalid.", "danger")
            return render_template("experiment_form.html", pages=pages_out, articles=articles_out, values=request.form)
        target = _experiment_target_doc(target_type, target_id)
        if not target:
            abort(404)
        if target_type == "page" and not target.get("published"):
            flash("Publish the page before starting an experiment.", "warning")
            return render_template("experiment_form.html", pages=pages_out, articles=articles_out, values=request.form)
        if target_type == "article" and not article_is_public(target):
            flash("Sign and publish the article before starting an experiment.", "warning")
            return render_template("experiment_form.html", pages=pages_out, articles=articles_out, values=request.form)

        primary_metric = clean_text(request.form.get("primary_metric"), 40) or "conversion_rate"
        if primary_metric not in PRIMARY_METRICS:
            primary_metric = "conversion_rate"
        try:
            control_weight = max(5, min(int(request.form.get("control_weight") or 50), 95))
        except Exception:
            control_weight = 50
        challenger_weight = 100 - control_weight
        try:
            min_sample = max(20, min(int(request.form.get("min_sample_size") or 100), 1000000))
        except Exception:
            min_sample = 100
        challenger = _experiment_variant_from_form()
        challenger["weight"] = challenger_weight
        control = {"id": "control", "name": "Control", "weight": control_weight, "overrides": {}}
        control_snapshot = _experiment_control_snapshot(target_type, target)
        doc = {
            **workspace_scope(),
            "created_by_user_id": ObjectId(current_user.id),
            "name": clean_text(request.form.get("name"), 160) or f"{_experiment_target_label({'target_type': target_type, 'target_id': ObjectId(target_id)})} test",
            "hypothesis": clean_text(request.form.get("hypothesis"), 1200),
            "target_type": target_type,
            "target_id": ObjectId(target_id),
            "target_revision": int(control_snapshot.get("revision") or 0),
            "control_snapshot": control_snapshot,
            "primary_metric": primary_metric,
            "min_sample_size": min_sample,
            "variants": [control, challenger],
            "status": "draft",
            "winner_variant_id": "",
            "created_at": now(), "updated_at": now(),
        }
        oid = experiments.insert_one(doc).inserted_id
        flash("Experiment created as a draft. Preview both variants before starting traffic allocation.", "success")
        return redirect(url_for("experiment_detail", experiment_id=str(oid)))
    return render_template("experiment_form.html", pages=pages_out, articles=articles_out, values=request.args)


@app.route("/experiments/<experiment_id>", methods=["GET", "POST"])
@login_required
def experiment_detail(experiment_id):
    exp = owned_experiment(experiment_id)
    if not exp:
        abort(404)
    if request.method == "POST":
        if exp.get("status") in {"running", "completed"}:
            flash("Pause the experiment before editing its challenger." if exp.get("status") == "running" else "Completed experiments are locked. Create a new experiment to test another challenger.", "warning")
            return redirect(url_for("experiment_detail", experiment_id=experiment_id))
        challenger = _experiment_variant_from_form()
        current_variants = list(exp.get("variants") or [])
        control = next((v for v in current_variants if v.get("id") == "control"), {"id": "control", "name": "Control", "weight": 50, "overrides": {}})
        try:
            control_weight = max(5, min(int(request.form.get("control_weight") or control.get("weight") or 50), 95))
        except Exception:
            control_weight = int(control.get("weight") or 50)
        control["weight"] = control_weight
        challenger["weight"] = 100 - control_weight
        metric = clean_text(request.form.get("primary_metric"), 40) or exp.get("primary_metric") or "conversion_rate"
        if metric not in PRIMARY_METRICS:
            metric = "conversion_rate"
        try:
            min_sample = max(20, min(int(request.form.get("min_sample_size") or exp.get("min_sample_size") or 100), 1000000))
        except Exception:
            min_sample = int(exp.get("min_sample_size") or 100)
        experiments.update_one({"_id": exp["_id"]}, {"$set": {
            "name": clean_text(request.form.get("name"), 160) or exp.get("name") or "Experiment",
            "hypothesis": clean_text(request.form.get("hypothesis"), 1200),
            "primary_metric": metric,
            "min_sample_size": min_sample,
            "variants": [control, challenger],
            "updated_at": now(),
        }})
        flash("Experiment settings updated.", "success")
        return redirect(url_for("experiment_detail", experiment_id=experiment_id))
    results = experiment_results(exp)
    decision = experiment_decision(exp, results)
    target = _experiment_target_doc(exp.get("target_type"), exp.get("target_id"))
    target_changed = bool(target and exp.get("target_revision") and _experiment_target_revision(target) != int(exp.get("target_revision") or 0))
    return render_template("experiment_detail.html", experiment=exp, target=target, target_label=_experiment_target_label(exp), results=results, decision=decision, target_changed=target_changed)



@app.post("/experiments/<experiment_id>/suggest")
@login_required
def experiment_suggest(experiment_id):
    exp = owned_experiment(experiment_id)
    if not exp:
        abort(404)
    if exp.get("status") in {"running", "completed"}:
        flash("Pause an active experiment before changing its challenger.", "warning")
        return redirect(url_for("experiment_detail", experiment_id=experiment_id))
    target = _experiment_target_doc(exp.get("target_type"), exp.get("target_id"))
    if not target:
        abort(404)
    if exp.get("target_revision") and _experiment_target_revision(target) != int(exp.get("target_revision") or 0):
        flash("The published target has changed. Create a new experiment for the new revision before generating another challenger.", "warning")
        return redirect(url_for("experiment_detail", experiment_id=experiment_id))
    control_snapshot = exp.get("control_snapshot") or {}
    if exp.get("target_type") == "page":
        meta = control_snapshot.get("metadata") or target.get("published_metadata") or target.get("metadata") or {}
        html_text = control_snapshot.get("html") or target.get("published_html") or target.get("html") or ""
        body_text = clean_text(html_text, 12000)
        title = target.get("title") or meta.get("title") or "Page"
        description = meta.get("meta_description") or meta.get("description") or ""
        campaign_ids = page_campaign_ids(target)
        campaign_rows = list(campaigns.find({"_id": {"$in": campaign_ids}}, {"website_url": 1})) if campaign_ids else []
        allowed_urls = [c.get("website_url") for c in campaign_rows if c.get("website_url")]
    else:
        meta = control_snapshot.get("metadata") or target.get("published_metadata") or target.get("metadata") or {}
        html_text = control_snapshot.get("content") or target.get("published_content") or target.get("content") or ""
        body_text = clean_text(html_text, 12000)
        title = meta.get("title") or target.get("title") or "Article"
        description = meta.get("description") or ""
        pids = [x for x in (target.get("product_ids") or []) if isinstance(x, ObjectId)]
        product_rows = list(products.find({"_id": {"$in": pids}}, {"name": 1, "cta_url": 1, "product_url": 1})) if pids else []
        allowed_urls = []
        for row in product_rows:
            allowed_urls.extend([row.get("cta_url"), row.get("product_url")])
    if not allowed_urls:
        for href in re.findall(r"href=[\"'](https?://[^\"']+)[\"']", str(html_text or ""), flags=re.I):
            allowed_urls.append(href)
    allowed_urls = list(dict.fromkeys([u for u in allowed_urls if isinstance(u, str) and u.startswith(("http://", "https://"))]))[:25]
    prompt = f"""
Create one focused A/B-test challenger for the currently published {exp.get('target_type')}.
Return JSON only with this exact shape:
{{"name":"Challenger","headline":"","subheadline":"","cta_label":"","cta_url":"","cta_placement":"existing|after_hero|after_content|sticky_bottom","rationale":""}}

TEST HYPOTHESIS:
{clean_text(exp.get('hypothesis'), 1600)}
PRIMARY METRIC: {exp.get('primary_metric') or 'conversion_rate'}
CURRENT TITLE: {clean_text(title, 300)}
CURRENT DESCRIPTION: {clean_text(description, 800)}
CURRENT CONTENT EXCERPT:
{body_text}

ALLOWED CTA DESTINATIONS (use one exactly or leave cta_url empty):
{json.dumps(allowed_urls, ensure_ascii=False)}

RULES:
- Change as little as necessary to test the hypothesis. A controlled test must be interpretable.
- Do not invent facts, claims, customer outcomes, pricing, endorsements, urgency or capabilities.
- Preserve the meaning and factual boundaries of the current content.
- Prefer one clear headline/deck or CTA hypothesis, not a wholesale rewrite.
- cta_url MUST be an exact URL from ALLOWED CTA DESTINATIONS or an empty string.
- Choose a CTA placement only if it materially supports the hypothesis.
- rationale must explain in 1-2 sentences what changed and why it isolates the hypothesis.
"""
    try:
        response = model_client().chat.completions.create(
            model=Config.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "You are a rigorous conversion experimentation editor. Design controlled, factual A/B-test challengers. Return strict JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.35,
        )
        payload = parse_json_object(response.choices[0].message.content)
    except Exception as exc:
        flash(f"Could not suggest a challenger: {str(exc)[:220]}", "danger")
        return redirect(url_for("experiment_detail", experiment_id=experiment_id))
    proposed_url = clean_text(payload.get("cta_url"), 2000)
    if proposed_url not in allowed_urls:
        proposed_url = ""
    placement = clean_text(payload.get("cta_placement"), 40) or "existing"
    if placement not in {"existing", "after_hero", "after_content", "sticky_bottom"}:
        placement = "existing"
    variants = list(exp.get("variants") or [])
    for variant in variants:
        if variant.get("id") != "control":
            variant["name"] = clean_text(payload.get("name"), 80) or variant.get("name") or "Challenger"
            variant["overrides"] = {
                "headline": clean_text(payload.get("headline"), 180),
                "subheadline": clean_text(payload.get("subheadline"), 500),
                "cta_label": clean_text(payload.get("cta_label"), 180),
                "cta_url": proposed_url,
                "cta_placement": placement,
            }
            variant["rationale"] = clean_text(payload.get("rationale"), 800)
    experiments.update_one({"_id": exp["_id"]}, {"$set": {"variants": variants, "updated_at": now(), "suggested_at": now()}})
    flash("Challenger suggested from the published content and known CTA destinations. Review it before starting.", "success")
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))

@app.post("/experiments/<experiment_id>/start")
@login_required
def experiment_start(experiment_id):
    exp = owned_experiment(experiment_id)
    if not exp:
        abort(404)
    target = _experiment_target_doc(exp.get("target_type"), exp.get("target_id"))
    if not target:
        flash("The experiment target no longer exists.", "danger")
        return redirect(url_for("experiment_detail", experiment_id=experiment_id))
    expected_revision = int(exp.get("target_revision") or 0)
    current_revision = _experiment_target_revision(target)
    if expected_revision and current_revision != expected_revision:
        experiments.update_one({"_id": exp["_id"]}, {"$set": {"status": "paused", "pause_reason": "target_changed", "paused_at": now(), "updated_at": now()}})
        flash("This target was republished after the experiment was created. Create a new experiment so the Control stays tied to one published revision.", "warning")
        return redirect(url_for("experiment_detail", experiment_id=experiment_id))
    conflict = experiments.find_one({
        **workspace_scope(), "target_type": exp.get("target_type"), "target_id": exp.get("target_id"),
        "status": "running", "_id": {"$ne": exp["_id"]},
    })
    if conflict:
        flash("Another experiment is already allocating traffic on this target. Pause it first.", "warning")
        return redirect(url_for("experiment_detail", experiment_id=experiment_id))
    experiments.update_one({"_id": exp["_id"]}, {
        "$set": {"status": "running", "winner_variant_id": "", "started_at": exp.get("started_at") or now(), "updated_at": now()},
        "$unset": {"pause_reason": "", "paused_at": ""},
    })
    flash("Experiment started. New visitors are now allocated between Control and Challenger.", "success")
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))


@app.post("/experiments/<experiment_id>/pause")
@login_required
def experiment_pause(experiment_id):
    exp = owned_experiment(experiment_id)
    if not exp:
        abort(404)
    experiments.update_one({"_id": exp["_id"]}, {"$set": {"status": "paused", "pause_reason": "manual", "paused_at": now(), "updated_at": now()}})
    flash("Experiment paused. The normal published version is being served while allocation is paused.", "success")
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))


@app.post("/experiments/<experiment_id>/complete")
@login_required
def experiment_complete(experiment_id):
    exp = owned_experiment(experiment_id)
    if not exp:
        abort(404)
    target = _experiment_target_doc(exp.get("target_type"), exp.get("target_id"))
    if not target or (exp.get("target_revision") and _experiment_target_revision(target) != int(exp.get("target_revision") or 0)):
        experiments.update_one({"_id": exp["_id"]}, {"$set": {"status": "paused", "pause_reason": "target_changed", "paused_at": now(), "updated_at": now()}})
        flash("The published target changed, so this experiment cannot lock a winner. Create a new test for the current revision.", "warning")
        return redirect(url_for("experiment_detail", experiment_id=experiment_id))
    winner = clean_text(request.form.get("winner_variant_id"), 40)
    valid = {str(v.get("id")) for v in (exp.get("variants") or [])}
    if winner not in valid:
        flash("Choose a valid winner.", "warning")
        return redirect(url_for("experiment_detail", experiment_id=experiment_id))
    experiments.update_one({"_id": exp["_id"]}, {"$set": {
        "status": "completed", "winner_variant_id": winner, "completed_at": now(), "updated_at": now(),
    }})
    flash("Winner locked. The selected variant now receives 100% of experiment traffic without rewriting the source content.", "success")
    return redirect(url_for("experiment_detail", experiment_id=experiment_id))


@app.post("/experiments/<experiment_id>/delete")
@login_required
def experiment_delete(experiment_id):
    exp = owned_experiment(experiment_id)
    if not exp:
        abort(404)
    if exp.get("status") == "running":
        flash("Pause the experiment before deleting it.", "warning")
        return redirect(url_for("experiment_detail", experiment_id=experiment_id))
    experiments.delete_one({"_id": exp["_id"]})
    flash("Experiment deleted. Historical analytics events remain available in attribution data.", "success")
    return redirect(url_for("experiment_list"))


@app.get("/experiments/<experiment_id>/preview/<variant_id>")
@login_required
def experiment_preview(experiment_id, variant_id):
    exp = owned_experiment(experiment_id)
    if not exp:
        abort(404)
    variant = next((v for v in (exp.get("variants") or []) if str(v.get("id")) == variant_id), None)
    if not variant:
        abort(404)
    target = _experiment_target_doc(exp.get("target_type"), exp.get("target_id"))
    if not target:
        abort(404)
    preview_ctx = {"experiment": exp}
    if exp.get("target_type") == "page":
        preview_page = dict(target)
        preview_page["html"] = target.get("published_html") or target.get("html") or ""
        preview_page["metadata"] = target.get("published_metadata") or target.get("metadata") or {}
        preview_page = _experiment_snapshot_page(preview_page, preview_ctx)
        article_docs = page_article_docs(preview_page, public_only=True)
        site, site_navigation, current_site_path = site_render_context(preview_page, domain=None, preview=True)
        html = compile_landing_html(preview_page, article_docs, public=False, site=site, navigation=site_navigation, current_path=current_site_path)
        html = apply_page_variant(html, variant)
        return Response(html, mimetype="text/html")
    public_doc = published_article_view(target)
    if not public_doc:
        abort(404)
    public_doc = _experiment_snapshot_article(public_doc, preview_ctx)
    public_doc = article_variant_view(public_doc, variant)
    campaign = campaigns.find_one({"_id": target.get("campaign_id")}) if target.get("campaign_id") else None
    hook = hooks.find_one({"_id": target.get("hook_id")}) if target.get("hook_id") else None
    return render_template(
        "public_article.html", article=public_doc, campaign=campaign, hook=hook, preview=True,
        preview_author=current_author_profile(), experiment_variant=variant, analytics_event_id=None, audience_capture_token=None,
    )

@app.route("/analytics")
@login_required
def analytics_workspace():
    start_date, end_date, start_dt, end_dt = parse_date_range(request.args)
    owner = {"$or": ownership_or()}
    campaign_docs = list(campaigns.find(owner, {"title": 1, "status": 1}).sort("title", 1))
    page_docs = list(landing_pages.find(owner, {"title": 1, "campaign_id": 1, "campaign_ids": 1, "published": 1, "public_id": 1, "updated_at": 1}).sort("updated_at", -1).limit(500))
    article_docs = list(articles.find(owner, {"metadata.title": 1, "published_metadata.title": 1, "title": 1, "campaign_id": 1, "product_ids": 1, "newsjacking_worker_id": 1, "hook_id": 1, "created_at": 1}).sort("created_at", -1).limit(1000))
    product_docs = list(products.find(owner, {"name": 1, "status": 1}).sort("name", 1))
    worker_docs = list(newsjacking_workers.find(owner, {"name": 1, "enabled": 1}).sort("name", 1))
    hook_ids = [a.get("hook_id") for a in article_docs if isinstance(a.get("hook_id"), ObjectId)]
    hook_docs = list(hooks.find({"_id": {"$in": hook_ids}}, {"title": 1, "source_url": 1})) if hook_ids else []
    domain_docs = list(domain_mappings.find(owner, {"domain": 1, "status": 1}).sort("domain", 1))
    internal_host = urlsplit(Config.PUBLIC_BASE_URL).hostname or Config.PUBLISHING_PRIMARY_HOST

    route_rows = list(domain_routes.find({"page_id": {"$in": [p["_id"] for p in page_docs]}})) if page_docs else []
    route_by_page = {}
    domain_by_id = {d["_id"]: d for d in domain_docs}
    for r in route_rows:
        d = domain_by_id.get(r.get("domain_id"))
        if d:
            route_by_page.setdefault(r.get("page_id"), []).append({"domain": d.get("domain"), "path": r.get("path") or "/"})

    campaign_map = {str(c["_id"]): c.get("title") or "Untitled campaign" for c in campaign_docs}
    product_map = {str(p["_id"]): p.get("name") or "Untitled product" for p in product_docs}
    worker_map = {str(w["_id"]): w.get("name") or "Untitled worker" for w in worker_docs}
    source_map = {str(h["_id"]): h.get("title") or h.get("source_url") or "Source story" for h in hook_docs}
    page_map = {str(p["_id"]): p.get("title") or "Untitled page" for p in page_docs}
    article_map = {str(a["_id"]): ((a.get("published_metadata") or {}).get("title") or (a.get("metadata") or {}).get("title") or a.get("title") or "Untitled article") for a in article_docs}

    selections = [v for v in request.args.getlist("entity") if v]
    campaign_filters = [v for v in request.args.getlist("campaign_id") if ObjectId.is_valid(v)]
    query = {"occurred_at": {"$gte": start_dt, "$lt": end_dt}, "$or": ownership_or(), "is_bot": {"$ne": True}, "internal_view": {"$ne": True}}
    entity_ors = []
    for value in selections:
        if value.startswith("domain:"):
            entity_ors.append({"domain": value.split(":", 1)[1].lower()})
        elif value.startswith("page:") and ObjectId.is_valid(value.split(":", 1)[1]):
            entity_ors.append({"content_type": "page", "content_id": ObjectId(value.split(":", 1)[1])})
        elif value.startswith("article:") and ObjectId.is_valid(value.split(":", 1)[1]):
            entity_ors.append({"content_type": "article", "content_id": ObjectId(value.split(":", 1)[1])})
    if entity_ors:
        query["$and"] = [{"$or": entity_ors}]
    if campaign_filters:
        campaign_oids = [ObjectId(v) for v in campaign_filters]
        query.setdefault("$and", []).append({"$or": [
            {"campaign_id": {"$in": campaign_oids}},
            {"campaign_ids": {"$in": campaign_oids}},
        ]})

    event_rows = list(analytics_events.find(query).sort("occurred_at", 1))
    sort = clean_text(request.args.get("sort"), 30) or "views"
    content_campaign = {}
    for p in page_docs:
        ids = page_campaign_ids(p)
        names = [campaign_map.get(str(cid)) for cid in ids if campaign_map.get(str(cid))]
        content_campaign[f"page:{p['_id']}"] = ", ".join(names) if names else "No campaign"
    for a in article_docs:
        content_campaign[f"article:{a['_id']}"] = campaign_map.get(str(a.get("campaign_id")), "No campaign")
    report = analytics_report(event_rows, start_date=start_date, end_date=end_date, lookup={
        "page": page_map, "article": article_map, "content_campaign": content_campaign,
        "campaign": campaign_map, "product": product_map, "worker": worker_map, "source": source_map,
    }, selections=selections, sort=sort)

    domains = [{"key": f"domain:{internal_host}", "domain": internal_host, "status": "internal", "internal": True}]
    domains += [{"key": f"domain:{d['domain']}", "domain": d["domain"], "status": d.get("status") or "pending", "internal": False} for d in domain_docs]
    pages = [{"key": f"page:{p['_id']}", "id": str(p["_id"]), "title": page_map[str(p["_id"])], "campaign": content_campaign.get(f"page:{p['_id']}", "No campaign"), "published": bool(p.get("published")), "routes": route_by_page.get(p["_id"], [])} for p in page_docs]
    article_entities = [{"key": f"article:{a['_id']}", "id": str(a["_id"]), "title": article_map[str(a["_id"])], "campaign": campaign_map.get(str(a.get("campaign_id")), "No campaign"), "created_at": a.get("created_at")} for a in article_docs]
    return render_template(
        "analytics.html", report=report, domains=domains, pages=pages, article_entities=article_entities,
        campaigns=campaign_docs, campaign_map=campaign_map, selected_entities=set(selections), selected_campaigns=set(campaign_filters),
        from_date=start_date.isoformat(), to_date=end_date.isoformat(), sort=sort, analytics_settings=analytics_settings_for({"organization_id": current_user.organization_id, "user_id": ObjectId(current_user.id)}),
    )


@app.route("/analytics/settings", methods=["GET", "POST"])
@login_required
def analytics_settings_view():
    owner_doc = {"organization_id": current_user.organization_id, "user_id": ObjectId(current_user.id)}
    scope = {"organization_id": current_user.organization_id} if current_user.organization_id else {"user_id": ObjectId(current_user.id), "organization_id": {"$exists": False}}
    if request.method == "POST":
        mode = clean_text(request.form.get("tracking_mode"), 40)
        if mode not in {"pseudonymous", "consent", "aggregate"}:
            mode = "pseudonymous"
        updates = {
            "user_id": ObjectId(current_user.id),
            "tracking_mode": mode,
            "consent_title": clean_text(request.form.get("consent_title"), 120) or "Privacy choices",
            "consent_text": clean_text(request.form.get("consent_text"), 1000) or "Allow pseudonymous analytics so we can understand which content is useful and improve this website.",
            "updated_at": now(),
        }
        if current_user.organization_id:
            updates["organization_id"] = current_user.organization_id
        analytics_settings.update_one(scope, {"$set": updates, "$setOnInsert": {"created_at": now()}}, upsert=True)
        flash("Analytics privacy settings updated.", "success")
        return redirect(url_for("analytics_settings_view"))
    return render_template("analytics_settings.html", settings=analytics_settings_for(owner_doc))


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404
