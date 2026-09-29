# NJS v3.9.5.1 — Article Media Hotfix

Fixes phantom article figures such as `<figure><img alt="..."></figure>` that contained no image source.

- Article language models are explicitly forbidden from emitting media markup.
- The article sanitizer strips model-authored `img`, `figure`, and `figcaption` tags.
- Only NJS-approved media is injected after text sanitization.
- Collection images continue to be inserted by NJS with real `src` URLs.
- OpenAI-generated article hero images continue to render from `metadata.hero_image.url`.
- Article review/revision jobs re-inject approved Collection images after rewriting text.
