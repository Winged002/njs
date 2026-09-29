from pymongo import MongoClient, ASCENDING, DESCENDING
from config import Config

client = MongoClient(
    Config.MONGO_URI,
    maxPoolSize=20,
    minPoolSize=1,
    serverSelectionTimeoutMS=8000,
)
db = client.get_database()

users = db.users
campaigns = db.campaigns
products = db.products
newsjacking_workers = db.newsjacking_workers
rss_feeds = db.rss_feeds
rss_feed_items = db.rss_feed_items
hooks = db.hooks
articles = db.articles
article_versions = db.article_versions
article_change_jobs = db.article_change_jobs
article_generation_queue = db.article_generation_queue
newsjacking_runs = db.newsjacking_runs
landing_pages = db.landing_pages
landing_page_versions = db.landing_page_versions
website_sites = db.website_sites
transactions = db.transactions
domain_mappings = db.domain_mappings
domain_routes = db.domain_routes
social_media_posts = db.social_media_posts
social_generation_jobs = db.social_generation_jobs
newsletter_schedules = db.newsletter_schedules
newsletter_editions = db.newsletter_editions
newsletter_edition_versions = db.newsletter_edition_versions
newsletter_design_jobs = db.newsletter_design_jobs
landing_page_change_jobs = db.landing_page_change_jobs
collections = db.collections
collection_sources = db.collection_sources
collection_items = db.collection_items
analytics_events = db.analytics_events
analytics_settings = db.analytics_settings
experiments = db.experiments
sso_organization_links = db.sso_organization_links
article_submissions = db.article_submissions

