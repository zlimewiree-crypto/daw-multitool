"""Official account authorization and Keychain storage; no passwords in config files."""
import base64
import fcntl
import hashlib
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

import requests

from .render import Cancelled
from .storage import NAMES

PLATFORMS = ("youtube", "instagram", "tiktok")
# key and secret first; Instagram's optional config_id is a Facebook Login for Business configuration
ACCOUNT_ID = {"youtube": "channel_id", "instagram": "user_id", "tiktok": "open_id"}  # TikTok returns open_id with its token
APP_KEYS = {"youtube": ("client_id", "client_secret"), "instagram": ("app_id", "app_secret", "config_id"),
            "tiktok": ("client_key", "client_secret")}


# platform error code -> what to do, shown after the platform's own message
HINTS = {
    "quotaExceeded": "Your Google app's daily YouTube quota is used up; try again tomorrow.",
    "uploadLimitExceeded": "This YouTube channel hit its upload limit; try again later.",
    "invalid_grant": "The sign-in expired or was removed: click Connect again in Accounts (your keys are saved, it "
                     "takes a few seconds). Google signs apps in Testing out every 7 days.",
    "accessNotConfigured": "Turn on YouTube uploads (YouTube Data API v3) for your Google project: Accounts → "
                           "YouTube → Setup steps.",
    "SERVICE_DISABLED": "Turn on YouTube uploads (YouTube Data API v3) for your Google project: Accounts → "
                        "YouTube → Setup steps.",
    "invalid_client": "The app keys are wrong: open Accounts → Setup steps and paste them again.",
    "insufficientPermissions": "The sign-in is missing a permission: click Connect again and tick every box.",
    "ACCESS_TOKEN_SCOPE_INSUFFICIENT": "The sign-in is missing a permission: click Connect again and tick every box.",
    "access_token_invalid": "The sign-in expired: reconnect in Accounts.",
    "scope_not_authorized": "The account didn't allow posting: reconnect and allow every permission.",
    "unaudited_client_can_only_post_to_private_accounts": "Until TikTok audits your app, set your TikTok account to "
                                                          "Private and choose SELF_ONLY (Only me).",
    "privacy_level_option_mismatch": "Untick and tick TikTok to reload its options, then choose a visibility again.",
    "spam_risk_too_many_posts": "TikTok's daily post limit for this account is reached; try tomorrow.",
    "spam_risk_user_banned_from_posting": "TikTok has blocked this account from posting.",
    "reached_active_user_cap": "Your TikTok app's daily user limit is reached; try tomorrow.",
    "rate_limit_exceeded": "Too many requests; wait a minute and try again.",
    190: "Instagram sign-in expired or was removed: reconnect Instagram.",
    10: "Instagram permission missing: reconnect and allow every permission.",
    200: "Instagram permission missing: reconnect and allow every permission.",
    9007: "Instagram is still preparing the video.",
}
RETRY_STATUS = (429, 500, 502, 503, 504)


def redirect_uri(platform, port):
    """Where the browser sign-in returns to. The setup guide shows this same address to register.
    Meta accepts http://localhost; Google and TikTok expect the loopback address."""
    host = "localhost" if platform == "instagram" else "127.0.0.1"
    return f"http://{host}:{port}/callback/{platform}"


MISSING_SCOPES = {
    "youtube": "Google didn't give Auto Poster every permission it needs, so nothing was saved. Click Connect YouTube "
               "again and, on Google's “what it can access” screen, tick every box (or Select all)",
    "tiktok": "TikTok didn't give permission to post videos, so nothing was saved. In your TikTok app, add Content "
              "Posting API with video.publish (Accounts → TikTok → Setup steps), then click Connect TikTok again",
}


def missing_scopes(platform, token, settings):
    """Permissions asked for but not granted. Google lets people untick boxes on its consent screen, and
    says to check the token's `scope`; TikTok returns its granted scopes the same way. [] if none are listed."""
    granted = set(re.split(r"[\s,]+", str(token.get("scope", "")).strip())) - {""}
    needed = settings["youtube_scopes"] if platform == "youtube" else ["video.publish"] if platform == "tiktok" else []
    return [scope for scope in needed if granted and scope not in granted]


