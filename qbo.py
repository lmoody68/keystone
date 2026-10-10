"""QuickBooks Online OAuth2 + API client for KEYSTONE.

Credentials come from qbo_config.json (gitignored); tokens are stored in qbo_tokens.json (gitignored).
Defaults to the Intuit SANDBOX. Flow: /qb/connect -> Intuit consent -> /qb/callback -> tokens -> query().
"""
import json, time, base64, urllib.request, urllib.parse, urllib.error
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "qbo_config.json"
TOKENS = BASE / "qbo_tokens.json"

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPE = "com.intuit.quickbooks.accounting"


def config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.exists() else {}
    except Exception:
        return {}

def configured() -> bool:
    c = config()
    return bool(c.get("client_id") and c.get("client_secret"))

def _redirect_uri(): return config().get("redirect_uri", "http://localhost:8750/qb/callback")
def _env():          return config().get("environment", "sandbox")
def api_base():      return "https://sandbox-quickbooks.api.intuit.com" if _env() == "sandbox" else "https://quickbooks.api.intuit.com"


def auth_url(state="keystone") -> str:
    c = config()
    return AUTH_URL + "?" + urllib.parse.urlencode(
        {"client_id": c.get("client_id", ""), "response_type": "code", "scope": SCOPE,
         "redirect_uri": _redirect_uri(), "state": state})


def _tokens() -> dict:
    try:
        return json.loads(TOKENS.read_text(encoding="utf-8")) if TOKENS.exists() else {}
    except Exception:
        return {}

def _save_tokens(t): TOKENS.write_text(json.dumps(t, indent=1), encoding="utf-8")

def _basic() -> str:
    c = config()
    return base64.b64encode(f"{c['client_id']}:{c['client_secret']}".encode()).decode()

def _post_token(data: dict) -> dict:
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(data).encode(), headers={
        "Authorization": f"Basic {_basic()}", "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json", "User-Agent": "KEYSTONE/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def exchange_code(code: str, realm_id: str) -> dict:
    t = _post_token({"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri()})
    t.update(realm_id=realm_id, obtained_at=time.time())
    _save_tokens(t)
    return t

def refresh() -> dict:
    t = _tokens()
    if not t.get("refresh_token"):
        raise RuntimeError("not connected")
    nt = _post_token({"grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
    nt.update(realm_id=t.get("realm_id"), obtained_at=time.time())
    _save_tokens(nt)
    return nt

def _access():
    t = _tokens()
    if not t.get("access_token"):
        raise RuntimeError("not connected")
    if time.time() - t.get("obtained_at", 0) > (t.get("expires_in", 3600) - 300):   # refresh if near expiry
        t = refresh()
    return t["access_token"], t["realm_id"]

def connected() -> bool:
    return bool(_tokens().get("access_token"))


def query(entity: str, maxresults: int = 100) -> list:
    token, realm = _access()
    q = urllib.parse.quote(f"SELECT * FROM {entity} MAXRESULTS {maxresults}")
    url = f"{api_base()}/v3/company/{realm}/query?query={q}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "KEYSTONE/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()).get("QueryResponse", {}).get(entity, [])


def status() -> dict:
    t = _tokens()
    return {"configured": configured(), "connected": connected(), "environment": _env(),
            "realm_id": t.get("realm_id"), "redirect_uri": _redirect_uri(),
            "token_age_min": round((time.time() - t["obtained_at"]) / 60) if t.get("obtained_at") else None}
