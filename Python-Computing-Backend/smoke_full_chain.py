"""
smoke_full_chain.py
───────────────────
Full pipeline smoke test:
  DebtInstrument subclasses → DebtPortfolio → ModelV2Layer1 + Layer2 → NudgeEngine

Scenario: มิน อายุ 28  เงินเดือน ฿20,000
  - บัตรเครดิต ฿40,000 @ 16% p.a.  — paying ฿1,400/mo (above min)
  - สินเชื่อส่วนบุคคล (นอนแบงก์) ฿60,000 @ 28% p.a. — paying ฿2,100/mo
  - Total P = ฿3,500/mo
"""

from debt_instruments          import (CreditCardDebt, EffectiveRateDebt,
                                        FlatRateDebt, DebtPurpose, InterestType)
from debt_instruments_extended import (CooperativeDebt, StepUpRateDebt,
                                        RatePeriod, FloatingRateDebt,
                                        PayoffCalculator, PERSONAL_LOAN_FEE_CONFIG)
from debt_portfolio            import DebtPortfolio
from model_v2                  import ModelV2Layer1, ModelV2Layer2
from nudge_engine              import NudgeEngine


# ── 1. Build debt instruments ─────────────────────────────────────────────────
#
# CreditCardDebt auto-computes min payment as max(floor, balance*pct).
# To model a user who pays a fixed ฿1,400 (above their own minimum), we use
# EffectiveRateDebt with minimum_payment set to the actual amount they pay.
# (No original_term_months → _compute_monthly_payment() falls back to minimum_payment.)

credit_card = EffectiveRateDebt(
    name             = "Credit Card / บัตรเครดิต",
    creditor         = "Thai Commercial Bank",
    purpose          = DebtPurpose.CREDIT_CARD,
    original_balance = 40_000,
    current_balance  = 40_000,
    annual_rate      = 0.16,          # 16% p.a.
    minimum_payment  = 1_400,         # ฿1,400/mo fixed payment
)

personal_loan = EffectiveRateDebt(
    name             = "Personal Loan (Non-bank) / สินเชื่อส่วนบุคคล",
    creditor         = "Non-bank Lender",
    purpose          = DebtPurpose.PERSONAL,
    original_balance = 60_000,
    current_balance  = 60_000,
    annual_rate      = 0.28,          # 28% p.a.
    minimum_payment  = 2_100,         # ฿2,100/mo fixed payment
)

print("── Individual Debt Instruments ──────────────────────────────────")
cc_s  = credit_card.summary()
pl_s  = personal_loan.summary()
for label, s in [("Credit Card", cc_s), ("Personal Loan", pl_s)]:
    print(f"  {label}:")
    print(f"    Balance : ฿{s['current_balance']:>10,.2f}")
    print(f"    EAR     :  {s['effective_annual_rate']:>8.2f}%  (effective p.a.)")
    print(f"    Payment : ฿{s['monthly_payment']:>10,.2f}/mo")
    print(f"    Interest: ฿{s['monthly_interest_charge']:>10,.2f}/mo")
    print(f"    t*      :  {s['months_remaining']} months")
    print(f"    VM cat  :  {s['vm_category']}")
    print()


# ── 2. Aggregate into portfolio ───────────────────────────────────────────────

portfolio = DebtPortfolio(label="มิน อายุ 28")
portfolio.add(credit_card).add(personal_loan)
portfolio.print_summary()

inputs = portfolio.to_model_v2_inputs()
print("  to_model_v2_inputs():")
for k in ("D","r","P","monthly_interest_total","monthly_principal_total",
          "portfolio_months_remaining","dominant_vm_category","drag_ratio"):
    v = inputs[k]
    if k == "r":
        print(f"    {k:30s} = {v*100:.4f}%")
    elif k == "drag_ratio":
        print(f"    {k:30s} = {v*100:.1f}%")
    else:
        print(f"    {k:30s} = {v}")
print()


# ── 3. Feed into Model V.2 Layer 1 ───────────────────────────────────────────

l1 = ModelV2Layer1(
    I               = 20_000,
    E_n             = 14_000,   # necessary expenses
    E_d             = 2_500,    # discretionary
    behavior_score  = 5,        # 1=bad, 10=best
    E_fund          = 8_000,    # emergency fund
    months_on_budget= 3,
)
l1.load_portfolio(inputs)
l1.print_layer1()


