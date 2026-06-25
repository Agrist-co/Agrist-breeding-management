"""
ブロイラー飼養管理システム - ロット・鶏舎割当管理
farms → lots → flock_houses の登録・編集・削除
"""

import streamlit as st
import pandas as pd
import math
from datetime import date, timedelta
from supabase import create_client, Client

st.set_page_config(page_title="ロット・鶏舎割当管理", layout="wide")

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

def calc_spare(chick_in_count: int, spare_pct: float) -> int:
    return math.ceil(chick_in_count * spare_pct / 100)

# ----------------------------------------------------------
# データ取得
# ----------------------------------------------------------
farms   = fetch("farms",  "farm_id")
houses  = fetch("houses", "house_id")
lots    = fetch("lots",   "lot_id")

farm_map    = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts   = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map   = {h["house_id"]: h["house_name"] for h in houses}
lot_map     = {
    l["lot_id"]: f"{farm_map.get(l['farm_id'],'')} - {l['lot_number']}"
    for l in lots
}

STATUS_OPTIONS = ["育成中", "出荷完了", "中止"]

# ----------------------------------------------------------
# UI
# ----------------------------------------------------------
st.title("⚙️ ロット・鶏舎割当管理")

tab1, tab2 = st.tabs(["📋 ロット管理", "🏗️ 鶏舎割当"])

