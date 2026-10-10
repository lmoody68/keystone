"""Deterministic analytics for KEYSTONE. Every figure is computed here from QuickBooks-shaped data.

Multi-entity: every function takes an optional `co` (company id, e.g. 'wayne'). Pass a company id to
scope to that ONE company, or None for the consolidated GROUP view. The LLM only narrates these numbers.
"""
import json, datetime as dt
from pathlib import Path

BASE = Path(__file__).resolve().parent
_DATA = None

def load():
    global _DATA
    if _DATA is None:
        _DATA = json.loads((BASE / "data.json").read_text(encoding="utf-8"))
    return _DATA

def _as_of():  return dt.date.fromisoformat(load()["as_of"])
def _co_name(cid): return next((c["name"] for c in load()["companies"] if c["id"] == cid), cid)
def _match(cid, co): return co is None or cid == co

def _days(iso_from, iso_to=None):
    a = dt.date.fromisoformat(iso_from)
    b = dt.date.fromisoformat(iso_to) if iso_to else _as_of()
    return (b - a).days

# ---------- JOB COSTING ----------
def job_costing(co=None):
    data = load()
    costs_by_job = {}
    for c in data["cost_entries"]:
        costs_by_job.setdefault(c["job_id"], {}).setdefault(c["type"], 0.0)
        costs_by_job[c["job_id"]][c["type"]] += c["amount"]
    billed_by_job = {}
    for inv in data["invoices"]:
        if inv.get("job_id"):
            billed_by_job[inv["job_id"]] = billed_by_job.get(inv["job_id"], 0.0) + inv["amount"]
    out = []
    for j in data["jobs"]:
        if not _match(j["company"], co):
            continue
        pct = j["pct_complete"] / 100.0
        actual = round(sum(costs_by_job.get(j["id"], {}).values()), 2)
        budget = j["budget_cost"]
        earned = round(j["contract_amount"] * pct, 2)
        billed = round(billed_by_job.get(j["id"], 0.0), 2)
        proj_cost = round(actual / pct, 2) if pct > 0 else budget
        proj_margin = round((j["contract_amount"] - proj_cost) / j["contract_amount"] * 100, 1) if j["contract_amount"] else 0
        out.append({"id": j["id"], "company": j["company"], "company_name": _co_name(j["company"]),
                    "name": j["name"], "customer": j["customer"], "contract": j["contract_amount"],
                    "pct_complete": j["pct_complete"], "budget_cost": budget, "actual_cost": actual,
                    "cost_variance": round(actual - budget * pct, 2), "earned_revenue": earned,
                    "billed_to_date": billed, "under_billed": round(max(0.0, earned - billed), 2),
                    "proj_cost_at_completion": proj_cost, "proj_margin_pct": proj_margin,
                    "over_budget": proj_cost > budget * 1.02})
    return out

# ---------- AR AGING / DSO ----------
_BUCKETS = ["current", "1-30", "31-60", "61-90", "90+"]
def _bucket(dpd):
    if dpd <= 0:  return "current"
    if dpd <= 30: return "1-30"
    if dpd <= 60: return "31-60"
    if dpd <= 90: return "61-90"
    return "90+"

def ar_detail(co=None):
    rows = []
    for inv in load()["invoices"]:
        if inv["status"] not in ("sent", "overdue") or not _match(inv["company"], co):
            continue
        dpd = _days(inv["due_date"])
        rows.append({**inv, "company_name": _co_name(inv["company"]),
                     "days_outstanding": _days(inv["issue_date"]), "days_past_due": max(0, dpd), "bucket": _bucket(dpd)})
    return rows

def ar_aging(co=None):
    rows = ar_detail(co)
    overall = {b: 0.0 for b in _BUCKETS}; by_co = {}
    for r in rows:
        overall[r["bucket"]] += r["amount"]
        by_co.setdefault(r["company"], {b: 0.0 for b in _BUCKETS})[r["bucket"]] += r["amount"]
    rnd = lambda d: {k: round(v, 2) for k, v in d.items()}
    return {"overall": rnd(overall), "by_company": {_co_name(c): rnd(v) for c, v in by_co.items()}}

