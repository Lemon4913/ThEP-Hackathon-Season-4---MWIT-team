"""
app.py
──────
Flask REST API — exposes the full Debt → Portfolio → Model V.2 pipeline
to any HTML/JS frontend via JSON endpoints.

Endpoints
─────────
POST /api/portfolio/analyze          Full pipeline: debts → portfolio → all 3 layers
POST /api/portfolio/summary          Portfolio aggregates only (D, r, P, breakdown)
POST /api/layer1                     Model V.2 Layer 1 only (forces, zone, t*, gap)
POST /api/layer2                     Model V.2 Layer 2 only (HCDF, LDER, trajectory)
POST /api/layer3                     Model V.2 Layer 3 only (debt quality, VM badge)
POST /api/debt/amortization          Amortization schedule for a single debt
POST /api/debt/payoff                Payoff quote (with full fee stack)
GET  /api/debt/types                 List supported debt types + required fields
GET  /api/health                     Health check

All requests and responses are JSON.
CORS is enabled for local HTML file development (file://).

Run:
    pip install flask flask-cors
    python app.py
Server starts at http://localhost:5000
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import math
import traceback

# ── Import the pipeline ───────────────────────────────────────────────────────
from debt_instruments import (
    DebtInstrument, FlatRateDebt, EffectiveRateDebt, CompoundRateDebt,
    CreditCardDebt, HirePurchaseDebt, BalloonDebt,
    DebtPurpose, InterestType,
)
from debt_instruments_extended import (
    StepUpRateDebt, RatePeriod,
    FloatingRateDebt,
    AnnualStepUpDebt,
    CooperativeDebt,
    PayoffCalculator, PayoffFeeConfig,
    MORTGAGE_FEE_CONFIG, PERSONAL_LOAN_FEE_CONFIG,
    COOPERATIVE_FEE_CONFIG, NO_FEE_CONFIG,
)
from debt_portfolio import DebtPortfolio
from model_v2 import ModelV2Layer1, ModelV2Layer2, ModelV2Layer3

from datetime import date

app = Flask(__name__)
CORS(app)  # allow requests from file:// and any localhost port


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitize(obj):
    """Recursively replace inf/nan with string so JSON doesn't choke."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float):
        if math.isinf(obj):
            return "Infinity"
        if math.isnan(obj):
            return None
    return obj


def _err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def _purpose(s: str) -> DebtPurpose:
    mapping = {
        "education":            DebtPurpose.EDUCATION,
        "mortgage":             DebtPurpose.MORTGAGE,
        "business":             DebtPurpose.BUSINESS,
        "auto":                 DebtPurpose.AUTO,
        "mixed":                DebtPurpose.MIXED,
        "credit_card":          DebtPurpose.CREDIT_CARD,
        "personal":             DebtPurpose.PERSONAL,
        "personal_consumption": DebtPurpose.PERSONAL,
        "medical":              DebtPurpose.MEDICAL,
        "other":                DebtPurpose.OTHER,
    }
    return mapping.get(str(s).lower(), DebtPurpose.OTHER)


