"""Generate a realistic, QuickBooks-shaped sample dataset modeled on The Burns Companies.

PRIVATE company -> no real financials exist. This is SYNTHETIC data modeled on their real
7-company structure, with deliberately planted 'stories' (an over-budget pool job, two
chronically-late seafood accounts dragging cash flow) so the copilot surfaces useful insights.

Entities mirror QuickBooks: Company, Customer, Job/Project, Invoice, CostEntry. Deterministic
(seeded) so the demo is stable.
"""
import json, random, datetime as dt
from pathlib import Path

random.seed(1897)
TODAY = dt.date(2026, 10, 9)
OUT = Path(__file__).resolve().parent / "data.json"

def d(days_ago):
    return (TODAY - dt.timedelta(days=days_ago)).isoformat()

COMPANIES = [
    {"id":"wayne", "name":"Wayne Contracting",        "industry":"General Contractor",     "model":"job",      "terms":30},
    {"id":"mhard", "name":"Meramec Hardscapes",       "industry":"Concrete / Hardscaping", "model":"job",      "terms":30},
    {"id":"mpool", "name":"Meramec Pools & Outdoors", "industry":"Pools / Outdoor Living", "model":"job",      "terms":30},
    {"id":"vizex", "name":"Vizex",                    "industry":"Graphics / Signage",     "model":"job",      "terms":30},
    {"id":"csw",   "name":"City Seafood (Wholesale)", "industry":"Wholesale Seafood",      "model":"recurring","terms":15},
    {"id":"csr",   "name":"City Seafood (Retail)",    "industry":"Retail Seafood",         "model":"retail",   "terms":0},
]
COST_TYPES = ["Labor","Materials","Subcontractor","Equipment","Other"]
CUSTOMERS = {
    "wayne":["Gateway Retail Group","Midwest Logistics Park","St. Charles Medical Plaza","Riverport Hospitality","Delmar Office Partners"],
    "mhard":["Whitmore HOA","Chesterfield Commons","Kirkwood Parks Dept","Ladue Estates","Frontenac Retail Center"],
    "mpool":["The Henderson Residence","Wildwood Country Club","Town & Country Estate","Ballwin Aquatics","The Pryor Residence"],
    "vizex":["National Tire Co","BrightMart Grocery","FitZone Gyms","Summit Bank","QuickFuel C-Stores"],
    "csw":  ["Prime 1012 Steakhouse","Blue Oyster Bistro","The Hill Trattoria","Schnucks Commissary","Harbor & Vine","Catch 314"],
    "csr":  ["Walk-in / Delivery"],
}
JOB_NAMES = {
    "wayne":["Gateway Retail Buildout","Logistics Park Phase 2","Medical Plaza Core/Shell","Riverport Hotel Renovation","Delmar TI — 3rd Floor"],
    "mhard":["Whitmore Driveway & Patio","Commons Stamped Walkways","Kirkwood Park Pavilion Slab","Ladue Retaining Wall","Frontenac Plaza Concrete"],
    "mpool":["Henderson Gunite Pool + Spa","Wildwood Club Lap Pool","T&C Infinity Pool","Ballwin Splash Pad","Pryor Pool + Tanning Ledge"],
    "vizex":["National Tire Rebrand (54 sites)","BrightMart Window Program","FitZone Interior Graphics","Summit Bank Signage","QuickFuel Pylon Wraps"],
}
# planted cash-flow story: these wholesale accounts pay chronically late
SLOW_PAYERS = {"Harbor & Vine", "Catch 314"}

invoices, jobs, cost_entries = [], [], []
inv_no, job_no = 3000, 100

def status_for(issued_days_ago, terms, slow=False):
    dpd = issued_days_ago - terms            # days past due
    if dpd <= 0:
        return "sent"                         # not yet due
    if slow:
        return random.choices(["paid","overdue"], weights=[30,70])[0]
    if dpd <= 15:
        return random.choices(["paid","sent","overdue"], weights=[55,25,20])[0]
    return random.choices(["paid","overdue"], weights=[85,15])[0]

