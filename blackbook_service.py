import base64
import hashlib
from urllib.parse import urljoin

import requests
from cryptography.fernet import Fernet, InvalidToken

from config import Config
from db import sso_organization_links


def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(Config.SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(value):
    value = str(value or "").strip()
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""


def connector_for_local_org(local_org_id):
    if not local_org_id:
        return {"configured": False, "enabled": False}
    link = sso_organization_links.find_one({"local_organization_id": local_org_id}) or {}
    raw = link.get("blackbook") or {}
    api_key = decrypt_secret(raw.get("api_key_encrypted"))
    base_url = str(raw.get("base_url") or Config.BLACKBOOK_BASE_URL).strip().rstrip("/")
    org_identifier = str(raw.get("organization_identifier") or link.get("syntal_org_id") or "").strip()
    enabled = bool(raw.get("enabled"))
    return {
        "configured": bool(enabled and api_key and base_url and org_identifier),
        "enabled": enabled,
        "base_url": base_url,
        "organization_identifier": org_identifier,
        "api_key": api_key,
        "auto_mailchimp": bool(raw.get("auto_mailchimp")),
        "updated_at": raw.get("updated_at"),
        "syntal_org_id": link.get("syntal_org_id"),
    }


def public_connector_status(local_org_id):
    cfg = connector_for_local_org(local_org_id)
    return {
        "configured": bool(cfg.get("configured")),
        "enabled": bool(cfg.get("enabled")),
        "base_url": cfg.get("base_url") or Config.BLACKBOOK_BASE_URL,
        "organization_identifier": cfg.get("organization_identifier") or "",
        "auto_mailchimp": bool(cfg.get("auto_mailchimp")),
        "updated_at": cfg.get("updated_at"),
    }


def sync_submission(submission, article, campaign=None):
    cfg = connector_for_local_org(article.get("organization_id"))
    if not cfg.get("configured"):
        raise RuntimeError("BlackBook integration is not configured for this organization")

    email = str(submission.get("email") or "").strip().lower()
    name = str(submission.get("name") or "").strip()
    survey = submission.get("survey") or []
    consent = submission.get("marketing_consent") or {}
    engagement = article.get("engagement") or {}
    metadata = article.get("published_metadata") or article.get("metadata") or {}
    article_title = str(metadata.get("title") or "Untitled article")[:300]
    campaign_title = str((campaign or {}).get("title") or "")[:300]

    survey_lines = []
    for item in survey[:20]:
        question = str(item.get("question") or "").strip()
        answer = item.get("answer")
        if isinstance(answer, list):
            answer = ", ".join(str(x) for x in answer)
        answer = str(answer or "").strip()
        if question and answer:
            survey_lines.append(f"{question}: {answer}")
    message = f"Audience response from NJS article: {article_title}"
    if survey_lines:
        message += "\n\nSurvey responses:\n" + "\n".join(f"- {line}" for line in survey_lines)

    tags = ["NJS:Audience", f"NJS:Article:{str(article.get('_id'))}"]
    if campaign:
        tags.append(f"NJS:Campaign:{campaign_title or str(campaign.get('_id'))}"[:100])

    payload = {
        "request_type": "general_contact",
        "source_type": "campaign_response",
        "name": name,
        "email": email,
        "message": message,
        "source_url": submission.get("source_url") or "",
        "external_source_id": submission.get("submission_id") or str(submission.get("_id") or ""),
        "source_detail": f"Newsjacking article · {article_title}"[:1000],
        "metadata": {
            "source_system": "newsjacking-core",
            "njs_submission_id": submission.get("submission_id") or str(submission.get("_id") or ""),
            "article_id": str(article.get("_id") or ""),
            "article_title": article_title,
            "campaign_id": str(article.get("campaign_id") or ""),
            "campaign_title": campaign_title,
            "domain": submission.get("domain") or "",
            "path": submission.get("path") or "",
            "utm": submission.get("utm") or {},
            "survey": survey,
            "marketing_consent": consent,
            "visitor_hash": submission.get("visitor_hash") or "",
        },
        "marketing_consent": {
            "granted": bool(consent.get("granted")),
            "text": consent.get("text") or "",
            "captured_at": consent.get("captured_at").isoformat() if hasattr(consent.get("captured_at"), "isoformat") else str(consent.get("captured_at") or ""),
            "source": "newsjacking_article",
        },
        "mailchimp_sync": bool(consent.get("granted") and submission.get("mailchimp_sync_requested") and cfg.get("auto_mailchimp")),
        "mailchimp_tags": tags,
    }
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "X-BlackBook-Organization": cfg["organization_identifier"],
        "Idempotency-Key": f"njs:{payload['external_source_id']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = urljoin(cfg["base_url"] + "/", "api/v1/prospects/intake")
    response = requests.post(url, json=payload, headers=headers, timeout=Config.BLACKBOOK_HTTP_TIMEOUT_SECONDS)
    try:
        data = response.json()
    except Exception:
        data = {"ok": False, "error": response.text[:1000]}
    if response.status_code >= 400:
        raise RuntimeError(f"BlackBook returned HTTP {response.status_code}: {data.get('error') or data}")
    return data


def _marketing_bridge_request(local_org_id, method, path, *, payload=None, params=None, idempotency_key=None, timeout=None):
    """Call the organization-scoped BlackBook marketing bridge.

    NJS never receives Mailchimp credentials. The same organization-scoped BlackBook
    integration credential already used for prospect intake authenticates the bridge.
    """
    cfg = connector_for_local_org(local_org_id)
    if not cfg.get("configured"):
        raise RuntimeError("BlackBook integration is not configured for this organization")
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "X-BlackBook-Organization": cfg["organization_identifier"],
        "Accept": "application/json",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = str(idempotency_key)[:240]
    url = urljoin(cfg["base_url"] + "/", path.lstrip("/"))
    response = requests.request(
        method.upper(), url, json=payload, params=params, headers=headers,
        timeout=timeout or Config.BLACKBOOK_HTTP_TIMEOUT_SECONDS,
    )
    try:
        data = response.json()
    except Exception:
        data = {"ok": False, "error": response.text[:1200]}
    if response.status_code >= 400:
        detail = data.get("error") if isinstance(data, dict) else data
        raise RuntimeError(f"BlackBook returned HTTP {response.status_code}: {detail or data}")
    return data


def marketing_context(local_org_id):
    return _marketing_bridge_request(local_org_id, "GET", "/api/v1/marketing/context")


def marketing_people(local_org_id, *, query="", limit=24, eligible_only=False):
    return _marketing_bridge_request(local_org_id, "GET", "/api/v1/marketing/people", params={
        "q": str(query or "")[:160],
        "limit": max(1, min(100, int(limit or 24))),
        "eligible": "1" if eligible_only else "0",
    })


def marketing_segments(local_org_id):
    return _marketing_bridge_request(local_org_id, "GET", "/api/v1/marketing/segments")


def marketing_campaigns(local_org_id, *, limit=30):
    return _marketing_bridge_request(local_org_id, "GET", "/api/v1/marketing/campaigns", params={
        "limit": max(1, min(100, int(limit or 30))),
    })


def marketing_audience_preview(local_org_id, *, segment_ids=None, person_ids=None, include_all_eligible=False,
                               engagement_buckets=None, interest_ids=None, interest_match="any"):
    match_mode = "all" if str(interest_match or "any").lower() == "all" else "any"
    return _marketing_bridge_request(local_org_id, "POST", "/api/v1/marketing/audience/preview", payload={
        "segment_ids": [str(x) for x in (segment_ids or []) if str(x).strip()][:50],
        "person_ids": [str(x) for x in (person_ids or []) if str(x).strip()][:500],
        "include_all_eligible": bool(include_all_eligible),
        "engagement_buckets": [str(x).lower() for x in (engagement_buckets or []) if str(x).strip()][:4],
        "interest_ids": [str(x) for x in (interest_ids or []) if str(x).strip()][:100],
        "interest_match": match_mode,
    })


def distribute_newsletter(local_org_id, *, edition_id, schedule_id, subject, preheader, html_body, text_body,
                           sender_name, reply_to, segment_ids=None, person_ids=None, include_all_eligible=False,
                           engagement_buckets=None, interest_ids=None, interest_match="any", action="draft"):
    action = "send" if str(action).lower() == "send" else "draft"
    payload = {
        "source": "njs",
        "edition_id": str(edition_id),
        "schedule_id": str(schedule_id),
        "subject": str(subject or "")[:150],
        "preheader": str(preheader or "")[:255],
        "html": str(html_body or ""),
        "plain_text": str(text_body or ""),
        "sender_name": str(sender_name or "")[:120],
        "reply_to": str(reply_to or "")[:320],
        "segment_ids": [str(x) for x in (segment_ids or []) if str(x).strip()][:50],
        "person_ids": [str(x) for x in (person_ids or []) if str(x).strip()][:500],
        "include_all_eligible": bool(include_all_eligible),
        "engagement_buckets": [str(x).lower() for x in (engagement_buckets or []) if str(x).strip()][:4],
        "interest_ids": [str(x) for x in (interest_ids or []) if str(x).strip()][:100],
        "interest_match": "all" if str(interest_match or "any").lower() == "all" else "any",
        "action": action,
    }
    return _marketing_bridge_request(
        local_org_id,
        "POST",
        "/api/v1/marketing/newsletters/distribute",
        payload=payload,
        idempotency_key=f"njs-newsletter:{edition_id}:{action}",
        timeout=max(Config.BLACKBOOK_HTTP_TIMEOUT_SECONDS, 45),
    )


def marketing_engagement_overview(local_org_id):
    return _marketing_bridge_request(local_org_id, "GET", "/api/v1/marketing/engagement/overview")


def marketing_engagement_people(local_org_id, *, bucket="queue", query="", interest="", limit=2500, offset=0):
    return _marketing_bridge_request(local_org_id, "GET", "/api/v1/marketing/engagement/people", params={
        "bucket": str(bucket or "queue")[:20],
        "q": str(query or "")[:160],
        "interest": str(interest or "")[:180],
        "limit": max(1, min(2500, int(limit or 2500))),
        "offset": max(0, int(offset or 0)),
    }, timeout=max(Config.BLACKBOOK_HTTP_TIMEOUT_SECONDS, 60))


def marketing_engagement_refresh_queue(local_org_id):
    return _marketing_bridge_request(
        local_org_id, "POST", "/api/v1/marketing/engagement/queue/refresh",
        payload={}, timeout=max(Config.BLACKBOOK_HTTP_TIMEOUT_SECONDS, 120),
    )


def marketing_engagement_bulk_analyze(local_org_id, *, person_ids, days=3650):
    ids = [str(x) for x in (person_ids or []) if str(x).strip()]
    if not ids:
        raise RuntimeError("Select at least one person")
    if len(ids) > 2500:
        raise RuntimeError("A maximum of 2500 people can be analyzed in one batch")
    return _marketing_bridge_request(
        local_org_id, "POST", "/api/v1/marketing/engagement/bulk-analyze",
        payload={"person_ids": ids, "days": max(30, min(36500, int(days or 3650)))},
        idempotency_key=f"njs-engagement:{hashlib.sha256('|'.join(ids).encode()).hexdigest()[:24]}",
        timeout=max(Config.BLACKBOOK_HTTP_TIMEOUT_SECONDS, 60),
    )


def marketing_engagement_interests(local_org_id):
    return _marketing_bridge_request(local_org_id, "GET", "/api/v1/marketing/engagement/interests", timeout=max(Config.BLACKBOOK_HTTP_TIMEOUT_SECONDS, 60))


def marketing_engagement_batches(local_org_id, *, limit=12):
    return _marketing_bridge_request(local_org_id, "GET", "/api/v1/marketing/engagement/batches", params={
        "limit": max(1, min(100, int(limit or 12))),
    })


def marketing_engagement_batch(local_org_id, batch_id):
    return _marketing_bridge_request(local_org_id, "GET", f"/api/v1/marketing/engagement/batches/{str(batch_id)}")
