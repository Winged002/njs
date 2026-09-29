import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import jwt
import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import session

from config import Config

_DISCOVERY = {"value": None, "expires": 0.0}


def _http_get_json(url, *, headers=None):
    response = requests.get(url, headers=headers or {}, timeout=Config.SSO_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def discovery(force=False):
    now = time.time()
    if not force and _DISCOVERY["value"] and _DISCOVERY["expires"] > now:
        return _DISCOVERY["value"]
    url = Config.SSO_ISSUER.rstrip("/") + "/.well-known/openid-configuration"
    payload = _http_get_json(url)
    required = ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri", "issuer")
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise RuntimeError("Syntal SSO discovery is missing: " + ", ".join(missing))
    expected = Config.SSO_ISSUER.rstrip("/")
    if str(payload.get("issuer") or "").rstrip("/") != expected:
        raise RuntimeError("Syntal SSO issuer mismatch")
    _DISCOVERY["value"] = payload
    _DISCOVERY["expires"] = now + Config.SSO_DISCOVERY_CACHE_SECONDS
    return payload


def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def begin_login(next_url=None, organization_id=None, prompt=None):
    cfg = discovery()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    session["syntal_oidc"] = {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "next": next_url or "/",
        "organization_id": str(organization_id or "").strip() or None,
        "created_at": int(time.time()),
    }
    query = {
        "client_id": Config.SSO_CLIENT_ID,
        "redirect_uri": Config.SSO_REDIRECT_URI,
        "response_type": "code",
        "scope": Config.SSO_SCOPES,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if organization_id:
        query["organization_id"] = str(organization_id).strip()
    if prompt:
        query["prompt"] = str(prompt).strip()
    return cfg["authorization_endpoint"] + "?" + urlencode(query)


def _exchange_code(code, verifier):
    cfg = discovery()
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": Config.SSO_REDIRECT_URI,
        "client_id": Config.SSO_CLIENT_ID,
        "code_verifier": verifier,
    }
    auth = None
    if Config.SSO_CLIENT_SECRET:
        auth = (Config.SSO_CLIENT_ID, Config.SSO_CLIENT_SECRET)
        data.pop("client_id", None)
    response = requests.post(cfg["token_endpoint"], data=data, auth=auth, timeout=Config.SSO_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("access_token") or not payload.get("id_token"):
        raise RuntimeError("Syntal SSO token response is incomplete")
    return payload


def _decode_access_token(access_token):
    cfg = discovery()
    jwk_client = jwt.PyJWKClient(cfg["jwks_uri"], cache_keys=True, lifespan=Config.SSO_DISCOVERY_CACHE_SECONDS)
    signing_key = jwk_client.get_signing_key_from_jwt(access_token).key
    return jwt.decode(
        access_token, signing_key, algorithms=Config.SSO_ALLOWED_ALGORITHMS,
        audience=Config.SSO_CLIENT_ID, issuer=Config.SSO_ISSUER.rstrip("/"),
        options={"require": ["exp", "iat", "iss", "aud", "sub"]}, leeway=30,
    )


def _decode_id_token(id_token, expected_nonce):
    cfg = discovery()
    jwk_client = jwt.PyJWKClient(cfg["jwks_uri"], cache_keys=True, lifespan=Config.SSO_DISCOVERY_CACHE_SECONDS)
    signing_key = jwk_client.get_signing_key_from_jwt(id_token).key
    claims = jwt.decode(
        id_token,
        signing_key,
        algorithms=Config.SSO_ALLOWED_ALGORITHMS,
        audience=Config.SSO_CLIENT_ID,
        issuer=Config.SSO_ISSUER.rstrip("/"),
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        leeway=30,
    )
    if expected_nonce and claims.get("nonce") != expected_nonce:
        raise RuntimeError("Syntal SSO nonce validation failed")
    return claims


def _userinfo(access_token):
    cfg = discovery()
    payload = _http_get_json(cfg["userinfo_endpoint"], headers={"Authorization": f"Bearer {access_token}"})
    if not payload.get("sub"):
        raise RuntimeError("Syntal SSO userinfo is missing sub")
    return payload


def complete_login(code, returned_state):
    pending = session.pop("syntal_oidc", None) or {}
    if not pending or not returned_state or not secrets.compare_digest(str(pending.get("state") or ""), str(returned_state)):
        raise RuntimeError("Syntal SSO state validation failed")
    if int(time.time()) - int(pending.get("created_at") or 0) > 600:
        raise RuntimeError("Syntal SSO login request expired")
    token = _exchange_code(code, pending.get("verifier") or "")
    id_claims = _decode_id_token(token["id_token"], pending.get("nonce"))
    access_claims = _decode_access_token(token["access_token"])
    id_org = str(id_claims.get("syntal_org_id") or id_claims.get("org_id") or id_claims.get("organization_id") or "").strip()
    access_org = str(access_claims.get("org_id") or access_claims.get("syntal_org_id") or access_claims.get("organization_id") or "").strip()
    expected_org = str(pending.get("organization_id") or "").strip()
    if id_org and access_org and id_org != access_org:
        raise RuntimeError("Syntal SSO organization mismatch between ID and access tokens")
    if expected_org and expected_org not in {id_org, access_org}:
        raise RuntimeError("Syntal SSO did not authorize the requested organization")
    userinfo = _userinfo(token["access_token"])
    if str(userinfo.get("sub")) != str(id_claims.get("sub")) or str(access_claims.get("sub")) != str(id_claims.get("sub")):
        raise RuntimeError("Syntal SSO subject mismatch")
    # Access-token claims are authoritative for application permissions/entitlement.
    # Userinfo adds profile fields; it must not erase the authorization claims.
    claims = dict(id_claims)
    claims.update(access_claims)
    claims.update(userinfo)
    if access_claims.get("permissions") is not None:
        claims["permissions"] = access_claims.get("permissions")
    if access_claims.get("entitlement") is not None:
        claims["entitlement"] = access_claims.get("entitlement")
    if access_claims.get("org_id") and not claims.get("syntal_org_id"):
        claims["syntal_org_id"] = access_claims.get("org_id")
    if access_claims.get("org_name") and not claims.get("organization_name"):
        claims["organization_name"] = access_claims.get("org_name")
    return claims, pending.get("next") or "/", token


def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(Config.SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_token(value):
    value = str(value or "").strip()
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_token(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return ""


def access_token_expiry(token_payload):
    try:
        expires_in = int((token_payload or {}).get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    if expires_in > 0:
        return int(time.time()) + expires_in
    access_token = str((token_payload or {}).get("access_token") or "")
    if access_token:
        try:
            claims = jwt.decode(access_token, options={"verify_signature": False, "verify_aud": False, "verify_exp": False})
            return int(claims.get("exp") or 0)
        except Exception:
            pass
    return 0


def refresh_tokens(refresh_token):
    refresh_token = str(refresh_token or "").strip()
    if not refresh_token:
        raise RuntimeError("No Syntal SSO refresh token is available")
    cfg = discovery()
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": Config.SSO_CLIENT_ID,
    }
    auth = None
    if Config.SSO_CLIENT_SECRET:
        auth = (Config.SSO_CLIENT_ID, Config.SSO_CLIENT_SECRET)
        data.pop("client_id", None)
    response = requests.post(cfg["token_endpoint"], data=data, auth=auth, timeout=Config.SSO_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("access_token"):
        raise RuntimeError("Syntal SSO refresh response is missing access_token")
    _decode_access_token(payload["access_token"])
    if not payload.get("refresh_token"):
        payload["refresh_token"] = refresh_token
    return payload


def _normalize_organization_rows(payload):
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("organizations")
        if rows is None:
            rows = payload.get("items")
        if rows is None:
            rows = payload.get("results")
        if rows is None:
            rows = payload.get("data")
        if isinstance(rows, dict):
            rows = rows.get("organizations") or rows.get("items") or rows.get("results") or []
    if not isinstance(rows, list):
        return []
    out = []
    seen = set()
    for raw in rows:
        if isinstance(raw, str):
            org_id = raw.strip()
            name = org_id
            role = None
        elif isinstance(raw, dict):
            nested = raw.get("organization") if isinstance(raw.get("organization"), dict) else {}
            org_id = str(
                raw.get("syntal_org_id") or raw.get("org_id") or raw.get("organization_id") or raw.get("id")
                or nested.get("syntal_org_id") or nested.get("org_id") or nested.get("id") or ""
            ).strip()
            name = str(
                raw.get("organization_name") or raw.get("org_name") or raw.get("display_name") or raw.get("name")
                or nested.get("display_name") or nested.get("name") or org_id
            ).strip()
            role = raw.get("role") or raw.get("membership_role") or raw.get("access_role")
        else:
            continue
        if not org_id or org_id in seen:
            continue
        seen.add(org_id)
        out.append({"id": org_id, "name": name or org_id, "role": str(role or "").strip() or None})
    return out


def available_organizations(access_token, *, current_org_id=None, current_org_name=None):
    """Return organizations available to the signed-in SSO identity.

    Prefer the current SSO organization-directory endpoint. Older SSO builds may
    expose the membership list only through userinfo, so userinfo is a compatible
    fallback rather than making the NJS menu unusable during rolling upgrades.
    """
    access_token = str(access_token or "").strip()
    if not access_token:
        raise RuntimeError("No Syntal SSO access token is available")
    headers = {"Authorization": f"Bearer {access_token}"}
    rows = []
    endpoint_error = None
    endpoint = Config.SSO_ORGANIZATIONS_ENDPOINT
    if endpoint:
        try:
            rows = _normalize_organization_rows(_http_get_json(endpoint, headers=headers))
        except Exception as exc:
            endpoint_error = exc
    if not rows:
        try:
            info = _userinfo(access_token)
            membership_payload = info.get("organizations") or info.get("memberships") or []
            rows = _normalize_organization_rows({"organizations": membership_payload})
            if not rows and endpoint_error:
                raise endpoint_error
        except Exception:
            if endpoint_error:
                raise endpoint_error
            raise
    current_org_id = str(current_org_id or "").strip()
    if current_org_id and all(row["id"] != current_org_id for row in rows):
        rows.insert(0, {"id": current_org_id, "name": str(current_org_name or current_org_id), "role": None})
    rows.sort(key=lambda row: (0 if row["id"] == current_org_id else 1, row["name"].casefold()))
    return rows


def _flatten_strings(value):
    out = []
    if isinstance(value, str):
        out.extend(part for part in value.replace(",", " ").split() if part)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            out.extend(_flatten_strings(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            if item is True:
                out.append(str(key))
            else:
                out.extend(_flatten_strings(item))
    return out


def extract_permissions(claims):
    values = []
    for key in ("permissions", "permission", "scp", "scope", "roles", "entitlements"):
        values.extend(_flatten_strings(claims.get(key)))
    for key in ("authorization", "access"):
        nested = claims.get(key)
        if isinstance(nested, dict):
            for nkey in ("permissions", "roles", "scopes", "entitlements"):
                values.extend(_flatten_strings(nested.get(nkey)))
    return sorted({str(value).strip() for value in values if str(value).strip()})


def has_app_access(claims):
    required = Config.SSO_REQUIRED_PERMISSION.strip()
    if not required:
        return True
    permissions = set(extract_permissions(claims))
    if required in permissions:
        return True
    applications = claims.get("applications") or claims.get("apps") or claims.get("app_access")
    if isinstance(applications, dict):
        value = applications.get(Config.SSO_CLIENT_ID)
        if value is True or (isinstance(value, dict) and value.get("enabled", True)):
            return True
    if isinstance(applications, list):
        for value in applications:
            if value == Config.SSO_CLIENT_ID or (isinstance(value, dict) and value.get("id") == Config.SSO_CLIENT_ID and value.get("enabled", True)):
                return True
    return not Config.SSO_ENFORCE_PERMISSION


def extract_identity(claims):
    org = claims.get("organization") if isinstance(claims.get("organization"), dict) else {}
    org_id = (
        claims.get("syntal_org_id") or claims.get("org_id") or claims.get("organization_id")
        or org.get("syntal_org_id") or org.get("org_id") or org.get("id")
    )
    org_name = claims.get("organization_name") or claims.get("org_name") or org.get("name") or org.get("display_name")
    username = claims.get("preferred_username") or claims.get("name") or claims.get("email") or str(claims.get("sub") or "")
    return {
        "syntal_user_id": str(claims.get("syntal_user_id") or claims.get("sub") or "").strip(),
        "syntal_org_id": str(org_id or "").strip() or None,
        "organization_name": str(org_name or "").strip() or None,
        "email": str(claims.get("email") or "").strip().lower() or None,
        "username": str(username or "").strip()[:200],
        "name": str(claims.get("name") or "").strip()[:240] or None,
        "permissions": extract_permissions(claims),
    }


def logout_url(post_logout_redirect_uri):
    cfg = discovery()
    endpoint = cfg.get("end_session_endpoint")
    if not endpoint:
        return Config.SSO_ISSUER.rstrip("/") + "/"
    return endpoint + "?" + urlencode({"client_id": Config.SSO_CLIENT_ID, "post_logout_redirect_uri": post_logout_redirect_uri})
