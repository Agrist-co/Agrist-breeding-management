"""
ブロイラー飼養管理システム - ロット・鶏舎割当管理
1フォームでロット＋鶏舎を同時に1レコードとして登録
"""

import streamlit as st
import pandas as pd
import math
from datetime import date, timedelta
from supabase import create_client, Client

st.set_page_config(page_title="ロット・鶏舎割当管理", layout="wide")

@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

supabase = get_supabase()

def fetch(table, order=None):
    q = supabase.table(table).select("*")
    if order:
        q = q.order(order)
    return q.execute().data

def insert(table, data):
    return supabase.table(table).insert(data).execute()

def update(table, id_col, id_val, data):
    return supabase.table(table).update(data).eq(id_col, id_val).execute()

def delete(table, id_col, id_val):
    return supabase.table(table).delete().eq(id_col, id_val).execute()

def calc_spare(count, pct):
    return math.ceil(count * pct / 100)

# ----------------------------------------------------------
# データ取得
# ----------------------------------------------------------
farms        = fetch("farms",        "farm_id")
houses       = fetch("houses",       "house_id")
lots         = fetch("lots",         "lot_id")
flock_houses = fetch("flock_houses", "flock_house_id")

farm_map  = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map = {h["house_id"]: h["house_name"] for h in houses}
tank_map  = {h["house_id"]: h.get("tank_number", "未登録") for h in houses}
area_map  = {h["house_id"]: h.get("floor_area_tsubo", "-") for h in houses}
cap_map   = {h["house_id"]: h.get("tank_capacity", "-")    for h in houses}
lot_label = {
    l["lot_id"]: f"{farm_map.get(l['farm_id'],'')} - {l['lot_number']}"
    for l in lots
}
fh_label  = {
    fh["flock_house_id"]:
        f"{lot_label.get(fh['lot_id'],'')} / {house_map.get(fh['house_id'],'')}"
    for fh in flock_houses
}

STATUS = ["育成中", "出荷完了", "中止"]

st.title("⚙️ ロット・鶏舎割当管理")

tab1, tab2, tab3 = st.tabs(["➕ 新規登録", "✏️ 編集・削除", "📋 一覧"])