# ── 4. Layer 2 ────────────────────────────────────────────────────────────────

l2 = ModelV2Layer2(
    layer1       = l1,
    current_age  = 28,
    career_type  = "technical_engineering",
    t_retire     = 60,
)
l2.print_layer2()


# ── 5. Nudge Engine ───────────────────────────────────────────────────────────

engine = NudgeEngine(
    layer1             = l1,
    layer2             = l2,
    behavior_questions = [1, 1, 2, 2, 1],
)
engine.print_report()


# ── 6. Payoff strategy ────────────────────────────────────────────────────────

print("── Payoff Strategy ──────────────────────────────────────────────")
print("  Avalanche (highest-rate first — mathematically optimal):")
for d in portfolio.avalanche_order():
    print(f"    → {d.name:<40} {d.effective_annual_rate*100:.1f}%  ฿{d.current_balance:>10,.0f}")
print("  Snowball (smallest-balance first — psychologically effective):")
for d in portfolio.snowball_order():
    print(f"    → {d.name:<40} ฿{d.current_balance:>10,.0f}")
print()
print(f"  Drag debt ratio  : {inputs['drag_ratio']*100:.0f}%  (all debts are drag category)")
print(f"  Dominant category: {inputs['dominant_vm_category']}")
print()


# ── 7. Extended: Cooperative Loan example ────────────────────────────────────

print("── Extended: Cooperative Loan (สหกรณ์ออมทรัพย์) ─────────────────")
coop = CooperativeDebt(
    name                  = "Savings Co-op Loan",
    purpose               = DebtPurpose.OTHER,
    original_balance      = 200_000,
    current_balance       = 200_000,
    loan_rate             = 0.06,        # 6% gross
    dividend_yield        = 0.065,       # 6.5% dividend on shares
    share_subscription    = 80_000,      # ฿80k already in shares
    monthly_share_deposit = 2_000,
    original_term_months  = 60,
    minimum_payment       = 4_000,
)
cs = coop.summary()
print(f"  Gross rate  : {cs['loan_rate_pct']:.2f}%")
print(f"  Dividend    : {cs['dividend_yield_pct']:.2f}% on ฿{cs['share_subscription']:,.0f} shares")
print(f"  Net eff. rate: {cs['net_effective_rate_pct']:.2f}%  ← what feeds Model V.2 r_i")
print(f"  Net monthly interest offset: ฿{cs['monthly_dividend_offset']:,.0f}/mo")
print(f"  Payoff (after share netting): ฿{cs['payoff_after_netting']:,.0f}")
div_summary = coop.dividend_benefit_summary()
print(f"  Total dividend savings over life: ฿{div_summary['total_dividend_savings']:,.0f}"
      f"  ({div_summary['savings_pct']:.1f}% of gross interest)")
print()


# ── 8. Extended: Step-Up Mortgage ─────────────────────────────────────────────

print("── Extended: Step-Up Mortgage (Thai bank promo pattern) ──────────")
mortgage = StepUpRateDebt(
    name                 = "Home Loan / สินเชื่อบ้าน",
    purpose              = DebtPurpose.MORTGAGE,
    original_balance     = 3_000_000,
    current_balance      = 2_800_000,
    original_term_months = 300,   # 25 years
    months_elapsed       = 24,    # 2 years in
    rate_schedule        = [
        RatePeriod(months_duration=36,  annual_rate=0.035, label="Year 1-3 promo"),
        RatePeriod(months_duration=24,  annual_rate=0.055, label="Year 4-5 transition"),
        RatePeriod(months_duration=9999, annual_rate=0.0700, label="MRR-0.5% standard"),
    ],
    minimum_payment      = 0,
)
ms = mortgage.summary()
print(f"  Current rate  : {ms['effective_annual_rate']:.2f}%  ({mortgage.current_rate_label})")
print(f"  Next change in: {mortgage.months_in_current_period_remaining} months")
print(f"  Monthly payment: ฿{ms['monthly_payment']:,.0f}")
print(f"  Monthly interest: ฿{ms['monthly_interest_charge']:,.0f}")
print(f"  Months remaining: {ms['months_remaining']}")
print()

print("── All done. Full chain verified ✓ ─────────────────────────────")