class APIError(RuntimeError):
    def __init__(self, message, uncertain=False, code="", rejected=False):
        super().__init__(message)
        self.uncertain = uncertain
        self.code = code  # the platform's own error code, e.g. "quotaExceeded" or 9007
        self.rejected = rejected  # the platform definitively refused this post; checking again can't change it


def platform_error(payload):
    """(code, message) from YouTube/Google, Meta or TikTok error JSON; URLs are removed from the message."""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, str):
        code, message = error, payload.get("error_description", "")
    elif isinstance(error, dict):
        reasons = [e.get("reason") for e in error.get("errors", []) if isinstance(e, dict) and e.get("reason")]
        code = reasons[0] if reasons else error.get("code", "")
        message = error.get("error_user_msg") or error.get("message", "")
    else:
        return "", ""
    message = re.sub(r"https?://\S+", "[link]", re.sub(r"<[^>]*>", "", str(message)))  # YouTube sends HTML links
    return code, " ".join(message.split())[:200]


def explain(summary, code, message, status=None):
    inside = ", ".join(str(part) for part in (f"HTTP {status}" if status else "", code) if part not in ("", None))
    parts = [summary + (f" ({inside})." if inside else ".")]
    if message:
        parts.append(message if message.endswith(".") else message + ".")
    if code in HINTS:
        parts.append(HINTS[code])
    return " ".join(parts)


def callback_page(platform, error=""):
    """What the browser shows when the platform sends the user back: plain words, then back to the app."""
    name = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}[platform]
    if not error:
        title, text = f"{name} sign-in done ✓", "DAW Multitool is finishing the connection. You can close this tab."
    elif error == "access_denied":
        title, text = "Nothing was connected", (f"The {name} sign-in was cancelled. Go back to DAW Multitool and click "
                                                f"Connect {name} when you're ready.")
    else:
        title, text = "The sign-in didn't finish", f"{name} said “{html.escape(error)}”. DAW Multitool says what to fix."
    return (f"<!doctype html><meta charset='utf-8'><title>{title}</title><style>"
            "body{font:17px -apple-system,system-ui,sans-serif;display:grid;place-items:center;min-height:90vh;"
            "margin:0;background:#f5f5f7;color:#1d1d1f}main{max-width:30em;padding:2em;text-align:center}"
            "h1{font-size:1.6em}@media(prefers-color-scheme:dark){body{background:#1d1d1f;color:#f5f5f7}}</style>"
            f"<main><h1>{title}</h1><p>{text}</p></main>").encode()


