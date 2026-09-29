# Newsletter Design Studio v3.9.3

## Mental model

Newsletter content and newsletter presentation are separate layers.

1. NJS generates the editorial payload: subject, preheader, introduction, ordered stories and closing.
2. The normal renderer creates a safe baseline email.
3. A visual prompt may revise that email's presentation.
4. The result is stored as a numbered design revision.
5. The final stored HTML is what preview, export and Mailchimp delivery use.

## Edition-level prompt editing

The Visual Design workspace posts a plain-language request to the asynchronous `newsletter.apply_visual_prompt` worker task. The task takes an exact design revision as its base and creates a new revision when complete.

The previous HTML is inserted into `newsletter_edition_versions` before the new HTML becomes current.

## Future-edition design direction

When `apply_to_future` is selected, the prompt is appended to the newsletter schedule's `visual_design_prompt`. Future editions apply that cumulative design brief to their own newly generated content.

This intentionally stores design intent rather than freezing one edition's exact HTML as a template. Story count, headlines, images and links can therefore change naturally from edition to edition while the visual direction remains consistent.

## Manual HTML fallback

Advanced users can expand **Advanced HTML source**, edit the current HTML and save it as another design revision. Active/scriptable elements are removed by the newsletter HTML sanitizer.

## Editorial changes

Saving subject/preheader/intro/closing recompiles the content and reapplies the current visual design prompt. The old design is snapshotted first.

## Delivery behavior

Prompt editing does not create a separate delivery format. The edition's current `html` remains the canonical HTML used by:

- browser preview;
- HTML download;
- delivery package export;
- BlackBook/Mailchimp draft creation;
- BlackBook/Mailchimp sending.
