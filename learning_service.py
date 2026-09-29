import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from bson import ObjectId

from db import analytics_events, articles, hooks, rss_feed_items, rss_feeds, products, newsjacking_workers

LEARNING_MODES = {"observe", "recommend", "adaptive"}
LEARNING_OBJECTIVES = {"conversion_rate", "product_ctr", "engagement_rate"}

STOPWORDS = {
    "the","and","for","with","from","that","this","into","over","after","before","about","amid","its","their","they","them","are","was","were","will","would","could","should","has","have","had","not","but","you","your","our","out","new","news","says","say","more","than","how","why","what","when","where","who","a","an","of","to","in","on","at","by","as","is","be","or"
}


def utcnow():
    return datetime.now(timezone.utc)


def _clamp(value, low, high):
    return max(low, min(high, value))


def _oid(value):
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _metric(row, objective):
    views = max(int(row.get("views") or 0), 1)
    if objective == "product_ctr":
        return (row.get("product_clicks") or 0) / views
    if objective == "engagement_rate":
        return (row.get("engaged") or 0) / views
    return (row.get("converted_journeys") or row.get("leads") or 0) / views


def _finalize(row, objective):
    views = int(row.get("views") or 0)
    visitors = row.pop("visitors", set()) if isinstance(row.get("visitors"), set) else set()
    converted = row.pop("converted_views", set()) if isinstance(row.get("converted_views"), set) else set()
    row["unique_visitors"] = len(visitors)
    row["converted_journeys"] = len(converted) or min(int(row.get("leads") or 0), views)
    row["engagement_rate"] = round((row.get("engaged") or 0) / views * 100, 1) if views else 0
    row["product_ctr"] = round((row.get("product_clicks") or 0) / views * 100, 1) if views else 0
    row["conversion_rate"] = round(row["converted_journeys"] / views * 100, 1) if views else 0
    row["objective_rate"] = round(_metric(row, objective) * 100, 2) if views else 0
    return row


def _freshness_hours(article, hook, item):
    published = (item or {}).get("published")
    observed = (hook or {}).get("created_at") or article.get("created_at")
    if not isinstance(published, datetime) or not isinstance(observed, datetime):
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return max(0.0, (observed - published).total_seconds() / 3600.0)


def _freshness_bucket(hours):
    if hours is None:
        return "Unknown"
    if hours <= 6:
        return "≤6h"
    if hours <= 12:
        return "6–12h"
    if hours <= 24:
        return "12–24h"
    if hours <= 48:
        return "24–48h"
    return "48h+"


def _confidence_bucket(value):
    try:
        v = float(value)
    except Exception:
        return "Unknown"
    if v < 0.65:
        return "<0.65"
    if v < 0.75:
        return "0.65–0.74"
    if v < 0.85:
        return "0.75–0.84"
    return "0.85+"


def _new_row(name, **extra):
    return {"name": name, "views": 0, "engaged": 0, "product_clicks": 0, "leads": 0, "visitors": set(), "converted_views": set(), **extra}


def _tokens(text):
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", (text or "").lower())
    return [w for w in words if w not in STOPWORDS and not w.isdigit()][:80]


def _article_outcomes(events, article_ids):
    out = {aid: _new_row("") for aid in article_ids}
    for event in events:
        aid = event.get("content_id") if event.get("content_type") == "article" else None
        if aid not in out:
            continue
        row = out[aid]
        et = event.get("event_type") or "view"
        if et == "view":
            row["views"] += 1
            row["engaged"] += 1 if event.get("engaged") else 0
            if event.get("visitor_hash"):
                row["visitors"].add(event.get("visitor_hash"))
        elif et == "product_click":
            row["product_clicks"] += 1
        elif et in {"lead", "signup", "purchase"}:
            row["leads"] += 1
            if event.get("parent_view_id"):
                row["converted_views"].add(str(event.get("parent_view_id")))
    return out


