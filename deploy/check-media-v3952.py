from pathlib import Path
from config import Config

print("VERSION", Config.APP_VERSION)
print("OPENAI_CONFIGURED", bool(Config.OPENAI_API_KEY))
for label, path in [
    ("ARTICLE_DIR", Config.ARTICLE_IMAGE_OUTPUT_DIR),
    ("LANDING_DIR", Config.LANDING_GENERATED_IMAGE_OUTPUT_DIR),
    ("SOCIAL_DIR", Config.SOCIAL_IMAGE_OUTPUT_DIR),
]:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    probe = p / ".njs-media-write-probe"
    probe.write_text("ok")
    print(label, str(p), "WRITABLE", probe.exists())
    probe.unlink(missing_ok=True)
print("ARTICLE_MODEL", Config.ARTICLE_IMAGE_MODEL)
print("ARTICLE_SIZE", Config.ARTICLE_IMAGE_SIZE)
print("LANDING_MODEL", Config.LANDING_GENERATED_IMAGE_MODEL)
