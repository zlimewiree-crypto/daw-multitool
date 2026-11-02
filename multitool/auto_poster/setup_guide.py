"""One-time setup of each platform's developer app, step by step, for Auto Poster's Accounts tab.
Each step's link opens the exact page for that step and says which fields to fill (everything else stays empty);
anything to paste comes from the same code the sign-in uses, so the two can't drift apart. It also turns the
common setup mistakes into a plain message and the page that fixes them. Wording follows the platforms' own
setup docs (checked 2026-10-30)."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import re

from .accounts import redirect_uri

NAMES = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}
APP_NAME = "DAW Multitool"
TESTING_DAYS = 7  # Google signs in Testing-mode apps for 7 days at a time


@dataclass(frozen=True)
class Step:
    text: str
    url: str = ""     # opened by "Open this page ↗"
    copy: str = ""    # shown with a Copy button
    fields: tuple = ()  # (what the page asks, what to do, text to copy or "")
    tip: str = ""     # one quieter line under the step
    id: str = ""      # lets messages say "step 4" without hard-coding numbers
    icon: bool = False  # shows "Save the icon to Downloads" (TikTok wants an app icon)


# the boxes at the end of each guide: (key saved in Keychain, label, hidden while typing)
FIELDS = {
    "youtube": (("client_id", "Client ID", False), ("client_secret", "Client secret", True)),
    "instagram": (("app_id", "App ID", False), ("app_secret", "App secret", True),
                  ("config_id", "Configuration ID", False)),
    "tiktok": (("client_key", "Client key", False), ("client_secret", "Client secret", True)),
}

INTRO = {
    "youtube": "A one-time setup (about 10 minutes): YouTube only lets apps upload through a free Google “app” of "
               "your own. Use the Google account that owns your channel. Fill only what each step lists.",
    "instagram": "A one-time setup (about 15 minutes): Instagram only lets apps post through a free Meta app of your "
                 "own. You need an Instagram Business or Creator account connected to a Facebook Page.",
    "tiktok": "A one-time setup (about 10 minutes): TikTok only lets apps post through a free TikTok app of your own. "
              "Until TikTok reviews it, posts are “Only me” and your TikTok account must be set to Private.",
}

CAPTURE = "When you copy a key, Auto Poster fills it in by itself."
DOWNLOADS = Path.home() / "Downloads"  # where Google's “Download JSON” saves the key file
AUDIENCE_PAGE = "https://console.cloud.google.com/auth/audience"
GOOGLE_CLIENTS_PAGE = "https://console.cloud.google.com/auth/clients"
YOUTUBE_API_PAGE = "https://console.cloud.google.com/apis/library/youtube.googleapis.com"


def steps(platform, settings):
    if platform == "youtube":
        return [
            Step("Create a Google Cloud project.", "https://console.cloud.google.com/projectcreate",
                 fields=(("Project name", APP_NAME, APP_NAME),), tip="Click Create, then wait a few seconds."),
            Step("Turn on YouTube uploads: click Enable.", YOUTUBE_API_PAGE, id="enable",
                 tip="The project name at the top of the page should say " + APP_NAME + "."),
            Step("Set up the sign-in screen: click Get started and fill only these.",
                 "https://console.cloud.google.com/auth/overview",
                 fields=(("App name", APP_NAME, APP_NAME), ("User support email", "your email", ""),
                         ("Audience", "External", ""), ("Contact information", "your email", ""),
                         ("Finish", "tick “I agree”, then Continue and Create", "")),
                 tip="Leave everything else empty and don't add a logo. On the long Branding page? "
                     "Only App name, User support email and Developer contact email matter: click Save."),
            Step("Add yourself as a test user: under Test users click Add users, type your Google email, Save.",
                 AUDIENCE_PAGE, id="test_users",
                 tip="Leave it on Testing: publishing needs a website and privacy policy."),
            Step("Create the key, then click Download JSON.", "https://console.cloud.google.com/auth/clients/create",
                 fields=(("Application type", "Desktop app", ""), ("Name", APP_NAME, APP_NAME)), id="key",
                 tip="Download it before closing that box: Google may not show the secret again. "
                     "Auto Poster finds the file in your Downloads by itself (if your Mac asks, click Allow)."),
            Step("Click Connect YouTube. Then, in the browser:",
                 fields=(("Choose an account", "the one that owns your channel", ""),
                         ("“Google hasn't verified this app”", "click Continue (it's your own app)", ""),
                         ("What it can access", "tick every box, then Continue", ""),
                         ("Channel", "pick it if Google asks", ""))),
        ]
    if platform == "instagram":
        return [
            Step("Make your Instagram a Business or Creator account (skip if it already is): Settings → "
                 "Account type and tools → Switch to professional account.", "https://help.instagram.com/2358103564437429"),
            Step("Connect it to a Facebook Page you manage: in Instagram, Edit profile → Page.",
                 "https://www.facebook.com/help/1148909221857370"),
            Step("Create a Meta app: pick the option for managing Instagram content with Facebook Login "
                 "(app type Business, if asked).", "https://developers.facebook.com/apps/creation/",
                 fields=(("App name", APP_NAME, APP_NAME), ("Contact email", "your email", ""))),
            Step("In your app open Facebook Login for Business → Settings. Paste this into Valid OAuth "
                 "Redirect URIs, then Save changes.", "https://developers.facebook.com/apps/",
                 redirect_uri("instagram", settings["instagram_callback_port"]), id="redirect"),
            Step("Facebook Login for Business → Configurations → Create configuration: choose User access token, "
                 "tick these permissions, Create. Then copy the Configuration ID.",
                 copy=", ".join(settings["instagram_scopes"]), id="config", tip=CAPTURE),
            Step("App settings → Basic: copy the App ID, then click Show and copy the App secret.",
                 "https://developers.facebook.com/apps/", id="key", tip=CAPTURE),
            Step("Click Connect Instagram. Log in with the Facebook account that manages your Page, tick the Page "
                 "and its Instagram account, and allow everything."),
        ]
    if platform == "tiktok":
        return [
            Step("Open Manage apps, click Connect an app and name it.", "https://developers.tiktok.com/portal/apps",
                 fields=(("App name", APP_NAME, APP_NAME),)),
            Step("Switch the toggle next to the app's name to Sandbox, then click Create Sandbox.",
                 tip="A Sandbox works straight away, without waiting for TikTok's review."),
            Step("Fill in the app details (TikTok won't save the app without them):", id="details", icon=True,
                 fields=(("App icon", "Click Save the icon below, then upload “DAW Multitool icon.png” from Downloads", ""),
                         ("Description", "Paste this", "Posts my own beat videos from my Mac to my TikTok."),
                         ("Platforms", "Tick Desktop", ""),
                         ("Desktop URL (website)", "Your own page: your YouTube channel, BeatStars or Instagram link", "")),
                 tip="TikTok only checks the website when an app is sent for review; a Sandbox doesn't need that."),
            Step("Add the products Login Kit and Content Posting API, and in Content Posting API turn on Direct Post. "
                 "Check this scope is listed:", copy=", ".join(settings["tiktok_scopes"]), id="products",
                 tip="Do this with the switch at the top on Sandbox: products added to Production don't count here."),
            Step("In Login Kit choose Desktop and add this Redirect URI:",
                 copy=redirect_uri("tiktok", settings["tiktok_callback_port"]), id="redirect"),
            Step("Sandbox settings → Target users → Add account: log in with the TikTok account you'll post to.",
                 tip="It can take up to an hour to show up."),
            Step("Click Apply changes, then copy the Client key and the Client secret (still on Sandbox).", id="key",
                 tip=CAPTURE),
            Step("Click Connect TikTok and log in with the same TikTok account.",
                 tip="First make that account Private (TikTok app → Profile → ☰ → Settings and privacy → Privacy → "
                     "Private account): until TikTok reviews your app, it can only post to private accounts."),
        ]
    raise ValueError(f"No setup guide for {platform}.")


def number(platform, settings, step_id):
    return next(n for n, step in enumerate(steps(platform, settings), start=1) if step.id == step_id)


def key_problem(platform, keys):
    """Why the pasted keys can't be right (a key in the wrong box, a stray space), or ""."""
    keys = {key: value.strip() for key, value in keys.items()}
    if any(re.search(r"\s", value) for value in keys.values()):
        return "A key has a space or line break inside it. Copy it again with the Copy icon on the platform's page."
    if platform == "youtube":
        client_id, secret = keys.get("client_id", ""), keys.get("client_secret", "")
        if secret.endswith(".apps.googleusercontent.com"):
            return "That's the Client ID in the Client secret box. Copy the Client secret instead."
        if client_id and not client_id.endswith(".apps.googleusercontent.com"):
            return "A Google Client ID ends in .apps.googleusercontent.com. Copy the Client ID again."
    if platform == "instagram":
        for key, label in (("app_id", "App ID"), ("config_id", "Configuration ID")):
            if keys.get(key) and not keys[key].isdigit():
                return f"The {label} is only numbers. Copy it again."
        if keys.get("app_secret", "").isdigit():
            return "That looks like the App ID in the App secret box. Click Show next to App secret and copy that."
    return ""


