# monthly_batch_runner.py
# ─────────────────────────────────────────────
# Auto Monthly CSV Processor for Model V2
#
# อ่านสลิปทั้งหมดจาก CSV
# แล้วคำนวณรายเดือนอัตโนมัติทุกเดือนที่พบในไฟล์
#
# Output:
# - summary dataframe
# - export csv รายงาน
# - ยิงเข้า Model V2 ทีละเดือน
# ─────────────────────────────────────────────

import pandas as pd
from pathlib import Path

from model_v2 import ModelV2Layer1


# ─────────────────────────────────────────────
# 1. Single Month Processor
# ─────────────────────────────────────────────

def process_month_group(month_df):
    """
    ประมวลผลข้อมูลภายใน 1 เดือน
    """

    # ลบ transfer ออก
    actual_flow_df = month_df[month_df['slip_type'] != 'transfer']

    # รายรับ
    total_income = float(
        actual_flow_df[
            actual_flow_df['slip_type'] == 'income'
        ]['amount'].sum()
    )

    # รายจ่าย
    expense_df = actual_flow_df[
        actual_flow_df['slip_type'] == 'expense'
    ]

    # จำเป็น
    necessary_expense = float(
        expense_df[
            expense_df['is_necessary'] == True
        ]['amount'].sum()
    )

    # ฟุ่มเฟือย
    discretionary_expense = float(
        expense_df[
            expense_df['is_necessary'] == False
        ]['amount'].sum()
    )

    return {
        "I": total_income,
        "E_n": necessary_expense,
        "E_d": discretionary_expense
    }


# ─────────────────────────────────────────────
# 2. Inject Into Model V2
# ─────────────────────────────────────────────

def run_model_v2(financial_data,
                 behavior_score=5.0,
                 e_fund=0,
                 months_on_budget=0):

    l1 = ModelV2Layer1(
        I=financial_data["I"],
        E_n=financial_data["E_n"],
        E_d=financial_data["E_d"],
        behavior_score=behavior_score,
        E_fund=e_fund,
        months_on_budget=months_on_budget
    )

    return {
        "stability_score": l1.S_E(),
        "financial_zone": l1.zone()
    }


# ─────────────────────────────────────────────
# 3. Full Automatic Monthly Pipeline
# ─────────────────────────────────────────────

def process_all_months(
    csv_path,
    behavior_score=5.0,
    e_fund=0,
    months_on_budget=0,
    export_csv=True
):
    """
    อ่านทั้งไฟล์ แล้วคำนวณทุกเดือนอัตโนมัติ
    """

    csv_path = Path(csv_path)

    if not csv_path.exists():
        raise FileNotFoundError(f"File not found: {csv_path}")

    # โหลด CSV
    df = pd.read_csv(csv_path)
    
    required_columns = {
        'amount',
        'date',
        'is_necessary',
        'category',
        'slip_type'
    }

    missing = required_columns - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing required columns: {missing}"
        )

    # แปลงเวลา
    df['date'] = pd.to_datetime(df['date'])

    # สร้าง year-month key
    df['year_month'] = df['date'].dt.to_period('M')

    # sort ตามเวลา
    df = df.sort_values('date')

    results = []

    # group ตามเดือน
    grouped = df.groupby('year_month')

    print("\n📊 START MONTHLY ANALYSIS")
    print("─" * 60)

    for period, month_df in grouped:

        year = period.year
        month = period.month

        print(f"\n🗓 Processing: {year}-{month:02d}")

        # สรุปการเงิน
        financial_summary = process_month_group(month_df)

        # ยิงเข้า model
        model_result = run_model_v2(
            financial_summary,
            behavior_score=behavior_score,
            e_fund=e_fund,
            months_on_budget=months_on_budget
        )

        row = {
            "year": year,
            "month": month,

            "income": financial_summary["I"],
            "necessary_expense": financial_summary["E_n"],
            "discretionary_expense": financial_summary["E_d"],

            "net_cashflow":
                financial_summary["I"]
                - financial_summary["E_n"]
                - financial_summary["E_d"],

            "stability_score":
                model_result["stability_score"],

            "financial_zone":
                model_result["financial_zone"]
        }

        results.append(row)

        print(
            f"💰 Income={row['income']:,.2f} | "
            f"E_n={row['necessary_expense']:,.2f} | "
            f"E_d={row['discretionary_expense']:,.2f}"
        )

        print(
            f"🔮 Zone={row['financial_zone']} | "
            f"S_E={row['stability_score']}"
        )

    # รวมเป็น dataframe
    result_df = pd.DataFrame(results)

    # export csv
    if export_csv:

        export_name = "monthly_financial_summary.csv"

        result_df.to_csv(
            export_name,
            index=False,
            encoding='utf-8-sig'
        )

        print("\n✅ Exported:")
        print(f"📁 {export_name}")

    return result_df


# ─────────────────────────────────────────────
# 4. Example Run
# ─────────────────────────────────────────────

if __name__ == "__main__":

    df_result = process_all_months(
        csv_path="slips_data.csv",

        # ปรับตามระบบจริง
        behavior_score=6.5,
        e_fund=10000,
        months_on_budget=4,

        export_csv=True
    )

    print("\n📈 FINAL SUMMARY")
    print(df_result)