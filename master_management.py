"""
ブロイラー飼養管理システム - マスタデータ管理
農場・鶏舎・担当者・飼料銘柄の登録・編集・削除
"""

import streamlit as st
import pandas as pd
from supabase import create_client, Client

st.set_page_config(page_title="マスタデータ管理", layout="wide")

# ----------------------------------------------------------
# コンパクトCSS
# ----------------------------------------------------------
st.markdown("""
<style>
h1 { font-size: 1.2rem !important; margin-bottom: 0.2rem !important; }
h2 { font-size: 1.0rem !important; margin-bottom: 0.2rem !important; }
h3 { font-size: 0.95rem !important; margin-bottom: 0.1rem !important; }
h4 { font-size: 0.88rem !important; margin-bottom: 0.1rem !important; }
.block-container { padding-top: 0.6rem !important; padding-bottom: 0.4rem !important; max-width: 100% !important; }
.stNumberInput label, .stSelectbox label, .stTextArea label, .stDateInput label, .stCheckbox label { font-size: 0.76rem !important; margin-bottom: 0 !important; }
.stNumberInput input, .stDateInput input { padding-top: 0.15rem !important; padding-bottom: 0.15rem !important; font-size: 0.82rem !important; }
.stSelectbox div[data-baseweb="select"] { font-size: 0.82rem !important; }
.stTextArea textarea { font-size: 0.82rem !important; }
[data-testid="metric-container"] label { font-size: 0.70rem !important; }
[data-testid="metric-container"] [data-testid="stMetricValue"] { font-size: 0.95rem !important; }
.stAlert { padding: 0.3rem 0.5rem !important; font-size: 0.78rem !important; }
.stButton button { padding: 0.2rem 0.7rem !important; font-size: 0.82rem !important; }
hr { margin: 0.3rem 0 !important; }
.stCaption, [data-testid="stCaptionContainer"] { font-size: 0.70rem !important; }
.stTabs [data-baseweb="tab"] { font-size: 0.80rem !important; padding: 0.25rem 0.7rem !important; }
div[data-testid="stVerticalBlock"] > div { gap: 0.15rem !important; }
</style>
""", unsafe_allow_html=True)

# ----------------------------------------------------------
# Supabase接続
# ----------------------------------------------------------
@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

supabase = get_supabase()

# ----------------------------------------------------------
# ヘルパー関数
# ----------------------------------------------------------
def fetch(table: str, order: str = None) -> list:
    q = supabase.table(table).select("*")
    if order:
        q = q.order(order)
    return q.execute().data

def insert(table: str, data: dict):
    return supabase.table(table).insert(data).execute()

def update(table: str, id_col: str, id_val, data: dict):
    return supabase.table(table).update(data).eq(id_col, id_val).execute()

def delete(table: str, id_col: str, id_val):
    return supabase.table(table).delete().eq(id_col, id_val).execute()

# ----------------------------------------------------------
# UI
# ----------------------------------------------------------
st.title("⚙️ マスタデータ管理")

tab1, tab2, tab3, tab4 = st.tabs(["🗺️ 農場", "🏗️ 鶏舎", "🪪 担当者", "📦 飼料銘柄"])

