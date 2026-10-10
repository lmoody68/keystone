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


# ---- WRITE-BACK (the accounting scope is read+write — these create real records in QB) ----
def post(entity: str, body: dict) -> dict:
    token, realm = _access()
    url = f"{api_base()}/v3/company/{realm}/{entity.lower()}?minorversion=65"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json",
        "Content-Type": "application/json", "User-Agent": "KEYSTONE/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def _find_one(entity: str, field: str, value: str):
    token, realm = _access()
    v = str(value).replace("'", "''")
    q = urllib.parse.quote(f"SELECT * FROM {entity} WHERE {field} = '{v}'")
    url = f"{api_base()}/v3/company/{realm}/query?query={q}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "KEYSTONE/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.loads(r.read().decode()).get("QueryResponse", {}).get(entity, [])
    return rows[0] if rows else None

def find_or_create_customer(name: str) -> str:
    c = _find_one("Customer", "DisplayName", name)
    return c["Id"] if c else post("Customer", {"DisplayName": name})["Customer"]["Id"]

def find_or_create_vendor(name: str) -> str:
    v = _find_one("Vendor", "DisplayName", name)
    return v["Id"] if v else post("Vendor", {"DisplayName": name})["Vendor"]["Id"]

def _first_item_id() -> str:
    items = query("Item", 20)
    for it in items:
        if it.get("Type") in ("Service", "NonInventory"):
            return it["Id"]
    return items[0]["Id"] if items else None

def _first_expense_account_id() -> str:
    accts = query("Account", 200)
    for a in accts:
        if a.get("AccountType") in ("Expense", "Cost of Goods Sold", "Other Expense"):
            return a["Id"]
    return accts[0]["Id"] if accts else None

def _first_purchasable_item_id() -> str:
    items = query("Item", 50)
    for it in items:                      # inventory/non-inventory items are purchasable; services often are not
        if it.get("Type") == "Inventory" and it.get("PurchaseCost") is not None:
            return it["Id"]
    for it in items:
        if it.get("Type") in ("Inventory", "NonInventory"):
            return it["Id"]
    return items[0]["Id"] if items else None

def create_invoice(customer: str, amount, description: str = "") -> dict:
    cid = find_or_create_customer(customer)
    iid = _first_item_id()
    body = {"CustomerRef": {"value": cid},
            "Line": [{"Amount": round(float(amount), 2), "DetailType": "SalesItemLineDetail",
                      "Description": description or "KEYSTONE invoice",
                      "SalesItemLineDetail": {"ItemRef": {"value": iid}}}]}
    inv = post("Invoice", body)["Invoice"]
    return {"id": inv.get("Id"), "doc": inv.get("DocNumber"), "total": inv.get("TotalAmt")}

def create_bill(vendor: str, amount, description: str = "") -> dict:
    vid = find_or_create_vendor(vendor)
    aid = _first_expense_account_id()
    body = {"VendorRef": {"value": vid},
            "Line": [{"Amount": round(float(amount), 2), "DetailType": "AccountBasedExpenseLineDetail",
                      "Description": description or "KEYSTONE bill",
                      "AccountBasedExpenseLineDetail": {"AccountRef": {"value": aid}}}]}
    bill = post("Bill", body)["Bill"]
    return {"id": bill.get("Id"), "doc": bill.get("DocNumber"), "total": bill.get("TotalAmt")}

def _oldest_open_invoice(customer_id: str):
    token, realm = _access()
    q = urllib.parse.quote(f"SELECT * FROM Invoice WHERE CustomerRef = '{customer_id}'")
    url = f"{api_base()}/v3/company/{realm}/query?query={q}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "KEYSTONE/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        invs = json.loads(r.read().decode()).get("QueryResponse", {}).get("Invoice", [])
    opens = [i for i in invs if float(i.get("Balance", 0) or 0) > 0.009]
    opens.sort(key=lambda i: i.get("TxnDate", ""))
    return opens[0] if opens else None

def create_payment(customer: str, amount, description: str = "") -> dict:
    """Record a customer payment received; apply to their oldest open invoice when there is one."""
    cid = find_or_create_customer(customer)
    amt = round(float(amount), 2)
    body = {"CustomerRef": {"value": cid}, "TotalAmt": amt}
    inv = _oldest_open_invoice(cid)
    applied = 0.0
    if inv:
        applied = min(amt, round(float(inv.get("Balance", 0) or 0), 2))
        if applied > 0:
            body["Line"] = [{"Amount": applied, "LinkedTxn": [{"TxnId": inv["Id"], "TxnType": "Invoice"}]}]
    pay = post("Payment", body)["Payment"]
    return {"id": pay.get("Id"), "doc": pay.get("DocNumber") or pay.get("Id"),
            "total": pay.get("TotalAmt"), "applied": applied, "invoice": (inv or {}).get("DocNumber")}

def create_po(vendor: str, amount, description: str = "") -> dict:
    vid = find_or_create_vendor(vendor)
    body = {"VendorRef": {"value": vid},
            "Line": [{"Amount": round(float(amount), 2), "DetailType": "AccountBasedExpenseLineDetail",
                      "Description": description or "KEYSTONE purchase order",
                      "AccountBasedExpenseLineDetail": {"AccountRef": {"value": _first_expense_account_id()}}}]}
    po = post("PurchaseOrder", body)["PurchaseOrder"]
    return {"id": po.get("Id"), "doc": po.get("DocNumber"), "total": po.get("TotalAmt")}
