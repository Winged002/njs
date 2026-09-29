# NJS_NEWSLETTER_BRIDGE_V1
# Companion API for BlackBook v13.x. Inserted before the application's error handlers.

def _njs_bridge_json_value(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _njs_bridge_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_njs_bridge_json_value(v) for v in value]
    return value


def _njs_bridge_ok(payload=None, status=200):
    body = {"ok": True}
    if payload:
        body.update(_njs_bridge_json_value(payload))
    return jsonify(body), status


def _njs_bridge_error(message, status=400):
    return jsonify({"ok": False, "error": str(message)[:1600]}), status


def _njs_bridge_people_from_payload(payload):
    payload = payload or {}
    people = {}
    segment_ids = [str(x) for x in (payload.get("segment_ids") or []) if ObjectId.is_valid(str(x))][:50]
    person_ids = [str(x) for x in (payload.get("person_ids") or []) if ObjectId.is_valid(str(x))][:500]
    if payload.get("include_all_eligible"):
        for person in db.people.find({"marketing.email_eligibility": "eligible", "do_not_contact": {"$ne": True}}).limit(10000):
            people[str(person["_id"])] = person
    for sid in segment_ids:
        segment = db.marketing_segments.find_one({"_id": ObjectId(sid)})
        if not segment:
            continue
        for person in marketing_segment_people(segment):
            people[str(person["_id"])] = person
    if person_ids:
        for person in db.people.find({"_id": {"$in": [ObjectId(x) for x in person_ids]}}):
            people[str(person["_id"])] = person
    # BlackBook consent/eligibility remains authoritative even for explicit person selection.
    eligible = []
    blocked = 0
    missing_email = 0
    for person in people.values():
        if person.get("do_not_contact") or (person.get("marketing") or {}).get("email_eligibility") != "eligible":
            blocked += 1
            continue
        email = normalize_prospect_email(person.get("email") or ((person.get("emails") or [""])[0] if person.get("emails") else ""))
        if not email:
            missing_email += 1
            continue
        eligible.append(person)
    return eligible, {"selected": len(people), "eligible": len(eligible), "blocked": blocked, "missing_email": missing_email}


