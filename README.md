# NJS — Newsjacking for Syntal

**NJS** is Syntal's AI-assisted newsjacking, publishing, audience-intelligence, newsletter, and content-distribution platform.

It turns external news, internal knowledge, audience data, and operator instructions into structured publishing workflows: discover a story, understand why it matters, generate or edit content, create supporting media, organize it into campaigns or collections, select the right audience, and publish or distribute the result.

NJS is designed to be used both directly by people through its web interface and programmatically by **Syntal AI** through a controlled application tool contract.

**Current release:** `v3.9.5.3`

**Production:** `https://njs.syntal.pro`

---

## What is NJS?

NJS is a content intelligence and publishing system built around the idea that finding a relevant story is only the beginning of the workflow.

A traditional newsjacking tool may help identify trending stories.

NJS goes further:

```text
Discover
   ↓
Understand
   ↓
Research
   ↓
Generate
   ↓
Design
   ↓
Review
   ↓
Target
   ↓
Publish
   ↓
Distribute
   ↓
Measure
   ↓
Learn
```

The platform combines several related workflows inside one application:

- news and story discovery;
- AI-assisted article generation;
- article editing and publishing;
- image and media generation;
- Collections;
- campaigns;
- products;
- landing pages;
- newsletter creation;
- prompt-driven newsletter design;
- audience selection;
- Mailchimp integration;
- engagement intelligence;
- interest discovery;
- BlackBook integration;
- Syntal SSO authentication and authorization;
- Syntal AI tool access.

The goal is not simply to generate content.

The goal is to provide the infrastructure required to move from **an event in the outside world to a targeted, publishable communication**.

---

# Why does NJS exist?

Organizations usually handle newsjacking across many disconnected systems.

A typical workflow might involve:

```text
News website
    ↓
Research tool
    ↓
LLM
    ↓
Image generator
    ↓
CMS
    ↓
Mailchimp
    ↓
CRM
    ↓
Analytics
```

Context is lost every time information moves between those systems.

Audience intelligence lives separately from editorial work. Campaign information lives separately from articles. Newsletter design is separated from newsletter content. Engagement data is rarely connected back to future targeting decisions.

NJS brings those workflows together.

The platform is designed around five principles.

### 1. Context should survive the entire workflow

A story discovered during research should remain connected to the resulting article, campaign, media, newsletter, and audience.

### 2. AI should operate through explicit capabilities

Syntal AI does not need unrestricted application access.

NJS exposes defined tools and contracts that allow the AI layer to perform specific operations while the application remains responsible for authorization and data integrity.

### 3. Human operators remain in control

AI can accelerate research, generation, classification, and design, while operators retain control over final content, targeting, and publication.

### 4. Audience intelligence should influence publishing

NJS connects editorial workflows with BlackBook and Mailchimp data so content can be targeted using interests and engagement instead of treating every contact identically.

### 5. Content should be reusable

Articles, media, collections, campaigns, audiences, newsletter designs, and other assets are treated as reusable resources rather than isolated one-time generations.

---

# How NJS works

At a high level, NJS consists of several cooperating layers.

```text
                         ┌─────────────────────┐
                         │     Syntal SSO      │
                         │ Identity / Org / ACL│
                         └──────────┬──────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────┐
│                        NJS                              │
│                                                         │
│  Discover → Create → Organize → Design → Publish       │
│                                                         │
│  Articles       Collections       Campaigns             │
│  Media          Products          Landing Pages         │
│  Newsletters    Audiences         Engagement            │
└───────────┬──────────────────┬──────────────────┬───────┘
            │                  │                  │
            ▼                  ▼                  ▼
      ┌───────────┐      ┌───────────┐      ┌───────────┐
      │ BlackBook │      │ Mailchimp │      │ OpenAI /  │
      │ CRM       │      │           │      │ AI Media  │
      └───────────┘      └───────────┘      └───────────┘
            │
            ▼
      Audience intelligence
      Interests
      Engagement
      Segmentation

                         ┌─────────────────────┐
                         │     Syntal AI       │
                         │   NJS Tool Layer    │
                         └─────────────────────┘
```

---

# Core workflows

## News discovery

NJS can be used to identify stories that may be relevant to an organization, audience, product, or campaign.

The important distinction is that discovery is not treated as the final output.

A discovered story can become input for:

- research;
- an article;
- a campaign;
- a newsletter;
- a landing page;
- audience-specific messaging;
- future content planning.

---

## Article generation

Articles can be generated from operator instructions, source material, campaign context, collections, and AI-assisted research.

The article remains editable before publication.

Generated articles can also contain generated media.

As of `v3.9.5.3`, the canonical article media contract supports both the newer media pipeline and older monolith consumers.

