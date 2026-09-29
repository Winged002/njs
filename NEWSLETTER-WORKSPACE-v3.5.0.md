# NJS v3.5.0 — Newsletter Workspace

Newsletters are now a first-class main navigation category rather than a subsection of Distribution.

## Information architecture

Primary navigation:

- Newsletters

Second-layer navigation:

- Create — define a recurring newsletter, cadence, campaign scope, audience and editorial defaults.
- Manage — edit schedules, pause recurring generation, generate an edition immediately, or archive schedules.
- View — review generated editions and filter the archive by draft, ready or distributed state.
- Distribute — prepare provider-neutral delivery packages and record the external delivery handoff.

## Edition lifecycle

`Draft -> Ready -> Distributed`

A delivery record belongs to the exact content that was handed off. Editing or regenerating a distributed edition clears that record and returns the edition to Draft so the interface never implies that modified content was already sent.

## Distribution package

The ZIP export contains:

- rendered newsletter HTML
- plain-text newsletter
- subject line
- preheader
- JSON manifest with edition ID, schedule ID, story count, state and timestamps

## Provider-neutral distribution

v3.5.0 does not invent an email-provider send action. The Distribute screen supports Mailchimp, BlackBook/Mailchimp, Brevo, SendGrid, SMTP/email services, manual export and other providers as recorded handoff methods. The record can include audience/list, recipient count, external campaign/reference, notes and delivery timestamp.