def _build_debt(d: dict) -> DebtInstrument:
    """
    Build the correct DebtInstrument subclass from a dict.
    Required fields (all types): type, name, current_balance, annual_rate
    """
    dtype = d.get("type", "effective").lower()

    common = dict(
        name             = d.get("name", "Unnamed"),
        creditor         = d.get("creditor", ""),
        purpose          = _purpose(d.get("purpose", "other")),
        original_balance = float(d.get("original_balance", d.get("current_balance", 0))),
        current_balance  = float(d.get("current_balance", 0)),
        annual_rate      = float(d.get("annual_rate", 0)),
        minimum_payment  = float(d.get("minimum_payment", 0)),
        original_term_months  = int(d["original_term_months"]) if "original_term_months" in d else None,
        remaining_term_months = int(d["remaining_term_months"]) if "remaining_term_months" in d else None,
        early_close_fee  = float(d.get("early_close_fee", 0)),
        other_fees       = float(d.get("other_fees", 0)),
    )

    if dtype == "credit_card":
        return CreditCardDebt(
            **common,
            min_payment_pct    = float(d.get("min_payment_pct", 0.05)),
            min_payment_floor  = float(d.get("min_payment_floor", 500)),
            monthly_new_charges= float(d.get("monthly_new_charges", 0)),
        )

    elif dtype == "hire_purchase":
        return HirePurchaseDebt(
            **common,
            total_installments = int(d.get("total_installments", 0)),
            paid_installments  = int(d.get("paid_installments", 0)),
            installment_amount = float(d.get("installment_amount", 0)),
        )

    elif dtype == "flat":
        return FlatRateDebt(
            **common,
            use_rule_of_78 = bool(d.get("use_rule_of_78", True)),
        )

    elif dtype == "balloon":
        return BalloonDebt(
            **common,
            balloon_amount = float(d.get("balloon_amount", 0)),
            balloon_month  = int(d.get("balloon_month", 0)),
            interest_only  = bool(d.get("interest_only", False)),
        )

    elif dtype == "compound":
        return CompoundRateDebt(
            **common,
            compounding_frequency = int(d.get("compounding_frequency", 12)),
        )

    elif dtype == "step_up":
        schedule = [
            RatePeriod(
                months_duration = int(p["months_duration"]),
                annual_rate     = float(p["annual_rate"]),
                label           = p.get("label", ""),
            )
            for p in d.get("rate_schedule", [])
        ]
        if not schedule:
            raise ValueError("step_up type requires rate_schedule list")
        return StepUpRateDebt(
            **common,
            rate_schedule  = schedule,
            months_elapsed = int(d.get("months_elapsed", 0)),
        )

    elif dtype == "floating":
        return FloatingRateDebt(
            **common,
            reference_rate = float(d.get("reference_rate", 0.0725)),
            spread         = float(d.get("spread", -0.015)),
            reference_name = d.get("reference_name", "MRR"),
        )

    elif dtype == "annual_step_up":
        return AnnualStepUpDebt(
            **common,
            initial_rate   = float(d.get("initial_rate", 0.01)),
            step_rate      = float(d.get("step_rate", 0.01)),
            max_rate       = float(d.get("max_rate", 0.01)),
            years_elapsed  = int(d.get("years_elapsed", 0)),
            grace_months   = int(d.get("grace_months", 0)),
            simple_interest= bool(d.get("simple_interest", True)),
        )

    elif dtype == "cooperative":
        return CooperativeDebt(
            **common,
            share_subscription    = float(d.get("share_subscription", 0)),
            monthly_share_deposit = float(d.get("monthly_share_deposit", 0)),
            dividend_yield        = float(d.get("dividend_yield", 0.065)),
            loan_rate             = float(d.get("loan_rate", common["annual_rate"])),
            share_offset_allowed  = bool(d.get("share_offset_allowed", True)),
            shares_netted_at_payoff = bool(d.get("shares_netted_at_payoff", True)),
        )

    else:
        # Default: effective rate (mortgage, personal loan, etc.)
        return EffectiveRateDebt(**common)


def _build_portfolio(debts_data: list) -> DebtPortfolio:
    portfolio = DebtPortfolio()
    for d in debts_data:
        portfolio.add(_build_debt(d))
    return portfolio


def _build_layer1(data: dict, model_inputs: dict) -> ModelV2Layer1:
    return ModelV2Layer1(
        I                    = float(data.get("income", 0)),
        E_n                  = float(data.get("necessary_expenses", 0)),
        E_d                  = float(data.get("discretionary_expenses", 0)),
        behavior_score       = float(data.get("behavior_score", 5)),
        E_fund               = float(data.get("emergency_fund", 0)),
        months_on_budget     = int(data.get("months_on_budget", 3)),
        target_freedom_months= int(data.get("target_freedom_months", 60)),
    ).load_portfolio(model_inputs)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "version": "model_v2"})


