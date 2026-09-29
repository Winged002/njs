#!/usr/bin/env python3
"""Repair v3.9.5.x article media into the monolith-compatible contract.

Default behavior repairs recent *unpublished* articles using already-known assets.
Pass --generate-missing to buy/generate an OpenAI hero only when no usable asset exists.
Signed/published articles are skipped to preserve publication signatures.
"""
import argparse
from copy import deepcopy
from bson import ObjectId

from config import Config
from db import articles, article_versions, campaigns
from services import clean_text, now
from editorial_media_service import generate_openai_image_asset, insert_article_media_assets


def candidate_from_article(article, campaign):
    metadata = deepcopy(article.get("metadata") or {})
    hero = metadata.get("image_info") or metadata.get("hero_image") or {}
    if isinstance(hero, dict) and hero.get("url"):
        return hero, metadata
    for item in metadata.get("collection_images") or []:
        if isinstance(item, dict) and item.get("url"):
            return item, metadata
    research = article.get("source_research") or {}
    for item in research.get("images") or []:
        if isinstance(item, dict) and item.get("url"):
            return {**item, "source": item.get("source") or "source_page", "role": "hero"}, metadata
    campaign_research = (campaign or {}).get("website_research") or {}
    for item in campaign_research.get("images") or []:
        if isinstance(item, dict) and item.get("url"):
            return {**item, "source": item.get("source") or "campaign_website", "role": "hero"}, metadata
    return None, metadata


def generated_asset(article, metadata):
    title = clean_text(metadata.get("title") or "News article", 180)
    description = clean_text(metadata.get("description") or metadata.get("summary") or "", 600)
    prompt = f"""Create a high-quality wide editorial image for a news article.
No text, logos, watermarks, UI mockups, or brand marks. Use credible contemporary publication imagery.
HEADLINE: {title}
ARTICLE CONTEXT: {description}
""".strip()
    return generate_openai_image_asset(
        kind="article",
        entity_id=str(article["_id"]),
        scene_prompt=prompt,
        alt_text=description or title,
        output_dir=Config.ARTICLE_IMAGE_OUTPUT_DIR,
        url_prefix=Config.ARTICLE_IMAGE_URL_PREFIX,
        model=Config.ARTICLE_IMAGE_MODEL,
        size=Config.ARTICLE_IMAGE_SIZE,
        quality=Config.ARTICLE_IMAGE_QUALITY,
        output_format=Config.ARTICLE_IMAGE_OUTPUT_FORMAT,
        compression=Config.ARTICLE_IMAGE_OUTPUT_COMPRESSION,
        extra_meta={"role": "hero"},
    )


def repair(article, generate_missing=False):
    if article.get("published") or int(article.get("published_revision") or 0) > 0:
        return "skipped_published", None
    campaign = campaigns.find_one({"_id": article.get("campaign_id")}) if article.get("campaign_id") else None
    hero, metadata = candidate_from_article(article, campaign)
    if not hero and generate_missing:
        hero = generated_asset(article, metadata)
    if not hero or not hero.get("url"):
        return "no_asset", None

    collection_assets = metadata.get("collection_images") or []
    updated_content = insert_article_media_assets(
        article.get("content") or "",
        hero_asset=hero,
        collection_assets=collection_assets,
        max_collection_images=Config.ARTICLE_INLINE_COLLECTION_IMAGE_LIMIT,
    )
    image_info = {
        "url": hero.get("url"),
        "filename": hero.get("filename"),
        "type": "generated" if hero.get("source") == "openai_generated" else (hero.get("source") or "image"),
        "source": hero.get("source"),
        "alt": hero.get("alt") or metadata.get("description") or metadata.get("title"),
        "model": hero.get("model"),
        "size": hero.get("size"),
        "quality": hero.get("quality"),
    }
    metadata["image_info"] = image_info
    metadata["hero_image"] = hero
    metadata.setdefault("open_graph", {})["og:image"] = hero.get("url")
    metadata.setdefault("media_status", {})["hero_source"] = hero.get("source") or "repaired"
    metadata["media_status"]["body_injected"] = True
    metadata["media_status"]["repaired_at"] = now()

    article_versions.insert_one({
        "article_id": article["_id"],
        "revision": int(article.get("revision") or 1),
        "reason": "Before v3.9.5.3 media contract repair",
        "content": article.get("content") or "",
        "metadata": article.get("metadata") or {},
        "quality": article.get("quality") or {},
        "editorial_plan": article.get("editorial_plan") or {},
        "review_status": article.get("review_status") or "pending_review",
        "published": False,
        "created_at": now(),
    })
    articles.update_one({"_id": article["_id"]}, {"$set": {
        "content": updated_content,
        "metadata": metadata,
        "updated_at": now(),
    }})
    return "repaired", hero.get("url")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--article-id")
    ap.add_argument("--latest", type=int, default=5)
    ap.add_argument("--generate-missing", action="store_true")
    args = ap.parse_args()
    if args.article_id:
        rows = [articles.find_one({"_id": ObjectId(args.article_id)})]
        rows = [r for r in rows if r]
    else:
        rows = list(articles.find({}).sort("created_at", -1).limit(max(1, min(args.latest, 100))))
    if not rows:
        print("No matching articles")
        return
    for article in rows:
        status, url = repair(article, generate_missing=args.generate_missing)
        print(article["_id"], status, url or "")


if __name__ == "__main__":
    main()
