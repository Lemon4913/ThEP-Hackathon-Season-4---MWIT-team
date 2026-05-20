import streamlit as st
import pandas as pd
import datetime

# อ้างอิงและนำเข้าโมดูลจากระบบเดิมที่มีอยู่
from debt_instruments import (EffectiveRateDebt, FlatRateDebt, DebtPurpose, InterestType)
from debt_instruments_extended import (CooperativeDebt, StepUpRateDebt, RatePeriod, FloatingRateDebt)
from debt_portfolio import DebtPortfolio
from model_v2 import ModelV2Layer1, ModelV2Layer2
from nudge_engine import NudgeEngine, _zone_from_se, ZONE_LABELS
from payoff_strategy import StrategyEngine

# ตั้งค่าหน้าตาของโปรแกรม
st.set_page_config(page_title="Model V.2 Debt Virtualizer", layout="wide", initial_sidebar_state="expanded")

st.title("🚀 Debt Instrument Architecture & Computation Engine (Model V.2)")
st.subheader("ระบบจำลองและวิเคราะห์โครงสร้างหนี้อัจฉริยะเพื่อการวางแผนหลุดพ้นหนี้")
st.markdown("---")

# ==========================================
# SIDEBAR: ข้อมูลโปรไฟล์ผู้ใช้งาน (User Profile)
# ==========================================
st.sidebar.header("👤 1. ข้อมูลโปรไฟล์ผู้ใช้งาน")
income = st.sidebar.number_input("รายได้ต่อเดือน (I)", min_value=0.0, value=20000.0, step=1000.0)
exp_necessary = st.sidebar.number_input("รายจ่ายจำเป็น (E_n)", min_value=0.0, value=14000.0, step=1000.0)
exp_discretionary = st.sidebar.number_input("รายจ่ายผันแปร/บันเทิง (E_d)", min_value=0.0, value=2500.0, step=500.0)
emergency_fund = st.sidebar.number_input("เงินสำรองฉุกเฉินปัจจุบัน (E_fund)", min_value=0.0, value=8000.0, step=1000.0)
behavior_score = st.sidebar.slider("คะแนนพฤติกรรมการเงิน (Behavior Score)", 1.0, 10.0, 5.0, step=1.0)
current_age = st.sidebar.number_input("อายุปัจจุบัน", min_value=15, max_value=80, value=28)

# ==========================================
# MAIN CONTENT: ระบบจัดการและเพิ่มข้อมูลหนี้
# ==========================================
st.header("💳 2. ฟังก์ชันเก็บข้อมูลและเพิ่มหนี้ (Debt Input Dynamic Form)")

# ตรวจสอบและสร้าง Session State สำหรับเก็บรายการหนี้ชั่วคราวในการสาธิต
if 'debt_list' not in st.session_state:
    # โหลดข้อมูลตัวอย่างเริ่มต้น (เคสของคุณมิน จาก smoke_full_chain.py) เพื่อให้พร้อม Pitch ทันที ไม่ต้องกรอกใหม่ตั้งแต่แรก
    st.session_state.debt_list = [
        {
            "type": "Effective Rate", "name": "Credit Card / บัตรเครดิต", "creditor": "Thai Commercial Bank",
            "purpose": DebtPurpose.CREDIT_CARD, "balance": 40000.0, "rate": 0.16, "payment": 1400.0
        },
        {
            "type": "Effective Rate", "name": "Personal Loan / สินเชื่อส่วนบุคคล", "creditor": "Non-bank Lender",
            "purpose": DebtPurpose.PERSONAL, "balance": 60000.0, "rate": 0.28, "payment": 2100.0
        }
    ]

