"""Results Tracker: views, likes, comments and shares for every published post found in the receipts.
Read-only: it only asks the platforms for numbers and keeps the last ones in app data."""
from datetime import datetime
import json
from pathlib import Path

from app_paths import DATA_DIR, atomic_json
from .accounts import APIError, api_request, response_json
from .storage import NAMES, manifest

STATS = ("views", "likes", "comments", "shares")


def results_file():
    return DATA_DIR / "results.json"


def receipts(settings, extra=()):
    """Every post.json under the output folder (where Auto Poster saves renders) plus any given receipts."""
    found = []
    folder = Path(settings["output_folder"]).expanduser()
    if folder.is_dir():
        found += sorted(folder.rglob("post.json"))
    return list(dict.fromkeys(Path(p).resolve() for p in [*found, *extra]))


def posts(paths):
    """One row per published platform: which track, which ID to ask about, and the link."""
    rows = []
    for path in paths:
        try:
            record = manifest(path)
        except (OSError, ValueError):
            continue
        for platform, delivery in record.get("delivery", {}).items():
            if delivery.get("state") != "published":
                continue
            ids = {"youtube": [delivery.get("remote_id")], "instagram": [delivery.get("post_id")],
                   "tiktok": delivery.get("post_ids") or []}.get(platform, [])
            rows.append({"receipt": str(path), "track": Path(record.get("audio", path.parent.name)).name,
                         "title": record.get("title", ""), "platform": platform, "ids": [str(i) for i in ids if i],
                         "url": delivery.get("url", ""), "posted": delivery.get("updated", "")})
    return rows


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch(rows, accounts, settings):
    """Adds stats (or a plain-language error) to each row, asking each platform in as few requests as it allows."""
    for platform, reader in (("youtube", _youtube), ("instagram", _instagram), ("tiktok", _tiktok)):
        mine = [row for row in rows if row["platform"] == platform]
        for row in mine:
            if not row["ids"]:
                row["error"] = ("No public post ID saved (private TikTok posts have none)." if platform == "tiktok"
                                else "No post ID saved for this upload.")
        mine = [row for row in mine if row["ids"]]
        if not mine:
            continue
        try:
            profile = accounts.credentials(platform)
            reader(mine, profile, accounts, settings)
        except APIError as error:
            for row in mine:
                row.setdefault("error", f"{error} Reconnect {NAMES[platform]} in Accounts to allow reading stats.")
    stamp = datetime.now().astimezone().isoformat(timespec="minutes")
    atomic_json(results_file(), {"fetched": stamp, "rows": rows})
    return rows


def _get(accounts, settings, method, url, token, **kwargs):
    return response_json(api_request(accounts.session, settings, method, url,
                                     headers={"Authorization": f"Bearer {token}"}, **kwargs))


def _youtube(rows, profile, accounts, settings):
    by_id = {row["ids"][0]: row for row in rows}
    ids = list(by_id)
    for start in range(0, len(ids), 50):  # videos.list takes up to 50 IDs
        result = _get(accounts, settings, "GET", settings["youtube_api_url"] + "/videos", profile["access_token"],
                      params={"part": "statistics", "id": ",".join(ids[start:start + 50])})
        for item in result.get("items", []):
            stats = item.get("statistics", {})
            by_id[item["id"]].update(views=_number(stats.get("viewCount")), likes=_number(stats.get("likeCount")),
                                     comments=_number(stats.get("commentCount")))
    for row in rows:
        if "views" not in row:
            row["error"] = "YouTube didn't return this video (deleted, or posted from another channel)."


def _instagram(rows, profile, accounts, settings):
    base, token = settings["instagram_api_url"], profile["access_token"]
    for row in rows:
        media = row["ids"][0]
        try:
            result = _get(accounts, settings, "GET", f"{base}/{media}", token,
                          params={"fields": "like_count,comments_count,permalink"})
        except APIError as error:
            row["error"] = str(error)
            continue
        row.update(likes=_number(result.get("like_count")), comments=_number(result.get("comments_count")),
                   url=result.get("permalink") or row["url"])
        try:  # views and shares need the instagram_manage_insights permission
            insights = _get(accounts, settings, "GET", f"{base}/{media}/insights", token,
                            params={"metric": "views,shares"})
            for metric in insights.get("data", []):
                value = (metric.get("values") or [{}])[0].get("value", metric.get("total_value", {}).get("value"))
                row[metric.get("name")] = _number(value)
        except APIError:
            row["note"] = "Views need Instagram insights permission: reconnect Instagram."


def _tiktok(rows, profile, accounts, settings):
    by_id = {}
    for row in rows:
        for video in row["ids"]:
            by_id[video] = row
    ids = list(by_id)
    for start in range(0, len(ids), 20):  # video/query takes up to 20 IDs
        result = _get(accounts, settings, "POST", settings["tiktok_api_url"] + "/video/query/", profile["access_token"],
                      params={"fields": "id,view_count,like_count,comment_count,share_count,share_url"},
                      json={"filters": {"video_ids": ids[start:start + 20]}})
        for video in result.get("data", {}).get("videos", []):
            row = by_id.get(str(video.get("id")))
            if row:
                for key, field in (("views", "view_count"), ("likes", "like_count"), ("comments", "comment_count"),
                                   ("shares", "share_count")):
                    row[key] = (row.get(key) or 0) + (_number(video.get(field)) or 0)
                row["url"] = video.get("share_url") or row["url"]
    for row in rows:
        if "views" not in row:
            row["error"] = "TikTok didn't return this video (deleted or made private)."


def last_results():
    try:
        saved = json.loads(results_file().read_text())
        return saved.get("fetched", ""), saved.get("rows", [])
    except (OSError, ValueError):
        return "", []


def by_track(rows):
    """[(track, totals, rows)] with the most-viewed beat first."""
    tracks = {}
    for row in rows:
        tracks.setdefault(row["receipt"], []).append(row)
    grouped = [(rows[0]["track"], {key: sum(r.get(key) or 0 for r in rows) for key in STATS}, rows) for rows in tracks.values()]
    return sorted(grouped, key=lambda item: item[1]["views"], reverse=True)