# ==========================================================
# タブ1: 新規登録（完全1フォーム）
# ==========================================================
with tab1:
    st.subheader("➕ 新規登録")

    if not farms:
        st.warning("先に農場マスタを登録してください")
        st.stop()
    if not houses:
        st.warning("先に鶏舎マスタを登録してください")
        st.stop()

    col1, col2 = st.columns([1, 1])
    with col1:

        # 農場
        sel_farm    = st.selectbox("農場", list(farm_opts.keys()), key="n_farm")
        lot_farm_id = farm_opts[sel_farm]

        # ロット番号
        lot_number  = st.text_input("ロット番号", placeholder="例: 2026-01", key="n_lot_no")

        # 鶏舎番号（農場で絞り込み）
        filtered    = [h for h in houses if h["farm_id"] == lot_farm_id]
        if not filtered:
            st.warning("この農場に鶏舎が登録されていません")
            st.stop()

        sel_house_id = st.selectbox("鶏舎番号",
            [h["house_id"] for h in filtered],
            format_func=lambda x: house_map[x],
            key="n_house")

        # タンク番号・面積・容量を自動表示
        st.info(
            f"🗂️ タンク番号: **{tank_map.get(sel_house_id,'未登録')}**　｜　"
            f"飼養面積: **{area_map.get(sel_house_id,'-')} 坪**　｜　"
            f"タンク容量: **{cap_map.get(sel_house_id,'-')} kg**"
        )

        # 入雛日
        chick_in_date = st.date_input("入雛日", value=date.today(), key="n_chick_date")

        # 初回飼料納入日
        feed_date = st.date_input("初回飼料納入日",
            value=chick_in_date - timedelta(days=1),
            max_value=chick_in_date,
            key="n_feed_date",
            help="入雛日以前の日付を設定")

        # 初回飼料納入量
        feed_qty = st.number_input("初回飼料納入量（kg）", min_value=0.0, step=100.0, key="n_feed_qty")

        # 入雛羽数
        chick_in_count = st.number_input("入雛羽数（正味）", min_value=1, value=6600, step=100, key="n_chick_count")

        # スペア率・スペア羽数自動計算
        spare_pct = st.number_input("スペア率（%）", min_value=0.0, max_value=10.0, value=3.0, step=0.5, key="n_spare_pct")
        spare     = calc_spare(chick_in_count, spare_pct)
        st.info(
            f"🔢 スペア羽数（自動計算）: **{spare:,} 羽**"
            f"（{spare_pct}% × {chick_in_count:,} 羽）\n\n"
            f"合計入雛数: **{chick_in_count + spare:,} 羽**"
        )

        # 出荷日齢（計画）
        planned_age = st.number_input("出荷日齢（計画）", min_value=30, max_value=70, value=46, step=1, key="n_age")

        # 摘要
        remarks = st.text_area("摘要", key="n_remarks")

        st.divider()
        if st.button("登録", key="btn_add", type="primary"):
            if not lot_number:
                st.error("ロット番号を入力してください")
            elif feed_date > chick_in_date:
                st.error("初回飼料納入日は入雛日以前に設定してください")
            else:
                try:
                    # 同農場・同ロット番号が既存か確認
                    existing_lot = next(
                        (l for l in lots
                         if l["farm_id"] == lot_farm_id and l["lot_number"] == lot_number),
                        None
                    )
                    if existing_lot:
                        lot_id = existing_lot["lot_id"]
                        st.warning(f"ロット「{lot_number}」は既に登録済みです。鶏舎を追加します。")
                    else:
                        res    = insert("lots", {
                            "farm_id":                   lot_farm_id,
                            "lot_number":                lot_number,
                            "lot_start_date":            str(chick_in_date),
                            "planned_shipment_age_days": planned_age,
                            "spare_pct":                 spare_pct,
                            "status":                    "育成中",
                            "remarks":                   remarks or None
                        })
                        lot_id = res.data[0]["lot_id"]

                    insert("flock_houses", {
                        "lot_id":                     lot_id,
                        "house_id":                   sel_house_id,
                        "initial_feed_delivery_date": str(feed_date),
                        "initial_feed_delivery_qty":  feed_qty or None,
                        "chick_in_date":              str(chick_in_date),
                        "chick_in_count":             chick_in_count,
                        "spare_count":                spare,
                        "status":                     "育成中"
                    })
                    st.success(
                        f"✅ 登録完了\n\n"
                        f"農場: {sel_farm}　ロット: {lot_number}　鶏舎: {house_map[sel_house_id]}\n"
                        f"入雛: {chick_in_date}　スペア: {spare:,}羽　合計: {chick_in_count+spare:,}羽"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"登録エラー: {e}")

    with col2:
        st.markdown("#### 📋 登録済み一覧（直近）")
        if flock_houses:
            df = pd.DataFrame(flock_houses)
            df["lot_label"]  = df["lot_id"].map(lot_label)
            df["house_name"] = df["house_id"].map(house_map)
            df["tank_no"]    = df["house_id"].map(tank_map)
            df["total"]      = df["chick_in_count"] + df["spare_count"].fillna(0).astype(int)
            df = df[["lot_label", "house_name", "tank_no", "chick_in_date",
                      "initial_feed_delivery_date", "chick_in_count",
                      "spare_count", "total", "status"]]
            df.columns = ["ロット", "鶏舎", "タンクNo", "入雛日",
                          "初回納入日", "入雛羽数", "スペア羽数", "合計羽数", "状態"]
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("まだ登録がありません")

