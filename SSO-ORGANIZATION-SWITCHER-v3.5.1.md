# NJS v3.5.1 — SSO Organization Switcher

v3.5.1 fixes the organization selector in the NJS navigation. The previous control started another OIDC authorization request immediately, which could return the user to the same organization without ever exposing the organizations available to that account.

## What changed

The organization control in both the workspace bar and the secondary navigation is now a real dropdown. When opened, NJS requests the signed-in user's organization directory from Syntal SSO and renders the available memberships in place.

The production directory endpoint defaults to:

```text
GET https://sso.syntal.pro/v1/organizations
Authorization: Bearer <signed-in user's SSO access token>
```

The endpoint is configurable with `SSO_ORGANIZATIONS_ENDPOINT`. During rolling SSO upgrades, NJS also accepts organization/membership data from the SSO userinfo response as a compatibility fallback.

## Secure switching flow

Selecting an organization does not locally rewrite the active workspace. NJS first verifies that the organization appears in the signed-in user's SSO directory, then starts a new OIDC authorization transaction with:

```text
organization_id=<selected Syntal organization id>
```

SSO therefore remains authoritative for membership, NJS entitlement and permissions. On callback, NJS verifies that the ID token and access token agree on the organization and, for a targeted switch, that the returned organization is the one requested. Only after those checks does NJS map the Syntal organization to its local workspace scope.

## Token handling

NJS now retains the SSO access token server-side so it can call the organization directory after login. Tokens are never placed in the browser session cookie or exposed to JavaScript.

The access token and refresh token are encrypted before storage in MongoDB using a Fernet key derived from the deployment `SECRET_KEY`. If the access token is near expiry, NJS uses the refresh token server-side and stores the rotated credentials encrypted again.

The browser only calls NJS's same-origin endpoint:

```text
GET /api/auth/organizations
```

That endpoint returns safe organization metadata, not SSO credentials.

## Existing sessions

Sessions created before v3.5.1 do not have stored SSO credentials. For those users, the selector safely shows the current organization and a **Choose in Syntal** fallback. Reauthenticate once through Syntal and NJS will capture the server-side token set; subsequent organization menus can load automatically.

## Caching and failure behavior

Organization directory results are cached per user for 120 seconds by default (`SSO_ORGANIZATIONS_CACHE_SECONDS`). The menu still requests NJS for a refresh when opened; NJS handles token refresh and SSO access server-side.

If SSO is temporarily unavailable, NJS uses the last safe organization list it previously received. It never fabricates memberships or lets a user switch to an organization not present in the SSO-backed list.

## Data isolation

No NJS content collection is made cross-organization by this change. Campaigns, products, workers, sources, articles, pages, newsletters, analytics and related objects continue to use the existing local organization scope derived from the currently authorized Syntal `org_id`.