def waiting(platform, settings):
    """Shown while the browser sign-in is open: the mistakes that show up in the browser, not in Auto Poster."""
    if platform == "youtube":
        return (f"Finish signing in in your browser. If Google says “Access blocked”, add your email as a test user "
                f"(Setup steps, step {number('youtube', settings, 'test_users')}); if it says “redirect_uri_mismatch”, "
                f"the key isn't a Desktop app key (step {number('youtube', settings, 'key')}). Press Stop to cancel.")
    if platform == "instagram":
        return (f"Finish logging in with Facebook in your browser. If Facebook says the URL is blocked, add the "
                f"redirect address from Setup steps, step {number('instagram', settings, 'redirect')}. Press Stop to cancel.")
    return (f"Finish logging in to TikTok in your browser. If TikTok says to correct “scope”, turn on Direct Post "
            f"(Setup steps, step {number('tiktok', settings, 'products')}) and click Apply changes; for redirect_uri "
            f"or client_key, check steps {number('tiktok', settings, 'redirect')} and {number('tiktok', settings, 'key')}. "
            "Press Stop to cancel.")


def fix_for(platform, error, settings):
    """(plain message, page that fixes it or "") for a known setup mistake behind a failed Connect, else None."""
    code, text, name = getattr(error, "code", ""), str(error), NAMES[platform]
    if platform == "youtube" and code in ("accessNotConfigured", "SERVICE_DISABLED"):
        project = re.search(r"project (\d+)", text)  # Google names the project whose API is off
        return (f"YouTube uploads aren't turned on for your Google project yet (Setup steps, step "
                f"{number('youtube', settings, 'enable')}). Click Enable on the page that opens, wait a minute, "
                "then click Connect YouTube again.", YOUTUBE_API_PAGE + (f"?project={project[1]}" if project else ""))
    if code == "access_denied":
        if platform == "youtube":
            return ("Google didn't let Auto Poster in. If you clicked “Back to safety”, click Connect YouTube again and "
                    "choose Continue. If Google said “Access blocked”, add your Google email as a test user (Setup "
                    f"steps, step {number('youtube', settings, 'test_users')}), then Connect again.", AUDIENCE_PAGE)
        return (f"The {name} sign-in was cancelled or declined. Click Connect {name} again and allow every permission.", "")
    if code == "invalid_client":
        pages = {"youtube": GOOGLE_CLIENTS_PAGE, "instagram": "https://developers.facebook.com/apps/",
                 "tiktok": "https://developers.tiktok.com/portal/apps"}
        return (f"{name} doesn't recognise these app keys. Copy them again from the page that opens and paste them "
                f"in Setup steps (step {number(platform, settings, 'key')}).", pages[platform])
    if code in ("scope_missing", "timeout"):
        return text, ""
    return None