def add_invoice(co, cust, amount, issued_days_ago, terms, status=None, job_id=None, kind="standard", slow=False):
    global inv_no
    inv_no += 1
    if status is None:
        status = status_for(issued_days_ago, terms, slow)
    if issued_days_ago >= terms:
        due = d(issued_days_ago - terms)
    else:
        due = (TODAY + dt.timedelta(days=terms - issued_days_ago)).isoformat()
    invoices.append({"id":f"INV-{inv_no}","company":co,"customer":cust,"job_id":job_id,
                     "amount":round(amount,2),"issue_date":d(issued_days_ago),"due_date":due,
                     "terms_days":terms,"status":status,"kind":kind})

def make_job(co, cust, name, contract, pct, days_started, cost_factor=1.0):
    global job_no
    job_no += 1
    jid = f"J{job_no}"
    target_margin = random.uniform(0.18, 0.28)
    budget_cost = round(contract * (1 - target_margin), 2)
    actual = round(budget_cost * (pct/100.0) * cost_factor, 2)
    weights = {"Labor":0.38,"Materials":0.34,"Subcontractor":0.18,"Equipment":0.06,"Other":0.04}
    for ct, w in weights.items():
        if actual*w > 0:
            cost_entries.append({"job_id":jid,"company":co,"type":ct,"amount":round(actual*w,2),"date":d(days_started-5)})
    jobs.append({"id":jid,"company":co,"customer":cust,"name":name,"contract_amount":contract,
                 "budget_cost":budget_cost,"pct_complete":pct,"start_date":d(days_started),
                 "status":"complete" if pct>=100 else "in_progress"})
    return jid