def dso(co=None):
    data = load()
    def _calc(cid):
        rows = [r for r in ar_detail() if _match(r["company"], cid)]
        open_ar = sum(r["amount"] for r in rows)
        sales = sum(i["amount"] for i in data["invoices"] if _match(i["company"], cid) and _days(i["issue_date"]) <= 90)
        wd = sum(r["amount"] * r["days_outstanding"] for r in rows); tot = sum(r["amount"] for r in rows)
        return {"open_ar": round(open_ar, 2), "dso_days": round(open_ar / sales * 90, 1) if sales else 0,
                "avg_days_outstanding": round(wd / tot, 1) if tot else 0}
    overall = _calc(co)
    by_co = {_co_name(c["id"]): _calc(c["id"]) for c in data["companies"]} if co is None else {}
    return {"overall": overall, "by_company": by_co}

# ---------- ACCOUNTS PAYABLE ----------
def ap_detail(co=None):
    rows = []
    for b in load().get("bills", []):
        if b["status"] not in ("open", "overdue") or not _match(b["company"], co):
            continue
        dpd = _days(b["due_date"])
        rows.append({**b, "company_name": _co_name(b["company"]),
                     "days_outstanding": _days(b["issue_date"]), "days_past_due": max(0, dpd), "bucket": _bucket(dpd)})
    return rows

def ap_summary(co=None):
    rows = ap_detail(co)
    aging = {b: 0.0 for b in _BUCKETS}
    for r in rows:
        aging[r["bucket"]] += r["amount"]
    return {"open_ap": round(sum(r["amount"] for r in rows), 2),
            "overdue_ap": round(sum(r["amount"] for r in rows if r["status"] == "overdue"), 2),
            "bill_count": len(rows), "aging": {k: round(v, 2) for k, v in aging.items()},
            "bills": sorted(rows, key=lambda r: r["amount"], reverse=True)}

# ---------- INVENTORY ----------
def inventory_status(co=None):
    inv = [i for i in load().get("inventory", []) if _match(i["company"], co)]
    return {"items": inv, "total_value": round(sum(i["value"] for i in inv), 2),
            "low_stock": [i for i in inv if i["low_stock"]], "item_count": len(inv)}

# ---------- PURCHASE ORDERS ----------
def po_open(co=None):
    pos = [{**p, "company_name": _co_name(p["company"])}
           for p in load().get("purchase_orders", []) if p["status"] == "open" and _match(p["company"], co)]
    return {"open_pos": pos, "committed": round(sum(p["amount"] for p in pos), 2), "count": len(pos)}

# ---------- CRM / CUSTOMERS & VENDORS ----------
def customer_rollup(co=None):
    cust = {}
    for i in load()["invoices"]:
        if not _match(i["company"], co):
            continue
        c = cust.setdefault((i["company"], i["customer"]),
                            {"company": _co_name(i["company"]), "customer": i["customer"], "invoiced": 0.0, "open": 0.0, "overdue": 0.0})
        c["invoiced"] += i["amount"]
        if i["status"] in ("sent", "overdue"): c["open"] += i["amount"]
        if i["status"] == "overdue":           c["overdue"] += i["amount"]
    out = [{**v, "invoiced": round(v["invoiced"], 2), "open": round(v["open"], 2), "overdue": round(v["overdue"], 2)} for v in cust.values()]
    return sorted(out, key=lambda x: x["invoiced"], reverse=True)

def vendor_rollup(co=None):
    v = {}
    for b in load().get("bills", []):
        if not _match(b["company"], co):
            continue
        c = v.setdefault((b["company"], b["vendor"]), {"company": _co_name(b["company"]), "vendor": b["vendor"], "billed": 0.0, "open": 0.0})
        c["billed"] += b["amount"]
        if b["status"] in ("open", "overdue"): c["open"] += b["amount"]
    return sorted(({**x, "billed": round(x["billed"], 2), "open": round(x["open"], 2)} for x in v.values()),
                  key=lambda x: x["billed"], reverse=True)

