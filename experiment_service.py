import hashlib
import math
import random
import re
from datetime import datetime, timezone

from bson import ObjectId
from bs4 import BeautifulSoup

from db import experiments, analytics_events

ACTIVE_STATUSES = {"running", "completed"}
PRIMARY_METRICS = {"conversion_rate", "cta_ctr", "product_ctr", "engagement_rate"}


def _now():
    return datetime.now(timezone.utc)


def experiment_cookie_name(experiment_id):
    token = hashlib.sha256(str(experiment_id).encode("utf-8")).hexdigest()[:16]
    return f"njs_exp_{token}"


def _normalize_variants(exp):
    variants = []
    for idx, raw in enumerate(exp.get("variants") or []):
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("id") or ("control" if idx == 0 else f"variant_{idx+1}"))[:40]
        weight = max(0, min(int(raw.get("weight") or 0), 100))
        variants.append({**raw, "id": key, "weight": weight})
    if not variants:
        variants = [{"id": "control", "name": "Control", "weight": 100, "overrides": {}}]
    total = sum(v["weight"] for v in variants)
    if total <= 0:
        even = max(1, 100 // len(variants))
        for v in variants:
            v["weight"] = even
    return variants


def active_experiment_for(target_type, target_id, owner_doc=None):
    query = {"target_type": target_type, "target_id": target_id, "status": {"$in": list(ACTIVE_STATUSES)}}
    if owner_doc:
        if owner_doc.get("organization_id"):
            query["organization_id"] = owner_doc.get("organization_id")
        elif owner_doc.get("user_id"):
            query["user_id"] = owner_doc.get("user_id")
    rows = list(experiments.find(query).sort("updated_at", -1).limit(10))
    # A newly running experiment always overrides an older completed winner.
    running = next((exp for exp in rows if exp.get("status") == "running"), None)
    chosen = running or next((exp for exp in rows if exp.get("status") == "completed" and exp.get("winner_variant_id")), None)
    if not chosen:
        return None
    expected_revision = int(chosen.get("target_revision") or 0)
    current_revision = int((owner_doc or {}).get("published_revision") or 0)
    if expected_revision and current_revision and expected_revision != current_revision:
        experiments.update_one({"_id": chosen["_id"]}, {"$set": {
            "status": "paused", "pause_reason": "target_changed", "paused_at": _now(), "updated_at": _now(),
        }})
        return None
    return chosen


def choose_variant(exp, request):
    variants = _normalize_variants(exp)
    winner = str(exp.get("winner_variant_id") or "")
    if exp.get("status") == "completed" and winner:
        chosen = next((v for v in variants if v["id"] == winner), variants[0])
        return chosen, experiment_cookie_name(exp["_id"]), False

    cookie_name = experiment_cookie_name(exp["_id"])
    saved = str(request.cookies.get(cookie_name) or "")
    if saved:
        hit = next((v for v in variants if v["id"] == saved), None)
        if hit:
            return hit, cookie_name, False

    # Variant-only functional cookie. It contains no visitor identifier.
    bucket = random.randint(1, max(1, sum(v["weight"] for v in variants)))
    cursor = 0
    chosen = variants[-1]
    for variant in variants:
        cursor += variant["weight"]
        if bucket <= cursor:
            chosen = variant
            break
    return chosen, cookie_name, True


def experiment_context_for_public(target_type, target_id, owner_doc, request):
    exp = active_experiment_for(target_type, target_id, owner_doc=owner_doc)
    if not exp:
        return None
    variant, cookie_name, cookie_new = choose_variant(exp, request)
    return {
        "experiment": exp,
        "variant": variant,
        "cookie_name": cookie_name,
        "cookie_new": cookie_new,
        "experiment_id": exp.get("_id"),
        "variant_id": variant.get("id"),
    }


def _first_primary_cta(soup):
    candidates = []
    for a in soup.find_all("a", href=True):
        cls = " ".join(a.get("class") or [])
        if a.has_attr("data-cta") or re.search(r"(^|[\s_-])(cta|button|btn|primary|action)([\s_-]|$)", cls, flags=re.I):
            return a
        href = str(a.get("href") or "")
        if href and not href.startswith(("#", "mailto:", "tel:")):
            candidates.append(a)
    return candidates[0] if candidates else None


def apply_page_variant(html, variant):
    overrides = (variant or {}).get("overrides") or {}
    if not html or not any(str(overrides.get(k) or "").strip() for k in ("headline", "subheadline", "cta_label", "cta_url", "cta_placement")):
        return html
    soup = BeautifulSoup(html, "html.parser")
    headline = str(overrides.get("headline") or "").strip()
    subheadline = str(overrides.get("subheadline") or "").strip()
    cta_label = str(overrides.get("cta_label") or "").strip()
    cta_url = str(overrides.get("cta_url") or "").strip()
    cta_placement = str(overrides.get("cta_placement") or "existing").strip()

    if headline:
        h1 = soup.find("h1")
        if h1:
            h1.string = headline
    if subheadline:
        h1 = soup.find("h1")
        target = None
        if h1:
            for sibling in h1.find_all_next(["p", "div"], limit=8):
                classes = " ".join(sibling.get("class") or [])
                if sibling.name == "p" or re.search(r"(sub|dek|lead|intro|hero-copy)", classes, flags=re.I):
                    target = sibling
                    break
        if target:
            target.string = subheadline

    cta = _first_primary_cta(soup)
    if cta and cta_label:
        cta.string = cta_label
    if cta and cta_url:
        cta["href"] = cta_url
        cta["data-cta"] = "experiment"

    if cta_placement in {"after_hero", "after_content", "sticky_bottom"} and (cta_label or cta_url):
        body = soup.body or soup
        link = soup.new_tag("a", href=cta_url or (cta.get("href") if cta else "#"))
        link["data-cta"] = "experiment"
        link["class"] = ["njs-experiment-cta"]
        link.string = cta_label or (cta.get_text(" ", strip=True) if cta else "Learn more")
        box = soup.new_tag("div")
        box["class"] = ["njs-experiment-cta-wrap", f"njs-experiment-cta-{cta_placement}"]
        box.append(link)
        style = soup.new_tag("style")
        style.string = ".njs-experiment-cta-wrap{display:flex;justify-content:center;padding:22px}.njs-experiment-cta-wrap a{display:inline-flex;align-items:center;justify-content:center;padding:12px 18px;border-radius:10px;background:#111827;color:#fff;text-decoration:none;font:700 14px/1.2 system-ui,-apple-system,sans-serif}.njs-experiment-cta-sticky_bottom{position:fixed;left:0;right:0;bottom:0;z-index:9999;background:rgba(255,255,255,.96);border-top:1px solid #e5e7eb;backdrop-filter:blur(10px);padding:10px 16px}.njs-experiment-cta-sticky_bottom a{min-width:min(360px,90vw)}"
        if soup.head:
            soup.head.append(style)
        if cta_placement == "after_hero":
            h1 = soup.find("h1")
            container = h1.find_parent(["header", "section", "div"]) if h1 else None
            if container:
                container.insert_after(box)
            else:
                body.insert(0, box)
        else:
            body.append(box)
    return str(soup)


def article_variant_view(article_view, variant):
    view = dict(article_view or {})
    overrides = (variant or {}).get("overrides") or {}
    metadata = dict(view.get("metadata") or {})
    if str(overrides.get("headline") or "").strip():
        metadata["title"] = str(overrides.get("headline")).strip()[:180]
    if str(overrides.get("subheadline") or "").strip():
        metadata["description"] = str(overrides.get("subheadline")).strip()[:300]
    view["metadata"] = metadata
    content = str(view.get("content") or "")
    if content and str(overrides.get("cta_placement") or "existing") == "existing" and (str(overrides.get("cta_label") or "").strip() or str(overrides.get("cta_url") or "").strip()):
        soup = BeautifulSoup(content, "html.parser")
        cta = _first_primary_cta(soup)
        if cta:
            if str(overrides.get("cta_label") or "").strip():
                cta.string = str(overrides.get("cta_label")).strip()
            if str(overrides.get("cta_url") or "").strip():
                cta["href"] = str(overrides.get("cta_url")).strip()
            cta["data-cta"] = "experiment"
            view["content"] = str(soup)
    view["experiment_cta"] = {
        "label": str(overrides.get("cta_label") or "").strip()[:180],
        "url": str(overrides.get("cta_url") or "").strip()[:2000],
        "placement": str(overrides.get("cta_placement") or "existing").strip(),
    }
    return view


def experiment_results(exp):
    rows = list(analytics_events.find({"experiment_id": exp.get("_id"), "is_bot": {"$ne": True}, "internal_view": {"$ne": True}}).sort("occurred_at", 1))
    variants = _normalize_variants(exp)
    result = []
    for variant in variants:
        vid = variant["id"]
        events = [e for e in rows if str(e.get("variant_id") or "") == vid]
        views = [e for e in events if (e.get("event_type") or "view") == "view"]
        interactions = [e for e in events if (e.get("event_type") or "view") != "view"]
        unique = len({e.get("visitor_hash") for e in views if e.get("visitor_hash")})
        engaged = sum(1 for e in views if e.get("engaged"))
        product_clicks = sum(1 for e in interactions if e.get("event_type") == "product_click")
        cta_clicks = sum(1 for e in interactions if e.get("event_type") in {"cta_click", "product_click"})
        conversions = [e for e in interactions if e.get("event_type") in {"lead", "signup", "purchase"}]
        converted_views = {str(e.get("parent_view_id")) for e in conversions if e.get("parent_view_id")}
        converted = len(converted_views) or min(len(conversions), len(views))
        n = max(1, len(views))
        result.append({
            "id": vid,
            "name": variant.get("name") or vid,
            "weight": variant.get("weight") or 0,
            "views": len(views),
            "unique_visitors": unique,
            "engaged_views": engaged,
            "engagement_rate": round(engaged / n * 100, 1) if views else 0,
            "product_clicks": product_clicks,
            "cta_clicks": cta_clicks,
            "cta_ctr": round(cta_clicks / n * 100, 1) if views else 0,
            "product_ctr": round(product_clicks / n * 100, 1) if views else 0,
            "conversions": len(conversions),
            "converted_journeys": converted,
            "conversion_rate": round(converted / n * 100, 1) if views else 0,
        })
    return result


def _normal_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def binary_confidence(control_success, control_n, challenger_success, challenger_n):
    if control_n <= 0 or challenger_n <= 0:
        return 0.0
    p1 = control_success / control_n
    p2 = challenger_success / challenger_n
    pooled = (control_success + challenger_success) / (control_n + challenger_n)
    se = math.sqrt(max(0.0, pooled * (1 - pooled) * (1 / control_n + 1 / challenger_n)))
    if se == 0:
        return 0.0
    z = abs(p2 - p1) / se
    return round((2 * _normal_cdf(z) - 1) * 100, 1)


def experiment_decision(exp, results):
    metric = exp.get("primary_metric") or "conversion_rate"
    min_sample = max(10, int(exp.get("min_sample_size") or 100))
    if len(results) < 2:
        return {"ready": False, "reason": "Add at least two variants."}
    control = results[0]
    challenger = max(results[1:], key=lambda r: r.get(metric, 0))
    if control["views"] < min_sample or challenger["views"] < min_sample:
        return {"ready": False, "reason": f"Wait for at least {min_sample} views per compared variant.", "control": control, "challenger": challenger}
    if metric == "conversion_rate":
        s1, s2 = control["converted_journeys"], challenger["converted_journeys"]
    elif metric == "cta_ctr":
        s1, s2 = control["cta_clicks"], challenger["cta_clicks"]
    elif metric == "product_ctr":
        s1, s2 = control["product_clicks"], challenger["product_clicks"]
    else:
        s1, s2 = control["engaged_views"], challenger["engaged_views"]
    confidence = binary_confidence(s1, control["views"], s2, challenger["views"])
    base = float(control.get(metric) or 0)
    comp = float(challenger.get(metric) or 0)
    lift = round(((comp - base) / base * 100), 1) if base else (100.0 if comp > 0 else 0.0)
    winner = challenger if comp > base else control
    return {
        "ready": True,
        "control": control,
        "challenger": challenger,
        "winner": winner,
        "confidence": confidence,
        "lift": lift,
        "recommended": bool(confidence >= 95 and winner["id"] != control["id"]),
    }