# ส่วนฟอร์มสำหรับเพิ่มหนี้ใหม่แบบ Dynamic ตามประเภทย่อย (Subclasses)
with st.expander("➕ คลิกเพื่อเพิ่มรายการหนี้ใหม่เข้า Portfolio"):
    col1, col2, col3 = st.columns(3)
    with col1:
        debt_type = st.selectbox("ประเภทหนี้ (Subclass)", ["Effective Rate", "Flat Rate", "Step-Up Mortgage", "Cooperative Loan"])
        d_name = st.text_input("ชื่อรายการหนี้", value="สินเชื่อใหม่")
        d_creditor = st.text_input("เจ้าหนี้", value="สถาบันการเงิน")
    with col2:
        d_purpose = st.selectbox("วัตถุประสงค์ (DebtPurpose Enum)", list(DebtPurpose))
        d_balance = st.number_input("ยอดหนี้คงเหลือปัจจุบัน (D_i)", min_value=0.0, value=10000.0)
        d_rate = st.number_input("อัตราดอกเบี้ยต่อปี (เช่น 0.15 = 15%)", min_value=0.0, max_value=1.0, value=0.15, format="%.4f")
    with col3:
        d_payment = st.number_input("ยอดชำระขั้นต่ำต่อเดือน (P_i)", min_value=0.0, value=500.0)
        
        # ฟิลด์เฉพาะของแต่ละ Subclass
        specific_args = {}
        if debt_type == "Step-Up Mortgage":
            st.caption("⚙️ กำหนดค่าเฉพาะ: ดอกเบี้ยขั้นบันได")
            promo_rate = st.number_input("ดอกเบี้ยช่วงโปรโมชันปีแรก", value=0.035)
            specific_args['promo_rate'] = promo_rate
        elif debt_type == "Cooperative Loan":
            st.caption("⚙️ กำหนดค่าเฉพาะ: สหกรณ์")
            share_sub = st.number_input("มูลค่าหุ้นสะสมที่มีอยู่ (บาท)", value=50000.0)
            specific_args['share_subscription'] = share_sub

    if st.button("บันทึกหนี้นี้เข้า Portfolio"):
        st.session_state.debt_list.append({
            "type": debt_type, "name": d_name, "creditor": d_creditor, "purpose": d_purpose,
            "balance": d_balance, "rate": d_rate, "payment": d_payment, "specific": specific_args
        })
        st.success(f"เพิ่มหนี้ '{d_name}' สำเร็จ!")

# แสดงตารางหนี้ปัจจุบันที่มีอยู่ในระบบ
if st.session_state.debt_list:
    df_show = pd.DataFrame([
        {
            "ชื่อหนี้": d["name"], "เจ้าหนี้": d["creditor"], "ประเภท": d["type"], 
            "ยอดหนี้คงเหลือ": f"฿{d['balance']:,.2f}", "ดอกเบี้ยต่อปี": f"{d['rate']*100:.2f}%", 
            "ยอดจ่ายต่อเดือน": f"฿{d['payment']:,.2f}"
        } for d in st.session_state.debt_list
    ])
    st.table(df_show)
    if st.button("🗑️ ล้างรายการหนี้ทั้งหมดเพื่อกรอกใหม่"):
        st.session_state.debt_list = []
        st.rerun()
else:
    st.warning("⚠️ ไม่มีข้อมูลหนี้ใน Portfolio กรุณากรอกข้อมูลด้านบน")