@app.get("/api/debt/types")
def debt_types():
    """Return supported debt types and their required/optional fields."""
    types = {
        "effective": {
            "description": "Standard amortizing loan (mortgage, personal loan)",
            "required": ["name", "current_balance", "annual_rate", "remaining_term_months"],
            "optional": ["original_balance", "original_term_months", "minimum_payment", "creditor", "purpose"],
        },
        "credit_card": {
            "description": "Revolving credit card debt",
            "required": ["name", "current_balance", "annual_rate"],
            "optional": ["minimum_payment", "min_payment_pct", "min_payment_floor", "monthly_new_charges"],
        },
        "hire_purchase": {
            "description": "Thai hire-purchase (ผ่อนชำระ) — flat rate installments",
            "required": ["name", "current_balance", "annual_rate", "total_installments", "paid_installments"],
            "optional": ["installment_amount", "original_balance"],
        },
        "flat": {
            "description": "Flat-rate personal loan",
            "required": ["name", "current_balance", "annual_rate", "original_term_months"],
            "optional": ["remaining_term_months", "use_rule_of_78"],
        },
        "balloon": {
            "description": "Balloon / bullet loan",
            "required": ["name", "current_balance", "annual_rate", "balloon_month"],
            "optional": ["balloon_amount", "interest_only"],
        },
        "compound": {
            "description": "Compound-rate debt (specify compounding frequency)",
            "required": ["name", "current_balance", "annual_rate"],
            "optional": ["compounding_frequency", "remaining_term_months"],
        },
        "step_up": {
            "description": "Step-up / promotional rate mortgage",
            "required": ["name", "current_balance", "rate_schedule"],
            "optional": ["months_elapsed", "original_term_months"],
            "rate_schedule_item": {"months_duration": "int", "annual_rate": "float", "label": "str"},
        },
        "floating": {
            "description": "MRR/MLR-linked floating rate debt",
            "required": ["name", "current_balance", "reference_rate", "spread"],
            "optional": ["reference_name", "remaining_term_months"],
        },
        "annual_step_up": {
            "description": "กยศ. student loan pattern — rate increases annually",
            "required": ["name", "current_balance", "initial_rate"],
            "optional": ["step_rate", "max_rate", "years_elapsed", "grace_months", "simple_interest"],
        },
        "cooperative": {
            "description": "สหกรณ์ออมทรัพย์ cooperative loan with dividend offset",
            "required": ["name", "current_balance", "loan_rate", "share_subscription"],
            "optional": ["monthly_share_deposit", "dividend_yield", "share_offset_allowed"],
        },
    }
    purposes = [e.value for e in DebtPurpose]
    return jsonify({"debt_types": types, "purposes": purposes})


@app.post("/api/portfolio/summary")
def portfolio_summary():
    """
    Input:  { "debts": [ <debt_object>, ... ] }
    Output: Portfolio aggregates + per-debt breakdown.
    """
    data = request.get_json(silent=True) or {}
    debts_data = data.get("debts", [])
    if not debts_data:
        return _err("Provide at least one debt in 'debts' array")
    try:
        portfolio    = _build_portfolio(debts_data)
        model_inputs = portfolio.to_model_v2_inputs()
        return jsonify(_sanitize(model_inputs))
    except Exception as e:
        return _err(f"Portfolio build failed: {e}\n{traceback.format_exc()}")


@app.post("/api/layer1")
def layer1_only():
    """
    Input:
      {
        "debts": [...],
        "income": 120000,
        "necessary_expenses": 35000,
        "discretionary_expenses": 15000,
        "behavior_score": 4,
        "emergency_fund": 90000,
        "months_on_budget": 4,
        "target_freedom_months": 60
      }
    Output: Full Layer 1 compute() dict.
    """
    data = request.get_json(silent=True) or {}
    if not data.get("debts"):
        return _err("'debts' array is required")
    try:
        portfolio    = _build_portfolio(data["debts"])
        model_inputs = portfolio.to_model_v2_inputs()
        layer1       = _build_layer1(data, model_inputs)
        return jsonify(_sanitize(layer1.compute()))
    except Exception as e:
        return _err(str(e))


@app.post("/api/layer2")
def layer2_only():
    """
    Input: same as /api/layer1 plus:
      { "current_age": 38, "career_type": "technical_engineering",
        "t_start": 22, "t_retire": 60 }
    Output: Layer 2 compute() dict.
    """
    data = request.get_json(silent=True) or {}
    if not data.get("debts"):
        return _err("'debts' array is required")
    try:
        portfolio    = _build_portfolio(data["debts"])
        model_inputs = portfolio.to_model_v2_inputs()
        layer1       = _build_layer1(data, model_inputs)
        layer2       = ModelV2Layer2(
            layer1      = layer1,
            current_age = int(data.get("current_age", 35)),
            career_type = data.get("career_type", "technical_engineering"),
            t_start     = int(data.get("t_start", 22)),
            t_retire    = int(data.get("t_retire", 60)),
        )
        return jsonify(_sanitize(layer2.compute()))
    except Exception as e:
        return _err(str(e))


