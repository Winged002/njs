import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from bson import ObjectId
from config import Config
from db import analytics_events, analytics_settings

TRACKING_MODES = {"pseudonymous", "consent", "aggregate"}
CONVERSION_EVENTS = {"lead", "signup", "purchase", "form_submit"}
CLICK_EVENTS = {"click", "cta_click", "product_click", "outbound_click"}


def _hash(value):
    return hmac.new(Config.SECRET_KEY.encode("utf-8"), str(value or "").encode("utf-8"), hashlib.sha256).hexdigest()


def analytics_settings_for(owner_doc):
    org_id = (owner_doc or {}).get("organization_id")
    user_id = (owner_doc or {}).get("user_id")
    row = None
    if org_id:
        row = analytics_settings.find_one({"organization_id": org_id})
    if not row and user_id:
        row = analytics_settings.find_one({"user_id": user_id, "organization_id": {"$exists": False}})
    mode = str((row or {}).get("tracking_mode") or "pseudonymous").strip().lower()
    if mode not in TRACKING_MODES:
        mode = "pseudonymous"
    return {
        "tracking_mode": mode,
        "consent_title": (row or {}).get("consent_title") or "Privacy choices",
        "consent_text": (row or {}).get("consent_text") or "Allow pseudonymous analytics so we can understand which content is useful and improve this website.",
        "updated_at": (row or {}).get("updated_at"),
    }


