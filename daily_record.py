"""
daily_record.py - ブロイラー飼養管理システム 日次記録
対象（農場・ロット・鶏舎・日付）が変わったら入力値を自動クリア
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from supabase import create_client, Client

st.set_page_config(page_title="日次記録", layout="wide")

# ----------------------------------------------------------
# コンパクトCSS
# ----------------------------------------------------------
st.markdown("""
<style>
h1 { font-size: 1.2rem !important; margin-bottom: 0.2rem !important; }
h2 { font-size: 1.0rem !important; margin-bottom: 0.2rem !important; }
h3 { font-size: 0.95rem !important; margin-bottom: 0.1rem !important; }
h4 { font-size: 0.88rem !important; margin-bottom: 0.1rem !important; }
.block-container {
    padding-top: 0.6rem !important;
    padding-bottom: 0.4rem !important;
    max-width: 100% !important;
}
.stNumberInput label, .stSelectbox label,
.stTextArea label, .stDateInput label, .stCheckbox label {
    font-size: 0.76rem !important;
    margin-bottom: 0 !important;
}
.stNumberInput input {
    padding-top: 0.15rem !important;
    padding-bottom: 0.15rem !important;
    font-size: 0.82rem !important;
}
.stDateInput input {
    font-size: 1.05rem !important;
    font-weight: 700 !important;
    color: #111 !important;
}
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

def upd(table, id_col, id_val, data):
    return supabase.table(table).update(data).eq(id_col, id_val).execute()

# ----------------------------------------------------------
# 入力値リセット
# ----------------------------------------------------------
INPUT_KEYS = [
    "dr_mort", "dr_cull",
    "dr_ht_max", "dr_ht_min", "dr_hum",
    "dr_ot_max", "dr_ot_min",
    "dr_fi", "dr_wi", "dr_fd", "dr_brand",
    "dr_has_weight", "dr_weight",
    "dr_log", "dr_worker"
]

def reset_inputs():
    for key in INPUT_KEYS:
        if key in st.session_state:
            del st.session_state[key]

# ----------------------------------------------------------
# マスタ取得
# ----------------------------------------------------------
farms        = fetch("farms",            "farm_id")
houses       = fetch("houses",           "house_id")
lot_numbers  = fetch("lot_numbers",      "lot_number_id")
flock_houses = fetch("flock_houses",     "flock_house_id")
feed_brands  = fetch("feed_brands",      "feed_brand_id")
workers      = fetch("workers",          "worker_id")
ross308      = fetch("ross308_standard", "day")

farm_map    = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts   = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map   = {h["house_id"]: h["house_name"] for h in houses}
ln_map      = {ln["lot_number_id"]: ln["lot_number"] for ln in lot_numbers}
brand_map   = {b["feed_brand_id"]: b["brand_name"]   for b in feed_brands}
brand_opts  = {b["brand_name"]: b["feed_brand_id"]   for b in feed_brands}
worker_map  = {w["worker_id"]: w["worker_name"]       for w in workers}
worker_opts = {w["worker_name"]: w["worker_id"]       for w in workers}
ross_dict   = {(r["sex"], r["day"]): r                for r in ross308}

def get_ross308(age_days):
    age = max(0, min(int(age_days), 56))
    return ross_dict.get(("as_hatched", age), {})

def get_remaining(fh, up_to_date):
    recs = supabase.table("daily_records") \
        .select("mortality_count,culling_count") \
        .eq("flock_house_id", fh["flock_house_id"]) \
        .lte("record_date", str(up_to_date)).execute().data
    mort  = sum(r["mortality_count"] or 0 for r in recs)
    cull  = sum(r["culling_count"]   or 0 for r in recs)
    ci    = fh["chick_in_count"] or 0
    sp    = fh["spare_count"]    or 0
    return ci + sp - mort - cull, ci - mort - cull, mort, cull

# ----------------------------------------------------------
# UI
# ----------------------------------------------------------
st.markdown("#### 📝 日次記録入力")

tab1, tab2, tab3 = st.tabs(["入力・編集", "記録一覧", "推移グラフ"])

