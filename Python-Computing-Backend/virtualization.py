import streamlit as st
import plotly.graph_objects as go
import pandas as pd

# 1. Import Core Engine จากไฟล์ที่คุณมีอยู่
from model_v2 import ModelV2Layer1, ModelV2Layer2
from nudge_engine import _zone_from_se, _lever_defs, BEHAVIOR_ACTIONS

# ตั้งค่าหน้าจอสำหรับใช้ Pitching
st.set_page_config(page_title="Debt Virtualizer - Behavior Engine", layout="wide")

st.title("🧠 Behavioral Score Dynamic Virtualizer")
st.markdown("---")

# ==========================================
# SIDEBAR: ข้อมูลพื้นฐานทางการเงิน (จำลองเป็น Input)
# ==========================================
st.sidebar.header("📊 1. User Financial Profile")
income = st.sidebar.number_input("Monthly Income (I) [฿]", value=50000.0, step=5000.0)
exp_necessary = st.sidebar.number_input("Necessary Expenses (E_n) [฿]", value=20000.0, step=2000.0)
exp_discretionary = st.sidebar.number_input("Discretionary Spending (E_d) [฿]", value=15000.0, step=1000.0)
debt_payment = st.sidebar.number_input("Monthly Debt Payment (P) [฿]", value=18000.0, step=1000.0)
e_fund = st.sidebar.number_input("Emergency Fund [฿]", value=10000.0, step=5000.0)
months_on_budget = st.sidebar.slider("Months on Budget", min_value=0, max_value=6, value=2)

st.sidebar.markdown("---")
st.sidebar.header("🕒 2. Career & Timeline")
age = st.sidebar.number_input("Current Age", value=28)
career = st.sidebar.selectbox("Career Profile", ["technical_engineering", "physical_trade", "management_strategy", "knowledge_advisory"])

# ==========================================
# MAIN PANEL: ส่วนของ Interactive Virtualization
# ==========================================
col1, col2 = st.columns([1, 1.2])

with col1:
    st.subheader("🕹️ Simulation: Adjust Behavior Score")
    st.markdown("ทดลองปรับเปลี่ยน **Behavior Score (0-10)** เพื่อดูผลกระทบต่อเสถียรภาพหนี้สินแบบ Real-time")
    
    # ตัวสไลเดอร์หลักสำหรับทำ Live Simulation บนเวที Pitch
    behavior_score = st.slider(
        "Current Behavior Score", 
        min_value=0.0, 
        max_value=10.0, 
        value=4.0, 
        step=0.5,
        help="คะแนนประเมินพฤติกรรม 0-10 คะแนน"
    )
    
    # 🌟 ฟีเจอร์จำลอง: ถ้าทำตาม Action แนะนำ คะแนนจะเพิ่มขึ้นเท่าไหร่
    st.markdown("#### ⚡ Quick Boost Actions")
    st.markdown("หากผู้ใช้ลงมือทำตามคำแนะนำพฤติกรรม (อิงจาก Nudge Engine):")
    
    selected_actions = []
    for i, action in enumerate(BEHAVIOR_ACTIONS[:3]): # ดึง 3 แอคชั่นแรกมาโชว์
        if st.checkbox(f"ทำสิ่งนี้: {action} (+1.0 Score)", key=f"act_{i}"):
            selected_actions.append(1.0)
            
    # คำนวณคะแนนรวมหลังปรับปรุงพฤติกรรม (แต่ไม่เกิน 10)
    boosted_score = min(behavior_score + sum(selected_actions), 10.0)
    if boosted_score > behavior_score:
        st.info(f"🚀 คะแนนพฤติกรรมของคุณจะเพิ่มขึ้นเป็น: **{boosted_score:.1f} / 10.0**")

with col2:
    st.subheader("🎯 Real-Time Status & Zone Diagnostic")
    
    # จำลองการส่งข้อมูลเข้า Engine Layer 1 (ข้อมูลสด VS ข้อมูลหลังปรับพฤติกรรม)
    # *หมายเหตุ: ในระบบจริง SE ของคุณคำนวณจาก F_net และ Behavior Score ใน model_v2 
    # โค้ดส่วนนี้แปลงตรรกะมาจำลองเป็นกราฟ Gauge เพื่อการจัดแสดงที่เข้าใจง่ายใน 3 วินาที
    
    # จำลองสูตรการคำนวณ SE (Stability Score) อย่างง่ายจากตัวแปรที่มีผลต่อพฤติกรรม
    def calculate_simulated_se(b_score):
        f_net = income - exp_necessary - exp_discretionary - debt_payment
        base_se = (f_net / income) * 100
        behavior_effect = (b_score - 5) * 4  # คะแนนพฤติกรรมดึงขึ้นหรือฉุดลง
        return base_se + behavior_effect

    se_current = calculate_simulated_se(behavior_score)
    se_boosted = calculate_simulated_se(boosted_score)
    
    zone_current = _zone_from_se(se_current)
    zone_boosted = _zone_from_se(se_boosted)
    
    # แสดงผล Gauge Indicator เปรียบเทียบความเปลี่ยนแปลง
    fig = go.Figure()
    
    # เข็มปัจจุบัน
    fig.add_trace(go.Indicator(
        mode = "gauge+number",
        value = se_boosted,
        title = {'text': f"Financial Stability State (S_E)<br>Current Zone: {zone_current.upper()}"},
        domain = {'x': [0, 1], 'y': [0, 1]},
        gauge = {
            'axis': {'range': [-30, 40], 'tickwidth': 1, 'tickcolor': "darkblue"},
            'bar': {'color': "#1f77b4" if boosted_score == behavior_score else "#2ca02c"}, # เปลี่ยนสีบาร์หากพฤติกรรมดีขึ้น
            'bgcolor': "white",
            'borderwidth': 2,
            'bordercolor': "gray",
            'steps': [
                {'range': [-30, -10], 'color': '#ff4d4d'},  # Black Hole
                {'range': [-10, 5], 'color': '#ff9933'},   # Debt Orbit
                {'range': [5, 20], 'color': '#ffff66'},    # Marginal Escape
                {'range': [20, 40], 'color': '#66ff66'}    # Escape Trajectory
            ],
            'threshold': {
                'line': {'color': "black", 'width': 4},
                'thickness': 0.75,
                'value': se_current
            }
        }
    ))
    
    fig.update_layout(height=350, margin=dict(l=20, r=20, t=50, b=20))
    st.plotly_chart(fig, use_container_width=True)

