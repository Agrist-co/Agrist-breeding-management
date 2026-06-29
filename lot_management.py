"""
ブロイラー飼養管理システム - ロット番号・鶏舎割当管理
farms → lot_numbers（マスタ） → flock_houses → daily_records
"""

import streamlit as st
import pandas as pd
import math
from datetime import date, timedelta
from supabase import create_client, Client

st.set_page_config(page_title="ロット・鶏舎割当管理", layout="wide")

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
lot_numbers  = fetch("lot_numbers",  "lot_number_id")
flock_houses = fetch("flock_houses", "flock_house_id")

farm_map  = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map = {h["house_id"]: h["house_name"] for h in houses}
tank_map  = {h["house_id"]: h.get("tank_number", "未登録") for h in houses}
area_map  = {h["house_id"]: h.get("floor_area_tsubo", "-") for h in houses}
cap_map   = {h["house_id"]: h.get("tank_capacity", "-")    for h in houses}
ln_label  = {
    ln["lot_number_id"]: f"{farm_map.get(ln['farm_id'],'')} - {ln['lot_number']}"
    for ln in lot_numbers
}
fh_label  = {
    fh["flock_house_id"]:
        f"{ln_label.get(fh['lot_number_id'],'')} / {house_map.get(fh['house_id'],'')}"
    for fh in flock_houses
}

STATUS = ["育成中", "出荷完了", "中止"]

st.title("⚙️ ロット・鶏舎割当管理")

tab0, tab1, tab2, tab3 = st.tabs([
    "🗂️ ロット番号マスタ",
    "➕ 新規登録",
    "✏️ 編集・削除",
    "📋 一覧"
])