def visitor_token_from_request(request, create=True):
    token = (request.cookies.get("njs_vid") or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
        return token, False
    if not create:
        return None, False
    return secrets.token_urlsafe(24), True


def _device_info(user_agent):
    ua = str(user_agent or "")
    low = ua.lower()
    is_bot = any(x in low for x in ("bot", "crawler", "spider", "slurp", "headless", "facebookexternalhit", "linkedinbot", "twitterbot"))
    if any(x in low for x in ("ipad", "tablet", "kindle")):
        device = "Tablet"
    elif any(x in low for x in ("iphone", "android", "mobile")):
        device = "Mobile"
    else:
        device = "Desktop"
    if "edg/" in low:
        browser = "Edge"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "firefox/" in low:
        browser = "Firefox"
    elif "chrome/" in low or "crios/" in low:
        browser = "Chrome"
    elif "safari/" in low:
        browser = "Safari"
    else:
        browser = "Other"
    if "windows" in low:
        os_name = "Windows"
    elif "iphone" in low or "ipad" in low or "ios" in low:
        os_name = "iOS"
    elif "android" in low:
        os_name = "Android"
    elif "mac os" in low or "macintosh" in low:
        os_name = "macOS"
    elif "linux" in low:
        os_name = "Linux"
    else:
        os_name = "Other"
    return device, browser, os_name, is_bot


def _geo_headers(request):
    country = request.headers.get("CF-IPCountry") or request.headers.get("X-Country-Code") or request.headers.get("X-Geo-Country")
    region = request.headers.get("X-Region") or request.headers.get("X-Geo-Region")
    city = request.headers.get("X-City") or request.headers.get("X-Geo-City")
    return (country or "Unknown")[:80], (region or "Unknown")[:120], (city or "Unknown")[:120]


def _channel(request):
    medium = (request.args.get("utm_medium") or "").strip().lower()
    source = (request.args.get("utm_source") or "").strip().lower()
    if medium in {"email", "newsletter"}:
        return "Email"
    if medium in {"social", "organic_social", "paid_social"}:
        return "Social"
    if medium in {"cpc", "ppc", "paid", "display"}:
        return "Paid"
    if source:
        return source[:80]
    referrer = request.referrer or ""
    try:
        host = (urlsplit(referrer).hostname or "").lower()
    except Exception:
        host = ""
    if any(x in host for x in ("linkedin.", "facebook.", "instagram.", "x.com", "twitter.", "threads.", "bsky.")):
        return "Social"
    if host:
        return "Referral"
    return "Direct"


def _ids(values):
    return [v for v in (values or []) if isinstance(v, ObjectId)]


def attribution_fields(owner_doc):
    owner_doc = owner_doc or {}
    return {
        "campaign_id": owner_doc.get("campaign_id") if isinstance(owner_doc.get("campaign_id"), ObjectId) else None,
        "campaign_ids": _ids(owner_doc.get("campaign_ids")) or ([owner_doc.get("campaign_id")] if isinstance(owner_doc.get("campaign_id"), ObjectId) else []),
        "product_ids": _ids(owner_doc.get("product_ids")),
        "newsjacking_worker_id": owner_doc.get("newsjacking_worker_id") if isinstance(owner_doc.get("newsjacking_worker_id"), ObjectId) else None,
        "hook_id": owner_doc.get("hook_id") if isinstance(owner_doc.get("hook_id"), ObjectId) else None,
        "source_feed_item_id": owner_doc.get("source_feed_item_id") if isinstance(owner_doc.get("source_feed_item_id"), ObjectId) else None,
        "source_type": str(owner_doc.get("source_type") or "")[:80],
        "experiment_id": owner_doc.get("experiment_id") if isinstance(owner_doc.get("experiment_id"), ObjectId) else None,
        "variant_id": str(owner_doc.get("variant_id") or "")[:40],
    }


def record_view(request, *, owner_doc, content_type, content_id, campaign_id=None, campaign_ids=None, domain=None, path=None, internal_view=False, experiment_id=None, variant_id=None):
    if request.method != "GET":
        return None, None, False
    settings = analytics_settings_for(owner_doc)
    mode = settings["tracking_mode"]
    consent = (request.cookies.get("njs_analytics_consent") or "").strip().lower()
    tracking_allowed = mode == "pseudonymous" or (mode == "consent" and consent == "granted")
    visitor_token, is_new = visitor_token_from_request(request, create=tracking_allowed)
    ua = request.headers.get("User-Agent", "")
    device, browser, os_name, is_bot = _device_info(ua)
    country, region, city = _geo_headers(request)
    remote_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    referrer = (request.referrer or "")[:2000]
    try:
        referrer_domain = (urlsplit(referrer).hostname or "").lower()
    except Exception:
        referrer_domain = ""
    now = datetime.now(timezone.utc)
    attrs = attribution_fields(owner_doc)
    if campaign_id is not None:
        attrs["campaign_id"] = campaign_id
    if campaign_ids is not None:
        attrs["campaign_ids"] = _ids(campaign_ids)
    if isinstance(experiment_id, ObjectId):
        attrs["experiment_id"] = experiment_id
    if variant_id is not None:
        attrs["variant_id"] = str(variant_id or "")[:40]
    event = {
        "event_type": "view",
        "content_type": content_type,
        "content_id": content_id,
        **attrs,
        "user_id": owner_doc.get("user_id"),
        "organization_id": owner_doc.get("organization_id"),
        "domain": (domain or request.host.split(":", 1)[0]).lower(),
        "path": path or request.path,
        "occurred_at": now,
        "day": now.strftime("%Y-%m-%d"),
        "visitor_hash": _hash(visitor_token) if visitor_token else "",
        "ip_hash": _hash(remote_ip) if tracking_allowed else "",
        "device": device,
        "browser": browser,
        "os": os_name,
        "is_bot": bool(is_bot),
        "internal_view": bool(internal_view),
        "country": country,
        "region": region,
        "city": city,
        "language": (request.headers.get("Accept-Language") or "").split(",")[0][:32],
        "referrer": referrer,
        "referrer_domain": referrer_domain or "Direct",
        "utm_source": (request.args.get("utm_source") or "")[:120],
        "utm_medium": (request.args.get("utm_medium") or "")[:120],
        "utm_campaign": (request.args.get("utm_campaign") or "")[:160],
        "utm_term": (request.args.get("utm_term") or "")[:160],
        "utm_content": (request.args.get("utm_content") or "")[:160],
        "channel": _channel(request),
        "privacy_mode": mode,
        "consent_state": "granted" if tracking_allowed and mode == "consent" else ("not_required" if mode == "pseudonymous" else (consent or "pending")),
        "tracking_allowed": tracking_allowed,
        "consent_title": settings.get("consent_title"),
        "consent_text": settings.get("consent_text"),
        "engaged": False,
        "duration_ms": 0,
        "max_scroll_pct": 0,
        "created_at": now,
        "updated_at": now,
    }
    result = analytics_events.insert_one(event)
    return str(result.inserted_id), visitor_token, is_new


def tracking_script(event_id):
    if not event_id:
        return ""
    try:
        parent = analytics_events.find_one({"_id": ObjectId(str(event_id))}, {"privacy_mode": 1, "tracking_allowed": 1, "consent_state": 1, "consent_title": 1, "consent_text": 1}) or {}
    except Exception:
        parent = {}
    mode = parent.get("privacy_mode") or "pseudonymous"
    allowed = bool(parent.get("tracking_allowed"))
    consent_ui = ""
    if mode == "consent" and not allowed and parent.get("consent_state") != "denied":
        import json
        consent_title = json.dumps(str(parent.get("consent_title") or "Privacy choices"))
        consent_text = json.dumps(str(parent.get("consent_text") or "Allow pseudonymous analytics so we can understand which content is useful and improve this website."))
        consent_ui = f'''
function consentBanner(){{if(document.getElementById('njs-analytics-consent'))return;var box=document.createElement('div');box.id='njs-analytics-consent';box.setAttribute('role','dialog');box.setAttribute('aria-label','Analytics privacy choices');box.style.cssText='position:fixed;z-index:2147483647;left:18px;right:18px;bottom:18px;max-width:720px;margin:auto;background:#111827;color:#fff;border:1px solid rgba(255,255,255,.14);border-radius:16px;padding:16px 18px;box-shadow:0 18px 50px rgba(0,0,0,.28);font:14px/1.45 system-ui,-apple-system,sans-serif';var title={{{{TITLE}}}},copy={{{{TEXT}}}};box.innerHTML='<strong style="display:block;margin-bottom:5px"></strong><span style="display:block;color:#d1d5db"></span><div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap"><button data-njs-consent="deny" style="border:1px solid #4b5563;background:transparent;color:#fff;border-radius:9px;padding:8px 12px;font-weight:700;cursor:pointer">Continue without analytics</button><button data-njs-consent="grant" style="border:0;background:#fff;color:#111827;border-radius:9px;padding:8px 12px;font-weight:700;cursor:pointer">Allow analytics</button></div>';box.querySelector('strong').textContent=title;box.querySelector('span').textContent=copy;document.body.appendChild(box);box.addEventListener('click',function(e){{var v=e.target&&e.target.getAttribute('data-njs-consent');if(!v)return;document.cookie='njs_analytics_consent='+(v==='grant'?'granted':'denied')+'; Max-Age=31536000; Path=/; SameSite=Lax'+(location.protocol==='https:'?'; Secure':'');if(v==='grant')location.reload();else box.remove();}});}}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',consentBanner);else consentBanner();
'''.replace('{{TITLE}}', consent_title).replace('{{TEXT}}', consent_text)
    if not allowed:
        return f"<script>(function(){{{consent_ui}}})();</script>" if consent_ui else ""
    return f'''<script>(function(){{
var id={event_id!r}, start=Date.now(), maxScroll=0, sent=false, startedForms=new WeakSet();
function pct(){{var d=document.documentElement,b=document.body;var h=Math.max(d.scrollHeight,b?b.scrollHeight:0)-innerHeight;return h<=0?100:Math.min(100,Math.round(scrollY/h*100));}}
function post(data,beacon){{data.event_id=id;var body=JSON.stringify(data);if(beacon&&navigator.sendBeacon){{navigator.sendBeacon('/api/analytics/client',new Blob([body],{{type:'application/json'}}));}}else{{fetch('/api/analytics/client',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:body,keepalive:true}}).catch(function(){{}});}}}}
window.njsTrackConversion=function(name,options){{options=options||{{}};post({{kind:'conversion',conversion_name:String(name||'conversion').slice(0,120),conversion_type:String(options.type||'lead').slice(0,40),label:String(options.label||name||'Conversion').slice(0,180),value:options.value||null,currency:String(options.currency||'').slice(0,12)}});}};
function send(final){{post({{kind:'engagement',screen_width:screen.width,screen_height:screen.height,viewport_width:innerWidth,viewport_height:innerHeight,timezone:(Intl.DateTimeFormat().resolvedOptions().timeZone||''),language:(navigator.language||''),duration_ms:Math.max(0,Date.now()-start),max_scroll_pct:Math.max(maxScroll,pct()),final:!!final}},final);}}
function label(el){{return ((el.getAttribute('aria-label')||el.textContent||el.value||'').trim().replace(/\\s+/g,' ').slice(0,180));}}
addEventListener('scroll',function(){{maxScroll=Math.max(maxScroll,pct());}},{{passive:true}});
document.addEventListener('click',function(e){{var a=e.target.closest&&e.target.closest('a[href]');if(!a)return;var href=a.href||'';if(!href||href.indexOf('javascript:')===0)return;var cls=String(a.className||'');var cta=!!(a.getAttribute('data-cta')!==null||a.closest('[data-cta]')||/(^|[\\s_-])(cta|button|btn|primary|action)([\\s_-]|$)/i.test(cls));post({{kind:'interaction',interaction:'click',target_url:href,label:label(a),cta_hint:cta}});}},true);
document.addEventListener('focusin',function(e){{var f=e.target.closest&&e.target.closest('form');if(!f||startedForms.has(f))return;startedForms.add(f);post({{kind:'interaction',interaction:'form_start',label:(f.getAttribute('aria-label')||f.id||f.getAttribute('action')||'Form').slice(0,180)}});}},true);
document.addEventListener('submit',function(e){{var f=e.target;if(!f||f.tagName!=='FORM')return;post({{kind:'interaction',interaction:'form_submit',label:(f.getAttribute('aria-label')||f.id||f.getAttribute('action')||'Form').slice(0,180)}});}},true);
setTimeout(function(){{send(false);}},1200);addEventListener('pagehide',function(){{if(!sent){{sent=true;send(true);}}}});
}})();</script>'''


def inject_tracking(html, event_id):
    script = tracking_script(event_id)
    if not script:
        return html
    low = html.lower()
    pos = low.rfind("</body>")
    if pos >= 0:
        return html[:pos] + script + html[pos:]
    return html + script


def child_event_from_view(parent, *, event_type, label="", target_url="", product_id=None, value=None, currency=None, conversion_name=None):
    now = datetime.now(timezone.utc)
    copied = {k: parent.get(k) for k in (
        "content_type", "content_id", "campaign_id", "campaign_ids", "product_ids", "newsjacking_worker_id", "hook_id", "source_feed_item_id", "source_type",
        "user_id", "organization_id", "domain", "path", "visitor_hash", "device", "browser", "os", "country", "region", "city", "language",
        "referrer_domain", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "channel", "privacy_mode", "consent_state",
        "experiment_id", "variant_id",
    )}
    doc = {
        **copied,
        "event_type": event_type,
        "parent_view_id": parent.get("_id"),
        "label": str(label or "")[:180],
        "target_url": str(target_url or "")[:2000],
        "product_id": product_id if isinstance(product_id, ObjectId) else None,
        "conversion_name": str(conversion_name or "")[:120],
        "value": value,
        "currency": str(currency or "")[:12].upper(),
        "occurred_at": now,
        "day": now.strftime("%Y-%m-%d"),
        "is_bot": bool(parent.get("is_bot")),
        "internal_view": bool(parent.get("internal_view")),
        "created_at": now,
        "updated_at": now,
    }
    return doc


def record_conversion_from_request(request, *, owner_doc, content_type, content_id, event_type="lead", label="", conversion_name="", value=None, currency=None, product_id=None, experiment_id=None, variant_id=None):
    token, _ = visitor_token_from_request(request, create=False)
    visitor_hash = _hash(token) if token else ""
    recent = None
    if visitor_hash:
        recent = analytics_events.find_one({
            "event_type": "view", "content_type": content_type, "content_id": content_id,
            "visitor_hash": visitor_hash, "occurred_at": {"$gte": datetime.now(timezone.utc) - timedelta(hours=8)},
        }, sort=[("occurred_at", -1)])
    if recent:
        if product_id is None and visitor_hash:
            latest_product_click = analytics_events.find_one({
                "event_type": "product_click", "content_type": content_type, "content_id": content_id,
                "visitor_hash": visitor_hash, "occurred_at": {"$gte": datetime.now(timezone.utc) - timedelta(hours=8)},
            }, sort=[("occurred_at", -1)])
            if latest_product_click and isinstance(latest_product_click.get("product_id"), ObjectId):
                product_id = latest_product_click.get("product_id")
        doc = child_event_from_view(recent, event_type=event_type, label=label, product_id=product_id, value=value, currency=currency, conversion_name=conversion_name)
    else:
        now = datetime.now(timezone.utc)
        attrs = attribution_fields(owner_doc)
        doc = {
            "event_type": event_type, "content_type": content_type, "content_id": content_id, **attrs,
            "user_id": owner_doc.get("user_id"), "organization_id": owner_doc.get("organization_id"),
            "domain": request.host.split(":", 1)[0].lower(), "path": request.path,
            "visitor_hash": visitor_hash, "product_id": product_id if isinstance(product_id, ObjectId) else None,
            "label": str(label or "")[:180], "conversion_name": str(conversion_name or "")[:120],
            "value": value, "currency": str(currency or "")[:12].upper(), "channel": _channel(request),
            "occurred_at": now, "day": now.strftime("%Y-%m-%d"), "is_bot": False, "internal_view": False,
            "created_at": now, "updated_at": now,
        }
    if not recent:
        if isinstance(experiment_id, ObjectId):
            doc["experiment_id"] = experiment_id
        if variant_id is not None:
            doc["variant_id"] = str(variant_id or "")[:40]
    result = analytics_events.insert_one(doc)
    return result.inserted_id


def parse_date_range(args):
    today = datetime.now(timezone.utc).date()
    preset = (args.get("preset") or "").lower()
    if preset == "today":
        start, end = today, today
    elif preset == "90d":
        start, end = today - timedelta(days=89), today
    elif preset == "30d" or not (args.get("from") and args.get("to")):
        start, end = today - timedelta(days=29), today
    elif preset == "7d":
        start, end = today - timedelta(days=6), today
    elif preset == "this_month":
        start, end = today.replace(day=1), today
    elif preset == "last_month":
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        start = end.replace(day=1)
    else:
        try:
            start = datetime.strptime(args.get("from"), "%Y-%m-%d").date()
            end = datetime.strptime(args.get("to"), "%Y-%m-%d").date()
        except Exception:
            start, end = today - timedelta(days=29), today
    if start > end:
        start, end = end, start
    if (end - start).days > 730:
        start = end - timedelta(days=730)
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return start, end, start_dt, end_dt


def _label_for_event(row, lookup):
    ctype = row.get("content_type")
    cid = str(row.get("content_id") or "")
    if ctype == "page":
        return lookup.get("page", {}).get(cid, "Website page")
    return lookup.get("article", {}).get(cid, "Article")


def _breakdown(events, field, limit=10):
    total = len(events)
    counts = {}
    for e in events:
        value = e.get(field) or "Unknown"
        if field == "referrer_domain" and value in ("", None):
            value = "Direct"
        counts[str(value)] = counts.get(str(value), 0) + 1
    return [{"label": k, "value": v, "pct": round(v / total * 100, 1) if total else 0} for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:limit]]