# ==========================================================
# タブ1: ロット管理
# ==========================================================
with tab1:
    st.subheader("📋 ロット管理")

    if not farms:
        st.warning("先に農場マスタを登録してください")
        st.stop()

    mode = st.radio("操作", ["新規登録", "編集", "削除"], horizontal=True, key="lot_mode")

    # ------------------------------------------------
    if mode == "新規登録":
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown("#### ➕ 新規登録")
            sel_farm    = st.selectbox("農場", list(farm_opts.keys()), key="lot_farm_new")
            lot_number  = st.text_input("ロット番号", placeholder="例: 2026-01", key="lot_no_new")
            chick_date  = st.date_input("入雛日", value=date.today(), key="lot_chick_date_new")
            planned_age = st.number_input("出荷日齢（計画）", min_value=30, max_value=70, value=46, step=1, key="lot_age_new")
            spare_pct   = st.number_input("スペア率（%）", min_value=0.0, max_value=10.0, value=3.0, step=0.5, key="lot_spare_new")
            remarks     = st.text_area("摘要", key="lot_remarks_new")

            if st.button("登録", key="add_lot"):
                if not lot_number:
                    st.error("ロット番号を入力してください")
                else:
                    try:
                        insert("lots", {
                            "farm_id":                    farm_opts[sel_farm],
                            "lot_number":                 lot_number,
                            "lot_start_date":             str(chick_date),
                            "planned_shipment_age_days":  planned_age,
                            "spare_pct":                  spare_pct,
                            "status":                     "育成中",
                            "remarks":                    remarks or None
                        })
                        st.success(f"✅ ロット「{lot_number}」を登録しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

    # ------------------------------------------------
    elif mode == "編集":
        if not lots:
            st.info("登録済みのロットがありません")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown("#### ✏️ 編集")
                edit_id = st.selectbox("編集するロット",
                    [l["lot_id"] for l in lots],
                    format_func=lambda x: lot_map[x],
                    key="lot_edit_id")
                t = next(l for l in lots if l["lot_id"] == edit_id)

                new_farm    = st.selectbox("農場", list(farm_opts.keys()),
                    index=list(farm_opts.values()).index(t["farm_id"]) if t["farm_id"] in farm_opts.values() else 0,
                    key="lot_edit_farm")
                new_no      = st.text_input("ロット番号", value=t["lot_number"], key="lot_edit_no")
                new_date    = st.date_input("入雛日",
                    value=date.fromisoformat(t["lot_start_date"]) if t["lot_start_date"] else date.today(),
                    key="lot_edit_date")
                new_age     = st.number_input("出荷日齢（計画）", min_value=30, max_value=70,
                    value=int(t["planned_shipment_age_days"] or 46), step=1, key="lot_edit_age")
                new_spare   = st.number_input("スペア率（%）", min_value=0.0, max_value=10.0,
                    value=float(t["spare_pct"] or 3.0), step=0.5, key="lot_edit_spare")
                new_status  = st.selectbox("ステータス", STATUS_OPTIONS,
                    index=STATUS_OPTIONS.index(t["status"]) if t["status"] in STATUS_OPTIONS else 0,
                    key="lot_edit_status")
                new_remarks = st.text_area("摘要", value=t["remarks"] or "", key="lot_edit_remarks")

                if st.button("更新", key="update_lot"):
                    try:
                        update("lots", "lot_id", edit_id, {
                            "farm_id":                   farm_opts[new_farm],
                            "lot_number":                new_no,
                            "lot_start_date":            str(new_date),
                            "planned_shipment_age_days": new_age,
                            "spare_pct":                 new_spare,
                            "status":                    new_status,
                            "remarks":                   new_remarks or None
                        })
                        st.success("✅ 更新しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"更新エラー: {e}")

    # ------------------------------------------------
    elif mode == "削除":
        if not lots:
            st.info("登録済みのロットがありません")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown("#### 🗑️ 削除")
                del_id = st.selectbox("削除するロット",
                    [l["lot_id"] for l in lots],
                    format_func=lambda x: lot_map[x],
                    key="lot_del_id")
                st.warning("⚠️ 削除すると関連する鶏舎割当・日次記録も削除されます")
                if st.button("削除", key="del_lot", type="primary"):
                    try:
                        delete("lots", "lot_id", del_id)
                        st.success("✅ 削除しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"削除エラー: {e}")

    # 一覧表示
    st.divider()
    st.markdown("#### 📋 登録済みロット一覧")
    lots_latest = fetch("lots", "lot_id")
    if lots_latest:
        df = pd.DataFrame(lots_latest)
        df["farm_name"] = df["farm_id"].map(farm_map)
        df = df[["lot_id", "farm_name", "lot_number", "lot_start_date",
                  "planned_shipment_age_days", "spare_pct", "status", "remarks"]]
        df.columns = ["ID", "農場", "ロット番号", "入雛日", "出荷日齢(計画)", "スペア率(%)", "状態", "摘要"]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("まだロットが登録されていません")

# ==========================================================
# タブ2: 鶏舎割当
# ==========================================================
with tab2:
    st.subheader("🏗️ 鶏舎割当")

    if not lots:
        st.warning("先にロットを登録してください")
        st.stop()
    if not houses:
        st.warning("先に鶏舎マスタを登録してください")
        st.stop()

    flock_houses = fetch("flock_houses", "flock_house_id")
    fh_map = {
        fh["flock_house_id"]: f"{lot_map.get(fh['lot_id'],'')} / {house_map.get(fh['house_id'],'')}"
        for fh in flock_houses
    }

    mode2 = st.radio("操作", ["新規登録", "編集", "削除"], horizontal=True, key="fh_mode")

    # ------------------------------------------------
    if mode2 == "新規登録":
        col1, col2 = st.columns([1, 1])
        with col1:
            st.markdown("#### ➕ 新規登録")

            # ロット選択
            sel_lot_id = st.selectbox("ロット番号",
                [l["lot_id"] for l in lots],
                format_func=lambda x: lot_map[x],
                key="fh_lot_new")
            sel_lot    = next(l for l in lots if l["lot_id"] == sel_lot_id)
            spare_pct_val = float(sel_lot["spare_pct"] or 3.0)
            lot_farm_id   = sel_lot["farm_id"]

            # 鶏舎選択（ロットの農場で絞り込み）
            filtered_houses = [h for h in houses if h["farm_id"] == lot_farm_id]
            if not filtered_houses:
                st.warning("この農場に鶏舎が登録されていません")
                st.stop()

            sel_house_id = st.selectbox("鶏舎番号",
                [h["house_id"] for h in filtered_houses],
                format_func=lambda x: house_map[x],
                key="fh_house_new")

            # タンク番号の自動表示
            sel_house = next(h for h in filtered_houses if h["house_id"] == sel_house_id)
            tank_no   = sel_house.get("tank_number") or "未登録"
            area      = sel_house.get("floor_area_tsubo") or "-"
            tank_cap  = sel_house.get("tank_capacity") or "-"
            st.info(f"🗂️ タンク番号: **{tank_no}**　｜　飼養面積: **{area} 坪**　｜　タンク容量: **{tank_cap} kg**")

            st.markdown("---")

            # 初回飼料納入日（入雛日より前）
            chick_in_date = st.date_input("入雛日", value=date.today(), key="fh_chick_date_new")
            feed_date     = st.date_input("初回飼料納入日",
                value=chick_in_date - timedelta(days=1),
                max_value=chick_in_date,
                key="fh_feed_date_new",
                help="初回飼料は入雛日以前に納入するため、入雛日と同日または以前の日付を設定")
            feed_qty      = st.number_input("初回飼料納入量（kg）", min_value=0.0, step=100.0, key="fh_feed_qty_new")

            st.markdown("---")

            # 入雛羽数・スペア計算
            chick_in_count = st.number_input("入雛羽数（正味）", min_value=1, value=6600, step=100, key="fh_chick_count_new")
            spare          = calc_spare(chick_in_count, spare_pct_val)
            st.info(f"🔢 スペア羽数（自動計算）: **{spare:,} 羽**（スペア率 {spare_pct_val}% × {chick_in_count:,} 羽）\n\n合計入雛数: **{chick_in_count + spare:,} 羽**")

            if st.button("登録", key="add_fh"):
                if feed_date > chick_in_date:
                    st.error("初回飼料納入日は入雛日以前に設定してください")
                else:
                    try:
                        insert("flock_houses", {
                            "lot_id":                      sel_lot_id,
                            "house_id":                    sel_house_id,
                            "initial_feed_delivery_date":  str(feed_date),
                            "initial_feed_delivery_qty":   feed_qty or None,
                            "chick_in_date":               str(chick_in_date),
                            "chick_in_count":              chick_in_count,
                            "spare_count":                 spare,
                            "status":                      "育成中"
                        })
                        st.success(f"✅ 鶏舎割当を登録しました（スペア: {spare:,}羽 / 合計: {chick_in_count+spare:,}羽）")
                        st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

    # ------------------------------------------------
    elif mode2 == "編集":
        if not flock_houses:
            st.info("登録済みの鶏舎割当がありません")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown("#### ✏️ 編集")
                edit_fh_id = st.selectbox("編集する鶏舎割当",
                    [fh["flock_house_id"] for fh in flock_houses],
                    format_func=lambda x: fh_map[x],
                    key="fh_edit_id")
                t = next(fh for fh in flock_houses if fh["flock_house_id"] == edit_fh_id)

                # タンク番号表示
                edit_house = next((h for h in houses if h["house_id"] == t["house_id"]), None)
                if edit_house:
                    st.info(f"🗂️ タンク番号: **{edit_house.get('tank_number') or '未登録'}**　｜　タンク容量: **{edit_house.get('tank_capacity') or '-'} kg**")

                target_lot    = next((l for l in lots if l["lot_id"] == t["lot_id"]), None)
                spare_pct_val = float(target_lot["spare_pct"] or 3.0) if target_lot else 3.0

                new_chick_date = st.date_input("入雛日",
                    value=date.fromisoformat(t["chick_in_date"]) if t["chick_in_date"] else date.today(),
                    key="fh_edit_chick_date")
                new_feed_date  = st.date_input("初回飼料納入日",
                    value=date.fromisoformat(t["initial_feed_delivery_date"]) if t["initial_feed_delivery_date"] else new_chick_date - timedelta(days=1),
                    max_value=new_chick_date,
                    key="fh_edit_feed_date")
                new_feed_qty   = st.number_input("初回飼料納入量（kg）",
                    min_value=0.0, value=float(t["initial_feed_delivery_qty"] or 0), step=100.0,
                    key="fh_edit_feed_qty")
                new_count      = st.number_input("入雛羽数（正味）",
                    min_value=1, value=int(t["chick_in_count"]), step=100,
                    key="fh_edit_chick_count")
                new_spare      = calc_spare(new_count, spare_pct_val)
                st.info(f"🔢 スペア羽数（自動計算）: **{new_spare:,} 羽**（{spare_pct_val}% × {new_count:,} 羽）")

                new_status = st.selectbox("ステータス", STATUS_OPTIONS,
                    index=STATUS_OPTIONS.index(t["status"]) if t["status"] in STATUS_OPTIONS else 0,
                    key="fh_edit_status")

                if st.button("更新", key="update_fh"):
                    try:
                        update("flock_houses", "flock_house_id", edit_fh_id, {
                            "chick_in_date":              str(new_chick_date),
                            "initial_feed_delivery_date": str(new_feed_date),
                            "initial_feed_delivery_qty":  new_feed_qty or None,
                            "chick_in_count":             new_count,
                            "spare_count":                new_spare,
                            "status":                     new_status
                        })
                        st.success("✅ 更新しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"更新エラー: {e}")

    # ------------------------------------------------
    elif mode2 == "削除":
        if not flock_houses:
            st.info("登録済みの鶏舎割当がありません")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                st.markdown("#### 🗑️ 削除")
                del_fh_id = st.selectbox("削除する鶏舎割当",
                    [fh["flock_house_id"] for fh in flock_houses],
                    format_func=lambda x: fh_map[x],
                    key="fh_del_id")
                st.warning("⚠️ 削除すると関連する日次記録も削除されます")
                if st.button("削除", key="del_fh", type="primary"):
                    try:
                        delete("flock_houses", "flock_house_id", del_fh_id)
                        st.success("✅ 削除しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"削除エラー: {e}")

    # 一覧表示
    st.divider()
    st.markdown("#### 📋 登録済み鶏舎割当一覧")
    flock_houses_latest = fetch("flock_houses", "flock_house_id")
    if flock_houses_latest:
        df = pd.DataFrame(flock_houses_latest)
        df["lot_label"]  = df["lot_id"].map(lot_map)
        df["house_name"] = df["house_id"].map(house_map)
        # タンク番号を追加
        tank_map = {h["house_id"]: h.get("tank_number", "") for h in houses}
        df["tank_no"]    = df["house_id"].map(tank_map)
        df["total"]      = df["chick_in_count"] + df["spare_count"].fillna(0).astype(int)
        df = df[["flock_house_id", "lot_label", "house_name", "tank_no",
                  "chick_in_date", "initial_feed_delivery_date",
                  "chick_in_count", "spare_count", "total",
                  "initial_feed_delivery_qty", "status"]]
        df.columns = ["ID", "ロット", "鶏舎", "タンクNo",
                      "入雛日", "初回納入日",
                      "入雛羽数", "スペア羽数", "合計羽数",
                      "初回納入量(kg)", "状態"]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("まだ鶏舎割当が登録されていません")