```text
metadata.image_info.url
metadata.hero_image
article.content
```

A generated cover image is persisted under:

```text
metadata.image_info.url
```

and mirrored to:

```text
metadata.hero_image
```

Verified image URLs are also inserted directly into:

```text
article.content
```

This allows older article renderers and newer Collection/OpenAI media workflows to operate against the same generated article.

---

# Media generation

NJS supports AI-assisted media generation for publishing workflows.

Media is treated as part of the content model rather than an unrelated image-generation step.

A generated image can therefore be associated with:

- an article;
- a Collection;
- a campaign;
- a newsletter;
- other publishing assets.

The newer Collection/OpenAI media pipeline remains available alongside the compatibility behavior introduced in `v3.9.5.3`.

---

# Collections

Collections provide a way to group related content and media.

They can represent:

- a subject;
- a campaign;
- an editorial theme;
- a product;
- an event;
- a group of related stories;
- reusable media associated with a publishing workflow.

Collections allow NJS to preserve context across multiple generated assets instead of treating every article independently.

---

# Campaigns

Campaigns organize publishing activity around a common objective.

A campaign may connect:

```text
Campaign
├── Stories
├── Articles
├── Collections
├── Media
├── Products
├── Landing pages
├── Newsletter editions
└── Audience segments
```

This provides a common context for both human operators and AI-assisted workflows.

---

# Products

Products can be represented inside NJS so editorial and campaign content can be associated with what an organization actually offers.

This is particularly useful when a news event creates an opportunity to connect a relevant product with a current conversation.

---

# Landing pages

NJS supports landing-page workflows alongside articles and newsletters.

Landing pages can be used when a campaign needs a dedicated destination rather than directing readers to a normal editorial article.

They can share the same campaign, product, media, and AI context used elsewhere in NJS.

---

# Newsletter Studio

NJS includes a newsletter workflow for turning stories and existing content into complete email editions.

A newsletter can combine:

- selected stories;
- generated content;
- existing NJS content;
- campaign context;
- audience targeting;
- visual design;
- Mailchimp distribution.

---

## Prompt-driven newsletter design

The Newsletter Design Studio allows operators to modify an email visually using natural-language instructions.

For example:

```text
Make this edition look more editorial and reduce the amount
of visual chrome around each story.
```

or:

```text
Use a cleaner technology publication style with stronger
headline hierarchy and more spacing between stories.
```

The design workflow supports:

- desktop preview;
- mobile preview;
- non-destructive revisions;
- restoration;
- advanced HTML source editing;
- reusable visual direction.

Content and design are intentionally treated as separate concerns so the appearance of a newsletter can change without unnecessarily rewriting its factual content.

---

# Audience Intelligence

NJS integrates with BlackBook to provide audience selection based on known interests and engagement.

Instead of sending every newsletter to the complete contact database, operators can build more focused audiences.

Audience selection can use:

```text
Interests
+
Engagement
+
Explicit selection
+
Campaign context
```

---

# Engagement Intelligence

Engagement Intelligence processes contact activity and places successfully analyzed contacts into one of four engagement groups.

```text
inactive
low
medium
high
```

There are also operational states:

```text
queue
failed
```

The complete state model is therefore:

| State | Meaning |
|---|---|
| `queue` | Waiting for analysis |
| `failed` | Most recent analysis attempt failed |
| `inactive` | Processed with no meaningful engagement |
| `low` | Low engagement |
| `medium` | Medium engagement |
| `high` | High engagement |

`failed` is an operational failure state, not an audience-engagement classification.

Smart Audience targeting therefore continues to use:

```text
Inactive
Low
Medium
High
```

---

## Failed engagement queue

Introduced in `v3.9.4`, failed contacts are separated from contacts that are still waiting to be processed.

Previously, an API or Mailchimp failure could leave a contact appearing to remain in Queue.

Now:

```text
analysis starts
      │
      ├── success ──→ inactive / low / medium / high
      │
      └── exception ──→ failed
```

Failure information can include:

- failure message;
- processing stage;
- timestamp;
- batch ID;
- failure count;
- normalized failure reason.

Mailchimp authorization failures such as HTTP `403`, `Forbidden`, and `Access Denied` are normalized as:

```text
mailchimp_access_denied
```

Failed contacts can later be selected and deliberately retried.

---

# Bulk audience processing

Engagement Intelligence supports bulk analysis of up to:

```text
2,500 contacts
```

per batch.

The corresponding BlackBook configuration is:

```env
NJS_ENGAGEMENT_MAX_SELECT=2500
```

A failure affecting one contact does not terminate the complete batch.

For example:

