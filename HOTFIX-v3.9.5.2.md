# NJS v3.9.5.2 — Media Reliability Hotfix

This hotfix fixes articles completing successfully while no usable image was produced.

## Root issue
v3.9.5/v3.9.5.1 treated OpenAI image generation as best-effort and swallowed all generation exceptions. The article could therefore complete with no `metadata.hero_image` at all.

## Changes
- OpenAI article/landing image generation now calls the Images REST API directly, avoiding incompatibilities with the application's pinned OpenAI SDK method signature.
- The returned base64 payload must decode to real image bytes and the persisted file must exist and exceed a minimum size before the asset is accepted.
- Image model fallback chain: configured model -> `gpt-image-2` -> `gpt-image-1.5` -> `gpt-image-1`.
- Article source research now captures `og:image`, `twitter:image`, and page image URLs.
- Article hero selection order is now:
  1. OpenAI-generated editorial hero
  2. selected Collection image
  3. original source article image
  4. campaign website image
- The article stores `metadata.media_status` with OpenAI generation status and the actual error when generation fails.
- Article Review shows the image-generation error instead of silently hiding it.
- `compose.yml` now explicitly uses v3.9.5.2 and explicitly shares article/landing media paths through the existing `generated_media` volume.

## Expected output
A newly generated article should have `metadata.hero_image.url`. With a working OpenAI key it should normally have `metadata.hero_image.source = openai_generated`. If OpenAI is unavailable but approved imagery exists, the article uses a real fallback asset rather than an empty `<img>`.
