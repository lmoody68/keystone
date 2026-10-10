"""Transform live QuickBooks Online entities into KEYSTONE's display shapes.

When KEYSTONE is connected to QBO, the operational modules call these instead of the modeled engine,
so the ERP renders REAL QuickBooks data. Shapes match engine.py so the same UI renders either source.
"""
import datetime as dt
import qbo

_BUCKETS = ["current", "1-30", "31-60", "61-90", "90+"]

def _today(): return dt.date.today()
def _pd(s):
    try: return dt.date.fromisoformat((s or "")[:10])
    except Exception: return None
def _dpd(due):
    d = _pd(due); return (_today() - d).days if d else 0
def _dout(txn):
    d = _pd(txn); return (_today() - d).days if d else 0
def _bucket(dpd):
    if dpd <= 0:  return "current"
    if dpd <= 30: return "1-30"
    if dpd <= 60: return "31-60"
    if dpd <= 90: return "61-90"
    return "90+"
def _f(x):
    try: return float(x or 0)
    except Exception: return 0.0

# cache queries within a single request cycle (cheap; QBO is the source of truth)
def _invoices(): return qbo.query("Invoice", 500)
def _bills():    return qbo.query("Bill", 500)

def company_name():
    try:
        ci = qbo.query("CompanyInfo", 1)
        return ci[0].get("CompanyName", "QuickBooks Company") if ci else "QuickBooks (live)"
    except Exception:
        return "QuickBooks (live)"

def ar():
    rows = []
    invs = _invoices()
    for i in invs:
        bal = _f(i.get("Balance"))
        if bal <= 0.009:
            continue
        dpd = _dpd(i.get("DueDate"))
        rows.append({"id": "INV-" + str(i.get("DocNumber") or i.get("Id", "")), "company_name": "QuickBooks",
                     "customer": (i.get("CustomerRef") or {}).get("name", ""), "amount": round(bal, 2),
                     "status": "overdue" if dpd > 0 else "sent", "days_past_due": max(0, dpd),
                     "days_outstanding": _dout(i.get("TxnDate")), "bucket": _bucket(dpd)})
    aging = {b: 0.0 for b in _BUCKETS}
    for r in rows:
        aging[r["bucket"]] += r["amount"]
    open_ar = sum(r["amount"] for r in rows)
    sales90 = sum(_f(i.get("TotalAmt")) for i in invs if _pd(i.get("TxnDate")) and _dout(i.get("TxnDate")) <= 90)
    wd = sum(r["amount"] * r["days_outstanding"] for r in rows); tot = sum(r["amount"] for r in rows)
    dso = {"open_ar": round(open_ar, 2), "dso_days": round(open_ar / sales90 * 90, 1) if sales90 else 0,
           "avg_days_outstanding": round(wd / tot, 1) if tot else 0}
    return {"aging": {"overall": {k: round(v, 2) for k, v in aging.items()}, "by_company": {}},
            "dso": {"overall": dso, "by_company": {}},
            "open": sorted(rows, key=lambda r: r["amount"], reverse=True), "drafts": []}

def ap():
    rows = []       # ALL bills (so the user can see & click them, even when paid)
    for b in _bills():
        bal = _f(b.get("Balance")); total = _f(b.get("TotalAmt"))
        paid = bal <= 0.009
        dpd = 0 if paid else _dpd(b.get("DueDate"))
        rows.append({"id": "BILL-" + str(b.get("DocNumber") or b.get("Id", "")), "company_name": "QuickBooks",
                     "vendor": (b.get("VendorRef") or {}).get("name", ""),
                     "amount": round(total if paid else bal, 2),
                     "status": "paid" if paid else ("overdue" if dpd > 0 else "open"),
                     "days_past_due": max(0, dpd), "bucket": "current" if paid else _bucket(dpd)})
    open_rows = [r for r in rows if r["status"] != "paid"]
    aging = {b: 0.0 for b in _BUCKETS}
    for r in open_rows:
        aging[r["bucket"]] += r["amount"]
    return {"open_ap": round(sum(r["amount"] for r in open_rows), 2),
            "overdue_ap": round(sum(r["amount"] for r in open_rows if r["status"] == "overdue"), 2),
            "bill_count": len(open_rows), "total_bills": len(rows),
            "aging": {k: round(v, 2) for k, v in aging.items()},
            "bills": sorted(rows, key=lambda r: (r["status"] == "paid", -r["amount"]))}

def customers():
    inv_by = {}
    for i in _invoices():
        n = (i.get("CustomerRef") or {}).get("name", "")
        a = inv_by.setdefault(n, {"invoiced": 0.0, "open": 0.0, "overdue": 0.0})
        a["invoiced"] += _f(i.get("TotalAmt"))
        bal = _f(i.get("Balance"))
        if bal > 0.009:
            a["open"] += bal
            if _dpd(i.get("DueDate")) > 0:
                a["overdue"] += bal
    out = []
    for c in qbo.query("Customer", 500):
        n = c.get("DisplayName", "")
        a = inv_by.get(n, {"invoiced": 0.0, "open": _f(c.get("Balance")), "overdue": 0.0})
        out.append({"company": "QuickBooks", "customer": n, "invoiced": round(a["invoiced"], 2),
                    "open": round(a["open"], 2), "overdue": round(a["overdue"], 2)})
    return sorted(out, key=lambda x: x["invoiced"], reverse=True)