# ---------- COMPANY ROLLUP ----------
def company_rollup(co=None):
    jc = job_costing(); ard = ar_detail(); apd = ap_detail()
    rows = []
    for c in load()["companies"]:
        if not _match(c["id"], co):
            continue
        cj = [j for j in jc if j["company"] == c["id"]]
        ar = [r for r in ard if r["company"] == c["id"]]
        ap = [r for r in apd if r["company"] == c["id"]]
        rows.append({"id": c["id"], "name": c["name"], "industry": c["industry"],
                     "active_jobs": len([j for j in cj if j["pct_complete"] < 100]),
                     "contract_value": round(sum(j["contract"] for j in cj), 2),
                     "cost_to_date": round(sum(j["actual_cost"] for j in cj), 2),
                     "under_billed": round(sum(j["under_billed"] for j in cj), 2),
                     "open_ar": round(sum(r["amount"] for r in ar), 2),
                     "overdue_ar": round(sum(r["amount"] for r in ar if r["status"] == "overdue"), 2),
                     "open_ap": round(sum(r["amount"] for r in ap), 2),
                     "overdue_ap": round(sum(r["amount"] for r in ap if r["status"] == "overdue"), 2),
                     "over_budget_jobs": len([j for j in cj if j["over_budget"]])})
    return rows

# ---------- FLAGS ----------
def flags(co=None):
    jc = job_costing(co); ar = ar_detail(co)
    over_budget = sorted([j for j in jc if j["over_budget"]], key=lambda j: j["cost_variance"], reverse=True)
    overdue = sorted([r for r in ar if r["status"] == "overdue"], key=lambda r: r["days_past_due"], reverse=True)
    by_cust = {}
    for r in ar:
        if r["status"] == "overdue":
            by_cust[(r["company_name"], r["customer"])] = by_cust.get((r["company_name"], r["customer"]), 0.0) + r["amount"]
    slow = sorted(({"company": k[0], "customer": k[1], "overdue": round(v, 2)} for k, v in by_cust.items()),
                  key=lambda x: x["overdue"], reverse=True)
    under_billed = sorted([j for j in jc if j["under_billed"] > 1000], key=lambda j: j["under_billed"], reverse=True)
    return {"over_budget_jobs": over_budget[:8], "overdue_invoices": overdue[:12],
            "slow_payers": slow[:6], "under_billed_jobs": under_billed[:8]}

# ---------- INVOICE DRAFTS ----------
def invoice_drafts(co=None):
    data = load()
    drafts = []
    for j in job_costing(co):
        if j["under_billed"] >= 1000:
            terms = next((c["terms"] for c in data["companies"] if c["id"] == j["company"]), 30)
            drafts.append({"company": j["company_name"], "customer": j["customer"], "job": j["name"], "job_id": j["id"],
                           "amount": j["under_billed"], "terms_days": terms, "pct_complete": j["pct_complete"],
                           "description": f"Progress billing — {j['name']} ({j['pct_complete']}% complete). "
                                          f"Earned ${j['earned_revenue']:,.0f}, previously billed ${j['billed_to_date']:,.0f}."})
    return sorted(drafts, key=lambda x: x["amount"], reverse=True)

def profiles(): return load().get("profiles", {})

# ---------- SNAPSHOT (group or single-company) ----------
def snapshot(co=None):
    roll = company_rollup(co); d = dso(co); fl = flags(co)
    aps = ap_summary(co); invs = inventory_status(co); po = po_open(co); jc = job_costing(co)
    group = {"open_ar": round(sum(r["open_ar"] for r in roll), 2),
             "overdue_ar": round(sum(r["overdue_ar"] for r in roll), 2),
             "under_billed": round(sum(r["under_billed"] for r in roll), 2),
             "contract_value": round(sum(r["contract_value"] for r in roll), 2),
             "dso_days": d["overall"]["dso_days"], "avg_days_outstanding": d["overall"]["avg_days_outstanding"],
             "over_budget_job_count": sum(1 for j in jc if j["over_budget"]),
             "open_ap": aps["open_ap"], "overdue_ap": aps["overdue_ap"],
             "inventory_value": invs["total_value"], "committed_po": po["committed"]}
    return {"as_of": load()["as_of"], "holding": load()["holding"], "scope": co or "group", "group": group,
            "companies": roll, "dso": d, "aging": ar_aging(co), "ap": aps, "inventory": invs, "po": po,
            "flags": fl, "draft_count": len(invoice_drafts(co))}

if __name__ == "__main__":
    for co in (None, "wayne", "csw"):
        g = snapshot(co)["group"]
        print(f"{co or 'GROUP':10} AR=${g['open_ar']:>10,.0f}  AP=${g['open_ap']:>9,.0f}  unbilled=${g['under_billed']:>9,.0f}  DSO={g['dso_days']}d")
