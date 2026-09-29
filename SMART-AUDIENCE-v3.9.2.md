# Smart Audience v3.9.2

## Purpose

Smart Audience turns BlackBook's behavioral intelligence into reusable newsletter targeting rules.

A rule can be as broad as:

```text
High OR Medium engagement
```

or as specific as:

```text
(High OR Medium engagement)
AND
(Cybersecurity OR Digital Identity)
```

With `ALL` interest matching:

```text
High engagement
AND
(Cybersecurity AND Digital Identity)
```

## Audience composition

A newsletter audience can contain four source types:

1. Smart Audience rule;
2. reusable BlackBook segments;
3. explicitly selected BlackBook people;
4. all eligible contacts.

Sources 1–3 are additive. Source 4 is exclusive.

## Dynamic behavior

A saved Smart Audience stores rule criteria, not resolved contact IDs. If a person's engagement bucket or interests change before delivery, their inclusion can change accordingly.

This keeps scheduled newsletters aligned to the latest imported Mailchimp behavior without requiring the operator to rebuild the audience manually.

## Eligibility

A behavioral match alone does not make a recipient sendable. BlackBook still removes contacts when:

- `do_not_contact` is set;
- email eligibility is not `eligible`;
- no usable email address exists.

Mailchimp synchronization and subscription state are handled by the existing distribution bridge.