def vendors():
    bill_by = {}
    for b in _bills():
        n = (b.get("VendorRef") or {}).get("name", "")
        a = bill_by.setdefault(n, {"billed": 0.0, "open": 0.0})
        a["billed"] += _f(b.get("TotalAmt"))
        if _f(b.get("Balance")) > 0.009:
            a["open"] += _f(b.get("Balance"))
    out = []
    for v in qbo.query("Vendor", 500):
        n = v.get("DisplayName", "")
        a = bill_by.get(n, {"billed": 0.0, "open": _f(v.get("Balance"))})
        out.append({"company": "QuickBooks", "vendor": n, "billed": round(a["billed"], 2), "open": round(a["open"], 2)})
    return sorted(out, key=lambda x: x["billed"], reverse=True)

def inventory():
    inv = []
    for it in qbo.query("Item", 500):
        if it.get("Type") != "Inventory":
            continue
        qty = _f(it.get("QtyOnHand")); cost = _f(it.get("PurchaseCost")) or _f(it.get("UnitPrice"))
        rp = _f(it.get("ReorderPoint"))
        inv.append({"company": "qb", "item": it.get("Name", ""), "unit_cost": round(cost, 2),
                    "qty_on_hand": int(qty), "reorder_point": int(rp), "value": round(qty * cost, 2),
                    "low_stock": qty < rp})
    return {"items": inv, "total_value": round(sum(i["value"] for i in inv), 2),
            "low_stock": [i for i in inv if i["low_stock"]], "item_count": len(inv)}

def po():
    out = []
    for p in qbo.query("PurchaseOrder", 200):
        if (p.get("POStatus") or "") == "Closed":
            continue
        out.append({"id": "PO-" + str(p.get("DocNumber") or p.get("Id", "")), "company_name": "QuickBooks",
                    "vendor": (p.get("VendorRef") or {}).get("name", ""), "amount": round(_f(p.get("TotalAmt")), 2),
                    "date": (p.get("TxnDate") or "")[:10]})
    return {"open_pos": out, "committed": round(sum(p["amount"] for p in out), 2), "count": len(out)}

def jobs():
    return []   # QuickBooks has no construction job-costing; live mode shows none

def overview():
    a = ar(); p = ap(); invs = inventory(); pos = po()
    rows = a["open"]
    open_ar = sum(r["amount"] for r in rows)
    overdue_ar = sum(r["amount"] for r in rows if r["status"] == "overdue")
    name = company_name()
    comp = {"id": "qb", "name": name, "industry": "QuickBooks Online (live)", "active_jobs": 0,
            "contract_value": 0, "cost_to_date": 0, "under_billed": 0,
            "open_ar": round(open_ar, 2), "overdue_ar": round(overdue_ar, 2),
            "open_ap": p["open_ap"], "overdue_ap": p["overdue_ap"], "over_budget_jobs": 0}
    group = {"open_ar": round(open_ar, 2), "overdue_ar": round(overdue_ar, 2), "under_billed": 0, "contract_value": 0,
             "dso_days": a["dso"]["overall"]["dso_days"], "avg_days_outstanding": a["dso"]["overall"]["avg_days_outstanding"],
             "over_budget_job_count": 0, "open_ap": p["open_ap"], "overdue_ap": p["overdue_ap"],
             "inventory_value": invs["total_value"], "committed_po": pos["committed"]}
    overdue_inv = sorted([r for r in rows if r["status"] == "overdue"], key=lambda r: r["days_past_due"], reverse=True)[:12]
    slow = {}
    for r in rows:
        if r["status"] == "overdue":
            slow[r["customer"]] = slow.get(r["customer"], 0.0) + r["amount"]
    slow_payers = sorted(({"company": "QuickBooks", "customer": k, "overdue": round(v, 2)} for k, v in slow.items()),
                         key=lambda x: x["overdue"], reverse=True)[:6]
    return {"as_of": str(_today()), "holding": {"name": name}, "scope": "qb", "group": group,
            "companies": [comp], "dso": a["dso"], "aging": a["aging"], "ap": p, "inventory": invs, "po": pos,
            "flags": {"over_budget_jobs": [], "overdue_invoices": overdue_inv, "slow_payers": slow_payers, "under_billed_jobs": []},
            "draft_count": 0}

def profile_card():
    name = company_name()
    return {"holding": {"name": name, "owner": "—", "hq": "QuickBooks Online sandbox",
                        "about": "Live company connected via the QuickBooks Online API.", "source": "QuickBooks Online API"},
            "qb": {"about": "This is live data pulled from your connected QuickBooks Online company.",
                   "source": "QuickBooks Online API"}}