def ensure_indexes():
    users.create_index([("syntal_user_id", ASCENDING)], unique=True, sparse=True)
    users.create_index([("syntal_org_id", ASCENDING), ("email", ASCENDING)])
    sso_organization_links.create_index([("syntal_org_id", ASCENDING)], unique=True)
    sso_organization_links.create_index([("local_organization_id", ASCENDING)], sparse=True)
    article_submissions.create_index([("submission_id", ASCENDING)], unique=True)
    article_submissions.create_index([("article_id", ASCENDING), ("created_at", DESCENDING)])
    article_submissions.create_index([("organization_id", ASCENDING), ("created_at", DESCENDING)])
    article_submissions.create_index([("blackbook.status", ASCENDING), ("created_at", ASCENDING)])
    article_submissions.create_index([("email_normalized", ASCENDING), ("article_id", ASCENDING), ("created_at", DESCENDING)])
    article_submissions.create_index([("experiment_id", ASCENDING), ("variant_id", ASCENDING), ("created_at", DESCENDING)], sparse=True)
    analytics_events.create_index([("user_id", ASCENDING), ("occurred_at", DESCENDING)])
    analytics_events.create_index([("organization_id", ASCENDING), ("occurred_at", DESCENDING)])
    analytics_events.create_index([("content_type", ASCENDING), ("content_id", ASCENDING), ("occurred_at", DESCENDING)])
    analytics_events.create_index([("campaign_id", ASCENDING), ("occurred_at", DESCENDING)])
    analytics_events.create_index([("campaign_ids", ASCENDING), ("occurred_at", DESCENDING)])
    analytics_events.create_index([("domain", ASCENDING), ("occurred_at", DESCENDING)])
    analytics_events.create_index([("visitor_hash", ASCENDING), ("occurred_at", DESCENDING)])
    analytics_events.create_index([("created_at", ASCENDING)], expireAfterSeconds=Config.ANALYTICS_RETENTION_DAYS * 86400, name="analytics_retention_ttl")
    analytics_events.create_index([("event_type", ASCENDING), ("organization_id", ASCENDING), ("occurred_at", DESCENDING)])
    analytics_events.create_index([("product_id", ASCENDING), ("occurred_at", DESCENDING)], sparse=True)
    analytics_events.create_index([("product_ids", ASCENDING), ("occurred_at", DESCENDING)], sparse=True)
    analytics_events.create_index([("newsjacking_worker_id", ASCENDING), ("occurred_at", DESCENDING)], sparse=True)
    analytics_events.create_index([("parent_view_id", ASCENDING), ("occurred_at", DESCENDING)], sparse=True)
    analytics_events.create_index([("experiment_id", ASCENDING), ("variant_id", ASCENDING), ("occurred_at", DESCENDING)], sparse=True)
    analytics_settings.create_index([("organization_id", ASCENDING)], unique=True, sparse=True)
    analytics_settings.create_index([("user_id", ASCENDING)], sparse=True)
    experiments.create_index([("organization_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)])
    experiments.create_index([("user_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)])
    experiments.create_index([("target_type", ASCENDING), ("target_id", ASCENDING), ("status", ASCENDING)])
    experiments.create_index([("organization_id", ASCENDING), ("target_type", ASCENDING), ("target_id", ASCENDING), ("status", ASCENDING)])
    collections.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
    collections.create_index([("organization_id", ASCENDING), ("updated_at", DESCENDING)])
    collection_sources.create_index([("collection_id", ASCENDING), ("created_at", DESCENDING)])
    collection_sources.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    collection_items.create_index([("collection_id", ASCENDING), ("created_at", DESCENDING)])
    collection_items.create_index([("source_id", ASCENDING), ("item_key", ASCENDING)], unique=True, sparse=True)
    campaigns.create_index([("user_id", ASCENDING), ("setup_status", ASCENDING), ("updated_at", DESCENDING)])
    products.create_index([("organization_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)])
    products.create_index([("user_id", ASCENDING), ("updated_at", DESCENDING)])
    newsjacking_workers.create_index([("organization_id", ASCENDING), ("enabled", ASCENDING), ("updated_at", DESCENDING)])
    newsjacking_workers.create_index([("user_id", ASCENDING), ("enabled", ASCENDING), ("updated_at", DESCENDING)])
    newsjacking_workers.create_index([("organization_id", ASCENDING), ("name", ASCENDING)])
    newsjacking_workers.create_index([("organization_id", ASCENDING), ("learning_mode", ASCENDING), ("updated_at", DESCENDING)])
    rss_feeds.create_index([("user_id", ASCENDING), ("url", ASCENDING)])
    rss_feed_items.create_index([("feed_id", ASCENDING), ("guid", ASCENDING)])
    rss_feed_items.create_index([("feed_id", ASCENDING), ("created_at", DESCENDING)])
    hooks.create_index([("newsjacking_key", ASCENDING)], unique=True, sparse=True)
    hooks.create_index([("campaign_id", ASCENDING), ("created_at", DESCENDING)])
    article_generation_queue.create_index([("newsjacking_key", ASCENDING)], unique=True, sparse=True)
    article_generation_queue.create_index([("user_id", ASCENDING), ("status", ASCENDING), ("created_at", ASCENDING)])
    articles.create_index([("campaign_id", ASCENDING), ("created_at", DESCENDING)])
    articles.create_index([("generation_queue_id", ASCENDING)], unique=True, sparse=True)
    # v2.7 migration: every legacy article enters review rather than remaining implicitly public.
    articles.update_many({"revision": {"$exists": False}}, {"$set": {"revision": 1}})
    articles.update_many({"review_status": {"$exists": False}}, {"$set": {"review_status": "pending_review", "status": "review", "published": False}})
    articles.create_index([("organization_id", ASCENDING), ("review_status", ASCENDING), ("created_at", DESCENDING)])
    articles.create_index([("published", ASCENDING), ("review_status", ASCENDING), ("created_at", DESCENDING)])
    article_versions.create_index([("article_id", ASCENDING), ("revision", DESCENDING)])
    article_versions.create_index([("article_id", ASCENDING), ("revision", ASCENDING)], name="article_revision_lookup")
    article_change_jobs.create_index([("article_id", ASCENDING), ("created_at", DESCENDING)])
    article_change_jobs.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    newsjacking_runs.create_index([("user_id", ASCENDING), ("started_at", DESCENDING)])
    landing_pages.create_index([("public_id", ASCENDING)], unique=True, sparse=True)
    landing_pages.create_index([("site_id", ASCENDING), ("created_at", ASCENDING)])
    website_sites.create_index([("organization_id", ASCENDING), ("site_key", ASCENDING)], sparse=True)
    website_sites.create_index([("user_id", ASCENDING), ("site_key", ASCENDING)], sparse=True)
    website_sites.create_index([("domain_id", ASCENDING)], sparse=True)
    landing_pages.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    landing_page_versions.create_index([("landing_page_id", ASCENDING), ("revision", DESCENDING)])
    landing_page_versions.create_index([("landing_page_id", ASCENDING), ("revision", ASCENDING)], name="landing_page_revision_lookup")
    landing_page_change_jobs.create_index([("landing_page_id", ASCENDING), ("created_at", DESCENDING)])
    landing_page_change_jobs.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    domain_mappings.create_index([("domain", ASCENDING)], unique=True)
    domain_mappings.create_index([("user_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)])
    domain_routes.create_index([("domain_id", ASCENDING), ("path_key", ASCENDING)], unique=True)
    domain_routes.create_index([("page_id", ASCENDING), ("updated_at", DESCENDING)])
    social_media_posts.create_index([("article_id", ASCENDING), ("platform", ASCENDING)], unique=True, name="social_article_platform_unique")
    social_media_posts.create_index([("campaign_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)])
    social_media_posts.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    social_generation_jobs.create_index([("job_key", ASCENDING)], unique=True, sparse=True)
    social_generation_jobs.create_index([("article_id", ASCENDING), ("created_at", DESCENDING)])
    newsletter_schedules.create_index([("user_id", ASCENDING), ("enabled", ASCENDING), ("updated_at", DESCENDING)])
    newsletter_schedules.create_index([("organization_id", ASCENDING), ("enabled", ASCENDING), ("updated_at", DESCENDING)])
    newsletter_editions.create_index([("schedule_id", ASCENDING), ("due_local_date", ASCENDING)], unique=True, name="newsletter_schedule_date_unique")
    newsletter_editions.create_index([("user_id", ASCENDING), ("due_at", DESCENDING)])
    newsletter_editions.create_index([("status", ASCENDING), ("due_at", ASCENDING)])
    newsletter_editions.create_index([("organization_id", ASCENDING), ("status", ASCENDING), ("distributed_at", DESCENDING)], sparse=True)
    newsletter_edition_versions.create_index([("edition_id", ASCENDING), ("revision", DESCENDING)])
    newsletter_design_jobs.create_index([("edition_id", ASCENDING), ("created_at", DESCENDING)])
    newsletter_design_jobs.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