# ==========================================================
# タブ0: ロット番号マスタ管理
# ==========================================================
with tab0:
    st.subheader("🗂️ ロット番号マスタ")
    st.caption("農場ごとのロット番号（01, 02, 03…）を事前登録します")

    if not farms:
        st.warning("先に農場マスタを登録してください")
    else:
        mode0 = st.radio("操作", ["新規登録", "削除"], horizontal=True, key="ln_mode")

        col1, col2 = st.columns([1, 1])
        with col1:
            if mode0 == "新規登録":
                st.markdown("#### ➕ 新規登録")
                ln_farm    = st.selectbox("農場", list(farm_opts.keys()), key="ln_farm")
                ln_numbers = st.text_input(
                    "ロット番号（複数まとめて登録する場合はカンマ区切り）",
                    placeholder="例: 01, 02, 03",
                    key="ln_numbers"
                )
                if st.button("登録", key="btn_ln_add"):
                    if not ln_numbers:
                        st.error("ロット番号を入力してください")
                    else:
                        nums = [n.strip() for n in ln_numbers.split(",") if n.strip()]
                        success, errors = [], []
                        for num in nums:
                            try:
                                insert("lot_numbers", {
                                    "farm_id":   farm_opts[ln_farm],
                                    "lot_number": num,
                                    "is_active":  True
                                })
                                success.append(num)
                            except Exception as e:
                                errors.append(f"{num}: {e}")
                        if success:
                            st.success(f"✅ 登録完了: {', '.join(success)}")
                        if errors:
                            st.error(f"エラー: {'; '.join(errors)}")
                        st.rerun()

            elif mode0 == "削除":
                st.markdown("#### 🗑️ 削除")
                if not lot_numbers:
                    st.info("登録済みのロット番号がありません")
                else:
                    del_ln_id = st.selectbox("削除するロット番号",
                        [ln["lot_number_id"] for ln in lot_numbers],
                        format_func=lambda x: ln_label[x],
                        key="ln_del_id")
                    st.warning("⚠️ 使用中のロット番号は削除できません")
                    if st.button("削除", key="btn_ln_del", type="primary"):
                        try:
                            delete("lot_numbers", "lot_number_id", del_ln_id)
                            st.success("✅ 削除しました")
                            st.rerun()
                        except Exception as e:
                            st.error(f"削除エラー: {e}")

        with col2:
            st.markdown("#### 📋 登録済みロット番号")
            ln_latest = fetch("lot_numbers", "lot_number_id")
            if ln_latest:
                df = pd.DataFrame(ln_latest)
                df["farm_name"] = df["farm_id"].map(farm_map)
                df = df[["lot_number_id", "farm_name", "lot_number", "is_active"]]
                df.columns = ["ID", "農場", "ロット番号", "稼働中"]
                st.dataframe(df, use_container_width=True, hide_index=True)
            else:
                st.info("まだロット番号が登録されていません")

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
    if not lot_numbers:
        st.warning("先にロット番号マスタを登録してください（🗂️ ロット番号マスタタブ）")
        st.stop()

    col1, col2 = st.columns([1, 1])
    with col1:

        # 農場選択
        sel_farm     = st.selectbox("農場", list(farm_opts.keys()), key="n_farm")
        lot_farm_id  = farm_opts[sel_farm]

        # ロット番号（農場で絞り込み）
        farm_lns = [ln for ln in lot_numbers if ln["farm_id"] == lot_farm_id and ln["is_active"]]
        if not farm_lns:
            st.warning("この農場のロット番号が登録されていません（🗂️ ロット番号マスタタブで登録）")
            st.stop()

        sel_ln_id = st.selectbox("ロット番号",
            [ln["lot_number_id"] for ln in farm_lns],
            format_func=lambda x: next(ln["lot_number"] for ln in farm_lns if ln["lot_number_id"] == x),
            key="n_ln")

        # 鶏舎番号（農場で絞り込み）
        filtered = [h for h in houses if h["farm_id"] == lot_farm_id]
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

        # 入雛羽数・スペア率・スペア羽数
        chick_in_count = st.number_input("入雛羽数（正味）", min_value=1, value=6600, step=100, key="n_chick_count")
        spare_pct      = st.number_input("スペア率（%）", min_value=0.0, max_value=10.0, value=3.0, step=0.5, key="n_spare_pct")
        spare          = calc_spare(chick_in_count, spare_pct)
        st.info(
            f"🔢 スペア羽数（自動計算）: **{spare:,} 羽**"
            f"（{spare_pct}% × {chick_in_count:,} 羽）\n\n"
            f"合計入雛数: **{chick_in_count + spare:,} 羽**"
        )

        # 出荷日齢（計画）
        planned_age = st.number_input("出荷日齢（計画）", min_value=30, max_value=70, value=46, step=1, key="n_age")

        # 飼料使用量
        st.markdown("**飼料使用量（kg）**")
        fq1, fq2 = st.columns(2)
        with fq1:
            starter_qty = st.number_input("前期使用量（kg）", min_value=0.0, value=0.0, step=500.0, key="n_starter_qty")
        with fq2:
            grower_qty  = st.number_input("中期使用量（kg）、0=中期なし", min_value=0.0, value=0.0, step=500.0, key="n_grower_qty")

        # 摘要
        remarks = st.text_area("摘要", key="n_remarks")

        st.divider()
        if st.button("登録", key="btn_add", type="primary"):
            if feed_date > chick_in_date:
                st.error("初回飼料納入日は入雛日以前に設定してください")
            else:
                try:
                    insert("flock_houses", {
                        "lot_number_id":              sel_ln_id,
                        "house_id":                   sel_house_id,
                        "chick_in_date":              str(chick_in_date),
                        "chick_in_count":             chick_in_count,
                        "spare_pct":                  spare_pct,
                        "spare_count":                spare,
                        "planned_shipment_age_days":  planned_age,
                        "initial_feed_delivery_date": str(feed_date),
                        "initial_feed_delivery_qty":  feed_qty or None,
                        "starter_qty_kg":             starter_qty or None,
                        "grower_qty_kg":              grower_qty or None,
                        "status":                     "育成中",
                        "remarks":                    remarks or None
                    })
                    sel_ln = next(ln for ln in lot_numbers if ln["lot_number_id"] == sel_ln_id)
                    st.success(
                        f"✅ 登録完了\n\n"
                        f"農場: {sel_farm}　ロット: {sel_ln['lot_number']}　鶏舎: {house_map[sel_house_id]}\n"
                        f"入雛: {chick_in_date}　スペア: {spare:,}羽　合計: {chick_in_count+spare:,}羽"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"登録エラー: {e}")

    with col2:
        st.markdown("#### 📋 登録済み一覧（直近）")
        if flock_houses:
            rows = []
            for fh in flock_houses:
                ln = next((l for l in lot_numbers if l["lot_number_id"] == fh["lot_number_id"]), {})
                rows.append({
                    "農場":       farm_map.get(ln.get("farm_id"), ""),
                    "ロット番号": ln.get("lot_number", ""),
                    "鶏舎":       house_map.get(fh["house_id"], ""),
                    "タンクNo":   tank_map.get(fh["house_id"], ""),
                    "入雛日":     fh.get("chick_in_date", ""),
                    "入雛羽数":   fh.get("chick_in_count", ""),
                    "スペア羽数": fh.get("spare_count", ""),
                    "合計羽数":   (fh.get("chick_in_count") or 0) + (fh.get("spare_count") or 0),
                    "状態":       fh.get("status", ""),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
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
        mode2 = st.radio("操作", ["編集", "削除"], horizontal=True, key="edit_mode")
        col1, col2 = st.columns([1, 1])
        with col1:
            sel_fh_id = st.selectbox("対象レコード",
                [fh["flock_house_id"] for fh in flock_houses],
                format_func=lambda x: fh_label[x],
                key="fh_sel")
            fh  = next(f for f in flock_houses if f["flock_house_id"] == sel_fh_id)
            fh_ln = next((ln for ln in lot_numbers if ln["lot_number_id"] == fh["lot_number_id"]), None)

            if mode2 == "編集":
                # ロット番号変更
                fh_farm_id   = fh_ln["farm_id"] if fh_ln else None
                farm_lns_e   = [ln for ln in lot_numbers if ln["farm_id"] == fh_farm_id and ln["is_active"]] if fh_farm_id else lot_numbers
                e_ln_id = st.selectbox("ロット番号",
                    [ln["lot_number_id"] for ln in farm_lns_e],
                    index=next((i for i, ln in enumerate(farm_lns_e) if ln["lot_number_id"] == fh["lot_number_id"]), 0),
                    format_func=lambda x: next((ln["lot_number"] for ln in farm_lns_e if ln["lot_number_id"] == x), ""),
                    key="e_ln")

                # 鶏舎番号変更（農場で絞り込み）
                e_filtered = [h for h in houses if h["farm_id"] == fh_farm_id] if fh_farm_id else houses
                e_house_id = st.selectbox("鶏舎番号",
                    [h["house_id"] for h in e_filtered],
                    index=next((i for i, h in enumerate(e_filtered) if h["house_id"] == fh["house_id"]), 0),
                    format_func=lambda x: house_map[x],
                    key="e_house")

                # タンク番号自動表示
                st.info(
                    f"🗂️ タンク番号: **{tank_map.get(e_house_id,'未登録')}**　｜　"
                    f"飼養面積: **{area_map.get(e_house_id,'-')} 坪**　｜　"
                    f"タンク容量: **{cap_map.get(e_house_id,'-')} kg**"
                )

                e_cd  = st.date_input("入雛日",
                    value=date.fromisoformat(fh["chick_in_date"]) if fh["chick_in_date"] else date.today(),
                    key="e_cd")
                e_fd  = st.date_input("初回飼料納入日",
                    value=date.fromisoformat(fh["initial_feed_delivery_date"]) if fh["initial_feed_delivery_date"] else e_cd - timedelta(days=1),
                    max_value=e_cd, key="e_fd")
                e_fq  = st.number_input("初回飼料納入量（kg）",
                    min_value=0.0, value=float(fh["initial_feed_delivery_qty"] or 0), step=100.0, key="e_fq")
                eq1, eq2 = st.columns(2)
                with eq1:
                    e_starter = eq1.number_input("前期使用量（kg）",
                        min_value=0.0, value=float(fh.get("starter_qty_kg") or 0), step=500.0, key="e_starter")
                with eq2:
                    e_grower  = eq2.number_input("中期使用量（kg）",
                        min_value=0.0, value=float(fh.get("grower_qty_kg") or 0), step=500.0, key="e_grower")
                e_cc  = st.number_input("入雛羽数（正味）",
                    min_value=1, value=int(fh["chick_in_count"]), step=100, key="e_cc")
                e_sp_pct = st.number_input("スペア率（%）",
                    min_value=0.0, max_value=10.0, value=float(fh["spare_pct"] or 3.0), step=0.5, key="e_sp_pct")
                e_sp  = calc_spare(e_cc, e_sp_pct)
                st.info(f"🔢 スペア羽数（自動計算）: **{e_sp:,} 羽**　合計: **{e_cc+e_sp:,} 羽**")
                e_age = st.number_input("出荷日齢（計画）", min_value=30, max_value=70,
                    value=int(fh["planned_shipment_age_days"] or 46), step=1, key="e_age")
                e_st  = st.selectbox("ステータス", STATUS,
                    index=STATUS.index(fh["status"]) if fh["status"] in STATUS else 0,
                    key="e_st")
                e_rem = st.text_area("摘要", value=fh["remarks"] or "", key="e_rem")

                if st.button("更新", key="btn_update", type="primary"):
                    try:
                        update("flock_houses", "flock_house_id", sel_fh_id, {
                            "lot_number_id":              e_ln_id,
                            "house_id":                   e_house_id,
                            "chick_in_date":              str(e_cd),
                            "initial_feed_delivery_date": str(e_fd),
                            "initial_feed_delivery_qty":  e_fq or None,
                            "starter_qty_kg":             e_starter or None,
                            "grower_qty_kg":              e_grower or None,
                            "chick_in_count":             e_cc,
                            "spare_pct":                  e_sp_pct,
                            "spare_count":                e_sp,
                            "planned_shipment_age_days":  e_age,
                            "status":                     e_st,
                            "remarks":                    e_rem or None
                        })
                        st.success("✅ 更新しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"更新エラー: {e}")

            elif mode2 == "削除":
                st.warning("⚠️ 削除すると関連する日次記録も削除されます")
                if st.button("削除", key="btn_delete", type="primary"):
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

    fh_latest = fetch("flock_houses", "flock_house_id")
    ln_latest = fetch("lot_numbers",  "lot_number_id")

    if fh_latest:
        rows = []
        for fh in fh_latest:
            ln = next((l for l in ln_latest if l["lot_number_id"] == fh["lot_number_id"]), {})
            rows.append({
                "農場":           farm_map.get(ln.get("farm_id"), ""),
                "ロット番号":     ln.get("lot_number", ""),
                "鶏舎":           house_map.get(fh["house_id"], ""),
                "タンクNo":       tank_map.get(fh["house_id"], ""),
                "入雛日":         fh.get("chick_in_date", ""),
                "初回納入日":     fh.get("initial_feed_delivery_date", ""),
                "初回納入量(kg)": fh.get("initial_feed_delivery_qty", ""),
                "前期(kg)":       fh.get("starter_qty_kg", ""),
                "中期(kg)":       fh.get("grower_qty_kg", ""),
                "入雛羽数":       fh.get("chick_in_count", ""),
                "スペア率(%)":    fh.get("spare_pct", ""),
                "スペア羽数":     fh.get("spare_count", ""),
                "合計羽数":       (fh.get("chick_in_count") or 0) + (fh.get("spare_count") or 0),
                "出荷日齢(計画)": fh.get("planned_shipment_age_days", ""),
                "状態":           fh.get("status", ""),
                "摘要":           fh.get("remarks", ""),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("まだ登録がありません")
