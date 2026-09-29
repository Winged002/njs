# v3.0.3 Website Builder behavior

A website is now the parent object; pages are children. The site owns the reusable design system, homepage reference and navigation manifest.

### Creating a homepage
Create a Home / landing page at `/`. Once generation completes, its design system becomes the reusable website style. Publish it normally.

### Creating `/about` or another page
Choose the page purpose and route. The generator receives the existing site design system (or the legacy homepage as a migration reference) and is required to preserve the website identity. The page starts outside navigation.

After review, use **Publish + add to navigation**. This publishes the page and enables its shared navigation item. Because navigation is rendered from the site manifest, the homepage and every sibling page immediately show the new link without AI-regenerating their body content.

### Reliability model
Navigation is application-owned rather than model-authored. Generated page HTML controls page-specific content and layout; the shared site header/footer and links are rendered by NJS. This prevents broken or inconsistent cross-page navigation and keeps future pages visually and structurally connected.
