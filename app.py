# KEYSTONE — an AI-native ERP for The Burns Companies, with QuickBooks built in.
# FastAPI backend. All financial numbers come from engine.py (deterministic); the LLM narrates/extracts;
# OpenAI generates Design-Studio renderings. Company PROFILES are real (sourced); transactions are
# realistic-synthetic (labeled) since Burns is private.
import os, re, json, base64, urllib.request, urllib.error
from pathlib import Path
from fastapi import FastAPI, Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import engine
import qbo
import qbo_map

def _live(source):
    return source == "qb" and qbo.connected()

BASE = Path(__file__).resolve().parent
app = FastAPI(title="KEYSTONE ERP")

GROQ_MODEL = os.getenv("KS_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_IMG_URL = "https://api.openai.com/v1/images/generations"
IMG_MODEL = os.getenv("KS_IMG_MODEL", "gpt-image-1")
_KEY_FILES = [Path(r"C:\Users\lesli\Documents\API_Keys.txt"),
              Path.home() / "Documents" / "API_Keys.txt"]


def _scan_key(env_name, pattern):
    k = os.getenv(env_name)
    if k:
        return k.strip()
    for p in _KEY_FILES:
        try:
            if p.exists():
                m = re.search(pattern, p.read_text(encoding="utf-8", errors="ignore"))
                if m:
                    return m.group(0)
        except Exception:
            pass
    return None

def groq_key():   return _scan_key("GROQ_API_KEY",  r"gsk_[A-Za-z0-9_\-]{20,}")
def openai_key(): return _scan_key("OPENAI_API_KEY", r"sk-proj-[A-Za-z0-9_\-]{20,}")


# ---------------- AI COPILOT (grounded narration) ----------------
def _r(n):
    return round(n) if isinstance(n, (int, float)) else n

def _facts(co=None):
    s = engine.snapshot(co)
    jobs = [{"job": j["name"], "co": j["company_name"], "cust": j["customer"], "contract": _r(j["contract"]),
             "pct": j["pct_complete"], "cost": _r(j["actual_cost"]), "cost_var": _r(j["cost_variance"]),
             "margin_pct": j["proj_margin_pct"], "over_budget": j["over_budget"], "unbilled": _r(j["under_billed"])}
            for j in engine.job_costing(co)]
    fl = s["flags"]
    flags = {"over_budget": [{"job": j["name"], "co": j["company_name"], "over_by": _r(j["cost_variance"])} for j in fl["over_budget_jobs"]],
             "overdue_invoices": [{"co": r["company_name"], "cust": r["customer"], "amount": _r(r["amount"]), "days_past_due": r["days_past_due"]} for r in fl["overdue_invoices"][:8]],
             "slow_payers": [{"cust": x["customer"], "co": x["company"], "overdue": _r(x["overdue"])} for x in fl["slow_payers"]],
             "under_billed": [{"job": j["name"], "co": j["company_name"], "amount": _r(j["under_billed"])} for j in fl["under_billed_jobs"]]}
    comp = [{"co": c["name"], "industry": c["industry"], "active_jobs": c["active_jobs"], "contract_value": _r(c["contract_value"]),
             "unbilled": _r(c["under_billed"]), "open_ar": _r(c["open_ar"]), "overdue": _r(c["overdue_ar"])} for c in s["companies"]]
    return {"as_of": s["as_of"], "holding": s["holding"]["name"], "group": s["group"], "companies": comp,
            "dso": s["dso"], "aging": s["aging"], "ap": {"open_ap": s["ap"]["open_ap"], "overdue_ap": s["ap"]["overdue_ap"], "aging": s["ap"]["aging"]},
            "inventory": {"value": s["inventory"]["total_value"], "low_stock": [i["item"] for i in s["inventory"]["low_stock"]]},
            "purchase_orders": {"open": s["po"]["count"], "committed": s["po"]["committed"]},
            "flags": flags, "jobs": jobs, "invoice_drafts": engine.invoice_drafts(co)}

SYSTEM = (
    "You are KEYSTONE — the AI copilot inside an ERP for The Burns Companies (a multi-company group: general "
    "contracting, hardscaping, pools, graphics/signage, and wholesale+retail seafood, running on QuickBooks). "
    "MULTILINGUAL: detect the language of the user's message and write your ENTIRE reply in that SAME language — the "
    "FACTS are in English, so translate your answer (but keep company/app/product names, invoice IDs, and $ amounts "
    "as-is). For a write-back ACTION, still output the JSON exactly as specified with English field names and values, "
    "but write the \"summary\" in the user's language. "
    "Answer using ONLY the FACTS JSON — every number is pre-computed and authoritative; quote it, never invent or "
    "recompute. Be concise and sharp like a construction financial controller. Use $ and round sensibly ($44K, $1.07M). "
    "Name the specific company/job/customer and the action to take. Lead with the headline number, then short bullets "
    "('- '). No markdown tables (the panel is narrow). If it's not in FACTS, say so briefly.\n\n"
    "ACTIONS (write-back — you can DO things, not just report): if the user clearly asks you to perform one of the "
    "operations below, respond with ONLY a JSON object (no prose, no code fence, no markdown): "
    "{\"action\":\"<name>\",\"params\":{...},\"summary\":\"<one sentence saying exactly what you will do>\"}. "
    "The five actions and their params:\n"
    "- create_invoice {customer, amount, description} — raise a customer invoice.\n"
    "- create_bill {vendor, amount, description} — post a vendor bill you owe.\n"
    "- record_payment {customer, amount} — record a customer payment received (applied to their open invoice).\n"
    "- create_po {vendor, amount, description} — raise a purchase order to a vendor.\n"
    "- send_reminder {customer, amount, days} — send a payment reminder to an overdue customer "
    "(amount = their overdue balance, days = days past due, both from FACTS).\n"
    "Use the amount and customer/vendor the user states explicitly; otherwise pull them from FACTS when the user "
    "refers to a specific job, draft, invoice, overdue item, or slow payer. Only when NEITHER the user NOR FACTS "
    "gives you an amount should you ask instead of acting — never make up a number the user didn't give. "
    "If the user says something like 'remind everyone overdue', pick the single largest overdue customer and send_reminder "
    "for them (one action per turn). For every other request (questions, summaries), answer in prose as usual (no JSON)."
    "\n\nFACTS:\n"
)

def _groq(messages, max_tokens=1500):
    key = groq_key()
    if not key:
        raise RuntimeError("no_key")
    body = json.dumps({"model": GROQ_MODEL, "messages": messages, "temperature": 0.2,
                       "max_tokens": max_tokens, "reasoning_effort": "low"}).encode()
    req = urllib.request.Request(GROQ_URL, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "curl/8.5.0"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"].strip()

def _facts_live():
    """Lean grounding facts built from the LIVE QuickBooks company (used when source=qb)."""
    o = qbo_map.overview()
    a = qbo_map.ar()
    custs = qbo_map.customers()[:10]
    vends = qbo_map.vendors()[:8]
    return {
        "as_of": o["as_of"], "source": "LIVE QuickBooks Online", "company": o["holding"]["name"],
        "group": o["group"], "dso": o["dso"]["overall"], "ar_aging": o["aging"]["overall"],
        "ap": {"open_ap": o["ap"]["open_ap"], "overdue_ap": o["ap"]["overdue_ap"], "aging": o["ap"]["aging"]},
        "inventory": {"value": o["inventory"]["total_value"], "low_stock": [i["item"] for i in o["inventory"]["low_stock"]]},
        "purchase_orders": {"open": o["po"]["count"], "committed": o["po"]["committed"]},
        "top_open_invoices": [{"id": r["id"], "cust": r["customer"], "amount": _r(r["amount"]),
                               "days_past_due": r["days_past_due"]} for r in a["open"][:10]],
        "overdue_invoices": [{"cust": r["customer"], "amount": _r(r["amount"]), "days_past_due": r["days_past_due"]}
                             for r in o["flags"]["overdue_invoices"][:8]],
        "slow_payers": o["flags"]["slow_payers"],
        "top_customers": [{"cust": c["customer"], "invoiced": _r(c["invoiced"]), "open": _r(c["open"]),
                           "overdue": _r(c["overdue"])} for c in custs],
        "top_vendors": [{"vendor": v["vendor"], "billed": _r(v["billed"]), "open": _r(v["open"])} for v in vends],
        "note": "This is a live QuickBooks company — it has no construction job-costing/budget data, so "
                "job-margin and over-budget questions have no live data to answer from.",
    }

def groq_chat(user, co=None, source=None):
    if _live(source):
        facts = _facts_live()
        scope = "(CURRENT SCOPE: LIVE QuickBooks Online company — every figure below is REAL data pulled from QuickBooks right now.)\n"
    else:
        facts = _facts(co)
        scope = (f"(CURRENT SCOPE: {engine._co_name(co)} ONLY — every figure below is for this one company, "
                 f"not the group.)\n") if co else ""
    return _groq([{"role": "system", "content": SYSTEM + scope + json.dumps(facts, default=str)},
                  {"role": "user", "content": user}])

def draft_reminder(customer, amount, days):
    """AI-draft a short, professional payment-reminder email for an overdue customer."""
    tone = "firm but courteous" if (float(days or 0) > 45) else "polite"
    msgs = [{"role": "system", "content":
             "You are the Accounts Receivable manager at The Burns Companies. Write a SHORT, professional, "
             f"{tone} payment-reminder email as plain text (90 words max). State the amount outstanding and how many "
             "days past due it is, give a clear call to pay, and offer to help if there's a question. "
             "Sign off 'Accounts Receivable, The Burns Companies'. Output ONLY the email body."},
            {"role": "user", "content": f"Customer: {customer}. Amount overdue: ${float(amount or 0):,.2f}. Days past due: {days}."}]
    return _groq(msgs, max_tokens=320)


# ---------------- EMAIL -> ERP (inbound AP automation) ----------------
SAMPLE_EMAILS = [
    {"id": "e1", "from": "ar@abcconcretesupply.com", "subject": "Invoice #AC-4821 — concrete delivery",
     "received": "today",
     "body": "Hi,\n\nPlease find invoice AC-4821 for ready-mix concrete delivered 10/06 to the Henderson pool project "
             "(PO 7003). 42 cubic yards @ $168/yd = $7,056.00, delivery $450. Total due: $7,506.00, Net 30.\n\nThanks,\nABC Concrete Supply"},
    {"id": "e2", "from": "billing@unitedrentals.com", "subject": "United Rentals — Invoice 90233117",
     "received": "today",
     "body": "Invoice 90233117. Equipment rental (mini excavator + skid steer) for the Gateway Retail Buildout, "
             "week of 09/29. Amount: $3,284.50. Terms Net 30. Job ref: Wayne Contracting / Gateway."},
    {"id": "e3", "from": "orders@gulfcoastseafood.com", "subject": "Gulf Coast Seafood — PO fulfillment INV-55120",
     "received": "yesterday",
     "body": "Your order INV-55120 shipped to City Seafood (Fenton). 400 lb salmon, 250 lb Gulf shrimp. "
             "Total $9,420.00. Net 15. Thank you for your business."},
    {"id": "e4", "from": "facturation@quebec-beton.ca", "subject": "Facture QB-7781 — livraison de béton",
     "received": "today",
     "body": "Bonjour,\n\nVeuillez trouver la facture QB-7781 pour le béton livré le 06/10 au projet de piscine "
             "Henderson (bon de commande 7003). 38 mètres cubes à 185 $/m³ = 7 030,00 $, plus 480 $ de livraison. "
             "Total dû : 7 510,00 $, net 30 jours.\n\nMerci de votre confiance,\nQuébec Béton inc."},
    {"id": "e5", "from": "cobros@aceros-delnorte.mx", "subject": "Factura AN-4420 — varilla de refuerzo",
     "received": "yesterday",
     "body": "Estimados,\n\nAdjunto la factura AN-4420 por varilla de refuerzo entregada a la obra Gateway "
             "(Wayne Contracting). 12 toneladas por un total de $8,640.00 USD. Condiciones: neto 30 días.\n\n"
             "Gracias,\nAceros del Norte S.A."},
]

def groq_extract(body):
    msgs = [{"role": "system", "content":
             "Extract the vendor invoice into STRICT JSON with keys: vendor, invoice_number, amount (number), "
             "terms_days (number), suggested_company (one of: Wayne Contracting, Meramec Hardscapes, Meramec Pools "
             "& Outdoors, Vizex, City Seafood (Wholesale), City Seafood (Retail)), suggested_job (string or null), "
             "suggested_gl (a construction GL account like 'Job Materials', 'Equipment Rental', 'Subcontractors', "
             "'COGS - Seafood'), confidence (0-1). Output ONLY the JSON object."},
            {"role": "user", "content": body}]
    raw = _groq(msgs, max_tokens=500)
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {"error": "could not parse", "raw": raw[:200]}


# ---------------- DESIGN STUDIO (AI renderings) ----------------
def generate_rendering(prompt):
    key = openai_key()
    if not key:
        raise RuntimeError("no_key")
    full = ("Photorealistic architectural visualization rendering of a residential backyard. " + prompt.strip() +
            ". Professional landscape-design presentation, natural daylight, realistic water, stone and decking "
            "materials, lush landscaping, high detail, wide-angle. No text or watermarks.")
    body = json.dumps({"model": IMG_MODEL, "prompt": full, "size": "1024x1024", "n": 1}).encode()
    req = urllib.request.Request(OPENAI_IMG_URL, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "curl/8.5.0"})
    with urllib.request.urlopen(req, timeout=150) as r:
        out = json.loads(r.read().decode())
    d0 = out["data"][0]
    if d0.get("b64_json"):
        return "data:image/png;base64," + d0["b64_json"]
    return d0.get("url", "")


def _multipart(fields, files):
    """Build a multipart/form-data body (no deps). files: list of (name, filename, content_type, bytes)."""
    boundary = "----KeystoneBoundary" + os.urandom(10).hex()
    buf = bytearray()
    for k, v in fields.items():
        buf += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, filename, ctype, data in files:
        buf += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
        buf += data + b"\r\n"
    buf += f"--{boundary}--\r\n".encode()
    return boundary, bytes(buf)


def render_on_photo(image_bytes, ctype, prompt):
    """img2img: add the described pool/outdoor design onto the client's actual backyard photo (OpenAI edits)."""
    key = openai_key()
    if not key:
        raise RuntimeError("no_key")
    full = ("Transform this backyard photo into a photorealistic professional landscape-design rendering. "
            "Keep the existing house and overall setting, and add: " + prompt.strip() +
            ". Integrate the pool and features naturally with realistic water, stone and decking materials, "
            "and landscaping that matches the yard. No text or watermarks.")
    ext = "png" if "png" in ctype else ("webp" if "webp" in ctype else "jpg")
    boundary, body = _multipart(
        {"model": IMG_MODEL, "prompt": full, "size": "1024x1024", "n": "1"},
        [("image", f"backyard.{ext}", ctype, image_bytes)])
    req = urllib.request.Request("https://api.openai.com/v1/images/edits", data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "curl/8.5.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.loads(r.read().decode())
    d0 = out["data"][0]
    return "data:image/png;base64," + d0["b64_json"] if d0.get("b64_json") else d0.get("url", "")


# ---------------- ROUTES ----------------
@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE / "index.html").read_text(encoding="utf-8")

@app.get("/api/health")
def health():
    return {"ok": True, "app": "KEYSTONE", "companies": len(engine.load()["companies"]),
            "llm_ready": bool(groq_key()), "image_ready": bool(openai_key()),
            "qb_connected": qbo.connected(), "as_of": engine.load()["as_of"]}

@app.get("/api/overview")
def overview(company: str = None, source: str = None):
    return qbo_map.overview() if _live(source) else engine.snapshot(company or None)

@app.get("/api/jobs")
def jobs(company: str = None, source: str = None):
    return {"jobs": qbo_map.jobs() if _live(source) else engine.job_costing(company or None)}

@app.get("/api/ar")
def ar(company: str = None, source: str = None):
    if _live(source):
        return qbo_map.ar()
    co = company or None
    return {"aging": engine.ar_aging(co), "dso": engine.dso(co),
            "open": sorted(engine.ar_detail(co), key=lambda r: r["amount"], reverse=True),
            "drafts": engine.invoice_drafts(co)}

@app.get("/api/ap")
def ap(company: str = None, source: str = None):
    return qbo_map.ap() if _live(source) else engine.ap_summary(company or None)

@app.get("/api/inventory")
def inventory(company: str = None, source: str = None):
    return qbo_map.inventory() if _live(source) else engine.inventory_status(company or None)

@app.get("/api/po")
def po(company: str = None, source: str = None):
    return qbo_map.po() if _live(source) else engine.po_open(company or None)

@app.get("/api/crm")
def crm(company: str = None, source: str = None):
    return {"customers": qbo_map.customers() if _live(source) else engine.customer_rollup(company or None)}

@app.get("/api/vendors")
def vendors(company: str = None, source: str = None):
    return {"vendors": qbo_map.vendors() if _live(source) else engine.vendor_rollup(company or None)}

@app.get("/api/companies")
def companies(): return {"profiles": engine.profiles(), "rollup": engine.company_rollup()}

@app.get("/api/qbsync")
def qbsync():
    d = engine.load()
    st = qbo.status()
    pending = {"invoice_drafts": len(engine.invoice_drafts()),
               "bills_to_post": sum(1 for b in d["bills"] if b["status"] == "open")}
    if st["connected"]:
        try:
            cust = qbo.query("Customer"); inv = qbo.query("Invoice"); item = qbo.query("Item")
            vend = qbo.query("Vendor"); bill = qbo.query("Bill")
            return {"connected": True, "live": True, "environment": f"QuickBooks Online — {st['environment'].upper()} (LIVE)",
                    "realm": st["realm_id"], "last_sync": "just now",
                    "synced": {"customers": len(cust), "vendors": len(vend), "invoices": len(inv), "items": len(item), "bills": len(bill)},
                    "pending_push": pending,
                    "note": "Connected to a REAL QuickBooks Online company via OAuth2 — the counts above are pulled live from QuickBooks."}
        except Exception as e:
            return {"connected": True, "live": False, "error": f"{type(e).__name__}: {e}", "realm": st["realm_id"],
                    "environment": f"QuickBooks Online — {st['environment']}", "synced": {}, "pending_push": pending,
                    "note": "Connected, but a live query failed — try Connect again to refresh the token."}
    return {"connected": False, "configured": st["configured"], "live": False,
            "environment": "QuickBooks Online — not connected (showing demo data)",
            "connect_url": "/qb/connect", "redirect_uri": st["redirect_uri"],
            "realm": "demo — 6 modeled company files", "last_sync": d["as_of"] + " (demo)",
            "synced": {"customers": len(engine.customer_rollup()), "vendors": len(engine.vendor_rollup()),
                       "invoices": len(d["invoices"]), "bills": len(d["bills"]), "items": len(d.get("inventory", []))},
            "pending_push": pending,
            "note": ("Add your Intuit client_id/client_secret to qbo_config.json, then click Connect to link a real "
                     "QuickBooks Online sandbox via OAuth2. Until then, KEYSTONE runs on modeled demo data.")}


@app.get("/qb/connect")
def qb_connect():
    if not qbo.configured():
        return HTMLResponse("<h3>⚠️ Add your Intuit <b>client_id</b> and <b>client_secret</b> to "
                            "<code>qbo_config.json</code> first, then try Connect again.</h3>")
    return RedirectResponse(qbo.auth_url())


@app.get("/qb/callback", response_class=HTMLResponse)
def qb_callback(code: str = None, realmId: str = None, state: str = None, error: str = None):
    if error:
        return f"<h3>QuickBooks connection failed: {error}</h3>"
    if not code or not realmId:
        return "<h3>Missing authorization code or realmId from Intuit.</h3>"
    try:
        qbo.exchange_code(code, realmId)
        return ("<h2 style='font-family:system-ui'>&#9989; QuickBooks connected!</h2>"
                f"<p style='font-family:system-ui'>Company (realm) {realmId} linked. Returning to KEYSTONE…</p>"
                "<script>setTimeout(()=>{window.location='/'},2500)</script>")
    except Exception as e:
        return f"<h3>Token exchange failed: {type(e).__name__}: {e}</h3>"

@app.get("/api/email")
def email():     return {"inbox": [{"id": e["id"], "from": e["from"], "subject": e["subject"],
                                    "received": e["received"], "body": e["body"]} for e in SAMPLE_EMAILS]}

@app.post("/api/email/extract")
def email_extract(data: dict = Body(...)):
    body = (data.get("body") or "").strip()[:2000]
    if not body:
        return JSONResponse({"error": "no email body"}, status_code=400)
    try:
        return {"bill": groq_extract(body)}
    except RuntimeError:
        return {"bill": {"error": "No Groq key — set GROQ_API_KEY."}}
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)

