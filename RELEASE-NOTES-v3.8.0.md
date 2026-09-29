# NJS v3.8.0 — Landing Page Studio

## Added

- First-class Landing Page Studio at `/landing-pages`.
- Manual landing-page creation with no AI request.
- AI-assisted creation from campaign/evidence context.
- Direct full-HTML editing with sanitization and revision preservation.
- SEO title, description and summary editing.
- Local editor-buffer preview plus existing server-rendered preview.
- Manual-to-AI and AI-to-manual workflow switching on the same page.
- AI revision instructions that preserve the reviewed revision.
- Page duplication as an unpublished editable copy.
- Explicit publish and unpublish controls.
- Revision list and restore.
- Permanent page deletion with route/navigation/history/change-job cleanup.
- Landing Page Studio entry in the normal NJS Content / Websites navigation.

## AI control expansion

NJS now exposes **120 first-party tools**. The landing-page surface includes:

- `njs.landing_pages.list`
- `njs.landing_pages.read`
- `njs.landing_pages.create_manual`
- `njs.landing_pages.create_ai`
- `njs.landing_pages.update`
- `njs.landing_pages.html.write`
- `njs.landing_pages.generate`
- `njs.landing_pages.ai.revise`
- `njs.landing_pages.duplicate`
- `njs.landing_pages.publish`
- `njs.landing_pages.unpublish`
- `njs.landing_pages.delete`
- `njs.landing_pages.versions.list`
- `njs.landing_pages.versions.restore`
- `njs.landing_pages.navigation.add`
- `njs.landing_pages.navigation.hide`
- existing landing-page change-job/review tools remain available.

## Authorization fixes retained

v3.8.0 includes the production cross-app SSO delegation model required for Syntal AI tokens. NJS does not expect an `ai`-audience bearer token to directly contain `njs.access`; it re-evaluates the authenticated user and active organization through SSO `/v1/ai/delegation-check`.

The tool manifest is discovery metadata. Actual operations remain fail-closed behind `X-Syntal-AI: 1`, SSO bearer validation, target-app delegation, actor/org matching and NJS workspace scoping.

## Syntal AI contract

The bundled `config/njs-v3.8-contract.json` describes all 120 tools. `deploy/install-syntal-ai-v38-contract.py` installs the snapshot into the current Syntal AI compatibility location and adds mandatory confirmation for permanent landing-page deletion.

## Compatibility

- Mongo document model remains compatible with v3.7.1.
- Existing landing pages continue to render and publish.
- Existing generated pages can be opened in the new manual editor.
- Existing AI-generated pages can be manually edited without losing revision history.
- Stable `syntal-njs-*` runtime names remain unchanged.
