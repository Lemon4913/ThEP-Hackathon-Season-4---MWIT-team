# test_sortslip.py

from sortslip import process_all_months


def main():

    result_df = process_all_months(
        csv_path= r"C:\Users\Acer\Downloads\slips_data.csv",

        # ค่า mock สำหรับต้นแบบ
        behavior_score=6.5,
        e_fund=10000,
        months_on_budget=4,

        export_csv=True
    )

    print("\n========== RESULT ==========")
    print(result_df)


if __name__ == "__main__":
    main()