# ==========================================================
# タブ1: 農場マスタ
# ==========================================================
with tab1:
    st.subheader("📡 農場マスタ")

    farms = fetch("farms", "farm_id")
    farm_map = {f["farm_id"]: f["farm_name"] for f in farms}

    # 操作モード選択
    mode = st.radio("操作", ["新規登録", "編集", "削除"], horizontal=True, key="farm_mode")

    if mode == "新規登録":
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown("#### ➕ 新規登録")
            farm_name = st.text_input("農場名", key="farm_name_new")
            farm_address = st.text_input("所在地（任意）", key="farm_address_new")
            if st.button("登録", key="add_farm"):
                if not farm_name:
                    st.error("農場名を入力してください")
                else:
                    try:
                        insert("farms", {"farm_name": farm_name, "address": farm_address or None})
                        st.success(f"✅ 農場「{farm_name}」を登録しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

    elif mode == "編集":
        if not farms:
            st.info("登録済みの農場がありません")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown("#### ✏️ 編集")
                edit_id = st.selectbox(
                    "編集する農場",
                    [f["farm_id"] for f in farms],
                    format_func=lambda x: farm_map[x],
                    key="farm_edit_id"
                )
                target = next(f for f in farms if f["farm_id"] == edit_id)
                new_name = st.text_input("農場名", value=target["farm_name"], key="farm_edit_name")
                new_address = st.text_input("所在地", value=target["address"] or "", key="farm_edit_address")
                if st.button("更新", key="update_farm"):
                    if not new_name:
                        st.error("農場名を入力してください")
                    else:
                        try:
                            update("farms", "farm_id", edit_id, {
                                "farm_name": new_name,
                                "address": new_address or None
                            })
                            st.success(f"✅ 更新しました")
                            st.rerun()
                        except Exception as e:
                            st.error(f"更新エラー: {e}")

    elif mode == "削除":
        if not farms:
            st.info("登録済みの農場がありません")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown("#### 🗑️ 削除")
                del_id = st.selectbox(
                    "削除する農場",
                    [f["farm_id"] for f in farms],
                    format_func=lambda x: farm_map[x],
                    key="farm_del_id"
                )
                st.warning("⚠️ 削除すると関連する鶏舎データも影響を受けます")
                if st.button("削除", key="del_farm", type="primary"):
                    try:
                        delete("farms", "farm_id", del_id)
                        st.success("✅ 削除しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"削除エラー: {e}")

    # 一覧表示（常時）
    st.divider()
    st.markdown("#### 📋 登録済み農場一覧")
    farms = fetch("farms", "farm_id")
    if farms:
        df = pd.DataFrame(farms)[["farm_id", "farm_name", "address"]]
        df.columns = ["ID", "農場名", "所在地"]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("まだ農場が登録されていません")

# ==========================================================
# タブ2: 鶏舎マスタ
# ==========================================================
with tab2:
    st.subheader("🏗️ 鶏舎マスタ")

    farms = fetch("farms", "farm_id")
    if not farms:
        st.warning("先に農場を登録してください")
    else:
        farm_options = {f["farm_name"]: f["farm_id"] for f in farms}
        farm_map = {f["farm_id"]: f["farm_name"] for f in farms}
        houses = fetch("houses", "house_id")
        house_map = {h["house_id"]: h["house_name"] for h in houses}

        mode = st.radio("操作", ["新規登録", "編集", "削除"], horizontal=True, key="house_mode")

        if mode == "新規登録":
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown("#### ➕ 新規登録")
                selected_farm = st.selectbox("農場", list(farm_options.keys()), key="house_farm_new")
                house_name = st.text_input("鶏舎名", key="house_name_new")
                tank_number = st.text_input("タンク番号", key="tank_number_new")
                floor_area = st.number_input("飼養面積（坪）", min_value=0.0, step=0.5, key="floor_area_new")
                tank_capacity = st.number_input("タンク容量（kg）", min_value=0.0, step=100.0, key="tank_capacity_new")
                transfer_coef = st.number_input("基本搬送係数（kg/min）", min_value=0.0, step=0.5, key="transfer_coef_new",
                    help="飼料搬送装置の基本搬送能力。採食時間（分）× 搬送係数 = 採食量（kg）")
                if st.button("登録", key="add_house"):
                    if not house_name:
                        st.error("鶏舎名を入力してください")
                    elif floor_area <= 0:
                        st.error("飼養面積を入力してください")
                    else:
                        try:
                            insert("houses", {
                                "farm_id": farm_options[selected_farm],
                                "house_name": house_name,
                                "tank_number": tank_number or None,
                                "floor_area_tsubo": floor_area,
                                "tank_capacity": tank_capacity or None,
                                "feed_transfer_coef": transfer_coef or None,
                                "is_active": True
                            })
                            st.success(f"✅ 鶏舎「{house_name}」を登録しました")
                            st.rerun()
                        except Exception as e:
                            st.error(f"登録エラー: {e}")

        elif mode == "編集":
            if not houses:
                st.info("登録済みの鶏舎がありません")
            else:
                col1, col2 = st.columns([1, 1])
                with col1:
                    st.markdown("#### ✏️ 編集")
                    edit_id = st.selectbox(
                        "編集する鶏舎",
                        [h["house_id"] for h in houses],
                        format_func=lambda x: f"{farm_map.get(next((h['farm_id'] for h in houses if h['house_id']==x), None), '')} - {house_map[x]}",
                        key="house_edit_id"
                    )
                    target = next(h for h in houses if h["house_id"] == edit_id)
                    new_farm = st.selectbox("農場", list(farm_options.keys()),
                        index=list(farm_options.values()).index(target["farm_id"]) if target["farm_id"] in farm_options.values() else 0,
                        key="house_edit_farm")
                    new_name = st.text_input("鶏舎名", value=target["house_name"], key="house_edit_name")
                    new_tank = st.text_input("タンク番号", value=target["tank_number"] or "", key="house_edit_tank")
                    new_area = st.number_input("飼養面積（坪）", value=float(target["floor_area_tsubo"] or 0), step=0.5, key="house_edit_area")
                    new_cap = st.number_input("タンク容量（kg）", value=float(target["tank_capacity"] or 0), step=100.0, key="house_edit_cap")
                    new_coef = st.number_input("基本搬送係数（kg/min）", value=float(target.get("feed_transfer_coef") or 0), step=0.5, key="house_edit_coef",
                        help="飼料搬送装置の基本搬送能力")
                    new_active = st.checkbox("稼働中", value=target["is_active"], key="house_edit_active")
                    if st.button("更新", key="update_house"):
                        try:
                            update("houses", "house_id", edit_id, {
                                "farm_id": farm_options[new_farm],
                                "house_name": new_name,
                                "tank_number": new_tank or None,
                                "floor_area_tsubo": new_area,
                                "tank_capacity": new_cap or None,
                                "feed_transfer_coef": new_coef or None,
                                "is_active": new_active
                            })
                            st.success("✅ 更新しました")
                            st.rerun()
                        except Exception as e:
                            st.error(f"更新エラー: {e}")

        elif mode == "削除":
            if not houses:
                st.info("登録済みの鶏舎がありません")
            else:
                col1, col2 = st.columns([1, 1])
                with col1:
                    st.markdown("#### 🗑️ 削除")
                    del_id = st.selectbox(
                        "削除する鶏舎",
                        [h["house_id"] for h in houses],
                        format_func=lambda x: f"{farm_map.get(next((h['farm_id'] for h in houses if h['house_id']==x), None), '')} - {house_map[x]}",
                        key="house_del_id"
                    )
                    st.warning("⚠️ 削除すると関連するロット・日次データも影響を受けます")
                    if st.button("削除", key="del_house", type="primary"):
                        try:
                            delete("houses", "house_id", del_id)
                            st.success("✅ 削除しました")
                            st.rerun()
                        except Exception as e:
                            st.error(f"削除エラー: {e}")

        # 一覧表示
        st.divider()
        st.markdown("#### 📋 登録済み鶏舎一覧")
        houses = fetch("houses", "house_id")
        if houses:
            df = pd.DataFrame(houses)
            df["farm_name"] = df["farm_id"].map(farm_map)
            df = df[["house_id", "farm_name", "house_name", "tank_number",
                      "floor_area_tsubo", "tank_capacity", "feed_transfer_coef", "is_active"]]
            df.columns = ["ID", "農場", "鶏舎名", "タンクNo", "面積(坪)", "タンク容量(kg)", "搬送係数(kg/min)", "稼働中"]
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("まだ鶏舎が登録されていません")

# ==========================================================
# タブ3: 担当者マスタ
# ==========================================================
with tab3:
    st.subheader("🪪 担当者マスタ")

    farms = fetch("farms", "farm_id")
    farm_options = {f["farm_name"]: f["farm_id"] for f in farms} if farms else {}
    farm_map = {f["farm_id"]: f["farm_name"] for f in farms} if farms else {}
    workers = fetch("workers", "worker_id")
    worker_map = {w["worker_id"]: w["worker_name"] for w in workers}

    mode = st.radio("操作", ["新規登録", "編集", "削除"], horizontal=True, key="worker_mode")

    if mode == "新規登録":
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown("#### ➕ 新規登録")
            worker_name = st.text_input("担当者名", key="worker_name_new")
            worker_farm = st.selectbox("主所属農場（任意）",
                ["未設定"] + list(farm_options.keys()), key="worker_farm_new")
            if st.button("登録", key="add_worker"):
                if not worker_name:
                    st.error("担当者名を入力してください")
                else:
                    try:
                        insert("workers", {
                            "worker_name": worker_name,
                            "farm_id": farm_options.get(worker_farm) if worker_farm != "未設定" else None,
                            "is_active": True
                        })
                        st.success(f"✅ 担当者「{worker_name}」を登録しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

    elif mode == "編集":
        if not workers:
            st.info("登録済みの担当者がありません")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown("#### ✏️ 編集")
                edit_id = st.selectbox("編集する担当者",
                    [w["worker_id"] for w in workers],
                    format_func=lambda x: worker_map[x], key="worker_edit_id")
                target = next(w for w in workers if w["worker_id"] == edit_id)
                new_name = st.text_input("担当者名", value=target["worker_name"], key="worker_edit_name")
                farm_list = ["未設定"] + list(farm_options.keys())
                current_farm = farm_map.get(target["farm_id"], "未設定")
                new_farm = st.selectbox("主所属農場", farm_list,
                    index=farm_list.index(current_farm) if current_farm in farm_list else 0,
                    key="worker_edit_farm")
                new_active = st.checkbox("稼働中", value=target["is_active"], key="worker_edit_active")
                if st.button("更新", key="update_worker"):
                    try:
                        update("workers", "worker_id", edit_id, {
                            "worker_name": new_name,
                            "farm_id": farm_options.get(new_farm) if new_farm != "未設定" else None,
                            "is_active": new_active
                        })
                        st.success("✅ 更新しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"更新エラー: {e}")

    elif mode == "削除":
        if not workers:
            st.info("登録済みの担当者がありません")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown("#### 🗑️ 削除")
                del_id = st.selectbox("削除する担当者",
                    [w["worker_id"] for w in workers],
                    format_func=lambda x: worker_map[x], key="worker_del_id")
                if st.button("削除", key="del_worker", type="primary"):
                    try:
                        delete("workers", "worker_id", del_id)
                        st.success("✅ 削除しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"削除エラー: {e}")

    st.divider()
    st.markdown("#### 📋 登録済み担当者一覧")
    workers = fetch("workers", "worker_id")
    if workers:
        df = pd.DataFrame(workers)
        df["farm_name"] = df["farm_id"].map(farm_map).fillna("未設定")
        df = df[["worker_id", "worker_name", "farm_name", "is_active"]]
        df.columns = ["ID", "担当者名", "所属農場", "稼働中"]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("まだ担当者が登録されていません")

# ==========================================================
# タブ4: 飼料銘柄マスタ
# ==========================================================
with tab4:
    st.subheader("📦 飼料銘柄マスタ")

    brands = fetch("feed_brands", "feed_brand_id")
    brand_map = {b["feed_brand_id"]: b["brand_name"] for b in brands}
    FEED_TYPES = ["スターター", "グロワー", "フィニッシャー", "その他"]

    mode = st.radio("操作", ["新規登録", "編集", "削除"], horizontal=True, key="brand_mode")

    if mode == "新規登録":
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown("#### ➕ 新規登録")
            brand_name = st.text_input("銘柄名", key="brand_name_new")
            manufacturer = st.text_input("メーカー（任意）", key="manufacturer_new")
            feed_type = st.selectbox("飼料種別", FEED_TYPES, key="feed_type_new")
            coef_ratio = st.number_input("搬送係数補正率", min_value=0.1, max_value=2.0,
                value=1.000, step=0.005, format="%.3f", key="brand_ratio_new",
                help="1.000=標準。実搬送係数 = 鶏舎基本係数 × 補正率")
            if st.button("登録", key="add_brand"):
                if not brand_name:
                    st.error("銘柄名を入力してください")
                else:
                    try:
                        insert("feed_brands", {
                            "brand_name": brand_name,
                            "manufacturer": manufacturer or None,
                            "feed_type": feed_type,
                            "is_active": True
                        })
                        st.success(f"✅ 飼料銘柄「{brand_name}」を登録しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

    elif mode == "編集":
        if not brands:
            st.info("登録済みの飼料銘柄がありません")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown("#### ✏️ 編集")
                edit_id = st.selectbox("編集する銘柄",
                    [b["feed_brand_id"] for b in brands],
                    format_func=lambda x: brand_map[x], key="brand_edit_id")
                target = next(b for b in brands if b["feed_brand_id"] == edit_id)
                new_name = st.text_input("銘柄名", value=target["brand_name"], key="brand_edit_name")
                new_mfr = st.text_input("メーカー", value=target["manufacturer"] or "", key="brand_edit_mfr")
                new_type = st.selectbox("飼料種別", FEED_TYPES,
                    index=FEED_TYPES.index(target["feed_type"]) if target["feed_type"] in FEED_TYPES else 0,
                    key="brand_edit_type")
                new_ratio = st.number_input("搬送係数補正率", min_value=0.1, max_value=2.0,
                    value=float(target.get("transfer_coef_ratio") or 1.0),
                    step=0.005, format="%.3f", key="brand_edit_ratio",
                    help="1.000=標準。実搬送係数 = 鶏舎基本係数 × 補正率")
                new_active = st.checkbox("稼働中", value=target["is_active"], key="brand_edit_active")
                if st.button("更新", key="update_brand"):
                    try:
                        update("feed_brands", "feed_brand_id", edit_id, {
                            "brand_name": new_name,
                            "manufacturer": new_mfr or None,
                            "feed_type": new_type,
                            "transfer_coef_ratio": new_ratio,
                            "is_active": new_active
                        })
                        st.success("✅ 更新しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"更新エラー: {e}")

    elif mode == "削除":
        if not brands:
            st.info("登録済みの飼料銘柄がありません")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown("#### 🗑️ 削除")
                del_id = st.selectbox("削除する銘柄",
                    [b["feed_brand_id"] for b in brands],
                    format_func=lambda x: brand_map[x], key="brand_del_id")
                st.warning("⚠️ 日次記録で使用中の銘柄は削除できません")
                if st.button("削除", key="del_brand", type="primary"):
                    try:
                        delete("feed_brands", "feed_brand_id", del_id)
                        st.success("✅ 削除しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"削除エラー: {e}")

    st.divider()
    st.markdown("#### 📋 登録済み飼料銘柄一覧")
    brands = fetch("feed_brands", "feed_brand_id")
    if brands:
        df = pd.DataFrame(brands)[["feed_brand_id", "brand_name", "manufacturer", "feed_type", "transfer_coef_ratio", "is_active"]]
        df.columns = ["ID", "銘柄名", "メーカー", "種別", "補正率", "稼働中"]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("まだ飼料銘柄が登録されていません")