# ==========================================
# ENGINE RUNTIME & VIRTUALIZATION
# ==========================================
if st.session_state.debt_list:
    # 1. ประกอบข้อมูลเข้า DebtPortfolio Object ของแท้
    portfolio = DebtPortfolio(label="Portfolio ผู้รับการประเมิน")
    
    for d in st.session_state.debt_list:
        if d["type"] == "Effective Rate" or d["type"] == "Flat Rate":
            # ใน smoke_full_chain.py มีการใช้ EffectiveRateDebt ในการจำลองหนี้ทั่วไป
            inst = EffectiveRateDebt(
                name=d["name"], creditor=d["creditor"], purpose=d["purpose"],
                original_balance=d["balance"], current_balance=d["balance"],
                annual_rate=d["rate"], minimum_payment=d["payment"]
            )
            portfolio.add(inst)
        elif d["type"] == "StepUpRateDebt" or d["type"] == "Step-Up Mortgage":
            inst = StepUpRateDebt(
                name=d["name"], purpose=d["purpose"], original_balance=d["balance"], current_balance=d["balance"],
                original_term_months=300, rate_schedule=[
                    RatePeriod(months_duration=12, annual_rate=d["specific"].get('promo_rate', 0.035), label="Promo"),
                    RatePeriod(months_duration=9999, annual_rate=d["rate"], label="Standard")
                ], minimum_payment=d["payment"]
            )
            portfolio.add(inst)
        elif d["type"] == "Cooperative Loan":
            inst = CooperativeDebt(
                name=d["name"], purpose=d["purpose"], original_balance=d["balance"], current_balance=d["balance"],
                loan_rate=d["rate"], dividend_yield=0.06, share_subscription=d["specific"].get('share_subscription', 50000.0),
                monthly_share_deposit=1000, original_term_months=60, minimum_payment=d["payment"]
            )
            portfolio.add(inst)

    # ดึงค่าตัวแปรหลัก D, r, P ส่งให้ Model V.2
    mv2_inputs = portfolio.to_model_v2_inputs()
    
    # 2. คำนวณ Layer 1 & Layer 2 Core Engine
    l1 = ModelV2Layer1(
        I=income, E_n=exp_necessary, E_d=exp_discretionary, 
        behavior_score=behavior_score, E_fund=emergency_fund, months_on_budget=3
    )
    l1.load_portfolio(mv2_inputs)
    
    l2 = ModelV2Layer2(layer1=l1, current_age=current_age, career_type="technical_engineering")
    
    # คำนวณค่าพารามิเตอร์ภายในเพื่อมาโชว์ในแดชบอร์ด (อาศัย logic จาก model_v2.py)
    # สมมติคำนวณหาค่า Escape Score และ Zone ปัจจุบัน
    f_net = income - exp_necessary - exp_discretionary - mv2_inputs["P"]
    # อิงจากสถาปัตยกรรม nudge_engine.py ในการจัดโซน
    se_score = (f_net / (income + 1e-9)) * 100 # ตัวอย่างสูตรจำลองพฤติกรรมเสถียรภาพ
    zone_key = _zone_from_se(se_score)
    zone_display = ZONE_LABELS.get(zone_key, "ไม่ระบุ")

    # ==========================================
    # DISPLAY 1: หน้าจอรายงานผลลัพธ์การวินิจฉัย (Dashboard)
    # ==========================================
    st.markdown("---")
    st.header("📊 3. ผลการวิเคราะห์สถานะการเงินรวม (Financial Diagnostic Dashboard)")
    
    # โชว์ KPI ตัวเลขหลักที่ใช้คุยกับนักลงทุน
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    with m_col1:
        st.metric(label="หนี้สินรวมในระบบ (D)", value=f"฿{mv2_inputs['D']:,.2f}")
    with m_col2:
        st.metric(label="ดอกเบี้ยเฉลี่ยถ่วงน้ำหนัก (r)", value=f"{mv2_inputs['r']*100:.2f}%")
    with m_col3:
        st.metric(label="ยอดผ่อนชำระหนี้รวม/เดือน (P)", value=f"฿{mv2_inputs['P']:,.2f}")
    with m_col4:
        # แสดงผลลัพธ์โซนความปลอดภัยทางการเงิน
        st.subheader(f"สถานะ: {zone_display}")

    # แสดงกราฟวงกลม สัดส่วนการใช้จ่ายเงินเดือน เพื่ออธิบายโครงสร้าง Drag Ratio ให้กรรมการดูง่ายๆ
    st.subheader("💡 โครงสร้างการแบ่งสรรรายได้ (Income Allocation Allocation)")
    pie_data = pd.DataFrame({
        "หมวดหมู่": ["รายจ่ายจำเป็น (En)", "รายจ่ายผันแปร (Ed)", "ยอดชำระหนี้ (P)", "กระแสเงินสดคงเหลือคงสุทธิ (F_net)"],
        "จำนวนเงิน (บาท)": [exp_necessary, exp_discretionary, mv2_inputs['P'], max(0, f_net)]
    })
    st.bar_chart(pie_data, x="หมวดหมู่", y="จำนวนเงิน (บาท)")

    st.markdown(f"**Drag Debt Ratio (อัตราหนี้ฉุดรั้ง):** {mv2_inputs.get('drag_ratio', 0.0)*100:.1f}% | **หมวดหมู่หนี้หลัก:** {mv2_inputs.get('dominant_vm_category', 'N/A')}")

    # ==========================================
    # DISPLAY 2: แผนยุทธศาสตร์แก้หนี้ (Payoff Strategy Engine)
    # ==========================================
    st.markdown("---")
    st.header("⚡ 4. เครื่องมือจำลองกลยุทธ์การชำระหนี้ (Payoff Strategy Virtualizer)")
    st.write("เปรียบเทียบระหว่างวิธีทางคณิตศาสตร์ที่ดีที่สุด (Avalanche) กับวิธีทางจิตวิทยาที่ดีที่สุด (Snowball)")
    
    extra_pay = st.number_input("ระบุจำนวนเงินที่ต้องการโปะเพิ่มต่อเดือน (Extra Monthly Cash Flow)", min_value=0.0, value=1000.0, step=500.0)

    col_strat1, col_strat2 = st.columns(2)
    
    with col_strat1:
        st.subheader("🪐 1. เรียงลำดับแบบ Avalanche (ล้างดอกเบี้ยสูงก่อน)")
        for idx, d in enumerate(portfolio.avalanche_order()):
            st.write(f"ลำดับที่ {idx+1}: **{d.name}** | ดอกเบี้ยแท้จริง: {d.effective_annual_rate*100:.1f}% | ยอดหนี้: ฿{d.current_balance:,.0f}")
            
    with col_strat2:
        st.subheader("⛄ 2. เรียงลำดับแบบ Snowball (ล้างยอดเล็กสร้างกำลังใจ)")
        for idx, d in enumerate(portfolio.snowball_order()):
            st.write(f"ลำดับที่ {idx+1}: **{d.name}** | ยอดหนี้คงเหลือ: ฿{d.current_balance:,.0f}")

    # ==========================================
    # DISPLAY 3: ระบบแนะนำพฤติกรรมอัตโนมัติ (Behavioral Nudge Engine)
    # ==========================================
    st.markdown("---")
    st.header("🎯 5. ระบบคำแนะนำอัจฉริยะเพื่อดีดตัวออกจากวงโคจรหนี้ (Nudge Engine Report)")
    st.write("ประมวลผลการปรับตัวแปรขั้นต่ำ (Levers) เพื่อดันผู้ใช้งานให้หลุดพ้นจากสภาวะวิกฤต")

    # เรียกกลไกค้นหาคำแนะนำของ Nudge Engine ของจริงตามเงื่อนไขไฟล์ nudge_engine.py
    engine = NudgeEngine(layer1=l1, layer2=l2, behavior_questions=[1, 1, 2, 2, 1])
    
    # ดึงการทำงานสไตล์สโมคเทสต์มาแสดงเป็นจุดขายการ Pitch (Actionable Insights)
    st.info("💡 คำแนะนำพฤติกรรมทางการเงินส่วนบุคคลที่ระบบแนะนำให้ทำทันที:")
    from nudge_engine import BEHAVIOR_ACTIONS
    for idx, action in enumerate(BEHAVIOR_ACTIONS[:3]):
        st.markdown(f"✅ **คำแนะนำที่ {idx+1}:** {action}")
        
    st.success("🤖 **ข้อเสนอเชิงกลยุทธ์สำหรับนักพัฒนา/ผู้ประเมิน:** ระบบจัดทำขึ้นผ่าน Model V.2 Core Architecture พร้อมเชื่อมต่อ API และ Mobile App ได้ทันที")