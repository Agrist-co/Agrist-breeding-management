"""
ブロイラー飼養管理システム - マスタデータ管理
農場・鶏舎・担当者・飼料銘柄の登録・編集
"""

import streamlit as st
import pandas as pd
from supabase import create_client, Client

st.set_page_config(page_title="マスタデータ管理", layout="wide")

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
st.title("🏭 マスタデータ管理")

tab1, tab2, tab3, tab4 = st.tabs(["🌾 農場", "🏠 鶏舎", "👷 担当者", "🌽 飼料銘柄"])

# ==========================================================
# タブ1: 農場マスタ
# ==========================================================
with tab1:
    st.subheader("農場マスタ")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.markdown("#### 新規登録")
        farm_name = st.text_input("農場名", key="farm_name")
        farm_address = st.text_input("所在地（任意）", key="farm_address")

        if st.button("登録", key="add_farm"):
            if not farm_name:
                st.error("農場名を入力してください")
            else:
                try:
                    insert("farms", {
                        "farm_name": farm_name,
                        "address": farm_address or None
                    })
                    st.success(f"✅ 農場「{farm_name}」を登録しました")
                    st.rerun()
                except Exception as e:
                    st.error(f"登録エラー: {e}")

    with col2:
        st.markdown("#### 登録済み農場")
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
    st.subheader("鶏舎マスタ")

    farms = fetch("farms", "farm_id")
    if not farms:
        st.warning("先に農場を登録してください")
    else:
        farm_options = {f["farm_name"]: f["farm_id"] for f in farms}

        col1, col2 = st.columns([1, 1])

        with col1:
            st.markdown("#### 新規登録")
            selected_farm = st.selectbox("農場", list(farm_options.keys()), key="house_farm")
            house_name = st.text_input("鶏舎名", key="house_name")
            tank_number = st.text_input("タンク番号", key="tank_number")
            floor_area = st.number_input("飼養面積（坪）", min_value=0.0, step=0.5, key="floor_area")
            tank_capacity = st.number_input("タンク容量（kg）", min_value=0.0, step=100.0, key="tank_capacity")

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
                            "is_active": True
                        })
                        st.success(f"✅ 鶏舎「{house_name}」を登録しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

        with col2:
            st.markdown("#### 登録済み鶏舎")
            houses = fetch("houses", "house_id")
            if houses:
                df = pd.DataFrame(houses)
                # 農場名を結合
                farm_map = {f["farm_id"]: f["farm_name"] for f in farms}
                df["farm_name"] = df["farm_id"].map(farm_map)
                df = df[["house_id", "farm_name", "house_name", "tank_number",
                          "floor_area_tsubo", "tank_capacity", "is_active"]]
                df.columns = ["ID", "農場", "鶏舎名", "タンクNo",
                              "面積(坪)", "タンク容量(kg)", "稼働中"]
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("まだ鶏舎が登録されていません")

# ==========================================================
# タブ3: 担当者マスタ
# ==========================================================
with tab3:
    st.subheader("担当者マスタ")

    farms = fetch("farms", "farm_id")
    farm_options = {f["farm_name"]: f["farm_id"] for f in farms} if farms else {}

    col1, col2 = st.columns([1, 1])

    with col1:
        st.markdown("#### 新規登録")
        worker_name = st.text_input("担当者名", key="worker_name")
        worker_farm = st.selectbox(
            "主所属農場（任意）",
            ["未設定"] + list(farm_options.keys()),
            key="worker_farm"
        )

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

    with col2:
        st.markdown("#### 登録済み担当者")
        workers = fetch("workers", "worker_id")
        if workers:
            df = pd.DataFrame(workers)
            farm_map = {f["farm_id"]: f["farm_name"] for f in farms} if farms else {}
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
    st.subheader("飼料銘柄マスタ")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.markdown("#### 新規登録")
        brand_name = st.text_input("銘柄名", key="brand_name")
        manufacturer = st.text_input("メーカー（任意）", key="manufacturer")
        feed_type = st.selectbox(
            "飼料種別",
            ["スターター", "グロワー", "フィニッシャー", "その他"],
            key="feed_type"
        )

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

    with col2:
        st.markdown("#### 登録済み飼料銘柄")
        brands = fetch("feed_brands", "feed_brand_id")
        if brands:
            df = pd.DataFrame(brands)[["feed_brand_id", "brand_name", "manufacturer",
                                        "feed_type", "is_active"]]
            df.columns = ["ID", "銘柄名", "メーカー", "種別", "稼働中"]
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("まだ飼料銘柄が登録されていません")
