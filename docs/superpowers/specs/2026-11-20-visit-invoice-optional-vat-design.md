# Optional 7% VAT on doctor visit invoices

This revises the tax-free-only portion of the doctor visit invoice design. Keep
the existing appointment linkage, discount authorization, native invoice
permissions, serialization, and uncertain-outcome handling.

The doctor chooses **Add 7% VAT** (default) or **No VAT** before preview. The
consultation fee excludes VAT. Fixed or percentage discount reduces the fee
first. ERPNext calculates VAT on the discounted net and controls cent rounding.
For a fee of 100.00 and a 15% discount, show net 85.00, VAT 5.95, and payable
90.95; No VAT shows payable 85.00. No VAT requires both server-verified visit
eligibility and independent doctor authorization. Neither the browser nor the
absence of a tax template establishes eligibility. Without positive evidence,
No VAT is unavailable.

**Approach.** Resolve an explicit, approved ERPNext template in the VAT branch:
one exclusive 7% charge on net total, with a verified tax liability account.
Use the same native calculation and posting context for preview and submit.
The No VAT branch must be intrinsically zero-tax under native defaults; never
suppress an applicable tax with an internal flag. Do not hard-code a tax amount
in Clinic or reuse the current disposable company's 6% default. Retain the
conservative Tax Rule and Item Tax guards.

Show fee, discount, net, VAT, and payable before confirmation. Changing VAT or
discount invalidates preview. Sign the selected mode, resolved tax facts, and
calculated totals. Verify persisted visit linkage, taxes, outstanding and exact
receivable/revenue/tax ledger split before commit. No VAT has no tax ledger row.

First release remains dedicated synthetic SIT only. Preserve the existing
templates; no shared SIT, production tax configuration, payments, percentage
other than 7%, inclusive pricing, or automatic tax classification.