def reconnect_by(profile):
    """When Google wants YouTube reconnected while the Google app stays in Testing, or None if unknown."""
    connected = (profile or {}).get("connected_at")
    return datetime.fromtimestamp(connected).astimezone() + timedelta(days=TESTING_DAYS) if connected else None


def recognise(platform, text, step_id):
    """(key, value) when freshly copied text is clearly one of this platform's keys, on a step that asks for keys.
    Meta's App ID and Configuration ID are both plain numbers, so the step decides which one it is.
    Anything else is ignored and can still be pasted by hand."""
    text = text.strip()
    if not text or len(text) > 200 or re.search(r"\s", text) or step_id not in ("config", "key", "final"):
        return None
    if platform == "youtube":
        if re.fullmatch(r"[\w.-]+\.apps\.googleusercontent\.com", text):
            return "client_id", text
        if re.fullmatch(r"GOCSPX-[\w-]{10,}", text):
            return "client_secret", text
    elif platform == "instagram":
        if re.fullmatch(r"[0-9a-f]{32}", text) and step_id != "config":
            return "app_secret", text
        if re.fullmatch(r"\d{6,25}", text) and step_id != "final":
            return ("config_id" if step_id == "config" else "app_id"), text
    elif platform == "tiktok" and step_id != "config":
        if re.fullmatch(r"(sb)?aw[A-Za-z0-9]{8,20}", text):  # keys are short, secrets 30+
            return "client_key", text
        if re.fullmatch(r"[A-Za-z0-9]{30,64}", text):
            return "client_secret", text
    return None


def google_json(newer_than=0):
    """(keys, file) from the newest Desktop-app key file Google's “Download JSON” put in Downloads, or None."""
    try:
        files = sorted((p for p in DOWNLOADS.glob("client_secret*.json") if p.stat().st_mtime >= newer_than),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:  # no access to Downloads: the boxes still work
        return None
    for path in files:
        try:
            client = json.loads(path.read_text())["installed"]  # Desktop app; a "web" client can't sign in here
            return {"client_id": client["client_id"], "client_secret": client["client_secret"]}, path
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None