# ==========================================================
# タブ1: 入力・編集
# ==========================================================
with tab1:

    if not farms:
        st.warning("農場マスタを登録してください")
        st.stop()

    # ---- 対象選択 ----
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_farm    = st.selectbox("農場", list(farm_opts.keys()), key="dr_farm")
        sel_farm_id = farm_opts[sel_farm]
    with c2:
        farm_lns = [ln for ln in lot_numbers
                    if ln["farm_id"] == sel_farm_id and ln["is_active"]]
        if not farm_lns:
            st.warning("ロット番号を登録してください")
            st.stop()
        sel_ln_id = st.selectbox("ロット番号",
            [ln["lot_number_id"] for ln in farm_lns],
            format_func=lambda x: ln_map[x], key="dr_ln")
    with c3:
        lot_fhs = [fh for fh in flock_houses if fh["lot_number_id"] == sel_ln_id]
        if not lot_fhs:
            st.warning("鶏舎割当を登録してください")
            st.stop()
        sel_fh_id = st.selectbox("鶏舎",
            [fh["flock_house_id"] for fh in lot_fhs],
            format_func=lambda x: house_map.get(
                next(fh["house_id"] for fh in lot_fhs if fh["flock_house_id"] == x), ""),
            key="dr_fh")
    with c4:
        record_date = st.date_input("記録日", value=date.today(), key="dr_date")

    # ---- 基本計算 ----
    sel_fh        = next(fh for fh in lot_fhs if fh["flock_house_id"] == sel_fh_id)
    chick_in_date = date.fromisoformat(sel_fh["chick_in_date"])
    age_days      = (record_date - chick_in_date).days
    prev_date     = record_date - timedelta(days=1)
    total_rem, perf_rem, total_mort, total_cull = get_remaining(sel_fh, prev_date)
    ross          = get_ross308(age_days)

    # ---- 対象が変わったらリセット＋既存レコードを再取得 ----
    current_target = f"{sel_fh_id}_{record_date}"
    if st.session_state.get("_last_target") != current_target:
        # 入力キーをクリア
        reset_inputs()
        # 新しい対象の既存レコードを取得
        existing = supabase.table("daily_records") \
            .select("*") \
            .eq("flock_house_id", sel_fh_id) \
            .eq("record_date", str(record_date)) \
            .execute().data
        rec = existing[0] if existing else None
        st.session_state["_cached_rec"] = rec
        st.session_state["_last_target"] = current_target

        # 既存レコードがある場合、値をセッションに書き込んでウィジェットに反映
        if rec:
            st.session_state["dr_mort"]       = int(rec.get("mortality_count")  or 0)
            st.session_state["dr_cull"]       = int(rec.get("culling_count")    or 0)
            st.session_state["dr_ht_max"]     = float(rec.get("house_temp_max") or 25.0)
            st.session_state["dr_ht_min"]     = float(rec.get("house_temp_min") or 20.0)
            st.session_state["dr_hum"]        = float(rec.get("house_humidity") or 60.0)
            st.session_state["dr_ot_max"]     = float(rec.get("outside_temp_max") or 20.0)
            st.session_state["dr_ot_min"]     = float(rec.get("outside_temp_min") or 15.0)
            st.session_state["dr_fi"]         = float(rec.get("feed_intake")    or 0.0)
            st.session_state["dr_wi"]         = float(rec.get("water_intake")   or 0.0)
            st.session_state["dr_fd"]         = float(rec.get("feed_delivery_qty") or 0.0)
            st.session_state["dr_has_weight"] = rec.get("avg_body_weight") is not None
            st.session_state["dr_weight"]     = float(rec.get("avg_body_weight") or 0.0)
            st.session_state["dr_log"]        = rec.get("work_log") or ""
            # brand・workerはselectboxのindexで制御するためスキップ
        st.rerun()

    # キャッシュから既存レコードを参照
    rec = st.session_state.get("_cached_rec")

    if rec:
        st.markdown(
            "<span style='font-size:0.75rem;color:#0068c9;'>✏️ 登録済み — 編集できます</span>",
            unsafe_allow_html=True)
    else:
        st.markdown(
            "<span style='font-size:0.75rem;color:#888;'>📋 未入力 — 新規登録</span>",
            unsafe_allow_html=True)

    # ---- サマリ表示（コンパクト） ----
    ross_wt = ross.get('weight_g', '-')
    st.markdown(
        f"""<div style="display:flex;gap:1.2rem;align-items:center;
            background:#f0f2f6;border-radius:6px;padding:4px 10px;
            font-size:0.85rem;color:#333;margin-bottom:4px;">
            <span>📅 <b>日齢</b>: {age_days} 日</span>
            <span>🐔 <b>残存（管理）</b>: {total_rem:,} 羽</span>
            <span>📊 <b>残存（成績）</b>: {perf_rem:,} 羽</span>
            <span>💀 <b>累計斃死+淘汰</b>: {total_mort + total_cull:,} 羽</span>
            <span>⚖️ <b>Ross308標準体重</b>: {ross_wt} g</span>
        </div>""",
        unsafe_allow_html=True
    )

    def v(key, default=None):
        """既存レコードの値 or デフォルト値を返す"""
        if rec and rec.get(key) is not None:
            return rec[key]
        return default

    # ---- 入力フォーム ----
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**羽数**")
        mortality = st.number_input("斃死数",  min_value=0,
            value=int(v("mortality_count", 0)), step=1, key="dr_mort")
        culling   = st.number_input("淘汰数",  min_value=0,
            value=int(v("culling_count",   0)), step=1, key="dr_cull")
        today_total = total_rem - mortality - culling
        today_perf  = perf_rem  - mortality - culling

        st.markdown("**舎内環境**")
        house_temp_max = st.number_input("舎内最高温度（℃）",
            min_value=-10.0, max_value=50.0,
            value=float(v("house_temp_max", 25.0)), step=0.1, key="dr_ht_max")
        house_temp_min = st.number_input("舎内最低温度（℃）",
            min_value=-10.0, max_value=50.0,
            value=float(v("house_temp_min", 20.0)), step=0.1, key="dr_ht_min")
        house_humidity = st.number_input("湿度（%）",
            min_value=0.0, max_value=100.0,
            value=float(v("house_humidity", 60.0)), step=1.0, key="dr_hum")
        avg_temp = (house_temp_max + house_temp_min) / 2
        st.caption(f"舎内平均温度: **{avg_temp:.1f} ℃**")

        st.markdown("**外気**")
        outside_temp_max = st.number_input("外気最高温度（℃）",
            min_value=-20.0, max_value=45.0,
            value=float(v("outside_temp_max", 20.0)), step=0.1, key="dr_ot_max")
        outside_temp_min = st.number_input("外気最低温度（℃）",
            min_value=-20.0, max_value=45.0,
            value=float(v("outside_temp_min", 15.0)), step=0.1, key="dr_ot_min")

    with col2:
        st.markdown("**飼料・飲水**")
        feed_intake  = st.number_input("採食量（kg）",
            min_value=0.0, value=float(v("feed_intake",  0.0)), step=10.0, key="dr_fi")
        water_intake = st.number_input("飲水量（L）",
            min_value=0.0, value=float(v("water_intake", 0.0)), step=10.0, key="dr_wi")

        if ross and ross.get("daily_intake_g") and today_total > 0:
            std_kg   = ross["daily_intake_g"] / 1000 * today_total
            diff     = feed_intake - std_kg
            diff_pct = diff / std_kg * 100 if std_kg > 0 else 0
            icon     = "🟢" if abs(diff_pct) <= 5 else ("🔴" if diff_pct < -5 else "🟡")
            st.caption(f"Ross308標準: **{std_kg:.1f} kg**　{icon} 差: **{diff:+.1f} kg**（{diff_pct:+.1f}%）")

        feed_delivery = st.number_input("飼料納品量（kg）",
            min_value=0.0, value=float(v("feed_delivery_qty", 0.0)),
            step=100.0, key="dr_fd", help="納品がない日は0のままでOK")

        if feed_brands:
            brand_names   = ["なし"] + list(brand_opts.keys())
            current_brand = brand_map.get(v("feed_brand_id"), "なし")
            sel_brand     = st.selectbox("納品飼料銘柄", brand_names,
                index=brand_names.index(current_brand) if current_brand in brand_names else 0,
                key="dr_brand")
        else:
            sel_brand = "なし"

        st.markdown("**平均体重**")
        has_weight = st.checkbox("本日体重測定あり",
            value=v("avg_body_weight") is not None, key="dr_has_weight")
        avg_weight = None
        if has_weight:
            avg_weight = st.number_input("平均体重（g）",
                min_value=0.0,
                value=float(v("avg_body_weight", ross.get("weight_g", 0) or 0)),
                step=10.0, key="dr_weight")
            if ross and ross.get("weight_g"):
                diff_w     = avg_weight - ross["weight_g"]
                diff_w_pct = diff_w / ross["weight_g"] * 100
                icon_w     = "🟢" if abs(diff_w_pct) <= 5 else ("🔴" if diff_w_pct < -5 else "🟡")
                st.caption(
                    f"Ross308標準: **{ross['weight_g']} g**　"
                    f"{icon_w} 差: **{diff_w:+.0f} g**（{diff_w_pct:+.1f}%）")

        st.markdown("**作業記録**")
        work_log = st.text_area("作業日誌",
            value=v("work_log", ""), key="dr_log", height=100)

        if workers:
            worker_names   = ["未選択"] + list(worker_opts.keys())
            current_worker = worker_map.get(v("worker_id"), "未選択")
            sel_worker     = st.selectbox("作業担当者", worker_names,
                index=worker_names.index(current_worker) if current_worker in worker_names else 0,
                key="dr_worker")
        else:
            sel_worker = "未選択"

    # ---- 保存 ----
    bcol1, bcol2 = st.columns([1, 5])
    with bcol1:
        save_btn = st.button("💾 保存", key="btn_save", type="primary")
    with bcol2:
        if st.button("🔄 入力をクリア", key="btn_clear"):
            reset_inputs()
            # 保存後はキャッシュをクリアして次入力に備える
            if "_cached_rec" in st.session_state:
                del st.session_state["_cached_rec"]
            if "_last_target" in st.session_state:
                del st.session_state["_last_target"]
            st.rerun()

    if save_btn:
        data = {
            "flock_house_id":    sel_fh_id,
            "record_date":       str(record_date),
            "mortality_count":   mortality,
            "culling_count":     culling,
            "house_temp_max":    house_temp_max,
            "house_temp_min":    house_temp_min,
            "house_humidity":    house_humidity,
            "outside_temp_max":  outside_temp_max,
            "outside_temp_min":  outside_temp_min,
            "feed_intake":       feed_intake  if feed_intake  > 0 else None,
            "water_intake":      water_intake if water_intake > 0 else None,
            "feed_delivery_qty": feed_delivery if feed_delivery > 0 else None,
            "feed_brand_id":     brand_opts.get(sel_brand) if sel_brand != "なし" else None,
            "avg_body_weight":   avg_weight,
            "work_log":          work_log or None,
            "worker_id":         worker_opts.get(sel_worker) if sel_worker != "未選択" else None,
        }
        try:
            if rec:
                upd("daily_records", "daily_record_id", rec["daily_record_id"], data)
                st.success(f"✅ {record_date} の記録を更新しました（日齢: {age_days}日）")
            else:
                insert("daily_records", data)
                st.success(f"✅ {record_date} の記録を保存しました（日齢: {age_days}日）")
            # 保存後は次の日付に切り替えやすいようリセット
            reset_inputs()
            # 保存後はキャッシュをクリアして次入力に備える
            if "_cached_rec" in st.session_state:
                del st.session_state["_cached_rec"]
            if "_last_target" in st.session_state:
                del st.session_state["_last_target"]
            st.rerun()
        except Exception as e:
            st.error(f"保存エラー: {e}")

