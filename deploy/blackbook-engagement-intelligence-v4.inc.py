# NJS_ENGAGEMENT_INTELLIGENCE_BRIDGE_V4
# Companion BlackBook bridge for NJS v3.9.4 Failed Engagement State + 2500-contact bulk analysis.
# Installed inside BlackBook app.py before the global error handlers.

_NJS_ENGAGEMENT_MAX_SELECT = min(2500, max(1, int(os.getenv("NJS_ENGAGEMENT_MAX_SELECT", "2500"))))
_NJS_ENGAGEMENT_LOW_MAX = max(1, min(98, int(os.getenv("NJS_ENGAGEMENT_LOW_MAX", "39"))))
_NJS_ENGAGEMENT_MEDIUM_MAX = max(_NJS_ENGAGEMENT_LOW_MAX + 1, min(99, int(os.getenv("NJS_ENGAGEMENT_MEDIUM_MAX", "69"))))

_NJS_ENGAGEMENT_PERSON_CAMPAIGN_LIMIT = max(3, min(50, int(os.getenv("NJS_ENGAGEMENT_PERSON_CAMPAIGN_LIMIT", "20"))))
_NJS_ENGAGEMENT_AI_CAMPAIGN_LIMIT = max(3, min(50, int(os.getenv("NJS_ENGAGEMENT_AI_CAMPAIGN_LIMIT", "20"))))
_NJS_ENGAGEMENT_CATALOG_AI_LIMIT = max(20, min(1000, int(os.getenv("NJS_ENGAGEMENT_CATALOG_AI_LIMIT", "250"))))


def _njs_engagement_bucket(score):
    score = max(0, min(100, int(score or 0)))
    if score <= 0:
        return "inactive"
    if score <= _NJS_ENGAGEMENT_LOW_MAX:
        return "low"
    if score <= _NJS_ENGAGEMENT_MEDIUM_MAX:
        return "medium"
    return "high"


def _njs_interest_key(value):
    import unicodedata
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[a-z0-9]+", text.casefold())
    stop = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "interest", "interests"}
    clean = []
    for word in words:
        if word in stop:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        clean.append(word)
    return " ".join(clean)[:180]


