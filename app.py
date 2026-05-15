import streamlit as st
import time
import base64
import sqlite3
import uuid
from datetime import datetime
from zhipuai import ZhipuAI

# --- 1. 初始化与配置 ---
st.set_page_config(page_title="鲜知AI - 冰箱管理助手", page_icon="🥬", layout="centered")

# 你的 API Key
# 代码会自动处理：在本地它找 secrets.toml，在云端它找 Secrets 面板
api_key = st.secrets["ZHIPU_API_KEY"]

client = ZhipuAI(api_key=api_key)


st.markdown("""
    <style>
    .main {background-color: #f5f7f9;}
    .stButton>button {width: 100%; border-radius: 12px; height: 3.2em; background-color: #4CAF50; color: white; font-weight: bold;}
    .stTextInput>div>div>input {border-radius: 10px;}
    .stProgress > div > div > div > div {background-color: #4CAF50;}
    </style>
    """, unsafe_allow_html=True)


# --- 2. 数据库与算法逻辑 ---
def init_db():
    conn = sqlite3.connect('fridge.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS inventory
                 (
                     id
                     TEXT
                     PRIMARY
                     KEY,
                     name
                     TEXT,
                     added_date
                     TEXT,
                     expiry
                     TEXT,
                     status
                     TEXT
                 )''')
    conn.commit()
    conn.close()


def get_inventory():
    conn = sqlite3.connect('fridge.db')
    c = conn.cursor()
    c.execute("SELECT * FROM inventory ORDER BY added_date DESC")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "date": r[2], "expiry": r[3], "status": r[4]} for r in rows]


def clear_db():
    conn = sqlite3.connect('fridge.db')
    c = conn.cursor()
    c.execute("DELETE FROM inventory")
    conn.commit()
    conn.close()


# 纯时间维度的动态新鲜度计算器 (假设默认保鲜期为5天)
def calculate_freshness(added_date_str):
    try:
        added_time = datetime.strptime(added_date_str, "%Y-%m-%d %H:%M")
        delta = datetime.now() - added_time
        days_passed = delta.total_seconds() / (24 * 3600)
        score = int(100 - (days_passed / 5) * 100)
        return max(0, min(100, score))  # 控制在 0-100 之间
    except:
        return 100


init_db()

if 'ai_raw_result' not in st.session_state:
    st.session_state.ai_raw_result = ""

# --- 3. 侧边栏导航 ---
st.sidebar.title("🥬 鲜知AI")
menu = st.sidebar.radio("功能菜单", ["智能首页", "拍照入库", "我的库存清单", "AI 大厨推荐"])

# --- 4. 模块逻辑 ---

if menu == "智能首页":
    current_inv = get_inventory()
    st.header("今日冰箱概况")
    col1, col2 = st.columns(2)
    col1.metric("总计食材", f"{len(current_inv)} 件")

    # 动态计算低于 60 分的警告数量
    warning_count = sum(1 for item in current_inv if calculate_freshness(item['date']) < 60)
    col2.metric("临期告警", f"{warning_count} 件")

    st.write("---")
    st.subheader("💡 冰箱小贴士")
    if not current_inv:
        st.write("冰箱空空的，点击左侧【拍照入库】开始管理吧！")
    else:
        st.info("系统正基于入库时间为您自动推算食材新鲜度。")

elif menu == "拍照入库":
    st.header("📷 拍照识别")
    img_file = st.file_uploader("拍摄冰箱照片", type=['jpg', 'jpeg', 'png'])

    if img_file:
        base64_image = base64.b64encode(img_file.read()).decode('utf-8')
        st.image(img_file, caption="照片预览")

        if st.button("🔍 提取食材 (最新状态)"):
            with st.spinner("AI 正在扫描您的冰箱..."):
                try:
                    # 恢复纯净版 Prompt，不再让 AI 打分
                    response = client.chat.completions.create(
                        model="glm-4v",
                        messages=[{
                            "role": "user",
                            "content": [
                                {"type": "text",
                                 "text": "提取图片中的食材。要求极度严格：绝对不要任何解释前缀！绝对不要用列表横杠！只输出食材名字，全部用英文逗号(,)隔开。示例：玉米,青豆,土豆,肉片"},
                                {"type": "image_url", "image_url": {"url": base64_image}}
                            ]
                        }]
                    )
                    raw_text = response.choices[0].message.content

                    if "：" in raw_text: raw_text = raw_text.split("：")[-1]
                    if ":" in raw_text: raw_text = raw_text.split(":")[-1]
                    cleaned_text = raw_text.replace("-", ",").replace("、", ",").replace("，", ",").replace("\n", ",")
                    final_items = [i.strip() for i in cleaned_text.split(",") if i.strip()]

                    st.session_state.ai_raw_result = ",".join(final_items)
                except Exception as e:
                    st.error(f"识别失败：{e}")

        # 核心逻辑：重合检测与入库确认
        if st.session_state.ai_raw_result:
            st.markdown("### ✍️ 核对识别结果")
            edited_items_str = st.text_input("您可以修改遗漏或错误的食材：", value=st.session_state.ai_raw_result)
            final_items_list = [i.strip() for i in edited_items_str.split(",") if i.strip()]

            # 获取当前库存，寻找重合项
            current_inv = get_inventory()
            current_dict = {item['name']: item['date'] for item in current_inv}

            overlap_items = [item for item in final_items_list if item in current_dict]

            # 记录用户的选择
            overlap_decisions = {}
            if overlap_items:
                st.warning("⚠️ 发现与现有库存重合的食材，请确认：")
                for item in overlap_items:
                    decision = st.radio(
                        f"图中的【{item}】是？",
                        ["之前剩下的 (沿用旧鲜度)", "刚买补货的 (满鲜度入库)"],
                        key=f"radio_{item}"
                    )
                    overlap_decisions[item] = decision

            if st.button("✅ 确认入库 (覆盖旧状态)"):
                now_date = datetime.now().strftime("%Y-%m-%d %H:%M")
                records_to_insert = []

                for item in final_items_list:
                    # 判断入库时间
                    if item in overlap_items and overlap_decisions[item] == "之前剩下的 (沿用旧鲜度)":
                        item_date = current_dict[item]  # 保留旧的入库时间
                    else:
                        item_date = now_date  # 全新的入库时间

                    records_to_insert.append((str(uuid.uuid4())[:8], item, item_date, "5天", "good"))

                # 清空旧库，写入全新记录
                clear_db()
                conn = sqlite3.connect('fridge.db')
                c = conn.cursor()
                for rec in records_to_insert:
                    c.execute("INSERT INTO inventory VALUES (?, ?, ?, ?, ?)", rec)
                conn.commit()
                conn.close()

                st.session_state.ai_raw_result = ""
                st.balloons()
                st.success("入库成功！界面已刷新。")
                time.sleep(1.5)
                st.rerun()

elif menu == "我的库存清单":
    st.header("📦 最新库存与新鲜度")
    current_inv = get_inventory()

    if not current_inv:
        st.write("目前冰箱没有存货。")
    else:
        st.caption("展示当前冰箱里的所有物品")
        for item in current_inv:
            with st.container():
                col_name, col_bar = st.columns([4, 6])

                # 调用函数实时计算分数
                score = calculate_freshness(item['date'])

                if score >= 80:
                    icon, status_txt = "🟢", "非常新鲜"
                elif score >= 50:
                    icon, status_txt = "🟡", "状态一般"
                else:
                    icon, status_txt = "🔴", "建议尽快食用"

                col_name.markdown(f"{icon} **{item['name']}** <br><small style='color:gray;'>{status_txt}</small>",
                                  unsafe_allow_html=True)
                with col_bar:
                    st.progress(score / 100.0, text=f"预估鲜度: {score}分")
                st.markdown("<hr style='margin:0.5em 0; opacity:0.2'>", unsafe_allow_html=True)

        if st.button("🗑️ 彻底清空当前库存"):
            clear_db()
            st.rerun()

elif menu == "AI 大厨推荐":
    st.header("🍳 智能大厨")
    current_inv = get_inventory()

    if not current_inv:
        st.warning("冰箱里没有东西，无法出菜谱哦！")
    else:
        inv_str = "、".join([item['name'] for item in current_inv])
        st.markdown(f"**当前可用食材：** `{inv_str}`")

        st.write("---")
        mode = st.radio("选择推荐模式：", ["模糊匹配 (灵感模式)", "严格匹配 (生存模式)", "极速模式 (简单快速)"],
                        horizontal=True)
        user_demand = st.text_input("有什么特殊要求？(如：想吃酸辣的、减脂、不用火)")

        if st.button("🪄 生成 3 套推荐方案"):
            with st.spinner("大厨正在构思方案..."):
                mode_prompts = {
                    "模糊匹配 (灵感模式)": "可以基于现有食材发挥，允许加入少量外部辅助配菜，提供高水平、有创意的做饭思路。",
                    "严格匹配 (生存模式)": "仅允许使用现有食材和基础调料，严禁加入任何外部食材，目标是清空库存。",
                    "极速模式 (简单快速)": "利用现有食材，提供步骤最简、时间最短的烹饪方案，越快越好。"
                }
                final_prompt = f"""我的食材：{inv_str}。\n模式要求：{mode_prompts[mode]}。\n用户额外要求：{user_demand if user_demand else "无"}。\n请提供3道不同的菜谱。每道菜请严格按以下格式输出，不要有其他废话：\n菜名：[名字]\n理由：[推荐理由]\n做法：[简述步骤]\n---"""
                try:
                    response = client.chat.completions.create(model="glm-4",
                                                              messages=[{"role": "user", "content": final_prompt}])
                    full_text = response.choices[0].message.content
                    recipes = full_text.split("---")
                    for i, recipe in enumerate(recipes[:3]):
                        if recipe.strip():
                            lines = [line for line in recipe.strip().split('\n') if line.strip()]
                            title = lines[0].replace("菜名：", "") if lines else f"推荐方案 {i + 1}"
                            with st.expander(f"🍴 {title}"):
                                st.markdown(recipe.replace("---", ""))
                except Exception as e:
                    st.error(f"生成菜谱出错：{e}")
