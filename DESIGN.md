# DESIGN.md — Debt Pipeline System Reference

Full technical reference for the Debt → Portfolio → Model V.2 → Nudge Engine pipeline,
its Python modules, REST API, and JavaScript client.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Python Modules](#2-python-modules)
   - [debt_instruments.py](#21-debt_instrumentspy)
   - [debt_instruments_extended.py](#22-debt_instruments_extendedpy)
   - [debt_portfolio.py](#23-debt_portfoliopy)
   - [model_v2.py](#24-model_v2py)
   - [nudge_engine.py](#25-nudge_enginepy)
3. [REST API — app.py](#3-rest-api--apppy)
4. [JavaScript Client — api.js](#4-javascript-client--apijs)
5. [Data Schemas](#5-data-schemas)
6. [Key Formulas](#6-key-formulas)
7. [Running the System](#7-running-the-system)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                     HTML / Frontend                      │
│                  (calls via api.js)                      │
└─────────────────────────┬────────────────────────────────┘
                          │ HTTP JSON
┌─────────────────────────▼────────────────────────────────┐
│                  app.py  (Flask API)                     │
│         localhost:5000  —  CORS enabled                  │
└──┬──────────┬──────────┬──────────┬──────────┬───────────┘
   │          │          │          │          │
   ▼          ▼          ▼          ▼          ▼
debt_       debt_      debt_     model_v2   nudge_
instruments instruments portfolio  .py      engine
.py         _extended   .py                 .py
            .py
```

All Python files sit in the same directory. `app.py` imports from all five
modules and exposes them as JSON endpoints. `api.js` wraps every endpoint
in typed async methods for use in any HTML page.

---

## 2. Python Modules

### 2.1 `debt_instruments.py`

**Purpose:** Compute per-debt financial properties — effective rate, monthly
payment, monthly interest charge, months remaining, and full amortization
schedule — for every Thai debt product type.

#### Base class: `DebtInstrument`

All subclasses share this interface. Model V.2 only needs these four properties:

| Property | Symbol | Description |
|---|---|---|
| `current_balance` | D_i | Outstanding principal right now |
| `effective_annual_rate` | r_i | Compound-equivalent annual rate |
| `monthly_payment` | P_i | Payment being made this month |
| `monthly_interest_charge` | F_g_i | Interest cost this month (D_i × r_i/12) |

Additional properties:

| Property | Description |
|---|---|
| `months_remaining` | t* for this debt using Model V.2 formula |
| `is_orbit_locked` | True if payment ≤ monthly interest (debt is growing) |
| `amortization_schedule()` | List of monthly dicts: balance, interest, principal |
| `summary()` | Full dict snapshot for portfolio aggregation and display |

#### Enums

**`InterestType`**
- `FLAT` — flat rate (total interest pre-calculated at signing)
- `EFFECTIVE` — compound/APR-style (standard amortization)
- `COMPOUND` — explicit compound with configurable frequency

**`DebtPurpose`** — used for Layer 3 VM category classification:

| Purpose | VM Category |
|---|---|
| `EDUCATION`, `MORTGAGE`, `BUSINESS` | `slingshot` (productive debt) |
| `CREDIT_CARD`, `PERSONAL` | `drag` (consumptive debt) |
| `AUTO`, `MEDICAL`, `MIXED`, `OTHER` | `neutral` |

#### Subclasses

**`FlatRateDebt`**
Thai-style loan where total interest = principal × flat_rate × years,
pre-calculated at signing. Monthly payment is fixed from day one.
Uses Rule of 78s by default (`use_rule_of_78=True`) to front-load interest.

Key fields: `original_balance`, `annual_rate`, `original_term_months`,
`remaining_term_months`, `use_rule_of_78`

**`EffectiveRateDebt`**
Standard amortizing loan (mortgage, personal loan, car loan with APR).
Uses textbook annuity formula: P = D × r / (1 − (1+r)^−n).

Key fields: `current_balance`, `annual_rate`, `remaining_term_months`,
`minimum_payment`

**`CompoundRateDebt`**
Like `EffectiveRateDebt` but lets you specify `compounding_frequency`
(e.g. 365 for daily compounding). Converts to monthly effective rate
via EAR = (1 + r/m)^m − 1.

**`CreditCardDebt`**
Revolving debt. Minimum payment = max(floor, balance × pct). New monthly
charges can be modeled. Re-computes minimum payment on actual balance
each month in the schedule.

Key fields: `annual_rate`, `min_payment_pct` (default 5%),
`min_payment_floor` (default ฿500), `monthly_new_charges`, `minimum_payment`

**`HirePurchaseDebt`**
Thai hire-purchase (ผ่อนชำระ) — flat rate with fixed installments.
Common for vehicles and electronics. Essentially `FlatRateDebt` but
driven by `total_installments`, `paid_installments`, and
`installment_amount` rather than term months.

**`BalloonDebt`**
Period loan with a lump-sum due at maturity. Supports interest-only
(`interest_only=True`) or partial amortization. The balloon payment
is the final row in the amortization schedule.

Key fields: `balloon_amount`, `balloon_month`, `interest_only`

#### Internal helpers

`_flat_to_effective_monthly(flat_annual_rate, term_months)` — Newton-Raphson
solver that converts a flat annual rate to its compound-equivalent monthly
rate. This is used by all flat-rate instruments so portfolio weighting stays
mathematically consistent.

---

### 2.2 `debt_instruments_extended.py`

**Purpose:** Five additional instrument types covering Thai-specific products,
plus a universal payoff calculator with a full fee stack.

#### `StepUpRateDebt`

Mortgage or loan with multiple scheduled rate periods (promotional → standard).
Typical Thai bank pattern: Year 1–2 promo rate, Year 3+ standard rate.

Key fields:
- `rate_schedule` — list of `RatePeriod(months_duration, annual_rate, label)`
- `months_elapsed` — how many months have already been served (positions
  the simulation at the correct point in the schedule)

The amortization schedule re-amortizes the payment at each rate change,
annotating every row with `rate_period_label` and `annual_rate_pct`.

Helper properties: `current_rate_label`, `months_in_current_period_remaining`

#### `FloatingRateDebt`

MRR/MLR/THOR-linked floating rate debt. Effective rate = `reference_rate + spread`
(spread is typically negative, e.g. MRR − 1.5%).

Key fields: `reference_rate`, `spread`, `reference_name`

Methods:
- `set_reference_rate(new_rate)` — update after a BOT policy change; recomputes
  payment and months_remaining in-place
- `rate_shock_analysis(shocks)` — returns impact table for a list of rate
  delta scenarios (e.g. [+0.005, +0.01, +0.02])

#### `AnnualStepUpDebt`

กยศ. student loan pattern — rate increases by `step_rate` each repayment year,
capped at `max_rate`. Supports a `grace_months` period where interest
capitalises but no payment is required. Uses simple interest by default
(`simple_interest=True`), matching กยศ. contract terms.

Key fields: `initial_rate`, `step_rate`, `max_rate`, `years_elapsed`,
`grace_months`, `simple_interest`

Helper method: `rate_ladder()` — returns the full rate schedule by year

#### `CooperativeDebt`

สหกรณ์ออมทรัพย์ loan. The member holds a share subscription that earns a
dividend, offsetting the gross loan interest. Net effective rate fed to
Model V.2 is: r_net = loan_rate − (shares / balance) × dividend_yield.

Key fields: `share_subscription`, `monthly_share_deposit`, `dividend_yield`,
`loan_rate`, `share_offset_allowed`, `shares_netted_at_payoff`

Overrides `effective_annual_rate` and `monthly_interest_charge` to use
net (post-dividend) values for portfolio weighting.

Extra property: `payoff_balance_after_share_netting` — actual cash needed
to exit if shares can be applied against the balance.

Method: `dividend_benefit_summary()` — total gross interest, dividend savings,
and net interest over the full amortization.

#### `PayoffCalculator`

Computes the true cash needed to fully close any `DebtInstrument` on a
given date, including all applicable fees.

```python
calc = PayoffCalculator(debt, fee_config=MORTGAGE_FEE_CONFIG, sign_date=date(2022,3,1))
result = calc.get_payoff_amount(as_of_date=date.today())
```

**`PayoffFeeConfig` fields:**

| Field | Description |
|---|---|
| `early_close_fee_pct` | % of remaining balance (prepayment penalty) |
| `early_close_fee_fixed` | Flat penalty ฿ |
| `early_close_fee_waived_after_year` | Year after which penalty is waived (0 = never) |
| `mortgage_release_fee_fixed` | Land Department discharge fee |
| `mortgage_release_fee_pct` | % of remaining balance |
| `stamp_duty_pct` | 0.05% of registered mortgage amount (standard) |
| `legal_fee_fixed` | Bank/lawyer processing fee |
| `valuation_fee` | Property re-valuation fee |
| `insurance_refund` | Credit life / fire insurance refund (negative) |
| `other_fees_fixed` | Any other flat fees |
| `share_netting` | For `CooperativeDebt` — net share balance from payoff total |

**Pre-built configs:** `MORTGAGE_FEE_CONFIG`, `PERSONAL_LOAN_FEE_CONFIG`,
`COOPERATIVE_FEE_CONFIG`, `NO_FEE_CONFIG`

**`get_payoff_amount()` returns:**
`outstanding_principal`, `accrued_interest`, `subtotal`, each fee line,
`total_payoff`, `fee_breakdown` (list of tuples for UI display),
`is_penalty_waived`, `years_held`

---

### 2.3 `debt_portfolio.py`

**Purpose:** Aggregate individual `DebtInstrument` objects into the three
scalar inputs that Model V.2 needs: D, r, P.

#### `DebtPortfolio`

```python
portfolio = (DebtPortfolio(label="My Portfolio")
             .add(debt1).add(debt2).add(debt3))
model_inputs = portfolio.to_model_v2_inputs()
```

**Core aggregates:**

| Property | Formula | Description |
|---|---|---|
| `D` | Σ D_i | Total outstanding debt |
| `r` | Σ(r_i × D_i) / Σ D_i | Balance-weighted average effective annual rate |
| `P` | Σ P_i | Total monthly debt payments |
| `total_monthly_interest` | Σ (D_i × r_i/12) | Total interest this month |
| `net_principal_paid_monthly` | P − Σ interest | Actual debt reduction per month |
| `total_remaining_months` | Model V.2 t* formula on D, r, P | Portfolio payoff time |
| `is_orbit_locked` | P ≤ Σ monthly interest | True if debt is growing overall |

**Payoff strategy helpers:**
- `avalanche_order()` — sorted by highest effective rate first (mathematically optimal)
- `snowball_order()` — sorted by smallest balance first (psychologically effective)
- `orbit_locked_debts()` — list of individual debts where payment ≤ interest
- `highest_rate_debt()` / `highest_balance_debt()`

**Layer 3 debt quality:**
- `drag_debt_balance` / `slingshot_debt_balance` — totals by VM category
- `drag_ratio` — fraction of total debt that is pure consumptive drag (0–1)
- `dominant_vm_category` — `"slingshot"`, `"neutral"`, or `"drag"` based on
  the largest balance share

**`to_model_v2_inputs()` — the main interface:**
Returns a single dict with D, r, P plus diagnostics (monthly interest,
monthly principal, months_remaining, orbit_locked, num_debts), Layer 3 fields
(dominant_vm_category, drag_ratio, slingshot_balance, drag_balance),
per-debt breakdown, and avalanche/snowball ordering.

---

### 2.4 `model_v2.py`

**Purpose:** Three-layer financial physics model. Consumes
`DebtPortfolio.to_model_v2_inputs()` plus user profile inputs.

#### Layer 1 — `ModelV2Layer1`: Instantaneous State

**User inputs:** `I` (monthly income), `E_n` (necessary expenses),
`E_d` (discretionary spending), `behavior_score` (0–10), `E_fund`
(emergency fund ฿), `months_on_budget` (0–6)

**Portfolio inputs (via `load_portfolio()`):** D, r, P

**Three Forces:**

| Symbol | Formula | Name |
|---|---|---|
| F_p | I − E_n − P | Propulsion Force |
| F_g | D × (r/12) | Debt Gravity |
| F_d | E_d × β | Spending Drag |
| F_net | F_p − F_g − F_d | Net Monthly Force |
| β | 1 + behavior_score/10 | Behavior multiplier ∈ [1.0, 2.0] |

**Orbital Zone** (based on F_net vs ε = 0.05 × I):

| Zone | Condition | Label |
|---|---|---|
| `escape_trajectory` | F_net > ε | 🚀 Escape Trajectory |
| `marginal_escape` | 0 < F_net ≤ ε | 🌕 Marginal Escape |
| `debt_orbit` | −ε ≤ F_net ≤ 0 | ⚠️ Debt Orbit |
| `black_hole` | F_net < −ε | 🕳️ Black Hole |

**Escape Score:**
- B_s = min(10, E_fund / (3 × E_n) × 10) — safety buffer bonus
- S_E = (F_net / I) × 100 + B_s

**Time to Debt Freedom:**
- t* = −ln(1 − D×r/12 / P) / (r/12) — months to pay off entire portfolio
- Returns `math.inf` if orbit-locked (P ≤ D × r/12)
- `t_star_adjusted` — stretched by +20% if momentum index is moderate (M < 0.8)

**Escape Velocity Gap:**
- P* = D × (r/12) / (1 − (1 + r/12)^−n) — minimum payment for target freedom
- Δ = P* − P (positive = need more per month)

**Momentum Index:**
- M = months_on_budget / 6  ∈ [0, 1]
- High ≥ 0.8, Moderate ≥ 0.5, Low < 0.5

**`compute()` returns:** all forces, zone, B_s, S_E, t*, P*, Δ, gap message,
momentum display, momentum warning, and raw inputs for audit

#### Layer 2 — `ModelV2Layer2`: Temporal Trajectory

**Inputs:** Layer 1 instance + `current_age`, `career_type`, `t_start`, `t_retire`

**Career profiles** (determines λ — earning decay rate):

| Career Type | t_peak | λ |
|---|---|---|
| `physical_trade` | 35 | 0.06 |
| `technical_engineering` | 42 | 0.03 |
| `management_strategy` | 50 | 0.02 |
| `knowledge_advisory` | 55 | 0.01 |

**Key metrics:**

| Symbol | Formula | Description |
|---|---|---|
| HCDF | (t_retire − age) / (t_retire − t_start) | Human Capital Discount Factor — remaining productive fraction |
| LDER | D₀ × e^(r×T) / Σ [I × HCDF × (1−λ)^t / (1+d)^t] | Lifetime Debt-to-Earning Ratio |

**LDER status:**

| LDER | Status |
|---|---|
| < 0.3 | ✅ Safe |
| 0.3–0.7 | ⚠️ Warning |
| 0.7–1.0 | 🔴 Critical |
| ≥ 1.0 | ⚫ True Event Horizon — mathematically unpayable |

#### Layer 3 — `ModelV2Layer3`: Debt Quality

**Input:** `portfolio_inputs` dict from `DebtPortfolio.to_model_v2_inputs()`

Reads `dominant_vm_category` and `drag_ratio` to produce:

| VM Category | Badge |
|---|---|
| `slingshot` | 🚀 Gravitational Slingshot |
| `neutral` | 🛸 Neutral |
| `drag` | 🪨 Pure Drag |

**`compute()` returns:** badge, drag_ratio_pct, slingshot_balance, drag_balance,
debt_breakdown list

---

### 2.5 `nudge_engine.py`

**Purpose:** Given a user's current Layer 1 state, find the minimum
behavioral changes that achieve a zone upgrade — and rank them by effort.

#### `NudgeEngine`

**Inputs:** `layer1` (ModelV2Layer1), `layer2` (ModelV2Layer2),
optional `behavior_questions` (list of 5 scores 0–3 for habit assessment)

**Four levers** (in priority order):

| # | Lever | Field | Direction | Step Size | Max Steps |
|---|---|---|---|---|---|
| 1 | Spending cut | E_d | ↓ | E_d × 10% | 2 |
| 2 | Behavior score | behavior_score | ↑ | 1 pt | 2 |
| 3 | Payment boost | P | ↑ | max(P × 10%, ฿100) | 2 |
| 4 | Income boost | I | ↑ | I × 10% | 1 |

**`generate()` algorithm:**

1. Test each lever individually: find minimum steps (1 or 2) to achieve zone change
2. Sort feasible singles by step count (fewest first) → take top 2
3. If a slot remains: search multi-lever combinations
   - Pass 1: all combinations of 2–4 levers at 1 step each
   - Pass 2: 2–3 levers, allowing one lever at 2 steps
4. Return top 3 total (singles + mix)

Returns empty list if already in `escape_trajectory`.

**Output per nudge:**
- `type` — `"single"` or `"mix"`
- `rank`, `steps`, `pct_change`, `base_value`, `new_value`
- `new_SE`, `delta_SE` — escape score impact
- `delta_t` — months faster (positive) or longer (negative), null if orbit-locked
- `new_zone`, `zone_upgraded`, `unlocks_orbit`
- `drag_saved` — ฿/month of actual drag removed (spending-cut nudge only)
- `behavior_hint` — specific habit action string (behavior-score nudge only,
  requires `behavior_questions` input)
- `mix_parts` — list of per-lever details (mix nudge only)

**Behavior habit actions** (indexed 0–4, triggered when corresponding
`behavior_questions[i] < 2`):

| Index | Action |
|---|---|
| 0 | Set up auto-payment to eliminate missed payments |
| 1 | Apply a 24-hour pause before any purchase over ฿500 |
| 2 | Track every expense daily for 30 days |
| 3 | Keep credit utilization below 30% of each card limit |
| 4 | Freeze new credit applications for 6 months |

---

## 3. REST API — `app.py`

Flask server, default port **5000**. CORS enabled for `file://` and any
localhost origin. All endpoints consume and return JSON. `math.inf` is
serialised as the string `"Infinity"`.

**Install and run:**
```bash
pip install flask flask-cors
python app.py
```

### Endpoint Reference

#### `GET /api/health`
Returns `{ "status": "ok", "version": "model_v2" }`

---

#### `GET /api/debt/types`
Returns all supported debt type keys, their required and optional fields,
and the full list of valid `purpose` values. Useful for dynamically
building debt-entry forms.

---

#### `POST /api/portfolio/summary`
**Input:** `{ "debts": [ <debt_object>, ... ] }`

Builds the portfolio and returns `DebtPortfolio.to_model_v2_inputs()` —
D, r, P, per-debt breakdown, orbit status, avalanche/snowball order,
and Layer 3 VM fields. Use when you only need the portfolio roll-up
without running the model layers.

---

#### `POST /api/layer1`
**Input:** debts array + user profile fields (see schema below)

Returns `ModelV2Layer1.compute()` — all three forces, orbital zone,
escape score, t*, P*, gap message, and momentum.

---

#### `POST /api/layer2`
**Input:** same as `/api/layer1` plus `current_age`, `career_type`,
`t_start`, `t_retire`

Returns `ModelV2Layer2.compute()` — HCDF, LDER, status label,
True Event Horizon warning.

---

#### `POST /api/layer3`
**Input:** `{ "debts": [ ... ] }`

Returns `ModelV2Layer3.compute()` — VM badge, drag ratio, slingshot
and drag balances, per-debt breakdown.

---

#### `POST /api/portfolio/analyze`
**Input:** full payload (debts + profile + career)

**The main dashboard endpoint.** Runs the complete pipeline in one call.

**Output:**
```json
{
  "portfolio":     { "D", "r", "P", "breakdown", "avalanche_order", ... },
  "layer1":        { "F_p", "F_g", "F_d", "F_net", "zone", "S_E", "t_star_display", ... },
  "layer2":        { "HCDF", "LDER", "lder_status", "teh_warning", ... },
  "layer3":        { "debt_type_badge", "drag_ratio_pct", ... },
  "nudge_summary": { "current_zone", "zone_label", "already_escaped", "num_nudges" }
}
```

---

#### `POST /api/nudge`
**Input:** same as `/api/portfolio/analyze` plus optional
`"behavior_questions": [0, 2, 1, 2, 1]`

**Output:**
```json
{
  "current_zone":    "debt_orbit",
  "zone_label":      "⚠️  Debt Orbit",
  "S_E":             -3.2,
  "t_star":          "4 years 2 months",
  "beta":            1.4,
  "already_escaped": false,
  "teh_warning":     null,
  "nudges": [
    {
      "rank":           1,
      "type":           "single",
      "lever_name":     "Spending cut",
      "lever_field":    "E_d",
      "lever_unit":     "฿/mo",
      "steps":          1,
      "pct_change":     10.0,
      "base_value":     15000,
      "new_value":      13500,
      "new_SE":         1.8,
      "delta_SE":       5.0,
      "delta_t_months": 8,
      "new_zone":       "marginal_escape",
      "new_zone_label": "🟡 Marginal Escape",
      "zone_upgraded":  true,
      "unlocks_orbit":  false,
      "drag_saved":     2100.0,
      "behavior_hint":  null,
      "mix_parts":      null
    }
  ]
}
```

For `type: "mix"` nudges, `mix_parts` is populated with per-lever details
and single-lever fields (`lever_name`, `pct_change`, etc.) are `null`.

---

#### `POST /api/debt/amortization`
**Input:**
```json
{ "debt": <debt_object>, "months": 12 }
```
`months: 0` returns the full schedule.

**Output:** `{ "summary": <debt summary dict>, "schedule": [ <rows> ] }`

Each schedule row: `month`, `balance_start`, `interest_charge`,
`principal_paid`, `payment`, `balance_end`, `cumulative_interest`,
`orbit_locked`. Hire-purchase rows add `cumulative_interest`;
cooperative rows add `gross_interest`, `dividend_offset`, `net_interest`,
`share_balance`; balloon rows add `is_balloon`.

---

#### `POST /api/debt/payoff`
**Input:**
```json
{
  "debt":       <debt_object>,
  "fee_preset": "mortgage",
  "sign_date":  "2022-03-01",
  "as_of_date": "2025-05-20"
}
```
`fee_preset` options: `"mortgage"`, `"personal_loan"`, `"cooperative"`, `"none"`

**Output:** Full payoff breakdown — `outstanding_principal`, `accrued_interest`,
each fee line, `total_payoff`, `fee_breakdown` (list of tuples),
`is_penalty_waived`, `years_held`.

---

### Internal Helper Functions

| Function | Description |
|---|---|
| `_sanitize(obj)` | Recursively replace `math.inf` / `NaN` for safe JSON serialisation |
| `_err(msg, code)` | Return `{ "error": msg }` JSON with HTTP status code |
| `_purpose(s)` | Map string to `DebtPurpose` enum |
| `_build_debt(d)` | Dispatch a JSON dict to the correct `DebtInstrument` subclass |
| `_build_portfolio(debts_data)` | Build a `DebtPortfolio` from a list of debt dicts |
| `_build_layer1(data, model_inputs)` | Construct `ModelV2Layer1` from request dict |

---

## 4. JavaScript Client — `api.js`

Drop-in browser client. No build step required.

```html
<script src="api.js"></script>
```

### `DebtAPI` class

**Constructor:** `new DebtAPI(baseUrl = "http://localhost:5000")`

All methods are `async` and return parsed JSON. They throw an `Error` with
a human-readable message on HTTP errors.

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

**`getPayoffQuote` options:**
```js
{ feePreset: "mortgage" | "personal_loan" | "cooperative" | "none",
  signDate:  "YYYY-MM-DD",
  asOfDate:  "YYYY-MM-DD" }
```

---

### `DebtBuilder` object

Factory functions that produce correctly-shaped debt objects for the API,
using camelCase argument names instead of snake_case field names.

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

Display helpers for binding API responses to HTML.

| Method | Description |
|---|---|
| `DebtFormat.baht(amount, decimals)` | Format as `฿1,234,567` |
| `DebtFormat.pct(rate, decimals)` | Format as `6.50%` |
| `DebtFormat.zoneLabel(layer1)` | Zone emoji + label from layer1 result |
| `DebtFormat.lderLabel(layer2)` | LDER status label from layer2 result |
| `DebtFormat.vmBadge(layer3)` | VM badge string from layer3 result |
| `DebtFormat.renderAmortTable(tableEl, schedule)` | Render schedule into a `<table>` element |
| `DebtFormat.summarizeAnalysis(result)` | Flatten full analysis into a display-ready object |

---

### `NudgeFormat` object

Display helpers specifically for `/api/nudge` responses.

| Method | Description |
|---|---|
| `NudgeFormat.summary(nudge)` | One-line description of what to change (e.g. `Spending cut ↓10%: ฿15,000 → ฿13,500`) |
| `NudgeFormat.outcome(nudge, currentZoneLabel)` | Zone-change + ΔS_E + time delta (e.g. `ΔS_E +5.0 → 🟡 Marginal Escape | 8 months faster`) |
| `NudgeFormat.renderList(ulEl, nudges, currentZoneLabel)` | Render all nudges into a `<ul>` DOM element with `data-rank`, `data-type`, `data-zone-upgraded`, `data-unlocks-orbit` attributes |

---

## 5. Data Schemas

### Debt Object (sent to API)

All debt objects require at minimum: `type`, `name`, `current_balance`, `annual_rate`.

**Common optional fields** (all types):
```
creditor, purpose, original_balance, original_term_months,
remaining_term_months, minimum_payment, early_close_fee, other_fees
```

**Type-specific required fields:**

| Type | Additional Required Fields |
|---|---|
| `effective` | `remaining_term_months` |
| `credit_card` | *(none beyond common)* |
| `hire_purchase` | `total_installments`, `paid_installments` |
| `flat` | `original_term_months` |
| `balloon` | `balloon_month` |
| `compound` | *(none)* |
| `step_up` | `rate_schedule` (array of `{months_duration, annual_rate, label}`) |
| `floating` | `reference_rate`, `spread` |
| `annual_step_up` | `initial_rate` |
| `cooperative` | `loan_rate`, `share_subscription` |

### User Profile Object (sent to analysis endpoints)

```json
{
  "income":                  120000,
  "necessary_expenses":      35000,
  "discretionary_expenses":  15000,
  "behavior_score":          4,
  "emergency_fund":          90000,
  "months_on_budget":        4,
  "target_freedom_months":   60,
  "current_age":             38,
  "career_type":             "technical_engineering",
  "t_start":                 22,
  "t_retire":                60,
  "behavior_questions":      [0, 2, 1, 2, 1]
}
```

`career_type` options: `physical_trade`, `technical_engineering`,
`management_strategy`, `knowledge_advisory`

`behavior_questions` — optional array of 5 integers (0–3) representing
consistency across 5 habit areas. Only used by `/api/nudge`.

---

## 6. Key Formulas

### Flat Rate → Effective Monthly Rate Conversion

Newton-Raphson solver (in `DebtInstrument._flat_to_effective_monthly`):

Finds r_monthly such that:
```
Σ_{t=1}^{n} PMT / (1+r)^t = 1
where PMT = (1 + flat_rate × T) / n   (per unit of debt)
```
Initial guess: `flat_rate / 12 × 1.8` (Rule of Thumb for Thai loans)

### Balance-Weighted Average Rate (Portfolio)

```
r = Σ(r_i × D_i) / Σ(D_i)
```

This ensures `D × r/12 = Σ(D_i × r_i/12)`, so Debt Gravity F_g remains
accurate at the portfolio level.

### Time to Debt Freedom (t*)

```
t* = −ln(1 − D × r_m / P) / r_m
where r_m = r / 12

Special cases:
  r_m = 0  →  t* = D / P
  P ≤ D × r_m  →  t* = ∞  (orbit lock)
```

### Escape Velocity Payment (P*)

```
P* = D × r_m / (1 − (1 + r_m)^−n)
where n = target_freedom_months
```

### Lifetime Debt-to-Earning Ratio (LDER)

```
Numerator:   D₀ × e^(r × T)          (debt grown at continuous rate over career)
Denominator: Σ_{t=0}^{T} I × HCDF × (1−λ)^t / (1+d)^t
                                      (discounted, decaying earning power)

where T = years to retirement, λ = career decay rate, d = 0.03 (discount rate)
```

---

## 7. Running the System

### Requirements

```bash
pip install flask flask-cors
```

All Python modules must be in the same directory:
`app.py`, `debt_instruments.py`, `debt_instruments_extended.py`,
`debt_portfolio.py`, `model_v2.py`, `nudge_engine.py`

### Start the server

```bash
python app.py
# → Running on http://127.0.0.1:5000
```

### Minimal HTML example

```html
<!DOCTYPE html>
<html>
<head><title>Debt Dashboard</title></head>
<body>
  <ul id="nudge-list"></ul>
  <script src="api.js"></script>
  <script>
    const api = new DebtAPI();

    const payload = {
      debts: [
        DebtBuilder.creditCard({ name: "KBank", currentBalance: 45000, annualRate: 0.18, minimumPayment: 2500 }),
        DebtBuilder.effective({ name: "GH Mortgage", purpose: "mortgage", currentBalance: 2400000,
                                annualRate: 0.065, remainingTermMonths: 180, minimumPayment: 18000 }),
      ],
      income: 120000, necessary_expenses: 35000, discretionary_expenses: 15000,
      behavior_score: 4, emergency_fund: 90000, months_on_budget: 4,
      target_freedom_months: 60, current_age: 38,
      career_type: "technical_engineering", t_start: 22, t_retire: 60,
    };

    (async () => {
      const result = await api.analyzePortfolio(payload);
      const display = DebtFormat.summarizeAnalysis(result);
      document.body.insertAdjacentHTML("afterbegin",
        `<h2>${display.zone} &nbsp; S_E: ${display.escapeScore}</h2>
         <p>${display.gapMessage}</p>
         <p>LDER: ${display.lder} — ${display.lderStatus}</p>`
      );

      const nudgeResult = await api.getNudges(payload);
      NudgeFormat.renderList(
        document.getElementById("nudge-list"),
        nudgeResult.nudges,
        nudgeResult.zone_label
      );
    })();
  </script>
</body>
</html>
```

### Development notes

- `_sanitize()` in `app.py` converts `math.inf` to the string `"Infinity"` — check for this in JS when rendering t* values
- `nudge_engine.py`'s `print_report()` references `layer2.R` which does not exist on `ModelV2Layer2`; the API uses `generate()` directly and is unaffected
- CORS is wide-open in development mode; lock it down for production with `CORS(app, origins=["https://yourdomain.com"])`
- All monetary values are in Thai Baht (฿); rates are always stored as decimals (0.18 = 18%)