class CredentialStore:
    """Every login and app key in ONE Keychain entry. Our self-signed builds each count as a new app to
    macOS, so after an update it asks once per entry it guards: one entry means one question per update."""
    ENTRY = "accounts"
    OLD_ENTRIES = [platform + suffix for platform in PLATFORMS for suffix in ("", "-app")]  # before v0.7.9

    def __init__(self, settings, backend=None, lock_path=None):
        if backend is None:
            from keyring.backends.macOS import Keyring
            backend = Keyring()  # explicitly use macOS Keychain, never a plaintext fallback
        self.backend, self.service = backend, settings["keychain_service"]
        if lock_path is None:
            from app_paths import DATA_DIR
            lock_path = DATA_DIR / "keychain.lock"
        self.lock_path = Path(lock_path)

    def _all(self):
        value = self.backend.get_password(self.service, self.ENTRY)
        if value:
            return json.loads(value)
        moved = {}  # first run after the update: gather the separate entries into the one
        for name in self.OLD_ENTRIES:
            old = self.backend.get_password(self.service, name)
            if old:
                moved[name] = json.loads(old)
        if moved:
            self.backend.set_password(self.service, self.ENTRY, json.dumps(moved))
            for name in moved:  # only after the combined entry is saved
                try:
                    self.backend.delete_password(self.service, name)
                except Exception:
                    pass  # a leftover copy is harmless; the combined entry is what's read
        return moved

    def _change(self, update):
        """Re-reads the entry under a lock before writing, so the app and the scheduled-post helper
        never overwrite each other's logins."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            values = self._all()
            update(values)
            self.backend.set_password(self.service, self.ENTRY, json.dumps(values))

    def get(self, platform):
        return self._all().get(platform)

    def set(self, platform, value):
        self._change(lambda values: values.__setitem__(platform, value))

    def delete(self, platform):
        if self.get(platform):
            self._change(lambda values: values.pop(platform, None))


def api_request(session, settings, method, url, retries=0, **kwargs):
    """`retries`: only for requests that are safe to repeat (status checks); a dropped connection,
    rate limit or server error is tried again after a short wait."""
    kwargs.setdefault("allow_redirects", False)  # YouTube's 308 responses acknowledge chunks; they are not redirects
    for attempt in range(retries + 1):
        try:
            response = session.request(method, url, timeout=(10, settings["http_timeout"]), **kwargs)
        except requests.RequestException:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            # Signed upload URLs and token responses must never appear in error logs.
            raise APIError("Connection interrupted. The remote service may still be processing this request.", True) from None
        if response.status_code in RETRY_STATUS and attempt < retries:
            time.sleep(2 ** attempt)
            continue
        break
    if response.status_code >= 400:
        try:
            code, message = platform_error(response.json())
        except (ValueError, AttributeError):
            code, message = "", ""
        raise APIError(explain("Platform rejected the request", code, message, response.status_code),
                       response.status_code >= 500, code)
    return response


def response_json(response):
    try:
        value = response.json()
    except ValueError:
        raise APIError("The platform returned an unreadable response; check the account before retrying.", True) from None
    error = value.get("error")
    if error and (not isinstance(error, dict) or error.get("code") not in (None, "ok", 0)):
        code, message = platform_error(value)
        raise APIError(explain("Platform returned an error", code or "unknown", message), code=code)
    return value


class Accounts:
    def __init__(self, settings, vault=None, session=None):
        self.settings = settings
        self.vault = vault or CredentialStore(settings)
        self.session = session or requests.Session()

    def credentials(self, platform):
        profile = self.vault.get(platform)
        if not profile:
            raise APIError(f"Connect {NAMES[platform]} in Accounts first.")
        if profile.get("expires_at", 0) and profile["expires_at"] < time.time() + 60:
            if not profile.get("refresh_token"):
                raise APIError(f"{NAMES[platform]} access expired. Reconnect the account.")
            data = {"grant_type": "refresh_token", "refresh_token": profile["refresh_token"]}
            if platform == "youtube":
                data.update(client_id=profile["client_id"], client_secret=profile["client_secret"])
            elif platform == "tiktok":
                data.update(client_key=profile["client_key"], client_secret=profile["client_secret"])
            else:
                raise APIError("Reconnect the Instagram account to renew its access token.")
            result = response_json(api_request(self.session, self.settings, "POST", self.settings[f"{platform}_token_url"], data=data))
            profile.update(result, expires_at=time.time() + result.get("expires_in", 3600))
            self.vault.set(platform, profile)
        return profile

    def creator_info(self, profile=None):
        profile = profile or self.credentials("tiktok")
        result = response_json(api_request(self.session, self.settings, "POST",
            self.settings["tiktok_api_url"] + "/post/publish/creator_info/query/",
            headers={"Authorization": f"Bearer {profile['access_token']}"}, json={}))
        return result["data"]

    def app_keys(self, platform):
        """Developer app keys, saved once in Keychain apart from the account token so Disconnect keeps them."""
        profile = self.vault.get(platform) or {}
        return self.vault.get(platform + "-app") or {k: profile[k] for k in APP_KEYS[platform] if profile.get(k)}

    def save_app_keys(self, platform, keys):
        keys = {k: v.strip() for k, v in keys.items() if v and v.strip()}
        if keys:
            self.vault.set(platform + "-app", {**self.app_keys(platform), **keys})

    def check_keys(self, platform, client):
        """Asks TikTok/Google whether the app keys are real before the browser opens, so a typo or swapped box
        shows up now instead of after the whole sign-in. Only a definite "unknown keys" answer counts."""
        if platform == "tiktok":
            data = {"client_key": client.get("client_key", ""), "grant_type": "client_credentials"}
        elif platform == "youtube":  # a made-up code: Google says invalid_grant for real keys, invalid_client for wrong ones
            data = {"client_id": client.get("client_id", ""), "code": "key-check", "grant_type": "authorization_code",
                    "redirect_uri": "http://127.0.0.1"}
        else:
            return
        try:
            reply = self.session.post(self.settings[f"{platform}_token_url"], timeout=15,
                                      data={**data, "client_secret": client.get("client_secret", "")}).json()
        except (requests.RequestException, ValueError):
            return  # offline or an odd reply: the sign-in itself will say more
        if isinstance(reply, dict) and reply.get("error") in ("invalid_client", "deleted_client"):
            raise APIError(f"{NAMES[platform]} doesn't recognise these app keys.", code="invalid_client")

    def instagram_accounts(self, client, code, redirect):
        """Facebook code -> long-lived user token -> each Instagram professional account on the shared Pages.
        A Page token taken from a long-lived user token doesn't expire."""
        base = self.settings["instagram_api_url"]
        app = {"client_id": client["app_id"], "client_secret": client["app_secret"]}
        short = response_json(api_request(self.session, self.settings, "GET", base + "/oauth/access_token",
                                          params={**app, "redirect_uri": redirect, "code": code}))
        user = response_json(api_request(self.session, self.settings, "GET", base + "/oauth/access_token",
            params={**app, "grant_type": "fb_exchange_token", "fb_exchange_token": short.get("access_token", "")}))
        found, after = [], None
        for _ in range(self.settings["instagram_page_limit"]):
            # Follow Meta's `after` cursor on the same endpoint, not the returned `next` URL (it carries the token)
            pages = response_json(api_request(self.session, self.settings, "GET", base + "/me/accounts",
                headers={"Authorization": f"Bearer {user.get('access_token', '')}"},
                params={"fields": "name,access_token,instagram_business_account{id,username}", "limit": 100,
                        **({"after": after} if after else {})}))
            found += [{"access_token": page["access_token"], "user_id": page["instagram_business_account"]["id"],
                       "name": page["instagram_business_account"].get("username") or page["name"], "page": page.get("name", "")}
                      for page in pages.get("data", []) if page.get("instagram_business_account") and page.get("access_token")]
            paging = pages.get("paging", {})
            after = paging.get("cursors", {}).get("after")
            if not paging.get("next") or not after:
                break
        if not found:
            raise APIError("No Instagram professional account was shared. Link it to a Facebook Page, "
                           "then Connect again and tick both the Page and the Instagram account.")
        return found

    def authorize(self, platform, client=None, cancelled=lambda: False, choose=lambda options: options[0]):
        """Open the platform's sign-in page in the browser, where the user picks the account.
        `choose` picks between several Instagram accounts the user shared."""
        if platform not in PLATFORMS:
            raise ValueError("Unsupported browser authorization provider.")
        client = client or self.app_keys(platform)
        key, secret = APP_KEYS[platform][:2]
        if not client.get(key) or not client.get(secret):
            raise ValueError(f"Add your {NAMES[platform]} app keys in Accounts first (one time only).")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if platform == "tiktok":
            challenge = hashlib.sha256(verifier.encode()).hexdigest()  # TikTok Desktop specifies hex, unlike Google's base64url
        received = {}
        callback_path = f"/callback/{platform}"

        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # callback URLs contain one-use authorization codes

            def do_GET(self):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path != callback_path or not secrets.compare_digest(query.get("state", [""])[0], state):
                    self.send_error(400, "Invalid authorization response")
                    return
                received.update(code=query.get("code", [""])[0], error=query.get("error", [""])[0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(callback_page(platform, received["error"] or ("" if received["code"] else "no code")))

        port = self.settings.get(f"{platform}_callback_port", 0)  # Google desktop clients accept any loopback port
        with HTTPServer(("127.0.0.1", port), Callback) as server:
            server.timeout = 0.5
            redirect = redirect_uri(platform, server.server_port)
            parameters = {"response_type": "code", "redirect_uri": redirect, "state": state}
            if platform == "instagram":
                # auth_type=rerequest shows the Page / Instagram account picker again on a reconnect
                parameters.update(client_id=client["app_id"], auth_type="rerequest",
                                  **({"config_id": client["config_id"]} if client.get("config_id")
                                     else {"scope": ",".join(self.settings["instagram_scopes"])}))
            else:
                parameters.update({key: client[key], "code_challenge": challenge, "code_challenge_method": "S256",
                                   "scope": (" ".join if platform == "youtube" else ",".join)(self.settings[f"{platform}_scopes"])})
            if platform == "youtube":
                parameters.update(access_type="offline", prompt="select_account consent")  # always show Google's account chooser
            if platform == "tiktok":
                parameters["disable_auto_auth"] = 1  # always show TikTok's login page instead of reusing the last account
            if not webbrowser.open(self.settings[f"{platform}_auth_url"] + "?" + urlencode(parameters)):
                raise APIError("Couldn't open your browser. Check your default browser and try again.")
            deadline = time.monotonic() + self.settings["oauth_timeout"]
            while not received:
                if cancelled():
                    raise Cancelled("Account connection stopped.")
                if time.monotonic() >= deadline:
                    raise APIError("The sign-in wasn't finished in time. If the browser showed an error, the "
                                   "message there says which setup step to fix; then click Connect again.", code="timeout")
                server.handle_request()
            if received.get("error") or not received.get("code"):
                reason = f" ({received['error']})" if received.get("error") else ""
                raise APIError(f"Account authorization was declined or incomplete{reason}. If it names a scope, "
                               "turn that permission on in your developer app, then Connect again.",
                               code=received.get("error") or "access_denied")
            if platform == "instagram":
                options = self.instagram_accounts(client, received["code"], redirect)
                profile = choose(options) if len(options) > 1 else options[0]
                if not profile:
                    raise Cancelled("No Instagram account chosen. Nothing was connected.")
                profile["connected_at"] = time.time()
                self.vault.set("instagram", profile)
                return profile["name"]
            payload = {key: client[key], "client_secret": client["client_secret"], "code": received["code"],
                       "grant_type": "authorization_code", "redirect_uri": redirect, "code_verifier": verifier}
            result = response_json(api_request(self.session, self.settings, "POST", self.settings[f"{platform}_token_url"], data=payload))
        profile = {**client, **result, "expires_at": time.time() + result.get("expires_in", 3600),
                   "connected_at": time.time()}
        if "access_token" not in profile:
            raise APIError("The provider did not return an access token.")
        missing = missing_scopes(platform, result, self.settings)
        if missing:  # a box left unticked on the permission screen: every upload would fail, so keep nothing
            raise APIError(MISSING_SCOPES[platform] + f" (missing: {', '.join(missing)})", code="scope_missing")
        # Save immediately so a failed identity lookup does not lose completed authorization.
        self.vault.set(platform, profile)
        if platform == "youtube":
            self._youtube_channel(profile)
        else:
            profile["name"] = self.creator_info(profile).get("creator_nickname", "TikTok creator")
        self.vault.set(platform, profile)
        return profile["name"]

    def _youtube_channel(self, profile):
        result = response_json(api_request(self.session, self.settings, "GET", self.settings["youtube_api_url"] + "/channels",
            headers={"Authorization": f"Bearer {profile['access_token']}"}, params={"part": "snippet", "mine": "true"}))
        if not result.get("items"):
            raise APIError("This Google account has no accessible YouTube channel. Create/select a channel and reconnect.")
        profile.update(name=result["items"][0]["snippet"]["title"], channel_id=result["items"][0].get("id", ""))

    def account_id(self, platform, profile=None, required=True):
        """Stable ID of the connected channel/account (not its name or token), so a post can't go to an account
        connected later. Pass the profile being used, so the check and the upload use the very same login.
        YouTube logins from before this was saved look it up once. Not `required`: "" when unknown."""
        profile = profile or self.credentials(platform)
        if platform == "youtube" and not profile.get("channel_id"):
            try:
                self._youtube_channel(profile)
            except APIError:
                if required:
                    raise
                return ""
            if (self.vault.get(platform) or {}).get("access_token") == profile.get("access_token"):
                self.vault.set(platform, profile)  # only if that same login is still the saved one
        found = str(profile.get(ACCOUNT_ID[platform]) or "")
        if required and not found:
            raise APIError(f"Reconnect {NAMES[platform]} in Accounts so Auto Poster can check which account it posts to.")
        return found