```text
2,500 selected

2,461 successful
39 failed

Batch progress: 100%
```

The failed contacts remain available separately for investigation or retry.

---

# BlackBook integration

NJS and BlackBook are intentionally separate systems.

BlackBook acts as the people and audience intelligence layer.

NJS acts as the publishing and campaign layer.

```text
                 BLACKBOOK
                     │
            People / Interests
              Engagement
                     │
                     ▼
                    NJS
                     │
        Content / Campaign / Audience
                     │
                     ▼
              Distribution
```

This allows NJS to use audience information without turning the publishing application itself into the system of record for every contact.

---

# Mailchimp integration

Mailchimp provides the email delivery and engagement-data side of the newsletter workflow.

Depending on the operation, NJS and BlackBook can use Mailchimp data for:

- subscriber information;
- newsletter distribution;
- campaign activity;
- engagement analysis;
- contact behavior.

Mailchimp is therefore one component of the NJS ecosystem rather than the location where editorial intelligence is stored.

---

# Interest discovery

Audience interests can be inferred and organized so future campaigns can select contacts based on subject relevance.

Conceptually:

```text
Person
├── Interest A
├── Interest B
├── Interest C
└── Engagement state
```

A newsletter about a particular subject can then target contacts with corresponding interests instead of relying only on static mailing lists.

---

# Smart Audiences

Smart Audiences combine audience intelligence with editorial context.

An audience can be narrowed using combinations such as:

```text
Interest: Artificial Intelligence
Engagement: High + Medium
```

or:

```text
Interest: Healthcare
Engagement: High
```

or:

```text
Interest: Cybersecurity
Engagement: Low + Medium + High
```

Operators remain responsible for choosing the final audience.

---

# Syntal AI integration

NJS exposes application capabilities to Syntal AI through a structured tool interface.

The `v3.9.4` contract contains **127 NJS tools**.

These tools allow Syntal AI to perform authorized operations against NJS rather than attempting to operate the application as an unrestricted agent.

Examples include:

```text
njs.engagement.people.list
njs.engagement.bulk_analyze
```

The engagement list API can request:

```text
bucket=failed
```

and receive associated failure metadata.

Bulk engagement analysis accepts up to:

```text
2,500 person IDs
```

The broader tool set covers NJS workflows including content, campaigns, collections, newsletters, audiences, and related application operations.

---

# Authentication and organizations

NJS uses **Syntal SSO** for authentication, organization context, application access, and authorization.

NJS should not maintain an independent identity system for normal application users.

Conceptually:

```text
User
  │
  ▼
Syntal SSO
  │
  ├── Identity
  ├── Organization
  ├── Role
  ├── Application entitlement
  └── Permission
        │
        ▼
       NJS
```

This allows NJS to participate in the wider Syntal application ecosystem.

---

# Where does NJS run?

The production application is deployed at:

```text
https://njs.syntal.pro
```

Within the Syntal infrastructure, NJS runs as its own application and communicates with other services through defined HTTP/API contracts.

A typical deployment topology is:

```text
Internet
   │
   ▼
Nginx / TLS
   │
   ▼
njs.syntal.pro
   │
   ▼
NJS application
   │
   ├── Syntal SSO
   ├── Syntal AI
   ├── BlackBook
   ├── Mailchimp
   └── AI / media services
```

The application should remain independently deployable even though it participates in the larger Syntal ecosystem.

---

# When should NJS be used?

NJS is appropriate when an organization needs to move from an external event or idea to publishable, targeted communication.

Typical examples include:

### A relevant news story appears

```text
Story discovered
→ research
→ article
→ generated media
→ review
→ publication
```

### A news event relates to a product

```text
News
→ relevance analysis
→ product
→ campaign
→ article
→ landing page
→ audience
```

### A newsletter needs to be created

```text
Stories
→ newsletter edition
→ visual design
→ audience
→ Mailchimp
```

### A campaign needs a targeted audience

```text
Campaign
→ topic
→ interests
→ engagement
→ Smart Audience
```

### Audience engagement needs refreshing

```text
Select contacts
→ bulk analysis
→ inactive / low / medium / high
                     │
                     └── failures → failed
```

### Syntal AI needs to operate NJS

```text
User instruction
→ Syntal AI
→ authorized NJS tool
→ NJS
→ result
```

---

# Who is NJS for?

NJS is intended for teams working with:

- publishing;
- communications;
- marketing;
- public relations;
- newsletters;
- audience development;
- research;
- campaign management;
- content operations.

It is also intended to serve as the publishing and news-intelligence application inside the wider Syntal platform.

---

# Repository structure

The exact layout may evolve, but the repository is conceptually divided into:

```text
NJS/
├── application
├── templates
├── static
├── services
├── integrations
├── config
├── deploy
├── tests
└── documentation
```

Important integration and release contracts are maintained alongside application code so deployments can be validated rather than relying only on manual testing.

---

# Production deployment

NJS is intended to run as a production service behind a reverse proxy.

Do not expose the development server directly to the public internet.

The expected production path is broadly:

```text
Client
  ↓ HTTPS
Nginx
  ↓
NJS application
  ↓
Application services / database / integrations
```

Persistent Docker volumes must not be deleted during a routine upgrade unless a specific release explicitly requires a destructive migration.

---

# Updating NJS

A normal upgrade should preserve:

- database data;
- application state;
- persistent Docker volumes;
- organization configuration;
- audience information;
- generated content;
- campaigns;
- Collections;
- newsletter data.

Before deployment:

```bash
docker compose ps
docker compose config
```

After installation, rebuild and recreate only the services required by the release.

Typical pattern:

```bash
docker compose build
docker compose up -d
```

Then validate:

```bash
docker compose ps
docker compose logs --tail=100
```

Release-specific validation scripts should be used whenever they are provided.

---

# Current release

## v3.9.5.3 — Monolith Media Contract

`v3.9.5.3` restores the legacy monolith article image contract while retaining the newer Collection/OpenAI media pipeline.

Generated articles persist a real cover image under:

```text
metadata.image_info.url
```

The image is mirrored to:

```text
metadata.hero_image
```

and verified image URLs are inserted directly into:

```text
article.content
```

This provides compatibility between older article consumers and the newer NJS media architecture.

---

# Recent release history

## v3.9.4 — Failed Engagement Queue

Introduced:

- dedicated Failed engagement state;
- failed tab and metric;
- failure persistence;
- Mailchimp error normalization;
- deliberate retries;
- batch continuation after individual failures;
- 2,500-contact bulk analysis.

Associated contracts:

```text
config/njs-v3.9.4-contract.json
NJS-AI-MANIFEST-v3.9.4.json
```

Associated documentation:

```text
RELEASE-NOTES-v3.9.4.md
ENGAGEMENT-INTELLIGENCE-v3.9.4.md
UPGRADE-v3.9.4.md
```

---

## v3.9.3 — Prompt Newsletter Design Studio

Introduced prompt-driven visual newsletter design while preserving newsletter stories, links, and factual content.

Capabilities include:

- natural-language visual redesign;
- desktop preview;
- mobile preview;
- design revisions;
- restoration;
- advanced HTML editing;
- reusable visual direction.

Documentation:

```text
NEWSLETTER-DESIGN-v3.9.3.md
```

---

# Release lineage

```text
NJS
│
├── News discovery
├── Article generation
├── Campaigns
├── Products
├── Collections
├── Landing pages
├── Media
├── Newsletters
├── BlackBook integration
├── Mailchimp integration
├── Audience intelligence
├── Syntal SSO
└── Syntal AI
        │
        ▼
v3.9.3
Prompt Newsletter Design Studio
        │
        ▼
v3.9.4
Failed Engagement Queue
2,500-contact bulk processing
        │
        ▼
v3.9.5.x
Article and media pipeline evolution
        │
        ▼
v3.9.5.3
Monolith Media Contract
```

---

# Design philosophy

NJS is built around a simple distinction:

> AI generation is a capability. Publishing is a system.

A production publishing platform needs more than a prompt and a model.

It needs:

```text
Identity
Permissions
Sources
Context
Content
Media
Organization
Audiences
Review
Distribution
Analytics
Failure handling
Auditability
```

NJS exists to connect those pieces into one operational workflow.

The result is a platform where an external event can become researched content, structured media, a campaign, a newsletter, and a targeted communication without losing the context that connected those steps in the first place.

---

# NJS in the Syntal ecosystem

```text
                         SYNTAL

                    ┌───────────┐
                    │    SSO    │
                    └─────┬─────┘
                          │
                          ▼
                    ┌───────────┐
                    │    NJS    │
                    └─────┬─────┘
                          │
          ┌───────────────┼───────────────┐
          │               │               │
          ▼               ▼               ▼
    ┌───────────┐   ┌───────────┐   ┌───────────┐
    │ BlackBook │   │ Syntal AI │   │ Mailchimp │
    └───────────┘   └───────────┘   └───────────┘
          │               │
          │               │
          ▼               ▼
     Audience data    AI operations
```

NJS is the Syntal platform's bridge between:

**what is happening in the world**

and

**what an organization should communicate about it.**

---

## Version

```text
Newsjacking Core
Version 3.9.5.3
```

## Production

```text
https://njs.syntal.pro
```