def compute_worker_learning(worker, *, persist=False):
    worker_id = _oid(worker.get("_id"))
    if not worker_id:
        return {"ready": False, "reason": "invalid_worker", "generated_at": utcnow()}
    lookback = int(_clamp(int(worker.get("learning_lookback_days") or 90), 14, 365))
    min_views = int(_clamp(int(worker.get("learning_min_views") or 80), 20, 5000))
    objective = worker.get("learning_objective") if worker.get("learning_objective") in LEARNING_OBJECTIVES else "conversion_rate"
    since = utcnow() - timedelta(days=lookback)
    events = list(analytics_events.find({
        "newsjacking_worker_id": worker_id,
        "occurred_at": {"$gte": since},
        "internal_view": {"$ne": True},
        "is_bot": {"$ne": True},
    }).sort("occurred_at", 1))
    article_ids = list({e.get("content_id") for e in events if e.get("content_type") == "article" and isinstance(e.get("content_id"), ObjectId)})
    article_docs = list(articles.find({"_id": {"$in": article_ids}})) if article_ids else []
    article_map = {a["_id"]: a for a in article_docs}
    hook_ids = [a.get("hook_id") for a in article_docs if isinstance(a.get("hook_id"), ObjectId)]
    hook_docs = list(hooks.find({"_id": {"$in": hook_ids}})) if hook_ids else []
    hook_map = {h["_id"]: h for h in hook_docs}
    feed_item_ids = [h.get("feed_item_id") for h in hook_docs if isinstance(h.get("feed_item_id"), ObjectId)]
    item_docs = list(rss_feed_items.find({"_id": {"$in": feed_item_ids}})) if feed_item_ids else []
    item_map = {i["_id"]: i for i in item_docs}
    feed_ids = list({i.get("feed_id") for i in item_docs if isinstance(i.get("feed_id"), ObjectId)})
    feed_docs = list(rss_feeds.find({"_id": {"$in": feed_ids}})) if feed_ids else []
    feed_map = {f["_id"]: f for f in feed_docs}
    product_ids = list({pid for a in article_docs for pid in (a.get("product_ids") or []) if isinstance(pid, ObjectId)})
    product_docs = list(products.find({"_id": {"$in": product_ids}})) if product_ids else []
    product_map = {p["_id"]: p for p in product_docs}
    outcomes = _article_outcomes(events, set(article_map))

    source_rows, product_rows, confidence_rows, freshness_rows = {}, {}, {}, {}
    term_rows = defaultdict(lambda: {"articles": set(), "views": 0, "engaged": 0, "product_clicks": 0, "leads": 0, "converted_journeys": 0})
    article_meta = {}

    for aid, article in article_map.items():
        outcome = outcomes.get(aid) or _new_row("")
        hook = hook_map.get(article.get("hook_id")) or {}
        item = item_map.get(hook.get("feed_item_id")) or {}
        feed = feed_map.get(item.get("feed_id")) or {}
        source_key = str(item.get("feed_id") or "unknown")
        source_name = feed.get("title") or feed.get("url") or "Unknown source"
        confidence = hook.get("match_confidence")
        freshness = _freshness_hours(article, hook, item)
        article_meta[aid] = {"feed_id": item.get("feed_id"), "confidence": confidence, "freshness_hours": freshness, "title": item.get("title") or hook.get("title") or article.get("title") or ""}

        for mapping, key, name in [
            (source_rows, source_key, source_name),
            (confidence_rows, _confidence_bucket(confidence), _confidence_bucket(confidence)),
            (freshness_rows, _freshness_bucket(freshness), _freshness_bucket(freshness)),
        ]:
            row = mapping.setdefault(key, _new_row(name, id=key))
            row["views"] += outcome.get("views", 0); row["engaged"] += outcome.get("engaged", 0)
            row["product_clicks"] += outcome.get("product_clicks", 0); row["leads"] += outcome.get("leads", 0)
            row["visitors"].update(outcome.get("visitors") or set()); row["converted_views"].update(outcome.get("converted_views") or set())

        for pid in article.get("product_ids") or []:
            if not isinstance(pid, ObjectId):
                continue
            prow = product_rows.setdefault(str(pid), _new_row((product_map.get(pid) or {}).get("name") or "Product", id=str(pid)))
            prow["views"] += outcome.get("views", 0); prow["engaged"] += outcome.get("engaged", 0)
            prow["visitors"].update(outcome.get("visitors") or set())

        title_tokens = set(_tokens(article_meta[aid]["title"]))
        for term in title_tokens:
            tr = term_rows[term]; tr["articles"].add(aid); tr["views"] += outcome.get("views", 0); tr["engaged"] += outcome.get("engaged", 0)
            tr["product_clicks"] += outcome.get("product_clicks", 0); tr["leads"] += outcome.get("leads", 0)
            tr["converted_journeys"] += len(outcome.get("converted_views") or set()) or min(outcome.get("leads", 0), outcome.get("views", 0))

    # Product-specific direct interactions are more reliable than crediting every eligible product.
    for e in events:
        pid = e.get("product_id")
        if not isinstance(pid, ObjectId):
            continue
        key = str(pid)
        row = product_rows.setdefault(key, _new_row((product_map.get(pid) or {}).get("name") or "Product", id=key))
        et = e.get("event_type")
        if et == "product_click":
            row["product_clicks"] += 1
        elif et in {"lead", "signup", "purchase"}:
            row["leads"] += 1
            if e.get("parent_view_id"):
                row["converted_views"].add(str(e.get("parent_view_id")))

    source_list = [_finalize(v, objective) for v in source_rows.values()]
    product_list = [_finalize(v, objective) for v in product_rows.values()]
    confidence_list = [_finalize(v, objective) for v in confidence_rows.values()]
    freshness_list = [_finalize(v, objective) for v in freshness_rows.values()]
    source_list.sort(key=lambda r: (-r["objective_rate"], -r["views"], r["name"].lower()))
    product_list.sort(key=lambda r: (-r["objective_rate"], -r["views"], r["name"].lower()))
    bucket_order = {"<0.65": 0, "0.65–0.74": 1, "0.75–0.84": 2, "0.85+": 3, "Unknown": 4}
    confidence_list.sort(key=lambda r: bucket_order.get(r["name"], 99))
    fresh_order = {"≤6h": 0, "6–12h": 1, "12–24h": 2, "24–48h": 3, "48h+": 4, "Unknown": 5}
    freshness_list.sort(key=lambda r: fresh_order.get(r["name"], 99))

    views = sum(1 for e in events if (e.get("event_type") or "view") == "view")
    engaged = sum(1 for e in events if (e.get("event_type") or "view") == "view" and e.get("engaged"))
    clicks = sum(1 for e in events if e.get("event_type") == "product_click")
    conversion_events = [e for e in events if e.get("event_type") in {"lead", "signup", "purchase"}]
    converted = len({str(e.get("parent_view_id")) for e in conversion_events if e.get("parent_view_id")}) or min(len(conversion_events), views)
    overall = {"views": views, "engaged": engaged, "product_clicks": clicks, "leads": len(conversion_events), "converted_journeys": converted}
    overall_rate = _metric(overall, objective)
    ready = views >= min_views and len(article_docs) >= 3

    # Bounded, explainable policy. It can tighten/weight; it never bypasses product/campaign relevance.
    source_weights = {}
    if ready and overall_rate >= 0:
        for row in source_list:
            if row["views"] < max(10, min_views // 8):
                continue
            rate = row["objective_rate"] / 100.0
            ratio = (rate + 0.005) / (overall_rate + 0.005)
            source_weights[row["id"]] = round(_clamp(1 + (ratio - 1) * 0.12, 0.94, 1.06), 3)

    product_weights = {}
    for row in product_list:
        if row["views"] < max(10, min_views // 8):
            continue
        rate = row["objective_rate"] / 100.0
        ratio = (rate + 0.005) / (overall_rate + 0.005)
        product_weights[row["id"]] = round(_clamp(1 + (ratio - 1) * 0.10, 0.95, 1.05), 3)

    base_conf = float(worker.get("min_confidence") or 0.68)
    conf_adjust = 0.0
    if ready:
        eligible = [r for r in confidence_list if r["views"] >= max(10, min_views // 8) and r["name"] != "Unknown"]
        if len(eligible) >= 2:
            low = eligible[0]; high = eligible[-1]
            if high["objective_rate"] > low["objective_rate"] * 1.25 and high["views"] >= 10:
                conf_adjust = 0.03
            elif low["objective_rate"] > high["objective_rate"] * 1.25:
                conf_adjust = -0.01
    conf_adjust = _clamp(conf_adjust, -0.01, 0.04)

    max_age = None
    if ready:
        useful = [r for r in freshness_list if r["name"] != "Unknown" and r["views"] >= max(8, min_views // 10)]
        if useful and overall_rate > 0:
            threshold_pct = overall_rate * 100 * 0.65
            for row in reversed(useful):
                if row["objective_rate"] >= threshold_pct:
                    max_age = {"≤6h": 6, "6–12h": 12, "12–24h": 24, "24–48h": 48, "48h+": 72}.get(row["name"])
                    break

    term_candidates = []
    for term, row in term_rows.items():
        n = len(row["articles"])
        if n < 2 or row["views"] < 8:
            continue
        rate = _metric(row, objective)
        lift = (rate + 0.003) / (overall_rate + 0.003)
        score = math.log1p(row["views"]) * lift
        term_candidates.append((score, lift, term, n, row["views"]))
    term_candidates.sort(reverse=True)
    positive_terms = [{"term": t, "lift": round(l, 2), "articles": n, "views": v} for s,l,t,n,v in term_candidates if l >= 1.15][:10]
    negative_terms = [{"term": t, "lift": round(l, 2), "articles": n, "views": v} for s,l,t,n,v in reversed(term_candidates) if l <= 0.75][:8]

    policy = {
        "confidence_adjustment": round(conf_adjust, 3),
        "recommended_min_confidence": round(_clamp(base_conf + conf_adjust, 0.50, 0.92), 2),
        "max_story_age_hours": max_age,
        "source_weights": source_weights,
        "product_weights": product_weights,
        "positive_terms": [x["term"] for x in positive_terms],
        "negative_terms": [x["term"] for x in negative_terms],
    }
    recommendations = []
    if not ready:
        recommendations.append({"kind": "sample", "title": "Keep observing", "detail": f"Collect at least {min_views} public views across several generated articles before adapting this Worker.", "actionable": False})
    else:
        if conf_adjust > 0:
            recommendations.append({"kind": "confidence", "title": "Raise match strictness", "detail": f"Higher-confidence stories are outperforming lower-confidence matches. Suggested threshold: {policy['recommended_min_confidence']:.2f}.", "actionable": True})
        elif conf_adjust < 0:
            recommendations.append({"kind": "confidence", "title": "Slightly broaden matching", "detail": f"The lower confidence band is performing well. Suggested threshold: {policy['recommended_min_confidence']:.2f}; the adjustment remains deliberately small.", "actionable": True})
        if max_age:
            recommendations.append({"kind": "freshness", "title": "Prefer fresher stories", "detail": f"Performance is strongest inside roughly {max_age} hours of publication. Adaptive mode can stop considering older stories.", "actionable": True})
        if source_weights:
            top = max(source_weights, key=source_weights.get)
            if source_weights[top] > 1.01:
                name = next((r["name"] for r in source_list if r["id"] == top), "a source")
                recommendations.append({"kind": "source", "title": f"Favor {name}", "detail": "This source is producing stronger downstream outcomes. Adaptive mode uses only a small ranking bias, not a hard preference.", "actionable": True})
        if positive_terms:
            recommendations.append({"kind": "topics", "title": "Use learned topic hints", "detail": "Recurring high-performing story terms: " + ", ".join(x["term"] for x in positive_terms[:6]) + ". These are tie-breakers only.", "actionable": True})
        if not recommendations:
            recommendations.append({"kind": "stable", "title": "Current recipe is stable", "detail": "No strong bounded adjustment is supported by the current sample. Keep collecting outcomes.", "actionable": False})

    result = {
        "generated_at": utcnow(), "lookback_days": lookback, "min_views": min_views, "objective": objective,
        "ready": ready, "sample": {"views": views, "articles": len(article_docs), "engaged_views": engaged, "product_clicks": clicks, "conversions": len(conversion_events), "converted_journeys": converted},
        "overall": {"engagement_rate": round(engaged/views*100,1) if views else 0, "product_ctr": round(clicks/views*100,1) if views else 0, "conversion_rate": round(converted/views*100,1) if views else 0, "objective_rate": round(overall_rate*100,2) if views else 0},
        "sources": source_list[:20], "products": product_list[:20], "confidence_bands": confidence_list,
        "freshness_bands": freshness_list, "positive_terms": positive_terms, "negative_terms": negative_terms,
        "policy": policy, "recommendations": recommendations,
    }
    if persist:
        newsjacking_workers.update_one({"_id": worker_id}, {"$set": {"learning_state": result, "learning_updated_at": result["generated_at"], "updated_at": utcnow()}})
    return result


def refresh_worker_learning(worker, *, force=False):
    current = worker.get("learning_state") or {}
    generated = current.get("generated_at")
    if isinstance(generated, datetime) and generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    if not force and isinstance(generated, datetime) and generated >= utcnow() - timedelta(hours=6):
        return current
    return compute_worker_learning(worker, persist=True)


def effective_match_settings(worker, feed_item):
    base = float(worker.get("min_confidence") or 0.68)
    mode = worker.get("learning_mode") if worker.get("learning_mode") in LEARNING_MODES else "observe"
    state = worker.get("learning_state") or {}
    if mode != "adaptive" or not state.get("ready"):
        return {"min_confidence": base, "skip": False, "adjustment": 0.0, "reasons": []}
    policy = state.get("policy") or {}
    adjustment = float(policy.get("confidence_adjustment") or 0.0)
    reasons = []
    source_weight = (policy.get("source_weights") or {}).get(str(feed_item.get("feed_id")))
    if source_weight:
        source_delta = _clamp((1.0 - float(source_weight)) * 0.25, -0.015, 0.015)
        adjustment += source_delta
        if abs(source_delta) >= 0.004:
            reasons.append("source performance")
    title = (feed_item.get("title") or "").lower()
    if any(term in title for term in (policy.get("positive_terms") or [])):
        adjustment -= 0.008; reasons.append("positive topic history")
    if any(term in title for term in (policy.get("negative_terms") or [])):
        adjustment += 0.008; reasons.append("weak topic history")
    adjustment = _clamp(adjustment, -0.02, 0.05)
    effective = _clamp(base + adjustment, max(0.50, base - 0.02), min(0.92, base + 0.05))
    max_age = policy.get("max_story_age_hours")
    skip = False
    if max_age and isinstance(feed_item.get("published"), datetime):
        pub = feed_item["published"]
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age = max(0.0, (utcnow() - pub).total_seconds()/3600.0)
        if age > float(max_age):
            skip = True; reasons.append(f"older than learned {max_age}h window")
    return {"min_confidence": round(effective, 3), "skip": skip, "adjustment": round(adjustment, 3), "reasons": reasons}


def learning_prompt_context(worker, product_docs):
    mode = worker.get("learning_mode") if worker.get("learning_mode") in LEARNING_MODES else "observe"
    state = worker.get("learning_state") or {}
    if mode != "adaptive" or not state.get("ready"):
        return {}
    policy = state.get("policy") or {}
    product_weights = policy.get("product_weights") or {}
    return {
        "objective": state.get("objective"),
        "positive_topic_hints": policy.get("positive_terms") or [],
        "negative_topic_hints": policy.get("negative_terms") or [],
        "product_tie_breakers": [
            {"product_id": str(p.get("_id")), "weight": product_weights.get(str(p.get("_id")), 1.0)}
            for p in product_docs if str(p.get("_id")) in product_weights
        ],
        "rule": "These are bounded historical tie-breakers only. Never choose an irrelevant product or distort the news to follow them.",
    }
