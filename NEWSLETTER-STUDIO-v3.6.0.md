# NJS v3.6.0 — Newsletter Studio

Newsletter Studio changes Newsletters from a set of CRUD pages into an operating workflow:

**Overview → Plan → Audience → Build → Review → Distribute → Learn**

## Workflow

- **Overview** surfaces the next operational action and the state of BlackBook/Mailchimp.
- **Plan** defines newsletter identity, editorial voice, source campaigns, cadence, story window and CTA rules.
- **Audience** reads BlackBook organization intelligence: directory counts, eligibility, reusable segments, selected people, Mailchimp membership, behavioral-analysis coverage, relationship strength and recent campaign signals.
- **Build** generates an edition from current NJS articles plus refreshed aggregate BlackBook audience intelligence.
- **Review** separates human editorial approval from generation and distribution.
- **Distribute** can create a Mailchimp draft or explicitly send through BlackBook. NJS never receives the Mailchimp credential.
- **Learn** shows imported Mailchimp campaign history, BlackBook behavioral categories and recent high-signal contacts.

## BlackBook Marketing Bridge

The release includes `deploy/install-blackbook-newsletter-bridge.py`. The installer adds organization-scoped endpoints to an existing BlackBook v13.x `app.py`:

- `GET /api/v1/marketing/context`
- `GET /api/v1/marketing/people`
- `GET /api/v1/marketing/segments`
- `GET /api/v1/marketing/campaigns`
- `POST /api/v1/marketing/audience/preview`
- `POST /api/v1/marketing/newsletters/distribute`

The bridge reuses BlackBook's existing organization-scoped integration credential and organization header. BlackBook remains authoritative for marketing eligibility, do-not-contact state, enrichment, behavioral analysis, segments, Mailchimp audience membership and Mailchimp credentials.

## Distribution behavior

For a BlackBook/Mailchimp handoff, BlackBook:

1. Resolves the selected BlackBook segments and explicit people.
2. Re-applies eligibility and do-not-contact rules.
3. Synchronizes eligible recipients with the organization's Mailchimp audience.
4. Builds a static Mailchimp segment for this exact NJS edition.
5. Creates a regular Mailchimp campaign and uploads NJS HTML/plain-text content.
6. Leaves it as a draft by default, or calls Mailchimp's send action only when the user explicitly selects **Send now via BlackBook**.
7. Writes a BlackBook audit event and returns the Mailchimp campaign reference to NJS.

## Generation intelligence

Before generation, NJS refreshes an aggregate audience snapshot through BlackBook. The generation prompt can use:

- audience size and eligibility counts;
- analyzed-audience coverage;
- audience category mix;
- average relationship strength;
- recent campaign sends/opens/clicks and rates.

Individual names, email addresses and complete dossiers are not inserted into the newsletter-generation prompt.

## v3.5 newsletter generation fix included

v3.6.0 permanently includes the schedule preservation fix for the prior `KeyError: '_id'`. `select_articles()` now applies normalized defaults without discarding Mongo/workspace fields such as `_id`, `organization_id`, `user_id` and `campaign_ids`.

### Delivery scaling note

The synchronous bridge can target up to 10,000 eligible people when they are already synchronized with Mailchimp. If more than 500 selected contacts still need first-time Mailchimp synchronization, the bridge asks the operator to run BlackBook's normal audience sync first; this keeps the NJS web request bounded and avoids a long-running delivery request.