# CONTRACTORS: jobs + progress billing
for co in ["wayne","mhard","mpool","vizex"]:
    names, custs = JOB_NAMES[co], CUSTOMERS[co]
    for i, name in enumerate(names):
        cust = custs[i % len(custs)]
        contract = {"wayne":random.uniform(350_000,1_900_000),"mhard":random.uniform(12_000,140_000),
                    "mpool":random.uniform(55_000,210_000),"vizex":random.uniform(8_000,95_000)}[co]
        contract = round(contract, -2)
        pct = random.choice([15,30,45,60,75,90,100])
        factor = 1.34 if (co=="mpool" and i==0) else random.uniform(0.93,1.07)   # Henderson pool runs hot
        started = random.randint(40, 220)
        jid = make_job(co, cust, name, contract, pct, started, factor)
        lag = random.choice([0, 0, 10, 15, 20, 25])        # some jobs bill behind completion -> unbilled work
        billed_pct = max(0, pct - lag)
        n_bills = max(1, int(billed_pct)//25)
        for b in range(n_bills):
            amt = contract * (billed_pct/100.0) / n_bills
            issued = max(5, started - int((started-10)*(b+1)/(n_bills+1)))
            add_invoice(co, cust, amt, issued, 30, job_id=jid, kind="progress")

# CITY SEAFOOD WHOLESALE: weekly Net-15 recurring; two accounts pay late (the cash-flow story)
for cust in CUSTOMERS["csw"]:
    slow = cust in SLOW_PAYERS
    for wk in range(8):
        issued = wk*7 + random.randint(0,2)
        amt = random.uniform(650, 9800)
        add_invoice("csw", cust, amt, issued, 15, kind="recurring", slow=slow)

# CITY SEAFOOD RETAIL: high-volume small due-on-receipt (mostly paid)
for n in range(60):
    issued = random.randint(0, 45)
    amt = random.uniform(28, 320)
    add_invoice("csr", "Walk-in / Delivery", amt, issued, 0,
                status=random.choices(["paid","sent"], weights=[90,10])[0], kind="retail")

# ================= ERP EXPANSION: the buy side + inventory =================
VENDORS = {
    "contractor": ["ABC Concrete Supply","Midwest Rebar & Steel","Gateway Electric (sub)",
                   "ProPlumb Mechanical (sub)","Sherwin Materials","United Rentals","BuildPro Lumber"],
    "signage":    ["Vinyl Pro Media","3M Graphics Distributor","SignFab Aluminum"],
    "seafood":    ["Gulf Coast Seafood Co","Pacific Catch Distributors","Bayou Shellfish","North Atlantic Fisheries"],
}
def vendors_for(co):
    if co in ("csw","csr"): return VENDORS["seafood"]
    if co == "vizex":        return VENDORS["contractor"][:3] + VENDORS["signage"]
    return VENDORS["contractor"]

bills, bill_no = [], 5000
def add_bill(co, vendor, amount, issued_days_ago, terms=30, job_id=None):
    global bill_no
    bill_no += 1
    dpd = issued_days_ago - terms
    if dpd <= 0:
        status = random.choices(["open", "paid"], weights=[70, 30])[0]          # not yet due: owed (current) or paid early
    else:
        status = random.choices(["paid", "open", "overdue"], weights=[72, 8, 20])[0]
    due = d(issued_days_ago-terms) if issued_days_ago >= terms else (TODAY+dt.timedelta(days=terms-issued_days_ago)).isoformat()
    bills.append({"id":f"BILL-{bill_no}","company":co,"vendor":vendor,"job_id":job_id,
                  "amount":round(amount,2),"issue_date":d(issued_days_ago),"due_date":due,
                  "terms_days":terms,"status":status})

for j in jobs:                                   # job-tied material/sub bills
    vs = vendors_for(j["company"])
    for _ in range(random.randint(2,4)):
        add_bill(j["company"], random.choice(vs), j["budget_cost"]*random.uniform(0.04,0.12), random.randint(5,150), job_id=j["id"])
for co in ("csw","csr"):                          # seafood COGS bills
    for _ in range(10):
        add_bill(co, random.choice(VENDORS["seafood"]), random.uniform(1500,18000), random.randint(2,40), terms=15)

pos, po_no = [], 7000                              # open purchase-order commitments
for j in random.sample(jobs, 10):
    po_no += 1
    pos.append({"id":f"PO-{po_no}","company":j["company"],"vendor":random.choice(vendors_for(j["company"])),
                "job_id":j["id"],"amount":round(j["budget_cost"]*random.uniform(0.05,0.15),2),
                "status":random.choice(["open","open","received"]),"date":d(random.randint(5,90))})

INV_ITEMS = [("Atlantic Salmon (lb)",8.90,1200,400),("Yellowfin Tuna (lb)",14.50,500,200),
             ("Gulf Shrimp 16/20 (lb)",9.75,900,300),("Blue Point Oysters (dz)",11.00,300,150),
             ("Sea Scallops U10 (lb)",22.00,250,120),("Cod Fillet (lb)",7.40,700,250),
             ("King Crab Legs (lb)",34.00,180,80),("Mahi Mahi (lb)",10.20,400,180)]
inventory = []
for name, cost, base, reorder in INV_ITEMS:
    qty = int(base*random.uniform(0.3,1.4))
    inventory.append({"company":"csw","item":name,"unit_cost":cost,"qty_on_hand":qty,
                      "reorder_point":reorder,"value":round(qty*cost,2),"low_stock":qty < reorder})

# ===== REAL, publicly-sourced company profiles (the factual backbone of the ERP) =====
PROFILES = {
  "holding": {"name":"The Burns Companies (Burns Holdings, LLC)","owner":"Brad Burns",
     "hq":"32 Progress Parkway, Maryland Heights, MO 63043","accounting":"QuickBooks (per-company files)",
     "about":"Privately held, bootstrapped umbrella of operating companies across commercial construction, graphics/signage, hardscaping & pools, and seafood.",
     "source":"theburnscos.com"},
  "wayne": {"founded":2014,"leadership":"Brad W. Burns (founder)",
     "services":["General contracting","Construction management","Multi-site project rollouts"],
     "sectors":["Retail","Hospitality","Food service"],"reach":"National",
     "about":"Flagship; concept-to-completion commercial GC. One of the fastest-growing US GCs, grown with no outside investment.",
     "source":"theburnscos.com; cbinsights; levelset"},
  "vizex": {"founded":2019,"services":["Vinyl graphics","Window displays","Visual-experience design & install"],
     "sectors":["Commercial","Retail"],"reach":"All 50 states",
     "about":"Strategize → design → print → install visual graphics for commercial & retail spaces.","source":"theburnscos.com"},
  "mhard": {"founded":2019,"services":["Concrete","Hardscaping","Landscape construction"],"reach":"Midwest",
     "about":"Decorative and structural concrete + hardscapes.","source":"theburnscos.com"},
  "mpool": {"founded":2021,"services":["Pool design & install","Spas","Decorative concrete","Retaining walls","Tanning ledges"],
     "about":"Complete outdoor-living projects; 20+ years of experience.","source":"theburnscos.com"},
  "csw": {"founded":2023,"leadership":"Brad Burns + Mike Vujevic (ex-Bob's Seafood VP; runs fish-cutting)",
     "facility":"Processing facility in Fenton, MO","model":"Wholesale (2023) + online D2C (late 2024)",
     "delivery":"35-40 mi radius from Fenton, $100 min; pickup at Fenton",
     "origin":"'Bob's 2.0' - founded after Bob's Seafood closed June 2022 (eminent domain for a Costco in University City); relocated Bob's equipment, coolers, trucks, and fish-cutters.",
     "growth":"Exploring a retail storefront; eyeing 15-20 Midwest markets (Tulsa, Omaha, Nashville).",
     "products_confirmed":["Salmon","Shrimp"],"source":"stlmag.com 'City Seafood's Brad Burns'; theburnscos.com"},
  "csr": {"founded":2024,"model":"Retail / online D2C - delivery + Fenton pickup",
     "about":"Direct-to-consumer arm of City Seafood.","source":"stlmag.com; theburnscos.com"},
  "arch": {"name":"Arch City Group","status":"Launching 2025","services":["Restaurant franchise operations","Investment & development"],
     "source":"theburnscos.com"},
}

data = {"as_of":TODAY.isoformat(),
        "holding":PROFILES["holding"], "profiles":PROFILES,
        "companies":COMPANIES,"jobs":jobs,"invoices":invoices,"cost_entries":cost_entries,
        "bills":bills,"purchase_orders":pos,"inventory":inventory,
        "_note":"Company PROFILES are REAL (publicly sourced; see each 'source'). Transactions (jobs, invoices, "
                "bills, POs, inventory quantities/prices) are SYNTHETIC, modeled on Burns' real operations for demo - "
                "NOT actual Burns financials. City Seafood products: Salmon & Shrimp confirmed real; others representative."}
OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")

open_ar = sum(i['amount'] for i in invoices if i['status'] in ('sent','overdue'))
overdue = sum(i['amount'] for i in invoices if i['status']=='overdue')
print(f"wrote {OUT}")
print(f"  companies={len(COMPANIES)} jobs={len(jobs)} invoices={len(invoices)} cost_entries={len(cost_entries)}")
print(f"  bills={len(bills)} purchase_orders={len(pos)} inventory_items={len(inventory)} profiles={len(PROFILES)}")
ap_open = sum(b['amount'] for b in bills if b['status'] in ('open','overdue'))
inv_val = sum(i['value'] for i in inventory)
print(f"  open AR=${open_ar:,.0f}  overdue=${overdue:,.0f}  ({overdue/open_ar*100:.0f}% overdue)")
print(f"  open AP=${ap_open:,.0f}  inventory value=${inv_val:,.0f}  low-stock items={sum(1 for i in inventory if i['low_stock'])}")
