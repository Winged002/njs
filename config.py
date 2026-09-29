import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

class Config:
    APP_VERSION = os.getenv("APP_VERSION", "3.9.5.3")
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/newsjacking")
    REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/2")
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    DEEPSEEK_REASONING_MODEL = os.getenv("DEEPSEEK_REASONING_MODEL", DEEPSEEK_MODEL)

    # v1.8 social publishing generation. Text uses the existing DeepSeek-compatible client;
    # images use OpenAI image generation exactly as the original social-calendar subsystem did.
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    SOCIAL_TEXT_MODEL = os.getenv("SOCIAL_TEXT_MODEL", DEEPSEEK_MODEL)
    SOCIAL_TEXT_TEMPERATURE = float(os.getenv("SOCIAL_TEXT_TEMPERATURE", "0.7"))
    SOCIAL_IMAGE_MODEL = os.getenv("SOCIAL_IMAGE_MODEL", "gpt-image-2")
    SOCIAL_IMAGE_SIZE = os.getenv("SOCIAL_IMAGE_SIZE", "1024x1024")
    SOCIAL_IMAGE_QUALITY = os.getenv("SOCIAL_IMAGE_QUALITY", "low").lower()
    SOCIAL_IMAGE_OUTPUT_FORMAT = os.getenv("SOCIAL_IMAGE_OUTPUT_FORMAT", "jpeg").lower()
    SOCIAL_IMAGE_OUTPUT_COMPRESSION = int(os.getenv("SOCIAL_IMAGE_OUTPUT_COMPRESSION", "88"))
    SOCIAL_IMAGE_OUTPUT_DIR = os.getenv("SOCIAL_IMAGE_OUTPUT_DIR", str(BASE_DIR / "static" / "generated" / "social"))
    SOCIAL_IMAGE_URL_PREFIX = os.getenv("SOCIAL_IMAGE_URL_PREFIX", "/static/generated/social")
    SOCIAL_IMAGE_FONT_BOLD = os.getenv("SOCIAL_IMAGE_FONT_BOLD", "")
    SOCIAL_IMAGE_FONT_REGULAR = os.getenv("SOCIAL_IMAGE_FONT_REGULAR", "")

    # v1.8 automated newsletter generation. Editions are created as reviewable drafts;
    # external delivery remains an explicit provider integration.
    NEWSLETTER_TEXT_MODEL = os.getenv("NEWSLETTER_TEXT_MODEL", DEEPSEEK_MODEL)
    NEWSLETTER_TEXT_TEMPERATURE = float(os.getenv("NEWSLETTER_TEXT_TEMPERATURE", "0.55"))
    NEWSLETTER_DESIGN_MODEL = os.getenv("NEWSLETTER_DESIGN_MODEL", NEWSLETTER_TEXT_MODEL)
    NEWSLETTER_DESIGN_TEMPERATURE = float(os.getenv("NEWSLETTER_DESIGN_TEMPERATURE", "0.45"))
    NEWSLETTER_SCHEDULE_TICK_SECONDS = int(os.getenv("NEWSLETTER_SCHEDULE_TICK_SECONDS", "900"))


    # v1.9 reusable evidence collections and multi-source ingestion.
    SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY", "")
    SUPADATA_BASE_URL = os.getenv("SUPADATA_BASE_URL", "https://api.supadata.ai/v1").rstrip("/")
    COLLECTION_FILE_DIR = os.getenv("COLLECTION_FILE_DIR", str(BASE_DIR / "data" / "collections"))
    COLLECTION_MAX_UPLOAD_BYTES = int(os.getenv("COLLECTION_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
    COLLECTION_URL_MAX_BYTES = int(os.getenv("COLLECTION_URL_MAX_BYTES", "2000000"))
    COLLECTION_ITEM_TEXT_MAX_CHARS = int(os.getenv("COLLECTION_ITEM_TEXT_MAX_CHARS", "50000"))
    COLLECTION_TRANSCRIPT_MAX_CHARS = int(os.getenv("COLLECTION_TRANSCRIPT_MAX_CHARS", "160000"))
    COLLECTION_DISCOVERED_LINK_LIMIT = int(os.getenv("COLLECTION_DISCOVERED_LINK_LIMIT", "120"))
    COLLECTION_IMAGE_LIMIT = int(os.getenv("COLLECTION_IMAGE_LIMIT", "40"))

    # v2.6 Syntal SSO / OIDC relying-party integration.
    SSO_ENABLED = os.getenv("SSO_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
    SSO_ISSUER = os.getenv("SSO_ISSUER", "https://sso.syntal.pro").rstrip("/")
    SSO_CLIENT_ID = os.getenv("SSO_CLIENT_ID", "njs").strip()
    SSO_CLIENT_SECRET = os.getenv("SSO_CLIENT_SECRET", "").strip()
    SSO_REDIRECT_URI = os.getenv("SSO_REDIRECT_URI", "https://njs.syntal.pro/auth/callback").strip()
    SSO_SCOPES = os.getenv("SSO_SCOPES", "openid profile email organization permissions offline_access").strip()
    SSO_REQUIRED_PERMISSION = os.getenv("SSO_REQUIRED_PERMISSION", "njs.access").strip()
    SSO_ADMIN_PERMISSION = os.getenv("SSO_ADMIN_PERMISSION", "njs.admin").strip()
    SSO_ENFORCE_PERMISSION = os.getenv("SSO_ENFORCE_PERMISSION", "true").lower() in {"1", "true", "yes", "on"}
    SSO_LOCAL_FALLBACK = os.getenv("SSO_LOCAL_FALLBACK", "false").lower() in {"1", "true", "yes", "on"}
    SSO_DISCOVERY_CACHE_SECONDS = int(os.getenv("SSO_DISCOVERY_CACHE_SECONDS", "3600"))
    SSO_HTTP_TIMEOUT_SECONDS = int(os.getenv("SSO_HTTP_TIMEOUT_SECONDS", "10"))
    SSO_ALLOWED_ALGORITHMS = [item.strip() for item in os.getenv("SSO_ALLOWED_ALGORITHMS", "RS256").split(",") if item.strip()]
    SSO_ACCOUNT_URL = os.getenv("SSO_ACCOUNT_URL", SSO_ISSUER + "/").strip()
    SSO_ORGANIZATIONS_ENDPOINT = os.getenv("SSO_ORGANIZATIONS_ENDPOINT", SSO_ISSUER + "/v1/organizations").strip()
    SSO_ORGANIZATIONS_CACHE_SECONDS = int(os.getenv("SSO_ORGANIZATIONS_CACHE_SECONDS", "120"))
    SSO_AI_DELEGATION_ENDPOINT = os.getenv("SSO_AI_DELEGATION_ENDPOINT", SSO_ISSUER + "/v1/ai/delegation-check").strip()

    # v2.8 audience capture + BlackBook CRM bridge. API keys are stored per Syntal-linked organization.
    BLACKBOOK_BASE_URL = os.getenv("BLACKBOOK_BASE_URL", "https://blackbook.syntal.pro").rstrip("/")
    BLACKBOOK_HTTP_TIMEOUT_SECONDS = int(os.getenv("BLACKBOOK_HTTP_TIMEOUT_SECONDS", "12"))
    AUDIENCE_CAPTURE_MAX_SUBMISSIONS_PER_10_MIN = int(os.getenv("AUDIENCE_CAPTURE_MAX_SUBMISSIONS_PER_10_MIN", "20"))
    AUDIENCE_CAPTURE_TOKEN_MAX_AGE_SECONDS = int(os.getenv("AUDIENCE_CAPTURE_TOKEN_MAX_AGE_SECONDS", "7200"))

    ANALYTICS_RETENTION_DAYS = int(os.getenv("ANALYTICS_RETENTION_DAYS", "730"))
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://njs.syntal.pro").rstrip("/")
    PUBLISHING_IP = os.getenv("PUBLISHING_IP", "95.179.253.170").strip()
    PUBLISHING_PRIMARY_HOST = os.getenv("PUBLISHING_PRIMARY_HOST", "njs.syntal.pro").strip().lower().rstrip(".")
    CUSTOM_DOMAIN_ROUTING = os.getenv("CUSTOM_DOMAIN_ROUTING", "true").lower() in {"1", "true", "yes", "on"}
    DOMAIN_AUTO_PROVISION = os.getenv("DOMAIN_AUTO_PROVISION", "true").lower() in {"1", "true", "yes", "on"}
    DOMAIN_PROVISIONER_SOCKET = os.getenv("DOMAIN_PROVISIONER_SOCKET", "/run/njs-domain-provisioner/provision.sock").strip()
    DOMAIN_PROVISIONER_TOKEN = os.getenv("DOMAIN_PROVISIONER_TOKEN", "").strip()
    DOMAIN_PROVISIONER_TIMEOUT_SECONDS = int(os.getenv("DOMAIN_PROVISIONER_TIMEOUT_SECONDS", "210"))
    DOMAIN_PROVISIONER_APP_PORT = int(os.getenv("DOMAIN_PROVISIONER_APP_PORT", os.getenv("HOST_PORT", "8010")))
    WEBSITE_RESEARCH_TIMEOUT_SECONDS = int(os.getenv("WEBSITE_RESEARCH_TIMEOUT_SECONDS", "15"))
    WEBSITE_RESEARCH_MAX_BYTES = int(os.getenv("WEBSITE_RESEARCH_MAX_BYTES", "1500000"))
    NEWSJACKING_INTERVAL_SECONDS = int(os.getenv("NEWSJACKING_INTERVAL_SECONDS", "3600"))
    NEWSJACKING_DEFAULT_MIN_CONFIDENCE = float(os.getenv("NEWSJACKING_DEFAULT_MIN_CONFIDENCE", "0.65"))
    NEWSJACKING_DEFAULT_MAX_ARTICLES_PER_RUN = int(os.getenv("NEWSJACKING_DEFAULT_MAX_ARTICLES_PER_RUN", "10"))
    NEWSJACKING_MAX_ARTICLES_PER_RUN = int(os.getenv("NEWSJACKING_MAX_ARTICLES_PER_RUN", "50"))
    NEWSJACKING_MAX_ITEMS_PER_RUN = int(os.getenv("NEWSJACKING_MAX_ITEMS_PER_RUN", "1000"))

    ARTICLE_MIN_WORDS = int(os.getenv("ARTICLE_MIN_WORDS", "900"))
    ARTICLE_TARGET_MAX_WORDS = int(os.getenv("ARTICLE_TARGET_MAX_WORDS", "1700"))
    ARTICLE_QUALITY_REWRITE_THRESHOLD = int(os.getenv("ARTICLE_QUALITY_REWRITE_THRESHOLD", "82"))
    ARTICLE_SOURCE_RESEARCH = os.getenv("ARTICLE_SOURCE_RESEARCH", "true").lower() in {"1", "true", "yes", "on"}
    AUTHOR_IMAGE_OUTPUT_DIR = os.getenv("AUTHOR_IMAGE_OUTPUT_DIR", str(BASE_DIR / "static" / "generated" / "authors"))
    AUTHOR_IMAGE_URL_PREFIX = os.getenv("AUTHOR_IMAGE_URL_PREFIX", "/static/generated/authors").rstrip("/")
    AUTHOR_IMAGE_MAX_BYTES = int(os.getenv("AUTHOR_IMAGE_MAX_BYTES", str(8 * 1024 * 1024)))

    ARTICLE_IMAGE_MODEL = os.getenv("ARTICLE_IMAGE_MODEL", SOCIAL_IMAGE_MODEL or "gpt-image-2")
    ARTICLE_IMAGE_SIZE = os.getenv("ARTICLE_IMAGE_SIZE", "1536x1024")
    ARTICLE_IMAGE_QUALITY = os.getenv("ARTICLE_IMAGE_QUALITY", "medium").lower()
    ARTICLE_IMAGE_OUTPUT_FORMAT = os.getenv("ARTICLE_IMAGE_OUTPUT_FORMAT", "jpeg").lower()
    ARTICLE_IMAGE_OUTPUT_COMPRESSION = int(os.getenv("ARTICLE_IMAGE_OUTPUT_COMPRESSION", "88"))
    ARTICLE_IMAGE_OUTPUT_DIR = os.getenv("ARTICLE_IMAGE_OUTPUT_DIR", str(BASE_DIR / "static" / "generated" / "articles"))
    ARTICLE_IMAGE_URL_PREFIX = os.getenv("ARTICLE_IMAGE_URL_PREFIX", "/static/generated/articles")
    ARTICLE_INLINE_COLLECTION_IMAGE_LIMIT = int(os.getenv("ARTICLE_INLINE_COLLECTION_IMAGE_LIMIT", "2"))

    LANDING_GENERATED_IMAGE_MODEL = os.getenv("LANDING_GENERATED_IMAGE_MODEL", SOCIAL_IMAGE_MODEL or "gpt-image-2")
    LANDING_GENERATED_IMAGE_SIZE = os.getenv("LANDING_GENERATED_IMAGE_SIZE", "1536x1024")
    LANDING_GENERATED_IMAGE_QUALITY = os.getenv("LANDING_GENERATED_IMAGE_QUALITY", "medium").lower()
    LANDING_GENERATED_IMAGE_OUTPUT_FORMAT = os.getenv("LANDING_GENERATED_IMAGE_OUTPUT_FORMAT", "jpeg").lower()
    LANDING_GENERATED_IMAGE_OUTPUT_COMPRESSION = int(os.getenv("LANDING_GENERATED_IMAGE_OUTPUT_COMPRESSION", "88"))
    LANDING_GENERATED_IMAGE_OUTPUT_DIR = os.getenv("LANDING_GENERATED_IMAGE_OUTPUT_DIR", str(BASE_DIR / "static" / "generated" / "landing"))
    LANDING_GENERATED_IMAGE_URL_PREFIX = os.getenv("LANDING_GENERATED_IMAGE_URL_PREFIX", "/static/generated/landing")
    LANDING_GENERATED_IMAGE_COUNT = int(os.getenv("LANDING_GENERATED_IMAGE_COUNT", "1"))

    LANDING_MAX_VISUAL_ASSETS = int(os.getenv("LANDING_MAX_VISUAL_ASSETS", "8"))
    LANDING_QUALITY_REWRITE_THRESHOLD = int(os.getenv("LANDING_QUALITY_REWRITE_THRESHOLD", "82"))
    LANDING_MAX_ARTICLES = int(os.getenv("LANDING_MAX_ARTICLES", "12"))

    ENABLE_CREDITS = os.getenv("ENABLE_CREDITS", "true").lower() in {"1", "true", "yes", "on"}
    NEWSJACKING_CHECK_CREDIT_COST = int(os.getenv("NEWSJACKING_CHECK_CREDIT_COST", "1"))
    NEWSJACKING_ARTICLE_CREDIT_COST = int(os.getenv("NEWSJACKING_ARTICLE_CREDIT_COST", "40"))

    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true").lower() in {"1", "true", "yes", "on"}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", str(100 * 1024 * 1024)))

    @classmethod
    def validate(cls):
        missing = []
        if not cls.SECRET_KEY:
            missing.append("SECRET_KEY")
        if not cls.DEEPSEEK_API_KEY:
            missing.append("DEEPSEEK_API_KEY")
        if cls.SSO_ENABLED:
            if not cls.SSO_ISSUER:
                missing.append("SSO_ISSUER")
            if not cls.SSO_CLIENT_ID:
                missing.append("SSO_CLIENT_ID")
            if not cls.SSO_REDIRECT_URI:
                missing.append("SSO_REDIRECT_URI")
        if missing:
            raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
