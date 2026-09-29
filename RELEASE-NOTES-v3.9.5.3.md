# NJS v3.9.5.3 — Monolith Media Contract

This release fixes the gap between successful image generation and image-less saved article HTML.

## Article media contract

A generated Newsjacked article now stores one canonical cover asset in all compatibility locations:

- `metadata.image_info.url` — legacy/monolith canonical cover image field.
- `metadata.hero_image.url` — retained v3.9.5.x alias.
- `metadata.open_graph["og:image"]` — share metadata compatibility.
- `article.content` — contains an application-injected `<figure><img src="...">...</figure>` using the same verified URL.

Collection image evidence is also inserted into `article.content` with real `src` URLs.

## Security/reliability

The text model is not trusted to create article media markup. Any model-authored `<figure>` or `<img>` markup is removed before the application injects verified OpenAI/Collection/source assets.

## Legacy behavior retained

The original monolith expected article cover imagery at `metadata.image_info.url`. v3.9.5.3 restores that field instead of requiring consumers to understand the newer `hero_image` field.

## Existing v3.9.5.x articles

Run `deploy/repair-article-media-v3953.py` to inject already-generated media into recent unpublished article bodies. Use `--generate-missing` only when you also want to generate a new OpenAI asset for articles with no usable existing image.