# ==========================================
# LOWER SECTION: การจำลองเปรียบเทียบเชิงลึก (Behavior vs Financial Zone)
# ==========================================
st.markdown("---")
st.subheader("📈 Behavior - Zone Sensitivity Analysis")
st.markdown("กราฟวิเคราะห์ความไว (Sensitivity Chart) เพื่อชี้ให้เห็นว่า **ทุกๆ 1 คะแนนที่เปลี่ยนไป** จะเปลี่ยนชีวิตผู้ใช้อย่างไร")

# สร้างตารางข้อมูลจำลองพฤติกรรมตั้งแต่คะแนน 0 ถึง 10
score_range = [i * 0.5 for i in range(21)]
se_trends = [calculate_simulated_se(s) for s in score_range]
zone_trends = [_zone_from_se(se) for se in se_trends]

# แมปชื่อโซนไทย-อังกฤษ เพื่อให้ออกกราฟสวยงาม
zone_colors = {
    "black_hole": "🕳️ Black Hole",
    "debt_orbit": "⚠️ Debt Orbit",
    "marginal_escape": "🟡 Marginal Escape",
    "escape_trajectory": "🚀 Escape Trajectory"
}
zone_display = [zone_colors[z] for z in zone_trends]

df_trend = pd.DataFrame({
    "Behavior Score": score_range,
    "Stability Score (S_E)": se_trends,
    "Financial Zone": zone_display
})

# พล็อตกราฟเส้นแสดงจุดเปลี่ยนผ่าน (Tipping Point)
import plotly.express as px
fig_line = px.line(
    df_trend, 
    x="Behavior Score", 
    y="Stability Score (S_E)", 
    color="Financial Zone",
    color_discrete_map={
        "🕳️ Black Hole": "#ff4d4d",
        "⚠️ Debt Orbit": "#ff9933",
        "🟡 Marginal Escape": "#ffcc00",
        "🚀 Escape Trajectory": "#2ca02c"
    },
    title="เส้นทางหลุดพ้นหนี้ตามระดับคะแนนพฤติกรรม (Tipping Point Visualization)",
    markers=True
)

# ขีดเส้นมาร์กตำแหน่งปัจจุบันของผู้ใช้บนกราฟ
fig_line.add_vline(x=behavior_score, line_dash="dash", line_color="blue", annotation_text="Current Score")
if boosted_score > behavior_score:
    fig_line.add_vline(x=boosted_score, line_dash="dash", line_color="green", annotation_text="Boosted Score")

st.plotly_chart(fig_line, use_container_width=True)

# ==========================================
# INSIGHTS FOR PITCHING
# ==========================================
with st.expander("💡 Key Pitching Takeaways (สำหรับเปิดอ่านหรือจำสคริปต์ตอนโชว์ฟังก์ชันนี้)"):
    st.markdown("""
    * **The Tipping Point Concept:** ชี้ให้กรรมการเห็นบนกราฟเส้นว่า ผู้ใช้ไม่จำเป็นต้องหักดิบอดออมจนเครียด แค่เปลี่ยนพฤติกรรมเล็กๆ น้อยๆ เพื่อดันคะแนนจาก `4.0` ไปเป็น `6.0` (เช่น การตั้ง auto-pay หรือหยุดคิด 24 ชม. ก่อนซื้อของใหญ่) ระบบจะคำนวณเลยว่า **S_E ทะลุแดนบวก** หลุดออกจาก *Debt Orbit* ขึ้นสู่ *Marginal Escape* ได้ทันที
    * **Actionable Nudge Dynamic:** ระบบของเราไม่ได้ให้คะแนนพฤติกรรมไว้ดูเล่นๆ แต่เชื่อมโยงโดยตรงกับความเร็วในการหลุดพ้นหนี้ ยิ่งคะแนนพฤติกรรมดี แรงต้านในการชำระหนี้จะยิ่งลดลง (แสดงผลเรียลไทม์ผ่านเข็มไมล์ด้านบน)
    """)