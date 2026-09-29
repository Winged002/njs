# Upgrade to v3.3.2

v3.3.2 is a focused interface/workflow release over v3.3.1. No destructive MongoDB migration is required.

## Main changes
- Campaign creation rebuilt as a compact evidence-first builder
- Campaign review rebuilt as an approval cockpit
- Product creation rebuilt around offer, messaging boundary, and evidence
- Product detail rebuilt into an editable workspace with usage/output rail
- Website builder reorganized into preview studio + inspector + collapsible page builder
- Secondary and tertiary navigation scroll positions persist across page loads

Preserve `.env`, volumes, uploads, generated files, SSO, domains, and TLS state. Rebuild web/worker/beat together.
