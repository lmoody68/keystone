import engine
s = engine.snapshot()
g = s["group"]
print("=== GROUP ===")
print(f"  AR open=${g['open_ar']:,.0f} overdue=${g['overdue_ar']:,.0f} unbilled=${g['under_billed']:,.0f} DSO={g['dso_days']}d")
print(f"  AP open=${g['open_ap']:,.0f} overdue=${g['overdue_ap']:,.0f} | inventory=${g['inventory_value']:,.0f} | open POs=${g['committed_po']:,.0f}")
print(f"  over-budget jobs={g['over_budget_job_count']} | invoice drafts={s['draft_count']}")
print("=== AP aging ===", s["ap"]["aging"])
print("=== Inventory low-stock ===", [(i["item"], i["qty_on_hand"], i["reorder_point"]) for i in s["inventory"]["low_stock"]])
print("=== Open POs ===", s["po"]["count"], "committed", f"${s['po']['committed']:,.0f}")
print("=== Top customers (CRM) ===")
for c in engine.customer_rollup()[:4]:
    print(f"  {c['customer']:24} {c['company']:26} invoiced ${c['invoiced']:>10,.0f}  open ${c['open']:>9,.0f}")
print("=== Profiles (REAL) ===", list(engine.profiles().keys()))
print("  City Seafood:", engine.profiles()["csw"]["origin"][:70], "...")
