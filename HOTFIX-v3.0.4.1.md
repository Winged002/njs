# Newsjacking Core v3.0.4.1 hotfix

Fixes a Jinja dictionary key collision in Collection evidence pickers.

`collection_cards` entries are dictionaries with an `items` key. In Jinja, `group.items` resolves to Python `dict.items` rather than the list stored under the `items` key. The Worker and Product evidence pickers now use explicit dictionary access (`group['items']`) for both item counts and loops.

Affected screens fixed:
- New Newsjack Worker
- New Product
- Product detail / evidence editor

No database migration is required.
