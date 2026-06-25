"""
ブロイラー飼養管理システム - ロット・鶏舎割当管理
ロット基本情報と鶏舎割当を一体化した登録画面
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
farms        = fetch("farms",  "farm_id")
houses       = fetch("houses", "house_id")
lots         = fetch("lots",   "lot_id")
flock_houses = fetch("flock_houses", "flock_house_id")

farm_map  = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map = {h["house_id"]: h["house_name"] for h in houses}
lot_map   = {
    l["lot_id"]: f"{farm_map.get(l['farm_id'],'')} - {l['lot_number']}"
    for l in lots
}

STATUS_OPTIONS = ["育成中", "出荷完了", "中止"]

st.title("⚙️ ロット・鶏舎割当管理")

tab1, tab2, tab3 = st.tabs(["➕ 新規登録", "✏️ 編集・削除", "📋 一覧"])

# ==========================================================
# タブ1: 新規登録（ロット＋鶏舎を一体で登録）
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
        # ---- ロット基本情報 ----
        st.markdown("#### 📋 ロット情報")
        sel_farm    = st.selectbox("農場", list(farm_opts.keys()), key="new_farm")
        lot_number  = st.text_input("ロット番号", placeholder="例: 2026-01", key="new_lot_no")
        planned_age = st.number_input("出荷日齢（計画）", min_value=30, max_value=70, value=46, step=1, key="new_age")
        spare_pct   = st.number_input("スペア率（%）", min_value=0.0, max_value=10.0, value=3.0, step=0.5, key="new_spare_pct")
        remarks     = st.text_area("摘要", key="new_remarks")

        st.divider()

        # ---- 鶏舎情報 ----
        st.markdown("#### 🏗️ 鶏舎情報")
        lot_farm_id     = farm_opts[sel_farm]
        filtered_houses = [h for h in houses if h["farm_id"] == lot_farm_id]

        if not filtered_houses:
            st.warning("この農場に鶏舎が登録されていません")
        else:
            sel_house_id = st.selectbox("鶏舎番号",
                [h["house_id"] for h in filtered_houses],
                format_func=lambda x: house_map[x],
                key="new_house")

            # タンク番号・面積・容量の自動表示
            sel_house = next(h for h in filtered_houses if h["house_id"] == sel_house_id)
            st.info(
                f"🗂️ タンク番号: **{sel_house.get('tank_number') or '未登録'}**　｜　"
                f"飼養面積: **{sel_house.get('floor_area_tsubo') or '-'} 坪**　｜　"
                f"タンク容量: **{sel_house.get('tank_capacity') or '-'} kg**"
            )

            chick_in_date = st.date_input("入雛日", value=date.today(), key="new_chick_date")
            feed_date     = st.date_input("初回飼料納入日",
                value=chick_in_date - timedelta(days=1),
                max_value=chick_in_date,
                key="new_feed_date",
                help="入雛日以前の日付を設定")
            feed_qty      = st.number_input("初回飼料納入量（kg）", min_value=0.0, step=100.0, key="new_feed_qty")

            chick_in_count = st.number_input("入雛羽数（正味）", min_value=1, value=6600, step=100, key="new_chick_count")
            spare          = calc_spare(chick_in_count, spare_pct)
            st.info(
                f"🔢 スペア羽数（自動計算）: **{spare:,} 羽**"
                f"（スペア率 {spare_pct}% × {chick_in_count:,} 羽）\n\n"
                f"合計入雛数: **{chick_in_count + spare:,} 羽**"
            )

            st.divider()
            if st.button("登録", key="add_all", type="primary"):
                if not lot_number:
                    st.error("ロット番号を入力してください")
                elif feed_date > chick_in_date:
                    st.error("初回飼料納入日は入雛日以前に設定してください")
                else:
                    try:
                        # 同一農場・ロット番号が既存かチェック
                        existing_lot = next(
                            (l for l in lots
                             if l["farm_id"] == lot_farm_id and l["lot_number"] == lot_number),
                            None
                        )

                        if existing_lot:
                            # ロットが既存なら鶏舎割当だけ追加
                            lot_id = existing_lot["lot_id"]
                            st.warning(f"ロット「{lot_number}」は既に登録済みです。鶏舎割当を追加します。")
                        else:
                            # ロットを新規作成
                            res = insert("lots", {
                                "farm_id":                   lot_farm_id,
                                "lot_number":                lot_number,
                                "lot_start_date":            str(chick_in_date),
                                "planned_shipment_age_days": planned_age,
                                "spare_pct":                 spare_pct,
                                "status":                    "育成中",
                                "remarks":                   remarks or None
                            })
                            lot_id = res.data[0]["lot_id"]

                        # 鶏舎割当を登録
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
                            f"✅ ロット「{lot_number}」- {house_map[sel_house_id]} を登録しました\n\n"
                            f"スペア: {spare:,}羽 / 合計: {chick_in_count + spare:,}羽"
                        )
                        st.rerun()

                    except Exception as e:
                        st.error(f"登録エラー: {e}")

    # 右カラム：登録済み鶏舎割当の確認
    with col2:
        st.markdown("#### 📋 登録済み鶏舎割当（直近）")
        if flock_houses:
            df = pd.DataFrame(flock_houses)
            df["lot_label"]  = df["lot_id"].map(lot_map)
            df["house_name"] = df["house_id"].map(house_map)
            tank_map         = {h["house_id"]: h.get("tank_number", "") for h in houses}
            df["tank_no"]    = df["house_id"].map(tank_map)
            df["total"]      = df["chick_in_count"] + df["spare_count"].fillna(0).astype(int)
            df = df[["lot_label", "house_name", "tank_no", "chick_in_date",
                      "initial_feed_delivery_date", "chick_in_count", "spare_count", "total", "status"]]
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

    edit_target = st.radio("対象", ["ロット", "鶏舎割当"], horizontal=True, key="edit_target")
    mode        = st.radio("操作", ["編集", "削除"], horizontal=True, key="edit_mode")

    # ---- ロット編集・削除 ----
    if edit_target == "ロット":
        if not lots:
            st.info("登録済みのロットがありません")
        else:
            col1, col2 = st.columns([1, 1])
            with col1:
                sel_id = st.selectbox("対象ロット",
                    [l["lot_id"] for l in lots],
                    format_func=lambda x: lot_map[x],
                    key="lot_sel")
                t = next(l for l in lots if l["lot_id"] == sel_id)

                if mode == "編集":
                    new_farm    = st.selectbox("農場", list(farm_opts.keys()),
                        index=list(farm_opts.values()).index(t["farm_id"]) if t["farm_id"] in farm_opts.values() else 0,
                        key="lot_e_farm")
                    new_no      = st.text_input("ロット番号", value=t["lot_number"], key="lot_e_no")
                    new_date    = st.date_input("入雛日",
                        value=date.fromisoformat(t["lot_start_date"]) if t["lot_start_date"] else date.today(),
                        key="lot_e_date")
                    new_age     = st.number_input("出荷日齢（計画）", min_value=30, max_value=70,
                        value=int(t["planned_shipment_age_days"] or 46), step=1, key="lot_e_age")
                    new_spare   = st.number_input("スペア率（%）", min_value=0.0, max_value=10.0,
                        value=float(t["spare_pct"] or 3.0), step=0.5, key="lot_e_spare")
                    new_status  = st.selectbox("ステータス", STATUS_OPTIONS,
                        index=STATUS_OPTIONS.index(t["status"]) if t["status"] in STATUS_OPTIONS else 0,
                        key="lot_e_status")
                    new_remarks = st.text_area("摘要", value=t["remarks"] or "", key="lot_e_remarks")

                    if st.button("更新", key="upd_lot"):
                        try:
                            update("lots", "lot_id", sel_id, {
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

                elif mode == "削除":
                    st.warning("⚠️ 削除すると関連する鶏舎割当・日次記録も削除されます")
                    if st.button("削除", key="del_lot", type="primary"):
                        try:
                            delete("lots", "lot_id", sel_id)
                            st.success("✅ 削除しました")
                            st.rerun()
                        except Exception as e:
                            st.error(f"削除エラー: {e}")

    # ---- 鶏舎割当 編集・削除 ----
    elif edit_target == "鶏舎割当":
        if not flock_houses:
            st.info("登録済みの鶏舎割当がありません")
        else:
            fh_map = {
                fh["flock_house_id"]: f"{lot_map.get(fh['lot_id'],'')} / {house_map.get(fh['house_id'],'')}"
                for fh in flock_houses
            }
            col1, col2 = st.columns([1, 1])
            with col1:
                sel_fh_id = st.selectbox("対象鶏舎割当",
                    [fh["flock_house_id"] for fh in flock_houses],
                    format_func=lambda x: fh_map[x],
                    key="fh_sel")
                t = next(fh for fh in flock_houses if fh["flock_house_id"] == sel_fh_id)

                # タンク番号表示
                fh_house = next((h for h in houses if h["house_id"] == t["house_id"]), None)
                if fh_house:
                    st.info(f"🗂️ タンク番号: **{fh_house.get('tank_number') or '未登録'}**")

                t_lot         = next((l for l in lots if l["lot_id"] == t["lot_id"]), None)
                spare_pct_val = float(t_lot["spare_pct"] or 3.0) if t_lot else 3.0

                if mode == "編集":
                    new_cd   = st.date_input("入雛日",
                        value=date.fromisoformat(t["chick_in_date"]) if t["chick_in_date"] else date.today(),
                        key="fh_e_cd")
                    new_fd   = st.date_input("初回飼料納入日",
                        value=date.fromisoformat(t["initial_feed_delivery_date"]) if t["initial_feed_delivery_date"] else new_cd - timedelta(days=1),
                        max_value=new_cd, key="fh_e_fd")
                    new_fq   = st.number_input("初回飼料納入量（kg）",
                        min_value=0.0, value=float(t["initial_feed_delivery_qty"] or 0), step=100.0, key="fh_e_fq")
                    new_cc   = st.number_input("入雛羽数（正味）",
                        min_value=1, value=int(t["chick_in_count"]), step=100, key="fh_e_cc")
                    new_sp   = calc_spare(new_cc, spare_pct_val)
                    st.info(f"🔢 スペア羽数（自動計算）: **{new_sp:,} 羽**")
                    new_st   = st.selectbox("ステータス", STATUS_OPTIONS,
                        index=STATUS_OPTIONS.index(t["status"]) if t["status"] in STATUS_OPTIONS else 0,
                        key="fh_e_st")

                    if st.button("更新", key="upd_fh"):
                        try:
                            update("flock_houses", "flock_house_id", sel_fh_id, {
                                "chick_in_date":              str(new_cd),
                                "initial_feed_delivery_date": str(new_fd),
                                "initial_feed_delivery_qty":  new_fq or None,
                                "chick_in_count":             new_cc,
                                "spare_count":                new_sp,
                                "status":                     new_st
                            })
                            st.success("✅ 更新しました")
                            st.rerun()
                        except Exception as e:
                            st.error(f"更新エラー: {e}")

                elif mode == "削除":
                    st.warning("⚠️ 削除すると関連する日次記録も削除されます")
                    if st.button("削除", key="del_fh", type="primary"):
                        try:
                            delete("flock_houses", "flock_house_id", sel_fh_id)
                            st.success("✅ 削除しました")
                            st.rerun()
                        except Exception as e:
                            st.error(f"削除エラー: {e}")

# ==========================================================
# タブ3: 一覧
# ==========================================================
with tab3:
    st.subheader("📋 一覧")

    st.markdown("#### ロット一覧")
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

    st.divider()
    st.markdown("#### 鶏舎割当一覧")
    fh_latest = fetch("flock_houses", "flock_house_id")
    if fh_latest:
        df = pd.DataFrame(fh_latest)
        df["lot_label"]  = df["lot_id"].map(lot_map)
        df["house_name"] = df["house_id"].map(house_map)
        tank_map         = {h["house_id"]: h.get("tank_number", "") for h in houses}
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