@app.get("/api/v1/marketing/context")
def njs_marketing_context_api():
    org, key_doc = _prospect_api_auth()
    with organization_scope(org["_id"]):
        mailchimp_ready = mailchimp_configured()
        audience_id = mailchimp_audience_id() if mailchimp_ready else ""
        segment_rows = []
        for segment in db.marketing_segments.find({}).sort("updated_at", DESCENDING).limit(100):
            segment_rows.append({
                "id": str(segment["_id"]), "name": segment.get("name") or "Segment",
                "description": segment.get("description") or "", "stage": segment.get("stage") or "",
                "tag": segment.get("tag") or "", "mailchimp_tag": segment.get("mailchimp_tag") or "",
                "count": db.people.count_documents(marketing_segment_query(segment)),
                "last_sync_at": segment.get("last_sync_at"), "last_sync_result": segment.get("last_sync_result") or {},
            })
        recent_campaigns = []
        for row in db.mailchimp_campaigns.find({}).sort([("send_time", DESCENDING), ("updated_at", DESCENDING)]).limit(20):
            recent_campaigns.append({
                "id": row.get("mailchimp_id") or str(row.get("_id")),
                "title": row.get("campaign_title") or row.get("title") or row.get("subject_line") or "Mailchimp campaign",
                "subject": row.get("subject_line") or "", "status": row.get("status") or "",
                "send_time": row.get("send_time"), "emails_sent": int(row.get("emails_sent") or 0),
                "unique_opens": int(row.get("unique_opens") or 0), "unique_clicks": int(row.get("unique_clicks") or 0),
                "open_rate": float(row.get("open_rate") or 0), "click_rate": float(row.get("click_rate") or 0),
                "unsubscribed": int(row.get("unsubscribed") or 0),
            })
        category_rows = list(db.people.aggregate([
            {"$match": {"marketing.ai_primary_category": {"$exists": True, "$ne": ""}}},
            {"$group": {"_id": "$marketing.ai_primary_category", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}, {"$limit": 20},
        ]))
        high_value = []
        for row in marketing_high_value_people(limit=12, days=45):
            person = row.get("person") or {}
            high_value.append({
                "id": str(person.get("_id") or ""), "name": person.get("name") or "",
                "organization": person.get("organization") or "", "role": person.get("role") or "",
                "relationship_strength": person.get("relationship_strength") or 0,
                "engagements": row.get("engagements") or 0, "clicks": row.get("clicks") or 0,
                "score": row.get("score") or 0, "last_engagement": row.get("last_engagement"),
                "primary_category": ((person.get("marketing") or {}).get("ai_primary_category") or ""),
            })
        return _njs_bridge_ok({
            "organization": {"id": str(org["_id"]), "name": org.get("name") or org.get("slug") or "BlackBook"},
            "mailchimp": {"configured": mailchimp_ready, "audience_id": audience_id},
            "counts": {
                "people": db.people.count_documents({}),
                "eligible": db.people.count_documents({"marketing.email_eligibility": "eligible", "do_not_contact": {"$ne": True}}),
                "subscribed": db.people.count_documents({"marketing.mailchimp.status": "subscribed"}),
                "blocked": db.people.count_documents({"$or": [{"marketing.email_eligibility": "prohibited"}, {"do_not_contact": True}]}),
                "analyzed": db.people.count_documents({"marketing.ai_analysis_status": "analyzed"}),
                "segments": len(segment_rows), "campaigns": db.mailchimp_campaigns.count_documents({}),
            },
            "segments": segment_rows, "recent_campaigns": recent_campaigns,
            "categories": [{"name": x.get("_id") or "", "count": x.get("count") or 0} for x in category_rows],
            "high_value_people": high_value,
        })


@app.get("/api/v1/marketing/people")
def njs_marketing_people_api():
    org, key_doc = _prospect_api_auth()
    with organization_scope(org["_id"]):
        q = str(request.args.get("q") or "").strip()[:160]
        limit = _safe_int(request.args.get("limit"), 24, 1, 100)
        query = {}
        if request.args.get("eligible") in {"1", "true", "yes"}:
            query.update({"marketing.email_eligibility": "eligible", "do_not_contact": {"$ne": True}})
        if q:
            rx = {"$regex": re.escape(q), "$options": "i"}
            search = {"$or": [{"name": rx}, {"organization": rx}, {"role": rx}, {"email": rx}, {"tags": rx}, {"marketing.ai_primary_category": rx}]}
            query = {"$and": [query, search]} if query else search
        rows = []
        for person in db.people.find(query).sort([("relationship_strength", DESCENDING), ("updated_at", DESCENDING)]).limit(limit):
            marketing = person.get("marketing") or {}
            profile = marketing.get("ai_interest_profile") or {}
            rows.append({
                "id": str(person["_id"]), "name": person.get("name") or "", "email": person.get("email") or "",
                "organization": person.get("organization") or "", "role": person.get("role") or "",
                "stage": person.get("stage") or "", "relationship_strength": person.get("relationship_strength") or 0,
                "tags": (person.get("tags") or [])[:20], "website": person.get("website") or "",
                "linkedin_url": person.get("linkedin_url") or "", "source": person.get("source") or "",
                "eligibility": marketing.get("email_eligibility") or "not_set",
                "mailchimp_status": (marketing.get("mailchimp") or {}).get("status") or "",
                "primary_category": marketing.get("ai_primary_category") or profile.get("primary_category") or "",
                "audience_categories": (profile.get("audience_categories") or [])[:8],
                "engagement_level": profile.get("engagement_level") or "",
                "confidence": profile.get("confidence"),
                "inferred_interests": (profile.get("inferred_interests") or [])[:8],
                "updated_at": person.get("updated_at"),
            })
        return _njs_bridge_ok({"people": rows, "count": len(rows)})


@app.get("/api/v1/marketing/segments")
def njs_marketing_segments_api():
    org, key_doc = _prospect_api_auth()
    with organization_scope(org["_id"]):
        rows = []
        for segment in db.marketing_segments.find({}).sort("updated_at", DESCENDING).limit(200):
            rows.append({
                "id": str(segment["_id"]), "name": segment.get("name") or "Segment", "description": segment.get("description") or "",
                "count": db.people.count_documents(marketing_segment_query(segment)), "stage": segment.get("stage") or "",
                "tag": segment.get("tag") or "", "mailchimp_tag": segment.get("mailchimp_tag") or "",
                "min_relationship": segment.get("min_relationship") or 0, "min_interest_relevance": segment.get("min_interest_relevance") or 0,
                "last_sync_at": segment.get("last_sync_at"), "last_sync_result": segment.get("last_sync_result") or {},
            })
        return _njs_bridge_ok({"segments": rows})


@app.get("/api/v1/marketing/campaigns")
def njs_marketing_campaigns_api():
    org, key_doc = _prospect_api_auth()
    with organization_scope(org["_id"]):
        limit = _safe_int(request.args.get("limit"), 30, 1, 100)
        rows = []
        for row in db.mailchimp_campaigns.find({}).sort([("send_time", DESCENDING), ("updated_at", DESCENDING)]).limit(limit):
            rows.append({
                "id": row.get("mailchimp_id") or str(row.get("_id")), "subject": row.get("subject_line") or "",
                "title": row.get("campaign_title") or row.get("title") or row.get("subject_line") or "Mailchimp campaign",
                "status": row.get("status") or "", "send_time": row.get("send_time"), "emails_sent": int(row.get("emails_sent") or 0),
                "unique_opens": int(row.get("unique_opens") or 0), "unique_clicks": int(row.get("unique_clicks") or 0),
                "open_rate": float(row.get("open_rate") or 0), "click_rate": float(row.get("click_rate") or 0),
                "unsubscribed": int(row.get("unsubscribed") or 0), "hard_bounces": int(row.get("hard_bounces") or 0),
                "soft_bounces": int(row.get("soft_bounces") or 0),
            })
        return _njs_bridge_ok({"campaigns": rows})


@app.post("/api/v1/marketing/audience/preview")
def njs_marketing_audience_preview_api():
    org, key_doc = _prospect_api_auth()
    payload = request.get_json(silent=True) or {}
    with organization_scope(org["_id"]):
        people, counts = _njs_bridge_people_from_payload(payload)
        categories = {}
        total_strength = 0.0
        subscribed = 0
        analyzed = 0
        sample = []
        for person in people:
            marketing = person.get("marketing") or {}
            profile = marketing.get("ai_interest_profile") or {}
            category = marketing.get("ai_primary_category") or profile.get("primary_category") or "Unclassified"
            categories[category] = categories.get(category, 0) + 1
            total_strength += float(person.get("relationship_strength") or 0)
            subscribed += int((marketing.get("mailchimp") or {}).get("status") == "subscribed")
            analyzed += int(bool(marketing.get("ai_analysis_status") == "analyzed" or profile))
            if len(sample) < 12:
                sample.append({"id": str(person["_id"]), "name": person.get("name") or "", "organization": person.get("organization") or "", "role": person.get("role") or "", "relationship_strength": person.get("relationship_strength") or 0, "primary_category": category})
        counts.update({"subscribed": subscribed, "analyzed": analyzed})
        return _njs_bridge_ok({
            "counts": counts,
            "average_relationship_strength": round(total_strength / max(1, len(people)), 1),
            "categories": [{"name": k, "count": v} for k, v in sorted(categories.items(), key=lambda x: x[1], reverse=True)[:15]],
            "sample_people": sample,
        })


@app.post("/api/v1/marketing/newsletters/distribute")
def njs_marketing_newsletter_distribute_api():
    org, key_doc = _prospect_api_auth()
    payload = request.get_json(silent=True) or {}
    action = "send" if str(payload.get("action") or "draft").lower() == "send" else "draft"
    subject = str(payload.get("subject") or "").strip()[:150]
    sender_name = str(payload.get("sender_name") or "").strip()[:120]
    reply_to = normalize_prospect_email(payload.get("reply_to"))
    html_body = str(payload.get("html") or "")
    plain_text = str(payload.get("plain_text") or "")
    preheader = str(payload.get("preheader") or "").strip()[:255]
    if not subject or not sender_name or not reply_to or not html_body:
        return _njs_bridge_error("subject, sender_name, reply_to and html are required")
    with organization_scope(org["_id"]):
        if not mailchimp_configured():
            return _njs_bridge_error("Mailchimp is not configured for this BlackBook organization", 409)
        people, counts = _njs_bridge_people_from_payload(payload)
        if not people:
            return _njs_bridge_error("No eligible BlackBook recipients matched this audience", 409)
        if len(people) > 10000:
            return _njs_bridge_error("Audience exceeds the 10,000-recipient newsletter bridge limit", 413)
        emails = []
        sync_counts = {"already_subscribed": 0, "synced": 0, "blocked": 0, "skipped": 0, "errors": 0}
        unsynced = []
        for person in people:
            marketing = person.get("marketing") or {}
            email = normalize_prospect_email(person.get("email") or ((person.get("emails") or [""])[0] if person.get("emails") else ""))
            if email and (marketing.get("mailchimp") or {}).get("status") == "subscribed":
                emails.append(email)
                sync_counts["already_subscribed"] += 1
            else:
                unsynced.append(person)
        # Keep the HTTP handoff bounded. Organizations with a large unsynced audience
        # should run BlackBook's normal audience sync first; already-subscribed contacts
        # do not require one API call per person here.
        if len(unsynced) > 500:
            return _njs_bridge_error("More than 500 selected recipients still need Mailchimp synchronization. Run BlackBook audience sync first, then retry.", 409)
        for person in unsynced:
            try:
                result = mailchimp_sync_person(person, extra_tags=["BB:NJS:Newsletter"])
                status = result.get("status")
                if status == "synced":
                    email = normalize_prospect_email(result.get("email") or person.get("email"))
                    if email:
                        emails.append(email)
                    sync_counts["synced"] += 1
                elif status == "blocked": sync_counts["blocked"] += 1
                else: sync_counts["skipped"] += 1
            except Exception:
                sync_counts["errors"] += 1
        emails = sorted(set(emails))
        if not emails:
            return _njs_bridge_error("BlackBook could not produce any subscribed Mailchimp recipients", 409)
        list_id = mailchimp_audience_id()
        edition_id = str(payload.get("edition_id") or "")[:100]
        segment_name = f"NJS · {subject[:70]} · {edition_id[-8:] or int(time.time())}"
        first_chunk = emails[:500]
        segment = mailchimp_api_request("POST", f"/lists/{quote(list_id)}/segments", payload={"name": segment_name, "static_segment": first_chunk})
        segment_id = segment.get("id")
        if not segment_id:
            return _njs_bridge_error("Mailchimp did not return a segment id", 502)
        for start in range(500, len(emails), 500):
            mailchimp_api_request("POST", f"/lists/{quote(list_id)}/segments/{quote(str(segment_id))}", payload={"members_to_add": emails[start:start + 500], "members_to_remove": []})
        campaign_payload = {
            "type": "regular",
            "recipients": {"list_id": list_id, "segment_opts": {"saved_segment_id": int(segment_id) if str(segment_id).isdigit() else segment_id}},
            "settings": {
                "subject_line": subject, "preview_text": preheader,
                "title": f"NJS · {subject}"[:150], "from_name": sender_name, "reply_to": reply_to,
            },
        }
        campaign = mailchimp_api_request("POST", "/campaigns", payload=campaign_payload)
        campaign_id = str(campaign.get("id") or "")
        if not campaign_id:
            return _njs_bridge_error("Mailchimp did not return a campaign id", 502)
        content_payload = {"html": html_body}
        if plain_text:
            content_payload["plain_text"] = plain_text
        mailchimp_api_request("PUT", f"/campaigns/{quote(campaign_id)}/content", payload=content_payload)
        status = "draft"
        if action == "send":
            mailchimp_api_request("POST", f"/campaigns/{quote(campaign_id)}/actions/send", payload={})
            status = "sent"
        audit_now = utcnow()
        raw_db.audit_log.insert_one({
            "organization_id": org["_id"], "user_id": None,
            "user_email": f"njs-bridge:{(key_doc or {}).get('name') or 'key'}",
            "action": "njs.newsletter.sent" if action == "send" else "njs.newsletter.draft_created",
            "detail": subject, "target_type": "mailchimp_campaign", "target_id": campaign_id,
            "metadata": {"edition_id": edition_id, "recipient_count": len(emails), "segment_id": segment_id, "sync": sync_counts},
            "created_at": audit_now,
        })
        return _njs_bridge_ok({
            "campaign_id": campaign_id, "mailchimp_segment_id": segment_id, "status": status,
            "recipient_count": len(emails), "audience": counts, "sync": sync_counts,
            "mailchimp_url": "", "created_at": audit_now,
        }, 201)
