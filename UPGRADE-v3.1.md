# Upgrade to v3.1.0

v3.1.0 can be deployed directly over v3.0.4.1. Preserve `.env`, Mongo volumes, uploads and generated assets as before.

On application startup `ensure_indexes()` creates the additional attribution and analytics-settings indexes. There is no destructive Mongo migration and no new required environment variable.

After deployment:

1. Open **Analytics -> Privacy settings** and select the organization's tracking mode.
2. Visit a published article or generated website page.
3. Confirm a new view appears in Analytics.
4. On an article generated from a Product-enabled Worker, click the Product CTA and confirm Product clicks increase.
5. Submit an enabled audience-response form and confirm the server-side conversion appears.

Older analytics events remain readable. Attribution dimensions begin filling as new v3.1 traffic arrives.
