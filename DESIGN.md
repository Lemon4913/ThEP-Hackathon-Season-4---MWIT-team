# DESIGN.md — Debt Pipeline System Reference

Complete technical reference for all Python modules, the REST API, and JavaScript client.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Module: debt_instruments.py](#2-module-debt_instrumentspy)
3. [Module: debt_instruments_extended.py](#3-module-debt_instruments_extendedpy)
4. [Module: debt_portfolio.py](#4-module-debt_portfoliopy)
5. [Module: model_v2.py](#5-module-model_v2py)
6. [Module: nudge_engine.py](#6-module-nudge_enginepy)
7. [Module: payoff_strategy.py](#7-module-payoff_strategypy)
8. [REST API — app.py](#8-rest-api--apppy)
9. [JavaScript Client — api.js](#9-javascript-client--apijs)
10. [Data Schemas](#10-data-schemas)
11. [Key Formulas](#11-key-formulas)
12. [Running the System](#12-running-the-system)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    HTML / Frontend                          │
│                  (calls via api.js)                         │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP JSON
┌──────────────────────────▼──────────────────────────────────┐
│                   app.py  (Flask API)                       │
│          localhost:5000  —  CORS + LAN IP enabled           │
└──┬──────────┬──────────┬─────────┬──────────┬──────────┬────┘
   │          │          │         │          │          │
   ▼          ▼          ▼         ▼          ▼          ▼
debt_       debt_      debt_    model_v2   nudge_    payoff_
instruments instruments portfolio  .py    engine    strategy
.py         _extended   .py               .py        .py
            .py
```

**Data flow:**
1. Individual debts (`debt_instruments.py` / `debt_instruments_extended.py`) are built from user input
2. `DebtPortfolio` aggregates them into D, r, P — the three Model V.2 scalars
3. `ModelV2Layer1` + `Layer2` compute orbital zone, escape score, time-to-freedom, lifetime trajectory
4. `NudgeEngine` finds the smallest behavioral changes to upgrade the zone
5. `StrategyEngine` simulates month-by-month paydown across up to 15+ strategy variants and ranks by total interest paid
6. `app.py` exposes all of this as a JSON API; `api.js` consumes it from the browser

---

## 2. Module: `debt_instruments.py`

**Purpose:** Per-debt financial engine. Each instrument computes its own
effective rate, monthly payment, interest charge, months remaining, and
full amortization schedule.

### Enums

**`InterestType`**

| Value | Description |
|---|---|
| `FLAT` | Total interest pre-calculated at signing (Thai personal loan style) |
| `EFFECTIVE` | Compound/APR-style — standard annuity amortization |
| `COMPOUND` | Explicit compound with configurable frequency |

**`DebtPurpose`** — drives Layer 3 VM category classification:

| Purpose | VM Category | Meaning |
|---|---|---|
| `EDUCATION`, `MORTGAGE`, `BUSINESS` | `slingshot` | Productive, wealth-building debt |
| `CREDIT_CARD`, `PERSONAL` | `drag` | Consumptive, wealth-eroding debt |
| `AUTO`, `MEDICAL`, `MIXED`, `OTHER` | `neutral` | Neither productive nor destructive |

### Base Class: `DebtInstrument`

All subclasses share this interface for Model V.2 integration:

| Property | Symbol | Description |
|---|---|---|
| `current_balance` | D_i | Outstanding principal right now |
| `effective_annual_rate` | r_i | Compound-equivalent annual rate |
| `monthly_payment` | P_i | Payment being made this month |
| `monthly_interest_charge` | F_g_i | Interest cost this month = D_i × r_i/12 |
| `months_remaining` | t_i | Months to pay off this debt at current payment |
| `is_orbit_locked` | — | True if payment ≤ monthly interest (balance growing) |
| `amortization_schedule()` | — | List of monthly dicts |
| `summary()` | — | Full snapshot dict for portfolio and display |

Computed fields populated by `__post_init__`: `_effective_monthly_rate`, `_computed_payment`

#### `_flat_to_effective_monthly(flat_annual_rate, term_months)`
Static Newton-Raphson solver. Converts a flat annual rate to its
compound-equivalent monthly rate by solving the annuity PV equation.
Initial guess: `flat_rate / 12 × 1.8` (Thai/ASEAN approximation).
Used by all flat-rate instruments to ensure consistent portfolio weighting.

### Subclasses

**`FlatRateDebt`**
Thai-style loan — total interest = principal × flat_rate × years, calculated
at signing. Monthly payment is fixed. Uses Rule of 78s by default
(`use_rule_of_78=True`) to front-load interest allocation in the schedule.

Key fields: `original_balance`, `annual_rate`, `original_term_months`,
`remaining_term_months`, `use_rule_of_78`

---

**`EffectiveRateDebt`**
Standard amortizing loan (mortgage, personal loan, car loan with APR).
Base class annuity formula applies directly. Most common type.

Key fields: `current_balance`, `annual_rate`, `remaining_term_months`,
`minimum_payment`

---

**`CompoundRateDebt`**
Like `EffectiveRateDebt` but with an explicit `compounding_frequency`
(12 = monthly, 365 = daily). Converts to monthly effective rate via:
`EAR = (1 + r/m)^m − 1`, then `monthly = (1 + EAR)^(1/12) − 1`.

---

**`CreditCardDebt`**
Revolving debt. Minimum payment = `max(min_payment_floor, balance × min_payment_pct)`.
New monthly charges can be modeled. Payment and balance are recomputed
each month in the schedule. Hard stop if balance grows to 100× original.

Key fields: `min_payment_pct` (default 5%), `min_payment_floor` (default ฿500),
`monthly_new_charges`, `minimum_payment`

---

**`HirePurchaseDebt`**
Thai hire-purchase (ผ่อนชำระ) — flat rate, fixed installments. Common for
vehicles and electronics. Driven by `total_installments` and
`paid_installments` to set `remaining_term_months` automatically.

Key fields: `total_installments`, `paid_installments`, `installment_amount`

---

**`BalloonDebt`**
Period loan with a lump-sum due at maturity. Supports pure interest-only
(`interest_only=True`) or partial amortization on the non-balloon portion.
The final amortization row is flagged `is_balloon: True`.

Key fields: `balloon_amount`, `balloon_month`, `interest_only`

---

## 3. Module: `debt_instruments_extended.py`

**Purpose:** Five additional instrument types for Thai-specific products,
plus a universal payoff calculator with a full fee stack.

### `RatePeriod`

A single segment in a step-up rate schedule.

| Field | Type | Description |
|---|---|---|
| `months_duration` | int | How many months this rate applies |
| `annual_rate` | float | Rate as decimal (e.g. 0.035 for 3.5%) |
| `label` | str | Human-readable tag (e.g. "Year 1-3 promo") |

---

### `StepUpRateDebt`

Mortgage or loan with multiple scheduled rate periods. Classic Thai bank
pattern: Year 1–3 promo rate → Year 4–5 transition → Year 6+ standard rate.

Key fields:
- `rate_schedule` — ordered list of `RatePeriod` objects covering the full term
- `months_elapsed` — months already served (positions simulation mid-schedule)

The amortization schedule re-amortizes the payment at each rate change.
Every row carries `rate_period_label` and `annual_rate_pct`.

Helper properties:
- `current_rate_label` — label of the active rate period
- `months_in_current_period_remaining` — months until next rate change

---

### `FloatingRateDebt`

MRR/MLR/THOR-linked debt. Effective rate = `reference_rate + spread`
(spread is typically negative, e.g. MRR − 1.5%).

Key fields: `reference_rate`, `spread`, `reference_name`

Methods:
- `set_reference_rate(new_rate)` — update after a BOT policy change;
  recomputes `annual_rate`, `_effective_monthly_rate`, and `_computed_payment` in-place
- `rate_shock_analysis(shocks)` — returns impact table for a list of
  rate delta scenarios (e.g. `[+0.005, +0.01, +0.02]`); restores original rate after

---

### `AnnualStepUpDebt`

กยศ. student loan pattern — rate increases by `step_rate` each repayment year,
capped at `max_rate`. Supports a `grace_months` period where interest
capitalises but no payment is required. Uses simple interest by default
(`simple_interest=True`) matching กยศ. contract terms.

Key fields: `initial_rate`, `step_rate`, `max_rate`, `years_elapsed`,
`grace_months`, `simple_interest`

Helper method: `rate_ladder()` — returns the full annual rate schedule as a list

---

### `CooperativeDebt`

สหกรณ์ออมทรัพย์ loan. The member holds a share subscription that earns an
annual dividend, which is smoothed to monthly and offsets gross loan interest.

Key fields: `share_subscription`, `monthly_share_deposit`, `dividend_yield`,
`loan_rate`, `share_offset_allowed`, `shares_netted_at_payoff`

Important overrides:
- `effective_annual_rate` — returns net (post-dividend) rate for portfolio weighting
- `monthly_interest_charge` — returns net cost for F_g calculation

Key properties:
- `gross_monthly_interest` — full interest before dividend offset
- `monthly_dividend_offset` — monthly equivalent of dividend earnings
- `net_monthly_interest` — actual cash cost = max(0, gross − dividend)
- `net_effective_annual_rate` — what feeds Model V.2's r_i
- `net_monthly_outflow` — total cash leaving member per month (payment + share deposit)
- `payoff_balance_after_share_netting` — actual cash needed to fully exit

Method: `dividend_benefit_summary()` — returns total gross interest, total
dividend savings, total net interest, and savings% over the full schedule

---

### `PayoffCalculator`

Computes the true cash needed to fully close any `DebtInstrument` on a
given date, including all applicable fees.

```python
calc = PayoffCalculator(debt, fee_config=MORTGAGE_FEE_CONFIG, sign_date=date(2022,3,1))
result = calc.get_payoff_amount(as_of_date=date.today())
```

#### `PayoffFeeConfig` fields

| Field | Description |
|---|---|
| `early_close_fee_pct` | % of remaining balance as prepayment penalty |
| `early_close_fee_fixed` | Flat prepayment penalty ฿ |
| `early_close_fee_waived_after_year` | Year after which penalty is waived (0 = never) |
| `mortgage_release_fee_fixed` | Land Department discharge fee ฿ |
| `mortgage_release_fee_pct` | % of remaining balance for release |
| `stamp_duty_pct` | 0.05% of registered mortgage amount (Thai standard) |
| `legal_fee_fixed` | Bank/lawyer processing fee ฿ |
| `valuation_fee` | Property re-valuation fee ฿ |
| `insurance_refund` | Credit life / fire insurance refund (negative = money back) |
| `other_fees_fixed` | Any other flat fees ฿ |
| `share_netting` | For `CooperativeDebt` — deduct share balance from payoff total |

**Pre-built configs:**

| Constant | Description |
|---|---|
| `MORTGAGE_FEE_CONFIG` | 2% prepayment penalty (waived after year 3), Land Dept ฿100, stamp duty 0.05%, legal ฿2,000 |
| `PERSONAL_LOAN_FEE_CONFIG` | 1% prepayment penalty, legal ฿500 |
| `COOPERATIVE_FEE_CONFIG` | Share netting enabled |
| `NO_FEE_CONFIG` | All fees zero |

**`get_payoff_amount()` output fields:**
`outstanding_principal`, `accrued_interest`, `subtotal`, `early_close_penalty`,
`is_penalty_waived`, `mortgage_release_fee`, `stamp_duty`, `legal_fee`,
`valuation_fee`, `insurance_refund`, `other_fees`, `share_netting`,
`total_payoff`, `fee_breakdown` (list of tuples for UI), `years_held`

---

## 4. Module: `debt_portfolio.py`

**Purpose:** Aggregate a list of `DebtInstrument` objects into the three
Model V.2 scalars (D, r, P) plus Layer 3 quality metrics.

### `DebtPortfolio`

```python
portfolio = (DebtPortfolio(label="My Portfolio")
             .add(debt1).add(debt2).add(debt3))
inputs = portfolio.to_model_v2_inputs()
```

#### Core aggregate properties

| Property | Formula | Description |
|---|---|---|
| `D` | Σ D_i | Total outstanding debt |
| `r` | Σ(r_i × D_i) / Σ D_i | Balance-weighted average effective annual rate |
| `P` | Σ P_i | Total monthly payments |
| `total_monthly_interest` | Σ (D_i × r_i/12) | Total interest charged this month |
| `net_principal_paid_monthly` | P − Σ interest | Actual debt reduction per month |
| `total_remaining_months` | t* formula on D, r, P | Aggregate portfolio payoff time |
| `is_orbit_locked` | P ≤ Σ monthly interest | True if portfolio debt is growing |

#### Payoff strategy helpers

| Method | Description |
|---|---|
| `avalanche_order()` | Sorted highest effective rate first (mathematically optimal) |
| `snowball_order()` | Sorted smallest balance first (psychologically effective) |
| `orbit_locked_debts()` | List of individual debts where payment ≤ interest |
| `highest_rate_debt()` | Single debt with highest effective rate |
| `highest_balance_debt()` | Single debt with highest balance |

#### Layer 3 debt quality properties

| Property | Description |
|---|---|
| `drag_debt_balance` | Total balance of `drag` category debts (CC, personal) |
| `slingshot_debt_balance` | Total balance of `slingshot` category debts (mortgage, education, business) |
| `drag_ratio` | Fraction of total debt that is pure drag (0 = none, 1 = all) |
| `dominant_vm_category` | `"slingshot"`, `"neutral"`, or `"drag"` based on largest share |

#### `to_model_v2_inputs()` — the main interface

Returns a single dict consumed by `ModelV2Layer1.load_portfolio()`:

```python
{
  # Core Model V.2 inputs
  "D": float, "r": float, "P": float,

  # Diagnostics
  "monthly_interest_total": float,
  "monthly_principal_total": float,
  "portfolio_months_remaining": float | None,
  "is_orbit_locked": bool,
  "num_debts": int,

  # Layer 3
  "dominant_vm_category": str,
  "drag_ratio": float,
  "slingshot_balance": float,
  "drag_balance": float,

  # Per-debt breakdown
  "debt_breakdown": [ <summary dict per debt> ],
  "avalanche_order": [ debt names ],
  "snowball_order":  [ debt names ],
}
```

---

## 5. Module: `model_v2.py`

**Purpose:** Three-layer financial physics model. Consumes portfolio
inputs plus user profile to compute orbital zone, escape score,
time-to-freedom, and lifetime trajectory.

### `ModelV2Layer1` — Instantaneous State

**Required inputs:**

| Field | Description |
|---|---|
| `I` | Monthly income ฿ |
| `E_n` | Necessary monthly expenses ฿ |
| `E_d` | Discretionary monthly spending ฿ |
| `behavior_score` | 1–10 (10 = best discipline, 1 = worst) |
| `E_fund` | Emergency fund balance ฿ |
| `months_on_budget` | 0–6: how many of last 6 months on budget |
| `target_freedom_months` | Target payoff timeline (default 60 months) |

Portfolio inputs loaded via `load_portfolio(dict)`: `D`, `r`, `P`

#### Three Forces

| Symbol | Formula | Name |
|---|---|---|
| β | 2 − behavior_score/10 ∈ [1.0, 2.0] | Behavior multiplier (higher = worse) |
| F_p | I − E_n − P | Propulsion Force |
| F_g | D × (r/12) | Debt Gravity |
| F_d | E_d × β | Spending Drag |
| F_net | F_p − F_g − F_d | Net Monthly Force |

#### Escape Score

| Symbol | Formula | Description |
|---|---|---|
| B_s | min(10, E_fund / (3 × E_n) × 10) | Safety buffer bonus (0–10 pts) |
| S_E | (F_net / I) × 100 + B_s | Escape Score |

#### Orbital Zone (based on S_E)

| Zone | S_E Condition | Label |
|---|---|---|
| `escape_trajectory` | S_E > 20 | 🚀 Escape Trajectory |
| `marginal_escape` | 5 ≤ S_E ≤ 20 | 🌕 Marginal Escape |
| `debt_orbit` | −10 ≤ S_E < 5 | ⚠️ Debt Orbit |
| `black_hole` | S_E < −10 | 🕳️ Black Hole |

#### Time to Debt Freedom

| Symbol | Formula | Description |
|---|---|---|
| t* | −ln(1 − D×r_m / P) / r_m | Months to pay off portfolio |
| t*_adjusted | t* × momentum multiplier | Adjusted for behavioral consistency |
| P* | D × r_m / (1 − (1+r_m)^−n) | Minimum payment for target freedom |
| Δ | P* − P | Monthly payment gap (positive = need more) |

`t*_adjusted` momentum multipliers:
- M ≥ 0.8 → ×1.00 (on track)
- M ≥ 0.5 → ×1.20 (moderate risk)
- M < 0.5 → ×1.50 (low confidence)

#### Momentum Index

`M = months_on_budget / 6` ∈ [0, 1]

| M | Confidence |
|---|---|
| ≥ 0.8 | High |
| ≥ 0.5 | Moderate |
| < 0.5 | Low — "Plan requires behavioral change first" |

#### `compute()` output keys

`beta`, `F_p`, `F_g`, `F_d`, `F_net`, `zone`, `zone_label`, `B_s`, `S_E`,
`t_star_raw`, `t_star_adjusted`, `t_star_display`, `orbit_locked`,
`P_star`, `delta`, `gap_message`, `M`, `momentum_display`, `momentum_warning`, `inputs`

---

### `ModelV2Layer2` — Temporal Trajectory

**Additional inputs:** `current_age`, `career_type`, `t_start` (default 22),
`t_retire` (default 60), `d_rate` (discount rate, default 0.03)

#### Career Profiles

| career_type | t_peak | λ (decay rate) |
|---|---|---|
| `physical_trade` | 35 | 0.06 |
| `technical_engineering` | 42 | 0.03 |
| `management_strategy` | 50 | 0.02 |
| `knowledge_advisory` | 55 | 0.01 |

#### Key Metrics

| Symbol | Formula | Description |
|---|---|---|
| HCDF | (t_retire − age) / (t_retire − t_start) | Human Capital Discount Factor — remaining productive career fraction |
| T | t_retire − age | Years to retirement |
| LDER | D₀ × e^(r×T) / Σ [I×12 × HCDF × (1−λ)^t / (1+d)^t] | Lifetime Debt-to-Earning Ratio |
| R | (P × t*_adjusted) / (I × 12 × T) | Lifetime Income Commitment Ratio — no career curve assumptions |

#### LDER Status

| LDER | Status | Label |
|---|---|---|
| < 0.3 | `safe` | ✅ Healthy |
| 0.3–0.7 | `warning` | ⚠️ Elevated |
| 0.7–1.0 | `critical` | 🔴 High |
| ≥ 1.0 | `true_event_horizon` | 🔴 Very High |

#### True Event Horizon Warning (based on R)

| R | Warning |
|---|---|
| = ∞ (orbit-locked) | ⚫ Orbit-locked — 100%+ of lifetime income cannot cover debt |
| > 0.8 | ⚫ True Event Horizon — R% of lifetime income committed |
| > 0.5 | 🔴 Critical |
| > 0.3 | ⚠️ Warning |
| ≤ 0.3 | No warning |

#### `compute()` output keys

`HCDF`, `career_type`, `career_lambda`, `T_years`, `LDER`, `lder_status`,
`lder_label`, `R`, `R_pct`, `teh_warning`

---

## 6. Module: `nudge_engine.py`

**Purpose:** Find the minimum behavioral changes that upgrade the user's
orbital zone, ranked by effort required.

### Constants

**`ZONE_LABELS`** — display strings for each zone key

**`ZONE_ORDER`** — `["black_hole", "debt_orbit", "marginal_escape", "escape_trajectory"]`
— used to compare zone ranks numerically

**`BEHAVIOR_ACTIONS`** — 5 habit action strings, indexed 0–4:

| Index | Action |
|---|---|
| 0 | Set up auto-payment to eliminate missed payments |
| 1 | Apply a 24-hour pause before any purchase over ฿500 |
| 2 | Track every expense daily for 30 days |
| 3 | Keep credit utilization below 30% of each card limit |
| 4 | Freeze new credit applications for 6 months |

Triggered when `behavior_questions[i] < 2`.

### `NudgeEngine`

**Inputs:** `layer1` (ModelV2Layer1), `layer2` (ModelV2Layer2),
optional `behavior_questions` (list of 5 scores 0–3)

#### Four Levers (in priority order)

| # | Lever | Field | Direction | Step Size | Max Steps |
|---|---|---|---|---|---|
| 1 | Spending cut | E_d | ↓ | E_d × 10% | 2 |
| 2 | Behavior score | behavior_score | ↑ | 1 pt | 2 |
| 3 | Payment boost | P | ↑ | max(P × 10%, ฿100) | 2 |
| 4 | Income boost | I | ↑ | I × 10% | 1 |

Income is capped at 1 step because a >10% raise is considered unrealistic as advice.

#### `generate()` Algorithm

1. Test each lever individually: find minimum steps (1 or 2) that achieve a zone upgrade
2. Sort feasible single-lever results by step count, take top 2
3. If a slot remains, search multi-lever combinations:
   - **Pass 1:** all combinations of 2–4 levers, each at exactly 1 step
   - **Pass 2:** 2–3 levers, allowing one lever at 2 steps
   - Returns the smallest combo that achieves zone upgrade
4. Return top 3 total (up to 2 singles + 1 mix)

Returns `[]` if already in `escape_trajectory`.

#### Nudge Output Dict Fields

| Field | Type | Description |
|---|---|---|
| `rank` | int | 1, 2, or 3 |
| `type` | str | `"single"` or `"mix"` |
| `steps` | int | Total steps applied |
| `new_SE` | float | Projected escape score |
| `delta_SE` | float | Change in escape score |
| `delta_t` | float\|None | Months faster (positive) or longer (negative); None if orbit-locked |
| `new_zone` | str | Zone key after nudge |
| `zone_upgraded` | bool | True if zone improved |
| `unlocks_orbit` | bool | True if t* goes from ∞ to finite |
| Single only: `lev` | dict | Lever definition |
| Single only: `pct` | float | % change in lever field |
| Single only: `new_val` | float | New field value |
| Mix only: `parts` | list | Per-lever details with name, field, steps, pct, base_val, new_val, sign |
| Mix only: `label` | str | Human-readable combined label |

---

## 7. Module: `payoff_strategy.py`

**Purpose:** Simulate month-by-month debt paydown across up to 15+
strategy variants and rank them by Binding Energy (total interest paid).

### Simulation Primitives

#### `SimDebt`

Lightweight mutable copy of a `DebtInstrument` used only inside the
simulator. Created via `SimDebt.from_instrument(d)`. Never mutates originals.

Fields: `name`, `balance`, `monthly_rate`, `min_payment`, `vm_category`, `paid_off`

Properties: `monthly_interest`, `effective_annual_rate`

#### `MonthlySnapshot`

State of the portfolio at the end of one simulated month.

Fields: `month`, `total_balance`, `total_interest`, `total_payment`,
`weighted_rate`, `debts_remaining`

#### `StrategyResult`

Output for one complete strategy run.

| Field | Description |
|---|---|
| `strategy_name` | Strategy label |
| `binding_energy` | Total interest paid over full paydown ฿ |
| `months_to_freedom` | Months until last debt reaches zero |
| `be_savings` | Interest saved vs minimum-payment baseline ฿ |
| `be_savings_pct` | Savings as % of baseline binding energy |
| `required_extra_monthly` | Extra ฿/month above current minimums |
| `monthly_snapshots` | List of `MonthlySnapshot` |
| `description` | Human-readable strategy description |

Properties: `years_to_freedom` (float), `years_str` (e.g. "4y 6mo")

### Core Simulation: `_simulate()`

Three-step monthly loop:

1. **Accrue interest** — add `balance × monthly_rate` to each debt's balance
2. **Pay minimums** — deduct `min(min_payment, balance)` from each debt;
   raise `ValueError` if minimum does not cover interest (no negative amortization)
3. **Cascade extra** — any freed minimum payments roll into `extra_pool`;
   distribute `extra_pool` to debts in `priority_fn` order

Raises `RuntimeError` if simulation does not converge within `max_months` (default 600).

Returns: `(total_interest, months, snapshots)`

### Priority Functions

| Function | Sort Logic |
|---|---|
| `_avalanche_priority(debts)` | Highest `monthly_rate` first — minimises total interest |
| `_snowball_priority(debts)` | Smallest `balance` first — maximises psychological wins |
| `_hybrid_priority(alpha)` | Weighted blend: `score = α × rate_norm + (1−α) × (1 − balance_norm)` |

Default hybrid α = 0.6 (60% toward avalanche, 40% toward snowball).

### Portfolio Transformation Helpers

| Function | Description |
|---|---|
| `annual_to_monthly(rate)` | Convert annual to compound-equivalent monthly rate |
| `_amortizing_payment(balance, monthly_rate, n_months)` | Standard annuity payment formula |
| `apply_retention(sim_debts, target_name, new_annual_rate, new_term_months)` | Reduce rate on one debt via negotiation; no fees; recomputes min_payment |
| `apply_retention_all(sim_debts, rate_reduction, new_term_months)` | Apply flat rate reduction to every debt |
| `apply_refinance(sim_debts, target_name, new_annual_rate, new_term_months, fee_pct, fee_fixed)` | Replace debt with new loan; fee rolled into balance |
| `apply_consolidation(sim_debts, names_to_merge, new_annual_rate, new_term_months, fee_pct, fee_fixed)` | Merge selected debts into one new loan; fee rolled in; `names_to_merge=None` merges all |

### `StrategyEngine`

**Constructor parameters:**

| Parameter | Default | Description |
|---|---|---|
| `portfolio` | required | `DebtPortfolio` source |
| `extra_monthly` | 0.0 | Extra ฿/month to apply |
| `refi_rate` | 0.12 | Target rate for refinanced debt |
| `refi_term_months` | 60 | Term for refinanced loan |
| `refi_fee_pct` | 0.01 | Refinancing fee as % of balance |
| `consolidation_rate` | 0.12 | Rate for consolidated loan |
| `consol_term_months` | 60 | Term for consolidated loan |
| `consol_fee_pct` | 0.015 | Consolidation fee as % of balance |
| `retention_reduction` | 0.04 | Annual rate reduction via negotiation |
| `retention_term` | 60 | Re-amortisation term after retention |

#### `run_all()` — Strategies Generated

| # | Strategy | Description |
|---|---|---|
| 0 | Minimum Payments (Baseline) | Minimums only, cascades freed payments |
| 1 | Avalanche | Highest rate first |
| 2 | Snowball | Smallest balance first |
| 3 | Hybrid (α=0.6) | 60/40 rate/balance blend |
| 4–N | Retention: \<debt\> → X% | Negotiate rate on each eligible debt individually, then Hybrid |
| N+1 | Retention: All Debts + Avalanche | Reduce all rates, then Avalanche |
| N+2 | Retention: All Debts + Hybrid | Reduce all rates, then Hybrid |
| N+3 | Refinance: \<debt\> → X% | Replace highest-rate debt with lower-rate loan, then Hybrid |
| N+4 | Refinance → Avalanche | Same refinance, then Avalanche |
| N+5 | Consolidate All → X% | Merge all debts, then Hybrid |
| N+6 | Consolidate All → Avalanche | Merge all debts, then Avalanche |
| N+7 | Consolidate Drag Debts → X% | Merge only drag debts; keep slingshot debts separate |
| N+8 | Retention + Consolidate → Avalanche | Negotiate reduction on all, then consolidate, then Avalanche |

Results are sorted: baseline first, then all others ascending by `binding_energy`.

#### `print_report(top_n=8)`

Prints a ranked comparison table showing interest paid, months to freedom,
฿ saved, and % saved for each strategy, followed by a detailed best-strategy
recommendation block.

---

## 8. REST API — `app.py`

Flask server. Binds to `0.0.0.0` (all interfaces) so phones on the same
WiFi can connect. Prints both localhost and LAN IP on startup.
CORS enabled for `file://` and any localhost origin.
`math.inf` serialised as the string `"Infinity"`.

**Install and run:**
```bash
pip install flask flask-cors
python app.py
# Prints:
# ====================================================
#   Debt API server starting
# ====================================================
#   Local  :  http://127.0.0.1:5000
#   Network:  http://192.168.x.x:5000  ← use this on phone
# ====================================================
```

### Internal Helpers

| Function | Description |
|---|---|
| `_sanitize(obj)` | Recursively replace `math.inf` / `NaN` for safe JSON |
| `_err(msg, code)` | Return `{"error": msg}` with HTTP status |
| `_purpose(s)` | Map string to `DebtPurpose` enum (case-insensitive) |
| `_build_debt(d)` | Dispatch JSON dict to correct `DebtInstrument` subclass |
| `_build_portfolio(debts_data)` | Build `DebtPortfolio` from list of debt dicts |
| `_build_layer1(data, model_inputs)` | Construct `ModelV2Layer1` from request dict |

### Endpoint Reference

#### `GET /api/health`
Returns `{"status": "ok", "version": "model_v2"}`

---

#### `GET /api/debt/types`
Returns all supported debt type keys, their required/optional fields,
and the full list of valid `purpose` values. Use to build dynamic forms.

---

#### `POST /api/portfolio/summary`
**Input:** `{"debts": [...]}`

Returns `DebtPortfolio.to_model_v2_inputs()` — D, r, P, breakdown, orbit
status, avalanche/snowball order, Layer 3 VM fields. No model layers run.

---

#### `POST /api/layer1`
**Input:** debts + user profile fields

Returns `ModelV2Layer1.compute()` — forces, zone, S_E, t*, P*, gap, momentum.

---

#### `POST /api/layer2`
**Input:** same as layer1 + `current_age`, `career_type`, `t_start`, `t_retire`

Returns `ModelV2Layer2.compute()` — HCDF, LDER, R, True Event Horizon warning.

---

#### `POST /api/layer3`
**Input:** `{"debts": [...]}`

Returns `ModelV2Layer3.compute()` — VM badge, drag ratio, slingshot/drag balances.

---

#### `POST /api/portfolio/analyze`
Full pipeline in one call.

**Output:**
```json
{
  "portfolio":     { "D", "r", "P", "breakdown", "avalanche_order", ... },
  "layer1":        { "F_p", "F_g", "F_d", "F_net", "zone", "S_E", "t_star_display", ... },
  "layer2":        { "HCDF", "LDER", "R_pct", "teh_warning", ... },
  "layer3":        { "debt_type_badge", "drag_ratio_pct", ... },
  "nudge_summary": { "current_zone", "zone_label", "already_escaped", "num_nudges" }
}
```

---

#### `POST /api/nudge`
**Input:** same as `/api/portfolio/analyze` + optional `"behavior_questions": [0,2,1,2,1]`

**Output:**
```json
{
  "current_zone": "debt_orbit",
  "zone_label":   "⚠️  Debt Orbit",
  "S_E":          -3.2,
  "t_star":       "4 years 2 months",
  "beta":         1.4,
  "already_escaped": false,
  "teh_warning":  null,
  "nudges": [
    {
      "rank": 1, "type": "single",
      "lever_name": "Spending cut", "lever_field": "E_d", "lever_unit": "฿/mo",
      "steps": 1, "pct_change": 10.0, "base_value": 15000, "new_value": 13500,
      "new_SE": 1.8, "delta_SE": 5.0, "delta_t_months": 8,
      "new_zone": "marginal_escape", "new_zone_label": "🟡 Marginal Escape",
      "zone_upgraded": true, "unlocks_orbit": false,
      "drag_saved": 2100.0, "behavior_hint": null, "mix_parts": null
    }
  ]
}
```

For `type: "mix"`, `mix_parts` is a list of per-lever objects and
single-lever fields (`lever_name`, `pct_change`, etc.) are `null`.

---

#### `POST /api/debt/amortization`
**Input:** `{"debt": <debt_object>, "months": 12}` (`months: 0` = full schedule)

**Output:** `{"summary": <debt summary>, "schedule": [<rows>]}`

Row fields vary by instrument type — all include `month`, `balance_start`,
`interest_charge`, `principal_paid`, `payment`, `balance_end`,
`cumulative_interest`, `orbit_locked`. Extended types add type-specific
fields (e.g. `rate_period_label` for step-up, `dividend_offset` for cooperative,
`is_balloon` for balloon).

---

#### `POST /api/debt/payoff`
**Input:**
```json
{
  "debt": <debt_object>,
  "fee_preset": "mortgage" | "personal_loan" | "cooperative" | "none",
  "sign_date":  "2022-03-01",
  "as_of_date": "2025-05-20"
}
```

Returns full payoff breakdown: each fee line, `total_payoff`, `fee_breakdown`
list of tuples for UI display, `is_penalty_waived`, `years_held`.

---

## 9. JavaScript Client — `api.js`

Drop-in browser client. No build step required.

```html
<script src="api.js"></script>
```

### `DebtAPI` class

**Constructor:** `new DebtAPI(baseUrl = "http://localhost:5000")`

All methods are `async`. They return parsed JSON on success and throw
`Error` with a human-readable message on failure.

| Method | Endpoint | Description |
|---|---|---|
| `health()` | GET /api/health | Ping server |
| `getDebtTypes()` | GET /api/debt/types | Supported types + fields |
| `getPortfolioSummary(debts)` | POST /api/portfolio/summary | Portfolio roll-up only |
| `analyzePortfolio(payload)` | POST /api/portfolio/analyze | Full pipeline |
| `getLayer1(params)` | POST /api/layer1 | Layer 1 only |
| `getLayer2(params)` | POST /api/layer2 | Layer 2 only |
| `getLayer3(debts)` | POST /api/layer3 | Layer 3 only |
| `getNudges(params)` | POST /api/nudge | Nudge recommendations |
| `getAmortization(debt, months)` | POST /api/debt/amortization | Schedule for one debt |
| `getPayoffQuote(debt, options)` | POST /api/debt/payoff | Payoff quote with fees |

`getPayoffQuote` options: `{ feePreset, signDate, asOfDate }` (all optional strings)

---

### `DebtBuilder` object

Factory functions producing correctly-shaped debt objects using camelCase.

| Method | Debt Type |
|---|---|
| `DebtBuilder.effective({...})` | `EffectiveRateDebt` |
| `DebtBuilder.creditCard({...})` | `CreditCardDebt` |
| `DebtBuilder.hirePurchase({...})` | `HirePurchaseDebt` |
| `DebtBuilder.flat({...})` | `FlatRateDebt` |
| `DebtBuilder.balloon({...})` | `BalloonDebt` |
| `DebtBuilder.stepUp({...})` | `StepUpRateDebt` |
| `DebtBuilder.floating({...})` | `FloatingRateDebt` |
| `DebtBuilder.annualStepUp({...})` | `AnnualStepUpDebt` |
| `DebtBuilder.cooperative({...})` | `CooperativeDebt` |

---

### `DebtFormat` object

| Method | Description |
|---|---|
| `baht(amount, decimals)` | Format as `฿1,234,567` |
| `pct(rate, decimals)` | Format as `6.50%` |
| `zoneLabel(layer1)` | Zone emoji + label from layer1 result |
| `lderLabel(layer2)` | LDER status label from layer2 result |
| `vmBadge(layer3)` | VM badge string from layer3 result |
| `renderAmortTable(tableEl, schedule)` | Render schedule into a `<table>` DOM element |
| `summarizeAnalysis(result)` | Flatten full analysis into a display-ready flat object |

---

### `NudgeFormat` object

| Method | Description |
|---|---|
| `summary(nudge)` | One-line description: `"Spending cut ↓10%: ฿15,000 → ฿13,500 ฿/mo"` |
| `outcome(nudge, currentZoneLabel)` | Zone-change + ΔS_E + time delta string |
| `renderList(ulEl, nudges, currentZoneLabel)` | Renders all nudges into a `<ul>` with `data-rank`, `data-type`, `data-zone-upgraded`, `data-unlocks-orbit` attributes |

---

## 10. Data Schemas

### Debt Object (sent to API)

All types require: `type`, `name`, `current_balance`, `annual_rate`

**Common optional fields (all types):**
`creditor`, `purpose`, `original_balance`, `original_term_months`,
`remaining_term_months`, `minimum_payment`, `early_close_fee`, `other_fees`

**Type-specific required fields:**

| type | Additional Required |
|---|---|
| `effective` | `remaining_term_months` |
| `credit_card` | *(none beyond common)* |
| `hire_purchase` | `total_installments`, `paid_installments` |
| `flat` | `original_term_months` |
| `balloon` | `balloon_month` |
| `compound` | *(none)* |
| `step_up` | `rate_schedule: [{months_duration, annual_rate, label}]` |
| `floating` | `reference_rate`, `spread` |
| `annual_step_up` | `initial_rate` |
| `cooperative` | `loan_rate`, `share_subscription` |

### User Profile Object

```json
{
  "income":                 120000,
  "necessary_expenses":     35000,
  "discretionary_expenses": 15000,
  "behavior_score":         4,
  "emergency_fund":         90000,
  "months_on_budget":       4,
  "target_freedom_months":  60,
  "current_age":            38,
  "career_type":            "technical_engineering",
  "t_start":                22,
  "t_retire":               60,
  "behavior_questions":     [0, 2, 1, 2, 1]
}
```

`career_type` values: `physical_trade`, `technical_engineering`,
`management_strategy`, `knowledge_advisory`

`behavior_questions` — optional; 5 integers (0–3) for habit consistency.
Only used by `/api/nudge`.

---

## 11. Key Formulas

### Flat Rate → Effective Monthly Rate

Newton-Raphson solver. Finds `r_monthly` such that:
```
Σ_{t=1}^{n} PMT / (1+r)^t = 1
where PMT = (1 + flat_rate × T) / n   (per unit of debt, T = n/12)
```
Initial guess: `flat_rate / 12 × 1.8`

### Balance-Weighted Average Rate

```
r_portfolio = Σ(r_i × D_i) / Σ D_i
```
Ensures `D × r/12 = Σ(D_i × r_i/12)` so portfolio Debt Gravity F_g is correct.

### Time to Debt Freedom (t*)

```
t* = −ln(1 − D × r_m / P) / r_m     where r_m = r / 12
Special: r_m = 0  →  t* = D / P
Special: P ≤ D × r_m  →  t* = ∞  (orbit lock)
```

### Escape Velocity Payment (P*)

```
P* = D × r_m / (1 − (1 + r_m)^−n)     where n = target_freedom_months
```

### Lifetime Debt-to-Earning Ratio (LDER)

```
Numerator:   D₀ × e^(r × T)
Denominator: Σ_{t=0}^{T} I × HCDF × (1−λ)^t / (1+d)^t
```
λ = career decay rate (from `CAREER_PROFILES`), d = 0.03 discount rate

### Hybrid Priority Score

```
score(d) = α × (monthly_rate / max_rate) + (1−α) × (1 − balance / max_balance)
```
Default α = 0.6. Higher score = pay off sooner.

---

## 12. Running the System

### File Layout

All Python files in the same directory:
```
app.py
debt_instruments.py
debt_instruments_extended.py
debt_portfolio.py
model_v2.py
nudge_engine.py
payoff_strategy.py
api.js
```

### Install

```bash
pip install flask flask-cors
```

### Start

```bash
python app.py
```

### Minimal HTML Example

```html
<!DOCTYPE html>
<html>
<body>
  <div id="zone"></div>
  <ul id="nudges"></ul>
  <script src="api.js"></script>
  <script>
  (async () => {
    const api = new DebtAPI();
    const payload = {
      debts: [
        DebtBuilder.creditCard({ name: "KBank CC", currentBalance: 45000, annualRate: 0.18, minimumPayment: 2500 }),
        DebtBuilder.effective({ name: "GH Mortgage", purpose: "mortgage",
          currentBalance: 2400000, annualRate: 0.065, remainingTermMonths: 180, minimumPayment: 18000 }),
      ],
      income: 120000, necessary_expenses: 35000, discretionary_expenses: 15000,
      behavior_score: 4, emergency_fund: 90000, months_on_budget: 4,
      target_freedom_months: 60, current_age: 38,
      career_type: "technical_engineering", t_start: 22, t_retire: 60,
    };

    const result  = await api.analyzePortfolio(payload);
    const display = DebtFormat.summarizeAnalysis(result);
    document.getElementById("zone").textContent =
      `${display.zone}  S_E: ${display.escapeScore}  —  ${display.gapMessage}`;

    const nudgeResult = await api.getNudges(payload);
    NudgeFormat.renderList(document.getElementById("nudges"),
      nudgeResult.nudges, nudgeResult.zone_label);
  })();
  </script>
</body>
</html>
```

### Notes

- All monetary values are Thai Baht (฿); rates are always decimals (0.18 = 18%)
- `math.inf` is serialised as the string `"Infinity"` — check for it when rendering t* in JS
- `nudge_engine.print_report()` calls `layer2.R` which exists in the current version of `model_v2.py`
- CORS is wide-open in dev mode; lock it down for production:
  `CORS(app, origins=["https://yourdomain.com"])`
- The `payoff_strategy.py` module is not yet wired into `app.py` — add a
  `POST /api/strategy` endpoint following the same pattern as the other routes