# ==========================================================
# タブ2: 記録一覧
# ==========================================================
with tab2:
    st.markdown("#### 📋 記録一覧")

    if not farms:
        st.stop()

    lc1, lc2, lc3 = st.columns(3)
    with lc1:
        l_farm    = st.selectbox("農場", list(farm_opts.keys()), key="list_farm")
        l_farm_id = farm_opts[l_farm]
    with lc2:
        l_lns = [ln for ln in lot_numbers if ln["farm_id"] == l_farm_id]
        if not l_lns:
            st.info("ロット番号がありません")
            st.stop()
        l_ln_id = st.selectbox("ロット番号",
            [ln["lot_number_id"] for ln in l_lns],
            format_func=lambda x: ln_map[x], key="list_ln")
    with lc3:
        l_fhs = [fh for fh in flock_houses if fh["lot_number_id"] == l_ln_id]
        if not l_fhs:
            st.info("鶏舎割当がありません")
            st.stop()
        l_fh_id = st.selectbox("鶏舎",
            [fh["flock_house_id"] for fh in l_fhs],
            format_func=lambda x: house_map.get(
                next(fh["house_id"] for fh in l_fhs if fh["flock_house_id"] == x), ""),
            key="list_fh")

    recs = supabase.table("daily_records") \
        .select("*").eq("flock_house_id", l_fh_id) \
        .order("record_date").execute().data

    if recs:
        l_fh_obj   = next(fh for fh in l_fhs if fh["flock_house_id"] == l_fh_id)
        chick_dt   = date.fromisoformat(l_fh_obj["chick_in_date"])
        df         = pd.DataFrame(recs)
        df["日齢"] = df["record_date"].apply(
            lambda d: (date.fromisoformat(d) - chick_dt).days)
        df["銘柄"]   = df["feed_brand_id"].map(brand_map).fillna("-")
        df["担当者"] = df["worker_id"].map(worker_map).fillna("-")
        df = df[["record_date","日齢","mortality_count","culling_count",
                 "house_temp_max","house_temp_min","house_humidity",
                 "outside_temp_max","outside_temp_min",
                 "feed_intake","water_intake","feed_delivery_qty",
                 "銘柄","avg_body_weight","work_log","担当者"]]
        df.columns = ["記録日","日齢","斃死","淘汰",
                      "舎内最高℃","舎内最低℃","湿度%",
                      "外気最高℃","外気最低℃",
                      "採食量kg","飲水量L","納品量kg",
                      "飼料銘柄","平均体重g","作業日誌","担当者"]
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"合計 {len(df)} 件")
    else:
        st.info("記録がありません")

