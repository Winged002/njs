# NJS v3.9.3 — Prompt Newsletter Design Studio

v3.9.3 adds prompt-driven visual editing for generated newsletter emails.

## Prompt-driven visual design

Each generated newsletter edition now has a Visual Design workspace. Instead of assembling layouts with a conventional drag/drop builder, the operator describes the desired visual result in plain language, for example:

- "Make this feel like a premium financial briefing with a dark navy masthead and thin dividers."
- "Make the first story dominant and the rest a compact digest."
- "Use more whitespace, restrained blue CTAs and an executive B2B style."

NJS sends the current email HTML, the edition content contract and the visual request to the configured newsletter design model. The model is instructed to change presentation without changing newsletter facts, story URLs or audience content.

## Non-destructive design revisions

Visual prompts create a new design revision rather than overwriting the only copy. The edition screen includes:

- current design revision number;
- saved visual revision history;
- one-click restore;
- live desktop/mobile email preview;
- advanced HTML source fallback;
- asynchronous visual design jobs with UI polling.

Any visual or editorial edit returns the edition to Draft and clears stale distribution/draft state.

## Standing visual direction

A prompt can optionally be marked **Use this direction for future editions**. NJS stores that instruction on the newsletter plan as `visual_design_prompt`. Future generated editions start from the normal content renderer and then apply the standing design brief to the new edition's content.

Edition-specific design prompts remain attached to that edition. Full editorial regeneration keeps the edition's current visual direction.

## Email-oriented generation rules

The design model is explicitly instructed to generate email HTML rather than a browser-only web page:

- table-first structure where useful;
- inline CSS for critical presentation;
- responsive desktop/mobile behavior;
- no JavaScript, forms, iframes, external CSS or external fonts;
- no invented links, claims, statistics or images;
- existing story and CTA URLs must remain intact.

Generated HTML is sanitized before it replaces the current revision.

## Syntal AI

The tool count remains **127**. `njs.newsletters.editions.update` now also accepts:

```json
{
  "visual_prompt": "Make the email minimal and executive-friendly...",
  "apply_to_future": true
}
```

When `visual_prompt` is present, the tool queues a non-destructive visual revision job rather than performing a normal editorial-copy update.

## Persistence

v3.9.3 adds two MongoDB collections, created automatically by normal startup/index initialization:

- `newsletter_edition_versions`
- `newsletter_design_jobs`

No persistent-volume migration is required.
