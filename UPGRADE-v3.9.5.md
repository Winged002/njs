# Upgrade to NJS v3.9.5

1. Deploy the updated codebase.
2. Ensure `OPENAI_API_KEY` is present if article/landing image generation should be enabled.
3. Optionally configure the new `ARTICLE_*` and `LANDING_GENERATED_*` variables.
4. Restart web + worker containers.
5. Generate a new article and a new landing page to confirm:
   - article hero image appears,
   - inline collection figures appear,
   - landing/page `visual_assets` contains both collection and generated assets.