def _attribution_rows(events, field, name_map, views):
    rows = {}
    for e in views:
        raw = e.get(field)
        values = raw if isinstance(raw, list) else [raw]
        if field == "product_ids" and e.get("product_id"):
            values = [e.get("product_id")]
        for value in values:
            if not value:
                continue
            key = str(value)
            row = rows.setdefault(key, {"id": key, "name": name_map.get(key, "Unknown"), "views": 0, "visitors": set(), "clicks": 0, "product_clicks": 0, "leads": 0, "form_submits": 0, "converted_views": set()})
            row["views"] += 1
            if e.get("visitor_hash"):
                row["visitors"].add(e.get("visitor_hash"))
    for e in events:
        if e.get("event_type") == "view":
            continue
        raw = e.get(field)
        values = raw if isinstance(raw, list) else [raw]
        if field == "product_ids":
            if e.get("product_id"):
                values = [e.get("product_id")]
            elif e.get("event_type") in {"lead", "signup", "purchase"}:
                # A content conversion without a product interaction is a Campaign/Worker outcome,
                # not enough evidence to credit every eligible product in the article.
                values = []
        for value in values:
            if not value:
                continue
            key = str(value)
            row = rows.setdefault(key, {"id": key, "name": name_map.get(key, "Unknown"), "views": 0, "visitors": set(), "clicks": 0, "product_clicks": 0, "leads": 0, "form_submits": 0, "converted_views": set()})
            et = e.get("event_type")
            if et in CLICK_EVENTS:
                row["clicks"] += 1
            if et == "product_click":
                row["product_clicks"] += 1
            if et in {"lead", "signup", "purchase"}:
                row["leads"] += 1
                if e.get("parent_view_id"):
                    row["converted_views"].add(str(e.get("parent_view_id")))
            if et == "form_submit":
                row["form_submits"] += 1
    out = []
    for row in rows.values():
        views_n = row["views"]
        row["unique_visitors"] = len(row.pop("visitors"))
        converted_views = len(row.pop("converted_views"))
        if not converted_views and row["leads"] and views_n:
            converted_views = min(row["leads"], views_n)
        row["converted_journeys"] = converted_views
        row["ctr"] = round(row["product_clicks"] / views_n * 100, 1) if views_n else 0
        row["conversion_rate"] = round(converted_views / views_n * 100, 1) if views_n else 0
        out.append(row)
    out.sort(key=lambda r: (-r["leads"], -r["product_clicks"], -r["views"], r["name"].lower()))
    return out