# ==========================================================
# タブ2: 編集・削除
# ==========================================================
with tab2:
    st.subheader("✏️ 編集・削除")

    if not flock_houses:
        st.info("登録済みのデータがありません")
    else:
        mode = st.radio("操作", ["編集", "削除"], horizontal=True, key="edit_mode")

        col1, col2 = st.columns([1, 1])
        with col1:
            sel_fh_id = st.selectbox("対象レコード",
                [fh["flock_house_id"] for fh in flock_houses],
                format_func=lambda x: fh_label[x],
                key="fh_sel")
            fh  = next(f for f in flock_houses if f["flock_house_id"] == sel_fh_id)
            lot = next((l for l in lots if l["lot_id"] == fh["lot_id"]), None)
            spare_pct_val = float(lot["spare_pct"] or 3.0) if lot else 3.0

            if mode == "編集":
                # ロット情報
                st.markdown("##### 📋 ロット情報")
                e_farm   = st.selectbox("農場", list(farm_opts.keys()),
                    index=list(farm_opts.values()).index(lot["farm_id"]) if lot and lot["farm_id"] in farm_opts.values() else 0,
                    key="e_farm")
                e_lot_no = st.text_input("ロット番号", value=lot["lot_number"] if lot else "", key="e_lot_no")
                e_age    = st.number_input("出荷日齢（計画）", min_value=30, max_value=70,
                    value=int(lot["planned_shipment_age_days"] or 46) if lot else 46, step=1, key="e_age")
                e_spare_pct = st.number_input("スペア率（%）", min_value=0.0, max_value=10.0,
                    value=spare_pct_val, step=0.5, key="e_spare_pct")
                e_status_lot = st.selectbox("ロットステータス", STATUS,
                    index=STATUS.index(lot["status"]) if lot and lot["status"] in STATUS else 0,
                    key="e_status_lot")
                e_remarks = st.text_area("摘要", value=lot["remarks"] or "" if lot else "", key="e_remarks")

                st.markdown("##### 🏗️ 鶏舎情報")
                # タンク番号表示
                st.info(f"🗂️ タンク番号: **{tank_map.get(fh['house_id'],'未登録')}**")
                e_chick_date = st.date_input("入雛日",
                    value=date.fromisoformat(fh["chick_in_date"]) if fh["chick_in_date"] else date.today(),
                    key="e_chick_date")
                e_feed_date  = st.date_input("初回飼料納入日",
                    value=date.fromisoformat(fh["initial_feed_delivery_date"]) if fh["initial_feed_delivery_date"] else e_chick_date - timedelta(days=1),
                    max_value=e_chick_date, key="e_feed_date")
                e_feed_qty   = st.number_input("初回飼料納入量（kg）",
                    min_value=0.0, value=float(fh["initial_feed_delivery_qty"] or 0), step=100.0, key="e_feed_qty")
                e_count      = st.number_input("入雛羽数（正味）",
                    min_value=1, value=int(fh["chick_in_count"]), step=100, key="e_count")
                e_spare      = calc_spare(e_count, e_spare_pct)
                st.info(f"🔢 スペア羽数（自動計算）: **{e_spare:,} 羽**　合計: **{e_count+e_spare:,} 羽**")
                e_status_fh  = st.selectbox("鶏舎ステータス", STATUS,
                    index=STATUS.index(fh["status"]) if fh["status"] in STATUS else 0,
                    key="e_status_fh")

                if st.button("更新", key="btn_update", type="primary"):
                    try:
                        if lot:
                            update("lots", "lot_id", lot["lot_id"], {
                                "farm_id":                   farm_opts[e_farm],
                                "lot_number":                e_lot_no,
                                "lot_start_date":            str(e_chick_date),
                                "planned_shipment_age_days": e_age,
                                "spare_pct":                 e_spare_pct,
                                "status":                    e_status_lot,
                                "remarks":                   e_remarks or None
                            })
                        update("flock_houses", "flock_house_id", sel_fh_id, {
                            "chick_in_date":              str(e_chick_date),
                            "initial_feed_delivery_date": str(e_feed_date),
                            "initial_feed_delivery_qty":  e_feed_qty or None,
                            "chick_in_count":             e_count,
                            "spare_count":                e_spare,
                            "status":                     e_status_fh
                        })
                        st.success("✅ 更新しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"更新エラー: {e}")

            elif mode == "削除":
                st.warning("⚠️ このレコードの日次記録も削除されます")
                del_lot = st.checkbox("ロットごと削除する（関連する全鶏舎割当も削除）", key="del_lot_chk")
                if st.button("削除", key="btn_delete", type="primary"):
                    try:
                        if del_lot and lot:
                            delete("lots", "lot_id", lot["lot_id"])
                            st.success("✅ ロットごと削除しました")
                        else:
                            delete("flock_houses", "flock_house_id", sel_fh_id)
                            st.success("✅ この鶏舎割当を削除しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"削除エラー: {e}")

# ==========================================================
# タブ3: 一覧
# ==========================================================
with tab3:
    st.subheader("📋 一覧")

    fh_latest = fetch("flock_houses", "flock_house_id")
    lots_latest = fetch("lots", "lot_id")

    if fh_latest:
        rows = []
        for fh in fh_latest:
            lot = next((l for l in lots_latest if l["lot_id"] == fh["lot_id"]), {})
            rows.append({
                "農場":           farm_map.get(lot.get("farm_id"), ""),
                "ロット番号":     lot.get("lot_number", ""),
                "鶏舎":           house_map.get(fh["house_id"], ""),
                "タンクNo":       tank_map.get(fh["house_id"], ""),
                "入雛日":         fh.get("chick_in_date", ""),
                "初回納入日":     fh.get("initial_feed_delivery_date", ""),
                "初回納入量(kg)": fh.get("initial_feed_delivery_qty", ""),
                "入雛羽数":       fh.get("chick_in_count", ""),
                "スペア羽数":     fh.get("spare_count", ""),
                "合計羽数":       (fh.get("chick_in_count") or 0) + (fh.get("spare_count") or 0),
                "出荷日齢(計画)": lot.get("planned_shipment_age_days", ""),
                "スペア率(%)":    lot.get("spare_pct", ""),
                "状態":           fh.get("status", ""),
                "摘要":           lot.get("remarks", ""),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("まだ登録がありません")
