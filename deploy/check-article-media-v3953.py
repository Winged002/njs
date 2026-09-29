#!/usr/bin/env python3
from pathlib import Path
from bs4 import BeautifulSoup
from config import Config
from db import articles

row = articles.find_one({}, sort=[("created_at", -1)])
print("VERSION", Config.APP_VERSION)
if not row:
    print("ARTICLE none")
    raise SystemExit(0)
meta = row.get("metadata") or {}
soup = BeautifulSoup(row.get("content") or "", "html.parser")
imgs = soup.find_all("img")
print("ARTICLE_ID", row.get("_id"))
print("TITLE", meta.get("title"))
print("CONTENT_IMAGE_COUNT", len(imgs))
for i, img in enumerate(imgs, 1):
    print(f"CONTENT_IMAGE_{i}", img.get("src"), "ALT=", img.get("alt"))
print("IMAGE_INFO_URL", (meta.get("image_info") or {}).get("url"))
print("HERO_IMAGE_URL", (meta.get("hero_image") or {}).get("url"))
print("OG_IMAGE", (meta.get("open_graph") or {}).get("og:image"))
print("MEDIA_STATUS", meta.get("media_status"))
for label, asset in (("image_info", meta.get("image_info") or {}), ("hero_image", meta.get("hero_image") or {})):
    url = str(asset.get("url") or "")
    if url.startswith("/static/generated/"):
        local = Path("/app") / url.lstrip("/")
        print(label.upper()+"_FILE", local, "EXISTS", local.exists(), "BYTES", local.stat().st_size if local.exists() else 0)