def analytics_report(events, *, start_date, end_date, lookup, selections=None, sort="views"):
    selections = selections or []
    view_events = [e for e in events if (e.get("event_type") or "view") == "view"]
    interaction_events = [e for e in events if (e.get("event_type") or "view") != "view"]
    total = len(view_events)
    visitors = len({e.get("visitor_hash") for e in view_events if e.get("visitor_hash")})
    engaged_events = [e for e in view_events if e.get("engaged")]
    avg_duration = int(sum(int(e.get("duration_ms") or 0) for e in view_events) / max(total, 1))
    avg_scroll = int(sum(int(e.get("max_scroll_pct") or 0) for e in view_events) / max(total, 1))
    product_clicks = sum(1 for e in interaction_events if e.get("event_type") == "product_click")
    cta_clicks = sum(1 for e in interaction_events if e.get("event_type") in {"cta_click", "product_click"})
    conversion_events = [e for e in interaction_events if e.get("event_type") in {"lead", "signup", "purchase"}]
    leads = len(conversion_events)
    converted_journeys = len({str(e.get("parent_view_id")) for e in conversion_events if e.get("parent_view_id")})
    if not converted_journeys and leads and total:
        converted_journeys = min(leads, total)
    form_starts = sum(1 for e in interaction_events if e.get("event_type") == "form_start")
    form_submits = sum(1 for e in interaction_events if e.get("event_type") == "form_submit")
    summary = {
        "views": total,
        "unique_visitors": visitors,
        "engaged_views": len(engaged_events),
        "engagement_rate": round((len(engaged_events) / total * 100), 1) if total else 0,
        "avg_duration_seconds": round(avg_duration / 1000, 1),
        "avg_scroll_pct": avg_scroll,
        "cta_clicks": cta_clicks,
        "product_clicks": product_clicks,
        "leads": leads,
        "form_starts": form_starts,
        "form_submits": form_submits,
        "product_ctr": round(product_clicks / total * 100, 1) if total else 0,
        "converted_journeys": converted_journeys,
        "conversion_rate": round(converted_journeys / total * 100, 1) if total else 0,
    }
    days = []
    cursor = start_date
    while cursor <= end_date:
        days.append(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=1)
    by_day = {d: 0 for d in days}
    unique_by_day = {d: set() for d in days}
    conversions_by_day = {d: 0 for d in days}
    clicks_by_day = {d: 0 for d in days}
    for e in view_events:
        d = e.get("day") or (e.get("occurred_at").strftime("%Y-%m-%d") if e.get("occurred_at") else "")
        if d in by_day:
            by_day[d] += 1
            if e.get("visitor_hash"):
                unique_by_day[d].add(e["visitor_hash"])
    for e in interaction_events:
        d = e.get("day") or (e.get("occurred_at").strftime("%Y-%m-%d") if e.get("occurred_at") else "")
        if d not in conversions_by_day:
            continue
        if e.get("event_type") in {"lead", "signup", "purchase"}:
            conversions_by_day[d] += 1
        if e.get("event_type") == "product_click":
            clicks_by_day[d] += 1
    trend = {
        "labels": days,
        "views": [by_day[d] for d in days],
        "uniques": [len(unique_by_day[d]) for d in days],
        "product_clicks": [clicks_by_day[d] for d in days],
        "conversions": [conversions_by_day[d] for d in days],
    }

    entity = {}
    for e in view_events:
        key = f"{e.get('content_type')}:{e.get('content_id')}"
        row = entity.setdefault(key, {"key": key, "type": e.get("content_type"), "label": _label_for_event(e, lookup), "campaign": lookup.get("content_campaign", {}).get(key, "No campaign"), "views": 0, "visitors": set(), "engaged": 0, "duration": 0, "scroll": 0, "product_clicks": 0, "leads": 0, "converted_views": set()})
        row["views"] += 1
        if e.get("visitor_hash"):
            row["visitors"].add(e["visitor_hash"])
        row["engaged"] += 1 if e.get("engaged") else 0
        row["duration"] += int(e.get("duration_ms") or 0)
        row["scroll"] += int(e.get("max_scroll_pct") or 0)
    for e in interaction_events:
        key = f"{e.get('content_type')}:{e.get('content_id')}"
        if key not in entity:
            continue
        if e.get("event_type") == "product_click":
            entity[key]["product_clicks"] += 1
        if e.get("event_type") in {"lead", "signup", "purchase"}:
            entity[key]["leads"] += 1
            if e.get("parent_view_id"):
                entity[key]["converted_views"].add(str(e.get("parent_view_id")))
    comparisons = []
    for row in entity.values():
        views_n = max(row["views"], 1)
        converted_views = len(row["converted_views"])
        if not converted_views and row["leads"]:
            converted_views = min(row["leads"], row["views"])
        comparisons.append({
            "key": row["key"], "type": row["type"], "label": row["label"], "campaign": row["campaign"], "views": row["views"],
            "unique_visitors": len(row["visitors"]), "engagement_rate": round(row["engaged"] / views_n * 100, 1),
            "avg_duration_seconds": round(row["duration"] / views_n / 1000, 1), "avg_scroll_pct": round(row["scroll"] / views_n),
            "product_clicks": row["product_clicks"], "leads": row["leads"], "converted_journeys": converted_views,
            "conversion_rate": round(converted_views / views_n * 100, 1),
        })
    sort_key = {"uniques": "unique_visitors", "engagement": "engagement_rate", "time": "avg_duration_seconds", "scroll": "avg_scroll_pct", "clicks": "product_clicks", "conversions": "leads"}.get(sort, "views")
    if sort == "campaign":
        comparisons.sort(key=lambda r: (r.get("campaign", "").lower(), -r.get("views", 0), r["label"].lower()))
    else:
        comparisons.sort(key=lambda r: (-r.get(sort_key, 0), r["label"].lower()))

    return {
        "summary": summary,
        "trend": trend,
        "devices": _breakdown(view_events, "device", 8),
        "browsers": _breakdown(view_events, "browser", 8),
        "operating_systems": _breakdown(view_events, "os", 8),
        "countries": _breakdown(view_events, "country", 12),
        "regions": _breakdown(view_events, "region", 12),
        "referrers": _breakdown(view_events, "referrer_domain", 12),
        "channels": _breakdown(view_events, "channel", 12),
        "campaign_sources": _breakdown(view_events, "utm_campaign", 10),
        "comparisons": comparisons,
        "campaign_attribution": _attribution_rows(events, "campaign_ids", lookup.get("campaign", {}), view_events),
        "product_attribution": _attribution_rows(events, "product_ids", lookup.get("product", {}), view_events),
        "worker_attribution": _attribution_rows(events, "newsjacking_worker_id", lookup.get("worker", {}), view_events),
        "source_attribution": _attribution_rows(events, "hook_id", lookup.get("source", {}), view_events),
    }