def _njs_interest_similarity(left, right):
    from difflib import SequenceMatcher
    a = _njs_interest_key(left); b = _njs_interest_key(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    aset, bset = set(a.split()), set(b.split())
    jaccard = len(aset & bset) / max(1, len(aset | bset))
    seq = SequenceMatcher(None, a, b).ratio()
    return max(jaccard, seq)


def _njs_canonical_interest(raw_name):
    name = normalize_interest_label(raw_name)
    key = _njs_interest_key(name)
    if not name or not key:
        return None
    exact = db.marketing_interest_catalog.find_one({"$or": [{"key": key}, {"alias_keys": key}]})
    if exact:
        aliases = list(exact.get("aliases") or [])
        alias_keys = list(exact.get("alias_keys") or [])
        if name.casefold() != str(exact.get("name") or "").casefold() and name not in aliases:
            aliases.append(name)
        if key not in alias_keys:
            alias_keys.append(key)
        db.marketing_interest_catalog.update_one({"_id": exact["_id"]}, {"$set": {"aliases": aliases[-30:], "alias_keys": alias_keys[-30:], "last_seen_at": utcnow(), "updated_at": utcnow()}})
        return db.marketing_interest_catalog.find_one({"_id": exact["_id"]})
    best = None; best_score = 0.0
    for row in db.marketing_interest_catalog.find({}).sort("last_seen_at", DESCENDING).limit(750):
        candidates = [row.get("name") or ""] + list(row.get("aliases") or [])
        score = max((_njs_interest_similarity(name, candidate) for candidate in candidates), default=0.0)
        if score > best_score:
            best = row; best_score = score
    if best and best_score >= 0.88:
        aliases = list(best.get("aliases") or [])
        alias_keys = list(best.get("alias_keys") or [])
        if name not in aliases:
            aliases.append(name)
        if key not in alias_keys:
            alias_keys.append(key)
        db.marketing_interest_catalog.update_one({"_id": best["_id"]}, {"$set": {"aliases": aliases[-30:], "alias_keys": alias_keys[-30:], "last_seen_at": utcnow(), "updated_at": utcnow()}})
        return db.marketing_interest_catalog.find_one({"_id": best["_id"]})
    now = utcnow()
    doc = {"name": name[:160], "key": key, "aliases": [], "alias_keys": [key], "source": "mailchimp_engagement_ai", "created_at": now, "updated_at": now, "last_seen_at": now}
    try:
        doc["_id"] = db.marketing_interest_catalog.insert_one(doc).inserted_id
        return doc
    except DuplicateKeyError:
        return db.marketing_interest_catalog.find_one({"key": key})


def _njs_save_person_interests(person, analysis, bucket, batch_id):
    inferred = (analysis or {}).get("inferred_interests") or []
    keep_ids = []
    catalog_rows = []
    for item in inferred[:25]:
        row = _njs_canonical_interest(item.get("name"))
        if not row:
            continue
        keep_ids.append(row["_id"])
        mapping = {
            "person_id": person["_id"], "interest_id": row["_id"], "interest_key": row.get("key"),
            "interest_name": row.get("name"), "bucket": bucket,
            "score": max(0, min(100, int(item.get("interest_score") or 0))),
            "confidence": max(0, min(100, int(item.get("confidence") or 0))),
            "rationale": str(item.get("rationale") or "")[:2000],
            "evidence": (item.get("evidence") or [])[:20], "batch_id": batch_id,
            "updated_at": utcnow(),
        }
        db.marketing_person_interests.update_one(
            {"person_id": person["_id"], "interest_id": row["_id"]},
            {"$set": mapping, "$setOnInsert": {"created_at": utcnow()}}, upsert=True,
        )
        catalog_rows.append({"id": str(row["_id"]), "key": row.get("key"), "name": row.get("name"), "score": mapping["score"], "confidence": mapping["confidence"]})
    stale = {"person_id": person["_id"]}
    if keep_ids:
        stale["interest_id"] = {"$nin": keep_ids}
    db.marketing_person_interests.delete_many(stale)
    return catalog_rows


def _njs_engagement_catalog_snapshot(limit=None):
    limit = max(20, min(1000, int(limit or _NJS_ENGAGEMENT_CATALOG_AI_LIMIT)))
    rows = []
    for row in db.marketing_interest_catalog.find({}, {"name": 1, "aliases": 1, "key": 1}).sort("last_seen_at", DESCENDING).limit(limit):
        rows.append({
            "name": str(row.get("name") or "")[:160],
            "aliases": [str(x)[:160] for x in (row.get("aliases") or [])[:6]],
        })
    return rows


def _njs_engagement_ai_analyze(person, summary, days=3650):
    campaign_payload = []
    for row in (summary.get("campaigns") or [])[:_NJS_ENGAGEMENT_AI_CAMPAIGN_LIMIT]:
        campaign_payload.append({
            "campaign_id": row.get("campaign_id"),
            "subject": row.get("subject") or "",
            "send_time": row.get("send_time").isoformat() if hasattr(row.get("send_time"), "isoformat") else str(row.get("send_time") or ""),
            "opens": int(row.get("opens") or 0),
            "clicks": int(row.get("clicks") or 0),
            "clicked_urls": (row.get("clicked_urls") or [])[:20],
            "unsubscribes": int(row.get("unsubscribes") or 0),
            "bounces": int(row.get("bounces") or 0),
        })
    catalog = _njs_engagement_catalog_snapshot()
    system_prompt = """
You are an evidence-disciplined Mailchimp engagement analyst inside a private CRM.
Analyze only the supplied person's Mailchimp behavior. Do not compare this person with other contacts.
Use clicks as stronger evidence than opens. Never infer sensitive or protected traits.

Interest taxonomy rules:
- Prefer an EXISTING canonical interest from existing_interest_catalog whenever it is semantically equivalent to the observed signal.
- You may propose a new concise interest name only when no existing canonical interest is a reasonable match.
- Do not invent interests unsupported by campaign subjects or clicked URLs.
- Keep inferred interests specific, non-sensitive, and evidence-linked.
- Engagement level is 1-100 for people with meaningful engagement. Zero-engagement contacts are handled before this AI call.
""".strip()
    user_prompt = json.dumps({
        "person": {
            "name": person.get("name") or "",
            "role": person.get("role") or "",
            "organization": person.get("organization") or "",
        },
        "behavior_window_days": int(days),
        "engagement_metrics": {
            "campaign_count": int(summary.get("campaign_count") or 0),
            "engaged_campaigns": int(summary.get("engaged_campaigns") or 0),
            "clicked_campaigns": int(summary.get("clicked_campaigns") or 0),
            "unique_clicked_urls": int(summary.get("unique_clicked_urls") or 0),
            "engagement_score": int(summary.get("engagement_score") or 0),
            "counts": summary.get("counts") or {},
        },
        "campaign_behavior": campaign_payload,
        "existing_interest_catalog": catalog,
    }, ensure_ascii=False, default=str)
    return deepseek_json(
        system_prompt,
        user_prompt,
        schema=MARKETING_INTEREST_ANALYSIS_SCHEMA,
        schema_name="njs_engagement_interest_analysis",
    )


def _njs_engagement_mailchimp_rates(person):
    marketing = person.get("marketing") or {}
    mailchimp = marketing.get("mailchimp") or {}
    stats = mailchimp.get("stats") or {}
    if "avg_open_rate" in stats or "avg_click_rate" in stats:
        return float(stats.get("avg_open_rate") or 0), float(stats.get("avg_click_rate") or 0), True
    email = primary_marketing_email(person)
    if not email:
        return 0.0, 0.0, True
    member = mailchimp_get_member(email)
    if not member:
        return 0.0, 0.0, True
    remote_stats = member.get("stats") or {}
    open_rate = float(remote_stats.get("avg_open_rate") or 0)
    click_rate = float(remote_stats.get("avg_click_rate") or 0)
    db.people.update_one({"_id": person["_id"]}, {"$set": {
        "marketing.mailchimp.stats": {
            "avg_open_rate": open_rate,
            "avg_click_rate": click_rate,
            "member_rating": int(member.get("member_rating") or 0),
        },
        "marketing.mailchimp.stats_at": utcnow(),
        "marketing.updated_at": utcnow(),
    }})
    return open_rate, click_rate, True


def _njs_engagement_failure_code(error, stage="analysis"):
    text = str(error or "").strip()
    lower = text.casefold()
    if "403" in lower or "access denied" in lower or "forbidden" in lower:
        return "mailchimp_access_denied"
    if "401" in lower or "unauthorized" in lower:
        return "mailchimp_unauthorized"
    if "429" in lower or "rate limit" in lower or "too many requests" in lower:
        return "mailchimp_rate_limited"
    if "timeout" in lower or "timed out" in lower:
        return "upstream_timeout"
    return f"{str(stage or 'analysis').lower()}_error"[:80]


def _njs_engagement_fail_person(person_id, batch_id, error, stage="analysis"):
    stamp = utcnow()
    code = _njs_engagement_failure_code(error, stage)
    db.people.update_one({"_id": person_id}, {
        "$set": {
            "marketing.engagement_intelligence.bucket": "failed",
            "marketing.engagement_intelligence.status": "failed",
            "marketing.engagement_intelligence.analysis_status": "failed",
            "marketing.engagement_intelligence.error": str(error or "Analysis failed")[:1200],
            "marketing.engagement_intelligence.failure_code": code,
            "marketing.engagement_intelligence.failure_stage": str(stage or "analysis")[:40],
            "marketing.engagement_intelligence.failed_at": stamp,
            "marketing.engagement_intelligence.batch_id": ObjectId(str(batch_id)),
            "marketing.engagement_intelligence.updated_at": stamp,
            "marketing.ai_analysis_status": "failed",
            "marketing.ai_analysis_status_at": stamp,
            "marketing.ai_analysis_error": str(error or "Analysis failed")[:1200],
            "marketing.updated_at": stamp,
            "updated_at": stamp,
        },
        "$inc": {"marketing.engagement_intelligence.failure_count": 1},
    })
    return code


def _njs_engagement_complete_person(person_id, batch_id, days, bucket, score, event_counts, meaningful, catalog_rows, analysis_status):
    intelligence = {
        "bucket": bucket,
        "score": int(score),
        "status": "complete",
        "analysis_status": analysis_status,
        "batch_id": ObjectId(str(batch_id)),
        "days": int(days),
        "meaningful_events": int(meaningful),
        "event_counts": event_counts or {},
        "interest_ids": [ObjectId(x["id"]) for x in catalog_rows],
        "interest_keys": [x["key"] for x in catalog_rows if x.get("key")],
        "interest_names": [x["name"] for x in catalog_rows],
        "imported_at": utcnow(),
        "analyzed_at": utcnow(),
    }
    db.people.update_one({"_id": person_id}, {"$set": {
        "marketing.engagement_intelligence": intelligence,
        "marketing.ai_analysis_status": analysis_status,
        "marketing.ai_analysis_status_at": utcnow(),
        "marketing.updated_at": utcnow(),
        "updated_at": utcnow(),
    }, "$unset": {"marketing.ai_analysis_error": ""}})


def _njs_engagement_record_progress(batch_id, bucket=None, error=False):
    batch_oid = ObjectId(str(batch_id))
    inc = {"processed": 1}
    if error or bucket == "failed":
        inc["counts.failed"] = 1
        inc["counts.errors"] = 1  # backward-compatible alias
    elif bucket in {"inactive", "low", "medium", "high"}:
        inc[f"counts.{bucket}"] = 1
    raw_db.marketing_analysis_batches.update_one(
        {"_id": batch_oid},
        {"$inc": inc, "$set": {"updated_at": utcnow()}},
    )
    row = raw_db.marketing_analysis_batches.find_one({"_id": batch_oid}) or {}
    total = max(1, int(row.get("total") or 1))
    processed = int(row.get("processed") or 0)
    progress = min(99, 5 + int(94 * processed / total))
    raw_db.marketing_analysis_batches.update_one({"_id": batch_oid}, {"$set": {"progress": progress, "updated_at": utcnow()}})
    return processed, total, progress


@celery.task(bind=True, name="blackbook.njs_engagement_person_import")
def njs_engagement_person_import_task(self, user_id, batch_id, person_id, days=3650):
    batch = raw_db.marketing_analysis_batches.find_one({"_id": ObjectId(str(batch_id))}) or {}
    org_id = batch.get("organization_id")
    try:
        with worker_user_context(user_id, org_id):
            pid = ObjectId(str(person_id))
            person = db.people.find_one({"_id": pid})
            if not person:
                raise ValueError("Person not found")
            db.people.update_one({"_id": pid}, {"$set": {
                "marketing.engagement_intelligence.status": "importing",
                "marketing.engagement_intelligence.batch_id": ObjectId(str(batch_id)),
                "marketing.engagement_intelligence.last_attempt_at": utcnow(),
                "marketing.engagement_intelligence.updated_at": utcnow(),
            }})

            # First use already-imported local activity. This avoids unnecessary Mailchimp calls.
            summary = marketing_person_engagement_summary(pid, days=int(days))
            counts = summary.get("counts") or {}
            meaningful = int(counts.get("open", 0)) + int(counts.get("click", 0))
            if meaningful > 0:
                return {"person_id": str(pid), "inactive": False, "imported": False, "meaningful": meaningful}

            # Queue refresh stores Mailchimp member rates. Most of a large audience can
            # therefore be classified inactive without fetching campaign activity.
            open_rate, click_rate, _ = _njs_engagement_mailchimp_rates(person)
            if open_rate <= 0 and click_rate <= 0:
                return {"person_id": str(pid), "inactive": True, "imported": False, "meaningful": 0}

            # Only contacts with a positive Mailchimp engagement signal get a detailed
            # per-person activity import. No all-audience campaign/activity import occurs.
            mailchimp_import_person_activity(
                person,
                campaign_limit=_NJS_ENGAGEMENT_PERSON_CAMPAIGN_LIMIT,
                job_id=None,
            )
            db.people.update_one({"_id": pid}, {"$set": {
                "marketing.mailchimp.last_activity_pull_at": utcnow(),
                "marketing.updated_at": utcnow(),
            }})
            summary = marketing_person_engagement_summary(pid, days=int(days))
            counts = summary.get("counts") or {}
            meaningful = int(counts.get("open", 0)) + int(counts.get("click", 0))
            return {"person_id": str(pid), "inactive": meaningful <= 0, "imported": True, "meaningful": meaningful}
    except Exception as exc:
        return {"person_id": str(person_id), "error": str(exc)[:1200], "stage": "import"}


@celery.task(bind=True, name="blackbook.njs_engagement_person_analyze")
def njs_engagement_person_analyze_task(self, import_result, user_id, batch_id, days=3650):
    batch = raw_db.marketing_analysis_batches.find_one({"_id": ObjectId(str(batch_id))}) or {}
    org_id = batch.get("organization_id")
    person_id = str((import_result or {}).get("person_id") or "")
    try:
        with worker_user_context(user_id, org_id):
            if (import_result or {}).get("error"):
                raise ValueError(import_result.get("error"))
            pid = ObjectId(person_id)
            person = db.people.find_one({"_id": pid})
            if not person:
                raise ValueError("Person not found")

            if (import_result or {}).get("inactive"):
                db.marketing_person_interests.delete_many({"person_id": pid})
                _njs_engagement_complete_person(pid, batch_id, days, "inactive", 0, {}, 0, [], "inactive")
                _njs_engagement_record_progress(batch_id, bucket="inactive")
                return {"person_id": person_id, "bucket": "inactive", "score": 0, "interests": []}

            summary = marketing_person_engagement_summary(pid, days=int(days))
            counts = summary.get("counts") or {}
            meaningful = int(counts.get("open", 0)) + int(counts.get("click", 0))
            if meaningful <= 0:
                db.marketing_person_interests.delete_many({"person_id": pid})
                _njs_engagement_complete_person(pid, batch_id, days, "inactive", 0, counts, 0, [], "inactive")
                _njs_engagement_record_progress(batch_id, bucket="inactive")
                return {"person_id": person_id, "bucket": "inactive", "score": 0, "interests": []}

            analysis = _njs_engagement_ai_analyze(person, summary, days=int(days)) or {}
            score = max(1, min(100, int(analysis.get("engagement_level") or summary.get("engagement_score") or 1)))
            bucket = _njs_engagement_bucket(score)
            catalog_rows = _njs_save_person_interests(person, analysis, bucket, ObjectId(str(batch_id)))
            now = utcnow()
            analysis_doc = {
                "person_id": pid,
                "analysis": analysis,
                "window_days": int(days),
                "campaign_count": int(summary.get("campaign_count") or 0),
                "event_count": meaningful,
                "click_count": int(counts.get("click") or 0),
                "open_count": int(counts.get("open") or 0),
                "model": DEEPSEEK_MODEL,
                "source": "njs_parallel_engagement",
                "created_at": now,
            }
            analysis_id = db.marketing_interest_analyses.insert_one(analysis_doc).inserted_id
            db.people.update_one({"_id": pid}, {"$set": {
                "marketing.ai_interest_profile": analysis,
                "marketing.ai_interest_profile_id": analysis_id,
                "marketing.ai_interest_profile_at": now,
                "marketing.ai_interest_profile_model": DEEPSEEK_MODEL,
            }})
            _njs_engagement_complete_person(pid, batch_id, days, bucket, score, counts, meaningful, catalog_rows, "analyzed")
            _njs_engagement_record_progress(batch_id, bucket=bucket)
            return {"person_id": person_id, "bucket": bucket, "score": score, "interests": [x.get("name") for x in catalog_rows]}
    except Exception as exc:
        stage = str((import_result or {}).get("stage") or "analysis")[:40]
        code = _njs_engagement_failure_code(exc, stage)
        if ObjectId.is_valid(person_id):
            pid = ObjectId(person_id)
            with worker_user_context(user_id, org_id):
                code = _njs_engagement_fail_person(pid, batch_id, exc, stage=stage)
        _njs_engagement_record_progress(batch_id, bucket="failed", error=True)
        return {"person_id": person_id, "bucket": "failed", "error": str(exc)[:1200], "failure_code": code, "failure_stage": stage}


@celery.task(bind=True, name="blackbook.njs_engagement_batch_finalize")
def njs_engagement_batch_finalize_task(self, results, job_id, user_id, batch_id):
    batch_oid = ObjectId(str(batch_id))
    result_rows = list(results or [])
    counts = {"total": len(result_rows), "queue": 0, "failed": 0, "inactive": 0, "low": 0, "medium": 0, "high": 0, "errors": 0}
    interest_counter = Counter()
    for row in result_rows:
        bucket = str(row.get("bucket") or "queue")
        if row.get("error") or bucket == "failed":
            counts["failed"] += 1
            counts["errors"] += 1  # backward-compatible alias
            continue
        counts[bucket if bucket in counts else "queue"] += 1
        for name in row.get("interests") or []:
            if name:
                interest_counter[str(name)] += 1
    status = "success" if counts["failed"] == 0 else "partial"
    result = {"counts": counts, "top_interests": interest_counter.most_common(25), "pipeline": "parallel_person_chains_v4"}
    stamp = utcnow()
    raw_db.marketing_analysis_batches.update_one({"_id": batch_oid}, {"$set": {
        "status": status,
        "progress": 100,
        "processed": len(result_rows),
        "counts": counts,
        "result": result,
        "completed_at": stamp,
        "updated_at": stamp,
    }})
    update_ai_job(
        job_id,
        status="success" if status == "success" else "success",
        progress=100,
        message=f"Engagement intelligence complete: {len(result_rows)} processed, {counts['failed']} failed",
        completed_at=stamp,
        result_url="/marketing/analysis",
        result=result,
    )
    return result


@celery.task(bind=True, name="blackbook.njs_engagement_bulk_analysis")
def njs_engagement_bulk_analysis_task(self, job_id, user_id, batch_id, person_ids, days=3650):
    """Orchestrator only: fan out one sequential import->AI chain per person."""
    try:
        from celery import chain, group, chord
        selected = [str(x) for x in person_ids if ObjectId.is_valid(str(x))][:_NJS_ENGAGEMENT_MAX_SELECT]
        if not selected:
            raise ValueError("No valid people were selected")
        stamp = utcnow()
        raw_db.marketing_analysis_batches.update_one({"_id": ObjectId(str(batch_id))}, {"$set": {
            "status": "running",
            "pipeline": "parallel_person_chains_v4",
            "started_at": stamp,
            "updated_at": stamp,
            "progress": 3,
            "processed": 0,
            "total": len(selected),
            "counts": {"total": len(selected), "queue": 0, "failed": 0, "inactive": 0, "low": 0, "medium": 0, "high": 0, "errors": 0},
        }})
        update_ai_job(job_id, status="started", started_at=stamp, progress=3, message=f"Dispatching {len(selected)} parallel engagement chains")
        pipelines = []
        for person_id in selected:
            pipelines.append(chain(
                njs_engagement_person_import_task.s(str(user_id), str(batch_id), person_id, int(days)),
                njs_engagement_person_analyze_task.s(str(user_id), str(batch_id), int(days)),
            ))
        callback = njs_engagement_batch_finalize_task.s(str(job_id), str(user_id), str(batch_id))
        async_result = chord(group(pipelines))(callback)
        raw_db.marketing_analysis_batches.update_one({"_id": ObjectId(str(batch_id))}, {"$set": {
            "fanout_task_id": async_result.id,
            "child_chain_count": len(pipelines),
            "updated_at": utcnow(),
        }})
        update_ai_job(job_id, progress=5, message=f"{len(selected)} person chains dispatched in parallel")
        return {"status": "dispatched", "chains": len(pipelines), "fanout_task_id": async_result.id}
    except Exception as exc:
        stamp = utcnow()
        raw_db.marketing_analysis_batches.update_one({"_id": ObjectId(str(batch_id))}, {"$set": {
            "status": "error", "error": str(exc)[:4000], "completed_at": stamp, "updated_at": stamp,
        }})
        update_ai_job(job_id, status="error", progress=100, message="Could not dispatch engagement analysis", error=str(exc)[:12000], completed_at=stamp)
        raise


@celery.task(bind=True, name="blackbook.njs_engagement_queue_refresh")
def njs_engagement_queue_refresh_task(self, job_id, user_id):
    job_doc = raw_db.ai_jobs.find_one({"_id": ObjectId(str(job_id))}) or {}
    org_id = job_doc.get("organization_id")
    try:
        update_ai_job(job_id, status="started", started_at=utcnow(), progress=2, message="Refreshing Mailchimp audience and engagement rates")
        with worker_user_context(user_id, org_id):
            if not mailchimp_configured():
                raise MailchimpAPIError("Mailchimp is not configured")
            audience_id = mailchimp_audience_id()
            offset = 0; pulled = 0; matched = 0; created = 0; blocked = 0
            while True:
                response = mailchimp_api_request("GET", f"/lists/{quote(audience_id)}/members", query={"count": MAILCHIMP_SYNC_PAGE_SIZE, "offset": offset})
                members = response.get("members") or []
                if not members:
                    break
                for member in members:
                    pulled += 1
                    person, was_created = _mailchimp_member_to_person(member, import_missing=True)
                    if not person:
                        continue
                    matched += 1; created += int(was_created)
                    status = str(member.get("status") or "").lower()
                    stats = member.get("stats") or {}
                    now = utcnow()
                    fields = {
                        "marketing.mailchimp.status": status,
                        "marketing.mailchimp.audience_id": audience_id,
                        "marketing.mailchimp.member_id": member.get("id", ""),
                        "marketing.mailchimp.subscriber_hash": mailchimp_member_hash(member.get("email_address", "")),
                        "marketing.mailchimp.last_pull_at": now,
                        "marketing.mailchimp.stats": {
                            "avg_open_rate": float(stats.get("avg_open_rate") or 0),
                            "avg_click_rate": float(stats.get("avg_click_rate") or 0),
                            "member_rating": int(member.get("member_rating") or 0),
                        },
                        "marketing.mailchimp.stats_at": now,
                        "marketing.updated_at": now,
                    }
                    if status == "subscribed":
                        fields["marketing.email_eligibility"] = "eligible"
                        fields["marketing.eligibility_source"] = "mailchimp"
                        fields["marketing.eligibility_note"] = "Subscribed in Mailchimp audience"
                    elif status in MAILCHIMP_BLOCKED_STATUSES:
                        blocked += 1
                        fields["marketing.email_eligibility"] = "prohibited"
                        fields["marketing.eligibility_source"] = "mailchimp"
                        fields["marketing.eligibility_note"] = f"Mailchimp status: {status}"
                    db.people.update_one({"_id": person["_id"]}, {"$set": fields})
                offset += len(members)
                total = max(int(response.get("total_items") or 0), offset)
                update_ai_job(job_id, progress=min(95, 5 + int(88 * offset / max(1, total))), message=f"Refreshing Mailchimp audience {min(offset,total)}/{total}")
                if len(members) < MAILCHIMP_SYNC_PAGE_SIZE:
                    break
            queue_count = db.people.count_documents({"marketing.mailchimp.audience_id": audience_id, "$or": [
                {"marketing.engagement_intelligence.bucket": {"$exists": False}},
                {"marketing.engagement_intelligence.bucket": "queue"},
            ]})
            result = {"pulled": pulled, "matched": matched, "created": created, "blocked": blocked, "queue_count": queue_count}
        update_ai_job(job_id, status="success", progress=100, message=f"Mailchimp queue refreshed: {queue_count} waiting", completed_at=utcnow(), result=result)
        return result
    except Exception as exc:
        update_ai_job(job_id, status="error", progress=100, message="Mailchimp queue refresh failed", error=str(exc)[:12000], completed_at=utcnow())
        raise

def _njs_engagement_base_query(bucket="all", interest=""):
    query = {"marketing.mailchimp.audience_id": mailchimp_audience_id()}
    bucket = str(bucket or "all").lower()
    if bucket == "queue":
        query["$or"] = [
            {"marketing.engagement_intelligence.bucket": {"$exists": False}},
            {"marketing.engagement_intelligence.bucket": "queue"},
        ]
    elif bucket in {"failed", "inactive", "low", "medium", "high"}:
        query["marketing.engagement_intelligence.bucket"] = bucket
    if interest:
        key = _njs_interest_key(interest)
        query["marketing.engagement_intelligence.interest_keys"] = key
    return query


def _njs_engagement_person_row(person):
    marketing = person.get("marketing") or {}; intel = marketing.get("engagement_intelligence") or {}; profile = marketing.get("ai_interest_profile") or {}
    bucket = intel.get("bucket") or "queue"
    return {
        "id": str(person["_id"]), "name": person.get("name") or "", "email": person.get("email") or "",
        "organization": person.get("organization") or "", "role": person.get("role") or "",
        "mailchimp_status": (marketing.get("mailchimp") or {}).get("status") or "",
        "bucket": bucket, "engagement_score": int(intel.get("score") or profile.get("engagement_level") or 0),
        "analysis_status": intel.get("status") or marketing.get("ai_analysis_status") or "queue",
        "last_import_at": intel.get("imported_at") or (marketing.get("mailchimp") or {}).get("last_activity_pull_at"),
        "analyzed_at": intel.get("analyzed_at") or marketing.get("ai_interest_profile_at"),
        "interests": [{"name": row.get("interest_name"), "key": row.get("interest_key"), "score": row.get("score"), "confidence": row.get("confidence")} for row in db.marketing_person_interests.find({"person_id": person["_id"]}).sort("score", DESCENDING).limit(10)],
        "error": intel.get("error") or marketing.get("ai_analysis_error") or "",
        "failure_code": intel.get("failure_code") or "",
        "failure_stage": intel.get("failure_stage") or "",
        "failed_at": intel.get("failed_at"),
        "failure_count": int(intel.get("failure_count") or 0),
    }


@app.get("/api/v1/marketing/engagement/overview")
def njs_engagement_overview_api():
    org, key_doc = _prospect_api_auth()
    with organization_scope(org["_id"]):
        if not mailchimp_configured():
            return _njs_bridge_error("Mailchimp is not configured for this BlackBook organization", 409)
        audience_id = mailchimp_audience_id(); base = {"marketing.mailchimp.audience_id": audience_id}
        counts = {"total": db.people.count_documents(base)}
        for bucket in ("failed", "inactive", "low", "medium", "high"):
            counts[bucket] = db.people.count_documents({**base, "marketing.engagement_intelligence.bucket": bucket})
        counts["queue"] = db.people.count_documents({**base, "$or": [
            {"marketing.engagement_intelligence.bucket": {"$exists": False}},
            {"marketing.engagement_intelligence.bucket": "queue"},
        ]})
        latest = db.marketing_analysis_batches.find_one({"kind": "njs_engagement_intelligence"}, sort=[("created_at", DESCENDING)])
        return _njs_bridge_ok({"counts": counts, "interest_count": db.marketing_interest_catalog.count_documents({}), "latest_batch": _njs_bridge_json_value(latest) if latest else None, "thresholds": {"low_max": _NJS_ENGAGEMENT_LOW_MAX, "medium_max": _NJS_ENGAGEMENT_MEDIUM_MAX, "high_min": _NJS_ENGAGEMENT_MEDIUM_MAX + 1}, "max_select": _NJS_ENGAGEMENT_MAX_SELECT})


@app.post("/api/v1/marketing/engagement/queue/refresh")
def njs_engagement_queue_refresh_api():
    org, key_doc = _prospect_api_auth()
    with organization_scope(org["_id"]):
        if not mailchimp_configured():
            return _njs_bridge_error("Mailchimp is not configured for this BlackBook organization", 409)
        user_id = (key_doc or {}).get("created_by")
        member = None
        if user_id and ObjectId.is_valid(str(user_id)):
            member = raw_db.organization_memberships.find_one({"organization_id": org["_id"], "user_id": ObjectId(str(user_id)), "status": {"$ne": "disabled"}})
        if not member:
            member = raw_db.organization_memberships.find_one({"organization_id": org["_id"], "status": {"$ne": "disabled"}}, sort=[("created_at", ASCENDING)])
            user_id = (member or {}).get("user_id")
        if not user_id:
            return _njs_bridge_error("The BlackBook Intake API key is not associated with an active user", 409)
        audience_id = mailchimp_audience_id()
        queue_count = db.people.count_documents({"marketing.mailchimp.audience_id": audience_id, "$or": [
            {"marketing.engagement_intelligence.bucket": {"$exists": False}},
            {"marketing.engagement_intelligence.bucket": "queue"},
        ]})
        job_id = create_ai_job("njs_engagement_queue_refresh", "Refresh Mailchimp audience for NJS Engagement Intelligence", ObjectId(str(user_id)), {"source": "njs_engagement_queue"})
        async_result = njs_engagement_queue_refresh_task.delay(str(job_id), str(user_id))
        update_ai_job(job_id, celery_task_id=async_result.id)
        return _njs_bridge_ok({"status": "queued", "job_id": str(job_id), "queue_count": queue_count, "message": "Mailchimp audience refresh queued in BlackBook"}, 202)


@app.get("/api/v1/marketing/engagement/people")
def njs_engagement_people_api():
    org, key_doc = _prospect_api_auth()
    with organization_scope(org["_id"]):
        if not mailchimp_configured():
            return _njs_bridge_error("Mailchimp is not configured for this BlackBook organization", 409)
        bucket = str(request.args.get("bucket") or "queue").lower(); interest = str(request.args.get("interest") or "").strip()[:180]
        q = str(request.args.get("q") or "").strip()[:160]; limit = _safe_int(request.args.get("limit"), 100, 1, _NJS_ENGAGEMENT_MAX_SELECT); offset = _safe_int(request.args.get("offset"), 0, 0, 1000000)
        query = _njs_engagement_base_query(bucket, interest)
        if q:
            rx = {"$regex": re.escape(q), "$options": "i"}; search = {"$or": [{"name": rx}, {"email": rx}, {"organization": rx}, {"role": rx}]}; query = {"$and": [query, search]}
        total = db.people.count_documents(query)
        rows = [_njs_engagement_person_row(person) for person in db.people.find(query).sort([("name", ASCENDING), ("updated_at", DESCENDING)]).skip(offset).limit(limit)]
        return _njs_bridge_ok({"people": rows, "count": len(rows), "total": total, "bucket": bucket, "limit": limit, "offset": offset})


@app.post("/api/v1/marketing/engagement/bulk-analyze")
def njs_engagement_bulk_analyze_api():
    org, key_doc = _prospect_api_auth(); payload = request.get_json(silent=True) or {}
    with organization_scope(org["_id"]):
        if not mailchimp_configured():
            return _njs_bridge_error("Mailchimp is not configured for this BlackBook organization", 409)
        raw_ids = [str(x) for x in (payload.get("person_ids") or []) if ObjectId.is_valid(str(x))]
        dedup = list(dict.fromkeys(raw_ids))
        if not dedup:
            return _njs_bridge_error("Select at least one queued person")
        if len(dedup) > _NJS_ENGAGEMENT_MAX_SELECT:
            return _njs_bridge_error(f"A maximum of {_NJS_ENGAGEMENT_MAX_SELECT} people can be analyzed in one batch", 413)
        audience_id = mailchimp_audience_id(); selected = [str(x["_id"]) for x in db.people.find({"_id": {"$in": [ObjectId(x) for x in dedup]}, "marketing.mailchimp.audience_id": audience_id}, {"_id": 1})]
        if not selected:
            return _njs_bridge_error("None of the selected people belong to the configured Mailchimp audience", 409)
        user_id = (key_doc or {}).get("created_by")
        member = None
        if user_id:
            member = raw_db.organization_memberships.find_one({"organization_id": org["_id"], "user_id": ObjectId(str(user_id)), "status": {"$ne": "disabled"}})
        if not member:
            member = raw_db.organization_memberships.find_one({"organization_id": org["_id"], "status": {"$ne": "disabled"}}, sort=[("created_at", ASCENDING)])
            user_id = (member or {}).get("user_id")
        if not user_id:
            return _njs_bridge_error("The BlackBook Intake API key is not associated with an active user", 409)
        days = _safe_int(payload.get("days"), 3650, 30, 36500); stamp = utcnow()
        batch_id = db.marketing_analysis_batches.insert_one({"kind": "njs_engagement_intelligence", "status": "queued", "progress": 0, "processed": 0, "total": len(selected), "counts": {"total": len(selected)}, "person_ids": [ObjectId(x) for x in selected], "days": days, "created_by": ObjectId(str(user_id)), "created_at": stamp, "updated_at": stamp}).inserted_id
        job_id = create_ai_job("njs_engagement_intelligence", f"Analyze Mailchimp engagement for {len(selected)} people", user_id, {"batch_id": str(batch_id), "count": len(selected), "days": days})
        enqueue_job(njs_engagement_bulk_analysis_task, job_id, str(user_id), str(batch_id), selected, days)
        db.marketing_analysis_batches.update_one({"_id": batch_id}, {"$set": {"job_id": job_id, "updated_at": utcnow()}})
        return _njs_bridge_ok({"batch_id": str(batch_id), "job_id": str(job_id), "status": "queued", "selected": len(selected), "max_select": _NJS_ENGAGEMENT_MAX_SELECT}, 202)


@app.get("/api/v1/marketing/engagement/interests")
def njs_engagement_interests_api():
    org, key_doc = _prospect_api_auth()
    with organization_scope(org["_id"]):
        grouped = list(db.marketing_person_interests.aggregate([{"$group": {"_id": {"interest_id": "$interest_id", "bucket": "$bucket"}, "count": {"$sum": 1}, "avg_score": {"$avg": "$score"}}}]))
        stats = {}
        for row in grouped:
            iid = row.get("_id", {}).get("interest_id"); bucket = row.get("_id", {}).get("bucket") or "queue"
            if not iid: continue
            item = stats.setdefault(str(iid), {"count": 0, "buckets": {}, "score_total": 0.0, "score_weight": 0})
            count = int(row.get("count") or 0); item["count"] += count; item["buckets"][bucket] = count; item["score_total"] += float(row.get("avg_score") or 0) * count; item["score_weight"] += count
        rows = []
        for interest in db.marketing_interest_catalog.find({}).sort("last_seen_at", DESCENDING).limit(1000):
            st = stats.get(str(interest["_id"]), {"count": 0, "buckets": {}, "score_total": 0, "score_weight": 0})
            rows.append({"id": str(interest["_id"]), "key": interest.get("key") or "", "name": interest.get("name") or "", "aliases": (interest.get("aliases") or [])[:10], "person_count": st["count"], "average_score": round(st["score_total"] / max(1, st["score_weight"]), 1), "buckets": st["buckets"], "last_seen_at": interest.get("last_seen_at")})
        rows.sort(key=lambda x: (x["person_count"], x["average_score"], x["name"]), reverse=True)
        return _njs_bridge_ok({"interests": rows})


@app.get("/api/v1/marketing/engagement/batches")
def njs_engagement_batches_api():
    org, key_doc = _prospect_api_auth()
    with organization_scope(org["_id"]):
        limit = _safe_int(request.args.get("limit"), 12, 1, 100)
        rows = []
        for batch in db.marketing_analysis_batches.find({"kind": "njs_engagement_intelligence"}).sort("created_at", DESCENDING).limit(limit):
            rows.append({"id": str(batch["_id"]), "status": batch.get("status") or "", "progress": int(batch.get("progress") or 0), "processed": int(batch.get("processed") or 0), "total": int(batch.get("total") or 0), "counts": batch.get("counts") or {}, "job_id": str(batch.get("job_id") or ""), "created_at": batch.get("created_at"), "completed_at": batch.get("completed_at"), "error": batch.get("error") or ""})
        return _njs_bridge_ok({"batches": rows})


@app.get("/api/v1/marketing/engagement/batches/<batch_id>")
def njs_engagement_batch_api(batch_id):
    org, key_doc = _prospect_api_auth()
    with organization_scope(org["_id"]):
        if not ObjectId.is_valid(str(batch_id)):
            return _njs_bridge_error("Invalid batch id", 400)
        batch = db.marketing_analysis_batches.find_one({"_id": ObjectId(str(batch_id)), "kind": "njs_engagement_intelligence"})
        if not batch:
            return _njs_bridge_error("Batch not found", 404)
        return _njs_bridge_ok({"batch": _njs_bridge_json_value(batch)})


try:
    db.marketing_interest_catalog.create_index([("key", ASCENDING)], unique=True)
    db.marketing_person_interests.create_index([("person_id", ASCENDING), ("interest_id", ASCENDING)], unique=True)
    db.marketing_person_interests.create_index([("interest_id", ASCENDING), ("bucket", ASCENDING)])
    db.marketing_analysis_batches.create_index([("kind", ASCENDING), ("created_at", DESCENDING)])
except Exception:
    pass
# END_NJS_ENGAGEMENT_INTELLIGENCE_BRIDGE_V4