@app.post("/api/render")
def render(data: dict = Body(...)):
    prompt = (data.get("prompt") or "").strip()[:800]
    if not prompt:
        return JSONResponse({"error": "describe the design"}, status_code=400)
    try:
        return {"image": generate_rendering(prompt)}
    except RuntimeError:
        return JSONResponse({"error": "No OpenAI key found for image generation."}, status_code=503)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        detail = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else str(e)
        return JSONResponse({"error": f"image model error: {detail}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)

@app.post("/api/render_edit")
def render_edit(data: dict = Body(...)):
    prompt = (data.get("prompt") or "").strip()[:800]
    img = data.get("image") or ""
    if "," not in img:
        return JSONResponse({"error": "upload a backyard photo"}, status_code=400)
    try:
        head, b64 = img.split(",", 1)
        ctype = "image/png" if "png" in head else ("image/webp" if "webp" in head else "image/jpeg")
        return {"image": render_on_photo(base64.b64decode(b64), ctype, prompt)}
    except RuntimeError:
        return JSONResponse({"error": "No OpenAI key found."}, status_code=503)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        detail = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else str(e)
        return JSONResponse({"error": f"image model error: {detail}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)

@app.post("/api/ask")
def ask(data: dict = Body(...)):
    q = (data.get("question") or "").strip()[:500]
    co = data.get("company") or None
    source = data.get("source") or None
    if not q:
        return JSONResponse({"error": "Ask a question about the books."}, status_code=400)
    try:
        raw = groq_chat(q, co, source)
        m = re.search(r"\{.*\}", raw, re.S)   # did the copilot propose a write-back action?
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict) and obj.get("action") in ACTIONS and obj.get("params"):
                    return {"action": obj}
            except Exception:
                pass
        return {"answer": raw}
    except RuntimeError:
        return {"answer": "⚠️ No Groq API key found — the dashboard numbers are still live."}
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        return {"answer": f"⚠️ The AI model is unreachable ({type(e).__name__}). The numbers are still live."}
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


QB_WRITES = ("create_invoice", "create_bill", "record_payment", "create_po")
ACTIONS = QB_WRITES + ("send_reminder",)

@app.post("/api/action/execute")
def action_execute(data: dict = Body(...)):
    act = (data.get("action") or "").strip()
    p = data.get("params") or {}
    amt = float(p.get("amount") or 0)

    # send_reminder is not a QuickBooks write — it drafts & queues a dunning email (works in demo too)
    if act == "send_reminder":
        cust = p.get("customer", "the customer")
        days = p.get("days")
        try:
            draft = draft_reminder(cust, amt, days)
        except Exception:
            draft = (f"Dear {cust},\n\nOur records show ${amt:,.2f} is {days or 'some'} days past due. "
                     "We'd appreciate payment at your earliest convenience — please reach out if you have any "
                     "questions.\n\nAccounts Receivable, The Burns Companies")
        return {"ok": True, "message": f"✉️ **Payment reminder queued for {cust}** — ${amt:,.0f}"
                + (f", {days} days overdue" if days else "") + f".\n\n_Draft:_\n{draft}"}

    if act not in QB_WRITES:
        return JSONResponse({"error": "unknown action"}, status_code=400)
    if not qbo.connected():
        return {"ok": True, "demo": True,
                "message": f"(Demo) Would {act.replace('_', ' ')} for ${amt:,.0f} — "
                           f"{p.get('customer') or p.get('vendor') or ''}. Connect QuickBooks to post for real."}
    try:
        if act == "create_invoice":
            r = qbo.create_invoice(p.get("customer", ""), amt, p.get("description", ""))
            return {"ok": True, "message": f"✅ **Invoice #{r['doc']}** created in QuickBooks — ${float(r['total']):,.2f} to **{p.get('customer')}**."}
        if act == "create_bill":
            r = qbo.create_bill(p.get("vendor", ""), amt, p.get("description", ""))
            return {"ok": True, "message": f"✅ **Bill #{r['doc']}** posted to QuickBooks — ${float(r['total']):,.2f} ({p.get('vendor')})."}
        if act == "record_payment":
            r = qbo.create_payment(p.get("customer", ""), amt, p.get("description", ""))
            where = f" applied to invoice #{r['invoice']}" if r.get("invoice") and r.get("applied") else " (recorded as unapplied credit)"
            return {"ok": True, "message": f"✅ **Payment recorded** in QuickBooks — ${float(r['total']):,.2f} from **{p.get('customer')}**{where}."}
        if act == "create_po":
            r = qbo.create_po(p.get("vendor", ""), amt, p.get("description", ""))
            return {"ok": True, "message": f"✅ **PO #{r['doc']}** raised in QuickBooks — ${float(r['total']):,.2f} to **{p.get('vendor')}**."}
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        detail = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else str(e)
        return JSONResponse({"error": f"QuickBooks rejected it: {detail}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


@app.post("/api/translate")
def translate(data: dict = Body(...)):
    text = (data.get("text") or "").strip()[:2000]
    to = (data.get("to") or "English").strip()[:40]
    if not text:
        return JSONResponse({"error": "Nothing to translate."}, status_code=400)
    msgs = [{"role": "system", "content":
             "You are a professional business interpreter for The Burns Companies. Detect the source language of the "
             f"user's text, then translate it faithfully and naturally into {to}. Preserve names, numbers, currency, "
             "and a professional tone. Respond with ONLY strict JSON (no other text): "
             "{\"source_language\":\"<source language name in English>\",\"translation\":\"<the " + to + " translation>\"}."},
            {"role": "user", "content": text}]
    try:
        raw = _groq(msgs, max_tokens=900)
        m = re.search(r"\{.*\}", raw, re.S)
        obj = json.loads(m.group(0)) if m else {}
        return {"source_language": obj.get("source_language", "?"), "translation": obj.get("translation", raw), "to": to}
    except RuntimeError:
        return JSONResponse({"error": "No Groq API key found."}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8750")))
