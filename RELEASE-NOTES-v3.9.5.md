# NJS v3.9.5 — Article + Landing Visual Assets

## Summary
v3.9.5 extends NJS media generation so that both the Newsjacked article pipeline and the Landing/Page Builder can now combine Collection imagery with OpenAI-generated visuals.

## Output matrix

| Output | Collection images | OpenAI-generated images |
| --- | --- | --- |
| Newsjacked article | YES | YES |
| Landing/page builder | YES | YES |
| Social bundle | No | Yes, `gpt-image-2` |

## What changed

### Newsjacked articles
- Article HTML sanitization now allows safe editorial imagery (`figure`, `img`, `figcaption`).
- Collection image evidence selected through Worker/Campaign evidence can now be inserted into the article body as inline supporting figures.
- Article generation now attempts to create an OpenAI-generated editorial hero image and stores it in article metadata for public/review rendering.
- Public article pages now render the generated hero image plus styled inline collection figures.

### Landing/page builder
- Landing generation still accepts campaign website imagery and selected Collection imagery.
- Landing generation can now also create OpenAI-generated visual assets and include them in the `visual_assets` pool used by the HTML generator.
- Prompting now explicitly tells the page generator to use both supplied categories when both exist.

### Social bundle
- No change in source policy: social still generates its own shared promotional image through OpenAI (`gpt-image-2`) and does not pull directly from Collection images.

## Configuration
New environment variables:
- `ARTICLE_IMAGE_MODEL`
- `ARTICLE_IMAGE_SIZE`
- `ARTICLE_IMAGE_QUALITY`
- `ARTICLE_IMAGE_OUTPUT_FORMAT`
- `ARTICLE_IMAGE_OUTPUT_COMPRESSION`
- `ARTICLE_IMAGE_OUTPUT_DIR`
- `ARTICLE_IMAGE_URL_PREFIX`
- `ARTICLE_INLINE_COLLECTION_IMAGE_LIMIT`
- `LANDING_GENERATED_IMAGE_MODEL`
- `LANDING_GENERATED_IMAGE_SIZE`
- `LANDING_GENERATED_IMAGE_QUALITY`
- `LANDING_GENERATED_IMAGE_OUTPUT_FORMAT`
- `LANDING_GENERATED_IMAGE_OUTPUT_COMPRESSION`
- `LANDING_GENERATED_IMAGE_OUTPUT_DIR`
- `LANDING_GENERATED_IMAGE_URL_PREFIX`
- `LANDING_GENERATED_IMAGE_COUNT`

## Notes
- OpenAI-generated article/landing imagery requires `OPENAI_API_KEY`.
- If `OPENAI_API_KEY` is missing, article and landing generation continue without failing; OpenAI image creation is skipped.