@app.post("/api/layer3")
def layer3_only():
    """
    Input: { "debts": [...] }
    Output: Layer 3 compute() dict (debt quality / VM badge).
    """
    data = request.get_json(silent=True) or {}
    if not data.get("debts"):
        return _err("'debts' array is required")
    try:
        portfolio    = _build_portfolio(data["debts"])
        model_inputs = portfolio.to_model_v2_inputs()
        layer3       = ModelV2Layer3(portfolio_inputs=model_inputs)
        return jsonify(_sanitize(layer3.compute()))
    except Exception as e:
        return _err(str(e))


@app.post("/api/portfolio/analyze")
def portfolio_analyze():
    """
    Full pipeline in one call.

    Input:
      {
        "debts": [ <debt_object>, ... ],
        "income": 120000,
        "necessary_expenses": 35000,
        "discretionary_expenses": 15000,
        "behavior_score": 4,
        "emergency_fund": 90000,
        "months_on_budget": 4,
        "target_freedom_months": 60,
        "current_age": 38,
        "career_type": "technical_engineering",
        "t_start": 22,
        "t_retire": 60
      }

    Output:
      {
        "portfolio":  { D, r, P, breakdown, ... },
        "layer1":     { forces, zone, escape_score, t_star, gap, momentum },
        "layer2":     { HCDF, LDER, status },
        "layer3":     { badge, drag_ratio, vm_category }
      }
    """
    data = request.get_json(silent=True) or {}
    if not data.get("debts"):
        return _err("'debts' array is required")
    try:
        portfolio    = _build_portfolio(data["debts"])
        model_inputs = portfolio.to_model_v2_inputs()

        layer1 = _build_layer1(data, model_inputs)
        layer2 = ModelV2Layer2(
            layer1      = layer1,
            current_age = int(data.get("current_age", 35)),
            career_type = data.get("career_type", "technical_engineering"),
            t_start     = int(data.get("t_start", 22)),
            t_retire    = int(data.get("t_retire", 60)),
        )
        layer3 = ModelV2Layer3(portfolio_inputs=model_inputs)

        result = {
            "portfolio": model_inputs,
            "layer1":    layer1.compute(),
            "layer2":    layer2.compute(),
            "layer3":    layer3.compute(),
        }
        return jsonify(_sanitize(result))
    except Exception as e:
        return _err(f"{e}\n{traceback.format_exc()}")


@app.post("/api/debt/amortization")
def debt_amortization():
    """
    Input:
      { "debt": <debt_object>, "months": 12 }   (months=0 means full schedule)
    Output:
      { "schedule": [ { month, interest_charge, principal_paid, balance_end, ... } ] }
    """
    data = request.get_json(silent=True) or {}
    debt_data = data.get("debt")
    if not debt_data:
        return _err("'debt' object is required")
    try:
        debt     = _build_debt(debt_data)
        schedule = debt.amortization_schedule()
        months   = int(data.get("months", 0))
        if months > 0:
            schedule = schedule[:months]
        summary  = debt.summary()
        return jsonify(_sanitize({"summary": summary, "schedule": schedule}))
    except Exception as e:
        return _err(str(e))


@app.post("/api/debt/payoff")
def debt_payoff():
    """
    Input:
      {
        "debt": <debt_object>,
        "fee_preset": "mortgage" | "personal_loan" | "cooperative" | "none",
        "sign_date": "2022-03-01",   (optional ISO date string)
        "as_of_date": "2025-05-20"  (optional ISO date string, defaults to today)
      }
    Output: Full payoff breakdown dict from PayoffCalculator.
    """
    data = request.get_json(silent=True) or {}
    debt_data = data.get("debt")
    if not debt_data:
        return _err("'debt' object is required")
    try:
        debt = _build_debt(debt_data)

        preset = data.get("fee_preset", "none").lower()
        fee_map = {
            "mortgage":     MORTGAGE_FEE_CONFIG,
            "personal_loan":PERSONAL_LOAN_FEE_CONFIG,
            "cooperative":  COOPERATIVE_FEE_CONFIG,
            "none":         NO_FEE_CONFIG,
        }
        fee_config = fee_map.get(preset, NO_FEE_CONFIG)

        sign_date  = None
        if data.get("sign_date"):
            sign_date = date.fromisoformat(data["sign_date"])

        as_of_date = date.today()
        if data.get("as_of_date"):
            as_of_date = date.fromisoformat(data["as_of_date"])

        calc   = PayoffCalculator(debt, fee_config=fee_config, sign_date=sign_date)
        result = calc.get_payoff_amount(as_of_date=as_of_date)
        return jsonify(_sanitize(result))
    except Exception as e:
        return _err(str(e))


# ── Dev server ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, port=5000)