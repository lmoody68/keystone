"""Calendar free/busy sync for KEYSTONE — connect-ready Google Calendar (+ Microsoft 365 / Graph) OAuth2.

Credentials come from calsync_config.json (gitignored); tokens in calsync_tokens.json (gitignored).
Until a calendar account is connected, KEYSTONE's Availability runs on modeled DEMO data; once connected,
engine.calendar()/directory() overlay REAL free/busy for employees whose email the connected account can see.

provider = "google" (default) or "microsoft".
Flow: /cal/connect -> provider consent -> /cal/callback -> tokens -> freebusy(emails, start, end).
Mirrors qbo.py (config/token files, near-expiry refresh). The live path needs Burns' own Google Workspace
or Microsoft 365 OAuth app; without it, connected() is False and KEYSTONE stays on demo availability.
"""
import json, time, urllib.request, urllib.parse, datetime as dt
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "calsync_config.json"
TOKENS = BASE / "calsync_tokens.json"

_PROVIDERS = {
    "google": {
        "auth": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "https://www.googleapis.com/auth/calendar.freebusy",
    },
    "microsoft": {
        "auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scope": "https://graph.microsoft.com/Calendars.Read offline_access",
    },
}


def config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    except Exception:
        return {}

def provider() -> str:
    return (config().get("provider") or "google").lower()

def _p() -> dict:
    return _PROVIDERS.get(provider(), _PROVIDERS["google"])

def configured() -> bool:
    c = config()
    return bool(c.get("client_id") and c.get("client_secret"))

def _redirect_uri() -> str:
    return config().get("redirect_uri", "http://localhost:8750/cal/callback")


def auth_url(state="keystone") -> str:
    c = config(); p = _p()
    params = {"client_id": c.get("client_id", ""), "response_type": "code",
              "scope": p["scope"], "redirect_uri": _redirect_uri(), "state": state}
    if provider() == "google":
        params.update(access_type="offline", prompt="consent")   # force a refresh_token
    return p["auth"] + "?" + urllib.parse.urlencode(params)


def _tokens() -> dict:
    try:
        return json.loads(TOKENS.read_text(encoding="utf-8")) if TOKENS.exists() else {}
    except Exception:
        return {}

def _save_tokens(t): TOKENS.write_text(json.dumps(t, indent=1), encoding="utf-8")

def _post_token(data: dict) -> dict:
    c = config()
    data = {**data, "client_id": c.get("client_id", ""), "client_secret": c.get("client_secret", "")}
    req = urllib.request.Request(_p()["token"], data=urllib.parse.urlencode(data).encode(), headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": "KEYSTONE/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def exchange_code(code: str) -> dict:
    t = _post_token({"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri()})
    t.update(provider=provider(), obtained_at=time.time())
    _save_tokens(t)
    return t

def refresh() -> dict:
    t = _tokens()
    if not t.get("refresh_token"):
        raise RuntimeError("not connected")
    nt = _post_token({"grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
    nt.setdefault("refresh_token", t["refresh_token"])   # Google omits refresh_token on refresh
    nt.update(provider=t.get("provider", provider()), obtained_at=time.time())
    _save_tokens(nt)
    return nt

def _access() -> str:
    t = _tokens()
    if not t.get("access_token"):
        raise RuntimeError("not connected")
    if time.time() - t.get("obtained_at", 0) > (t.get("expires_in", 3600) - 300):
        t = refresh()
    return t["access_token"]

def connected() -> bool:
    return bool(_tokens().get("access_token"))

def status() -> dict:
    t = _tokens()
    return {"configured": configured(), "connected": connected(), "provider": provider(),
            "redirect_uri": _redirect_uri(),
            "token_age_min": round((time.time() - t["obtained_at"]) / 60) if t.get("obtained_at") else None}


def freebusy(emails, start=None, end=None) -> dict:
    """Return {email: [{start,end}, ...]} busy blocks in the window (ISO8601). Best-effort per provider."""
    emails = [e for e in (emails or []) if e]
    if not emails:
        return {}
    now = dt.datetime.now(dt.timezone.utc)
    tmin = (start or now).isoformat()
    tmax = (end or (now + dt.timedelta(days=7))).isoformat()
    token = _access()
    if provider() == "microsoft":
        return _ms_schedule(token, emails, tmin, tmax)
    body = json.dumps({"timeMin": tmin, "timeMax": tmax, "items": [{"id": e} for e in emails]}).encode()
    req = urllib.request.Request("https://www.googleapis.com/calendar/v3/freeBusy", data=body, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "KEYSTONE/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        cals = json.loads(r.read().decode()).get("calendars", {})
    return {e: [{"start": b["start"], "end": b["end"]} for b in cals.get(e, {}).get("busy", [])] for e in emails}


def _ms_schedule(token, emails, tmin, tmax) -> dict:
    body = json.dumps({"schedules": emails, "startTime": {"dateTime": tmin, "timeZone": "UTC"},
                       "endTime": {"dateTime": tmax, "timeZone": "UTC"}, "availabilityViewInterval": 30}).encode()
    req = urllib.request.Request("https://graph.microsoft.com/v1.0/me/calendar/getSchedule", data=body, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "KEYSTONE/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        val = json.loads(r.read().decode()).get("value", [])
    out = {}
    for item in val:
        out[item.get("scheduleId")] = [{"start": it.get("start", {}).get("dateTime"),
                                        "end": it.get("end", {}).get("dateTime")}
                                       for it in item.get("scheduleItems", [])]
    return out