# ==========================================================
# タブ3: 推移グラフ
# ==========================================================
with tab3:
    st.markdown("#### 📈 推移グラフ")

    if not farms:
        st.stop()

    gc1, gc2, gc3 = st.columns(3)
    with gc1:
        g_farm    = st.selectbox("農場", list(farm_opts.keys()), key="g_farm")
        g_farm_id = farm_opts[g_farm]
    with gc2:
        g_lns = [ln for ln in lot_numbers if ln["farm_id"] == g_farm_id]
        if not g_lns:
            st.stop()
        g_ln_id = st.selectbox("ロット番号",
            [ln["lot_number_id"] for ln in g_lns],
            format_func=lambda x: ln_map[x], key="g_ln")
    with gc3:
        g_fhs = [fh for fh in flock_houses if fh["lot_number_id"] == g_ln_id]
        if not g_fhs:
            st.stop()
        g_fh_id = st.selectbox("鶏舎",
            [fh["flock_house_id"] for fh in g_fhs],
            format_func=lambda x: house_map.get(
                next(fh["house_id"] for fh in g_fhs if fh["flock_house_id"] == x), ""),
            key="g_fh")

    g_recs = supabase.table("daily_records") \
        .select("*").eq("flock_house_id", g_fh_id) \
        .order("record_date").execute().data

    if g_recs:
        g_fh_obj   = next(fh for fh in g_fhs if fh["flock_house_id"] == g_fh_id)
        g_chick_dt = date.fromisoformat(g_fh_obj["chick_in_date"])
        df         = pd.DataFrame(g_recs)
        df["日齢"] = df["record_date"].apply(
            lambda d: (date.fromisoformat(d) - g_chick_dt).days)
        df["標準採食量_g"] = df["日齢"].apply(lambda a: get_ross308(a).get("daily_intake_g"))
        df["標準体重_g"]   = df["日齢"].apply(lambda a: get_ross308(a).get("weight_g"))

        item = st.selectbox("グラフ項目",
            ["体重（実績 vs 標準）","採食量（実績 vs 標準）","斃死+淘汰数","温度・湿度"],
            key="g_item")

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.family"] = "DejaVu Sans"
        fig, ax = plt.subplots(figsize=(10, 4))

        if item == "体重（実績 vs 標準）":
            w = df[df["avg_body_weight"].notna()]
            ax.plot(w["日齢"], w["avg_body_weight"], "o-", label="Actual(g)", color="steelblue")
            ax.plot(df["日齢"], df["標準体重_g"], "--", label="Ross308(g)", color="orange", alpha=0.7)
            ax.set_ylabel("Body Weight (g)")
        elif item == "採食量（実績 vs 標準）":
            ax.bar(df["日齢"], df["feed_intake"], label="Actual(kg)", color="steelblue", alpha=0.7)
            std = df["標準採食量_g"].apply(
                lambda g: g / 1000 * g_fh_obj["chick_in_count"] if g else None)
            ax.plot(df["日齢"], std, "--", label="Ross308(kg)", color="orange")
            ax.set_ylabel("Feed Intake (kg)")
        elif item == "斃死+淘汰数":
            df["計"] = df["mortality_count"] + df["culling_count"]
            ax.bar(df["日齢"], df["計"], color="tomato", alpha=0.8)
            ax.set_ylabel("Count")
        elif item == "温度・湿度":
            ax.plot(df["日齢"], df["house_temp_max"], "r-",  label="House Max")
            ax.plot(df["日齢"], df["house_temp_min"], "b-",  label="House Min")
            ax.plot(df["日齢"], df["house_humidity"], "g--", label="Humidity%", alpha=0.7)
            ax.set_ylabel("Temp(C) / Humidity(%)")

        ax.set_xlabel("Age (days)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        st.pyplot(fig)
        plt.close()
    else:
        st.info("記録がありません")
