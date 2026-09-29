# Upgrade to v3.9.5.3

1. Deploy and rebuild `web`, `worker`, and `beat`.
2. Confirm NJS remains bound to host port `8010`.
3. Confirm `APP_VERSION=3.9.5.3` in the running worker and web containers.
4. Repair recent unpublished articles:

```bash
docker compose exec -T worker python deploy/repair-article-media-v3953.py --latest 10
```

If an old article has no existing generated/source/Collection asset and you explicitly want to buy a new image generation:

```bash
docker compose exec -T worker python deploy/repair-article-media-v3953.py --latest 10 --generate-missing
```

5. Generate one new Newsjacked article and verify `article.content` includes `<img src="/static/generated/articles/...">` and Mongo metadata includes `image_info.url`.
