"""
demo.py
───────
Full pipeline demo: individual debts → portfolio → Model V.2

Thai user scenario:
  - Credit card debt at 18% p.a.
  - Car hire purchase (flat rate 3%)
  - Personal loan (effective rate 12%)
  - Home mortgage (effective rate 6.5%)
"""

from debt_instruments import (
    FlatRateDebt, EffectiveRateDebt, CreditCardDebt,
    HirePurchaseDebt, BalloonDebt, DebtPurpose, InterestType
)
from debt_portfolio import DebtPortfolio
from model_v2 import ModelV2Layer1, ModelV2Layer2, ModelV2Layer3

def run_demo():
    print("\n" + "="*60)
    print("  DEBT → PORTFOLIO → MODEL V.2  |  Full Pipeline Demo")
    print("="*60)

    # ── Step 1: Define individual debts ───────────────────────────────────────

    # Credit card — revolving, 18% p.a., minimum 5%
    cc = CreditCardDebt(
        name              = "Kasikorn Credit Card",
        creditor          = "KBank",
        purpose           = DebtPurpose.PERSONAL,
        current_balance   = 45_000,
        annual_rate       = 0.18,
        minimum_payment   = 2_500,     # paying more than minimum
        min_payment_pct   = 0.05,
        min_payment_floor = 500,
        monthly_new_charges = 0,       # no new charges (paying down)
    )

    # Car hire purchase — flat rate 3% p.a., 60 months, paid 24 already
    car = HirePurchaseDebt(
        name                = "Toyota Vios (Hire Purchase)",
        creditor            = "Toyota Leasing",
        purpose             = DebtPurpose.AUTO,
        original_balance    = 600_000,
        current_balance     = 360_000,   # approximately 60% remaining
        annual_rate         = 0.03,      # 3% flat
        total_installments  = 60,
        paid_installments   = 24,
        installment_amount  = 11_500,    # fixed monthly payment
    )

    # Personal loan — effective rate 12% p.a., 36-month term, 18 remaining
    personal = EffectiveRateDebt(
        name                  = "SCB Personal Loan",
        creditor              = "SCB",
        purpose               = DebtPurpose.PERSONAL,
        original_balance      = 150_000,
        current_balance       = 80_000,
        annual_rate           = 0.12,
        original_term_months  = 36,
        remaining_term_months = 18,
        minimum_payment       = 5_000,
    )

    # Home mortgage — effective rate 6.5% p.a., 20-year term, 15 years remaining
    mortgage = EffectiveRateDebt(
        name                  = "Home Mortgage — GH Bank",
        creditor              = "Government Housing Bank",
        purpose               = DebtPurpose.MORTGAGE,
        original_balance      = 3_000_000,
        current_balance       = 2_400_000,
        annual_rate           = 0.065,
        original_term_months  = 240,
        remaining_term_months = 180,
        minimum_payment       = 18_000,
    )

    # ── Step 2: Build portfolio ────────────────────────────────────────────────

    portfolio = (DebtPortfolio(label="Demo User Portfolio")
                 .add(cc)
                 .add(car)
                 .add(personal)
                 .add(mortgage))

    portfolio.print_summary()

    # ── Step 3: Export to Model V.2 inputs ────────────────────────────────────

    model_inputs = portfolio.to_model_v2_inputs()
    print(f"  Portfolio → Model V.2 core inputs:")
    print(f"    D = ฿{model_inputs['D']:>12,.2f}  (total debt)")
    print(f"    r =  {model_inputs['r']*100:>10.4f}%  (weighted effective annual rate)")
    print(f"    P = ฿{model_inputs['P']:>12,.2f}  (total monthly payment)")

    # ── Step 4: Layer 1 ───────────────────────────────────────────────────────

    layer1 = ModelV2Layer1(
        I               = 120_000,   # monthly income ฿120k
        E_n             = 35_000,    # necessary expenses
        E_d             = 15_000,    # discretionary spending
        behavior_score  = 4,         # moderate impulse (β=1.4)
        E_fund          = 90_000,    # ≈2.6 months of E_n
        months_on_budget= 4,         # 4/6 months on budget
        target_freedom_months = 60,
    ).load_portfolio(model_inputs)

    layer1.print_layer1()

    # ── Step 5: Layer 2 ───────────────────────────────────────────────────────

    layer2 = ModelV2Layer2(
        layer1      = layer1,
        current_age = 38,
        career_type = "technical_engineering",
        t_start     = 22,
        t_retire    = 60,
    )
    layer2.print_layer2()

    # ── Step 6: Layer 3 ───────────────────────────────────────────────────────

    layer3 = ModelV2Layer3(portfolio_inputs=model_inputs)
    layer3.print_layer3()

    # ── Step 7: Check individual amortization schedules ───────────────────────

    print("  ── Credit Card: First 6 months ─────────────────────────")
    for row in cc.amortization_schedule()[:6]:
        print(f"    Month {row['month']:>2}: interest=฿{row['interest_charge']:>7,.0f}  "
              f"principal=฿{row['principal_paid']:>7,.0f}  "
              f"balance=฿{row['balance_end']:>9,.0f}")

    print("\n  ── Car Hire Purchase: First 6 months ────────────────────")
    for row in car.amortization_schedule()[:6]:
        print(f"    Month {row['month']:>2}: interest=฿{row['interest_charge']:>7,.0f}  "
              f"principal=฿{row['principal_paid']:>7,.0f}  "
              f"balance=฿{row['balance_end']:>9,.0f}")

    print()


if __name__ == "__main__":
    run_demo()
