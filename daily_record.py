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

/* data_editor セル編集枠をセルサイズに合わせる */
div[data-testid="stDataFrameResizable"] input {
    height: 100% !important;
    min-height: unset !important;
    padding: 0 4px !important;
    margin: 0 !important;
    font-size: 0.82rem !important;
    box-sizing: border-box !important;
    border-radius: 2px !important;
}
/* 編集枠（赤枠）をセル境界内に収める */
[class*="gdg-style"] input,
[class*="cell-edit"] input {
    height: 100% !important;
    padding: 0 4px !important;
    margin: 0 !important;
    box-sizing: border-box !important;
    border-radius: 0 !important;
}
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
DEFAULTS = {
    "dr_mort":       0,
    "dr_cull":       0,
    "dr_ht_max":     25.0,
    "dr_ht_min":     20.0,
    "dr_hum":        60.0,
    "dr_ot_max":     20.0,
    "dr_ot_min":     15.0,
    "dr_fi":         0.0,
    "dr_wi":         0.0,
    "dr_fd":         0.0,
    "dr_has_weight": False,
    "dr_weight":     0.0,
    "dr_log":        "",
    "dr_brand_idx":  0,
    "dr_worker_idx": 0,
}

def reset_inputs():
    """デフォルト値をセッションに書き込んでウィジェットを確実にリセット"""
    for key, val in DEFAULTS.items():
        st.session_state[key] = val

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

        # 既存レコードがある場合は値を、ない場合はデフォルト値をセッションに書き込み
        if rec:
            st.session_state["dr_mort"]       = int(rec.get("mortality_count")    or 0)
            st.session_state["dr_cull"]       = int(rec.get("culling_count")      or 0)
            st.session_state["dr_ht_max"]     = float(rec.get("house_temp_max")   or 25.0)
            st.session_state["dr_ht_min"]     = float(rec.get("house_temp_min")   or 20.0)
            st.session_state["dr_hum"]        = float(rec.get("house_humidity")   or 60.0)
            st.session_state["dr_ot_max"]     = float(rec.get("outside_temp_max") or 20.0)
            st.session_state["dr_ot_min"]     = float(rec.get("outside_temp_min") or 15.0)
            st.session_state["dr_fi"]         = float(rec.get("feed_duration_min") or 0.0)
            st.session_state["dr_wi"]         = float(rec.get("water_intake")     or 0.0)
            st.session_state["dr_fd"]         = float(rec.get("feed_delivery_qty") or 0.0)
            st.session_state["dr_has_weight"] = rec.get("avg_body_weight") is not None
            st.session_state["dr_weight"]     = float(rec.get("avg_body_weight")  or 0.0)
            st.session_state["dr_log"]        = rec.get("work_log") or ""
            # brand/workerインデックスを設定
            brand_names_tmp = ["なし"] + list(brand_opts.keys())
            current_brand_tmp = brand_map.get(rec.get("feed_brand_id"), "なし")
            st.session_state["dr_brand_idx"] = brand_names_tmp.index(current_brand_tmp) if current_brand_tmp in brand_names_tmp else 0
            worker_names_tmp = ["未選択"] + list(worker_opts.keys())
            current_worker_tmp = worker_map.get(rec.get("worker_id"), "未選択")
            st.session_state["dr_worker_idx"] = worker_names_tmp.index(current_worker_tmp) if current_worker_tmp in worker_names_tmp else 0
        else:
            reset_inputs()
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
            <span>🐔 <b>残存（実質）</b>: {total_rem:,} 羽</span>
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
        feed_duration = st.number_input("採食時間（分）",
            min_value=0.0, value=float(v("feed_duration_min", 0.0)), step=1.0, key="dr_fi")
        # 搬送係数を鶏舎基本係数×銘柄補正率で自動計算
        _house_coef = float(sel_fh.get("house_transfer_coef") or 0) if "house_transfer_coef" in (sel_fh or {}) else 0.0
        # sel_fhにはhouse情報がないためhousesマスタから取得
        _h = next((h for h in houses if h["house_id"] == sel_fh.get("house_id")), {})
        _house_coef = float(_h.get("feed_transfer_coef") or 0)
        # 現在選択中の飼料銘柄補正率（後のselectboxで確定するため暫定1.0）
        feed_intake_kg = 0.0
        if _house_coef > 0 and feed_duration > 0:
            feed_intake_kg = feed_duration * _house_coef  # 銘柄補正は後で適用

        water_intake = st.number_input("飲水量（L）",
            min_value=0.0, value=float(v("water_intake", 0.0)), step=10.0, key="dr_wi")

        # 採食量（暫定）を表示（銘柄選択後に更新）
        if _house_coef > 0 and feed_duration > 0:
            st.caption(f"基本搬送係数: **{_house_coef} kg/min**　暫定採食量: **{feed_intake_kg:.1f} kg**（銘柄補正前）")

        if ross and ross.get("daily_intake_g") and today_total > 0:
            std_kg   = ross["daily_intake_g"] / 1000 * today_total
            diff     = feed_intake_kg - std_kg
            diff_pct = diff / std_kg * 100 if std_kg > 0 else 0
            icon     = "🟢" if abs(diff_pct) <= 5 else ("🔴" if diff_pct < -5 else "🟡")
            st.caption(f"Ross308標準: **{std_kg:.1f} kg**　{icon} 差: **{diff:+.1f} kg**（{diff_pct:+.1f}%）")

        feed_delivery = st.number_input("飼料納品量（kg）",
            min_value=0.0, value=float(v("feed_delivery_qty", 0.0)),
            step=100.0, key="dr_fd", help="納品がない日は0のままでOK")

        if feed_brands:
            brand_names = ["なし"] + list(brand_opts.keys())
            sel_brand   = st.selectbox("納品飼料銘柄", brand_names,
                index=min(int(st.session_state.get("dr_brand_idx", 0)), len(brand_names)-1),
                key="dr_brand")
            # 銘柄補正率を適用して採食量を再計算
            if sel_brand != "なし" and _house_coef > 0 and feed_duration > 0:
                _brand = next((b for b in feed_brands if b["brand_name"] == sel_brand), {})
                _ratio = float(_brand.get("transfer_coef_ratio") or 1.0)
                _actual_coef = _house_coef * _ratio
                feed_intake_kg = feed_duration * _actual_coef
                st.caption(f"実搬送係数: **{_actual_coef:.3f} kg/min**（{_house_coef} × 補正率{_ratio:.3f}）　→ 採食量: **{feed_intake_kg:.1f} kg**")
        else:
            sel_brand = "なし"

        st.markdown("**平均体重**")
        has_weight = st.checkbox("本日体重測定あり",
            value=bool(st.session_state.get("dr_has_weight", False)), key="dr_has_weight")
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
            worker_names = ["未選択"] + list(worker_opts.keys())
            sel_worker   = st.selectbox("作業担当者", worker_names,
                index=min(int(st.session_state.get("dr_worker_idx", 0)), len(worker_names)-1),
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
            "feed_duration_min":    feed_duration  if feed_duration  > 0 else None,
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
# タブ2: 記録一覧（data_editor 直接編集）
# ==========================================================
with tab2:
    st.markdown("#### 📋 記録一覧・直接編集")

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

    if not recs:
        st.info("記録がありません")
    else:
        l_fh_obj = next(fh for fh in l_fhs if fh["flock_house_id"] == l_fh_id)
        chick_dt = date.fromisoformat(l_fh_obj["chick_in_date"])

        # ---- 編集用DataFrameを構築 ----
        df = pd.DataFrame(recs)
        df["日齢"] = df["record_date"].apply(
            lambda d: (date.fromisoformat(d) - chick_dt).days)

        # 飼料銘柄・担当者はラベル文字列で表示
        brand_names_list   = ["なし"] + list(brand_opts.keys())
        worker_names_list  = ["未選択"] + list(worker_opts.keys())
        # 採食量（kg）= 採食時間 × 鶏舎基本係数 × 銘柄補正率
        _h2 = next((h for h in houses if h["house_id"] == l_fh_obj["house_id"]), {})
        _hcoef = float(_h2.get("feed_transfer_coef") or 0)
        _brand_ratio_map = {b["feed_brand_id"]: float(b.get("transfer_coef_ratio") or 1.0) for b in feed_brands}
        def calc_intake(r):
            if pd.isna(r.get("feed_duration_min")) or _hcoef == 0:
                return None
            ratio = _brand_ratio_map.get(r.get("feed_brand_id"), 1.0)
            return round(float(r["feed_duration_min"]) * _hcoef * ratio, 2)
        df["採食量kg"] = df.apply(calc_intake, axis=1)
        df["飼料銘柄"] = df["feed_brand_id"].map(brand_map).fillna("なし")
        df["担当者"]   = df["worker_id"].map(worker_map).fillna("未選択")

        # data_editor用に列を選択・リネーム
        edit_df = df[[
            "daily_record_id",
            "record_date", "日齢",
            "mortality_count", "culling_count",
            "house_temp_max", "house_temp_min", "house_humidity",
            "outside_temp_max", "outside_temp_min",
            "feed_duration_min", "採食量kg", "water_intake", "feed_delivery_qty",
            "飼料銘柄", "avg_body_weight", "work_log", "担当者"
        ]].copy()

        edit_df.columns = [
            "id",
            "記録日", "日齢",
            "斃死", "淘汰",
            "舎内最高℃", "舎内最低℃", "湿度%",
            "外気最高℃", "外気最低℃",
            "採食時間min", "採食量kg", "飲水量L", "納品量kg",
            "飼料銘柄", "平均体重g", "作業日誌", "担当者"
        ]

        # 日齢列をint→string変換（Noneを空欄表示・新規行も空欄で見やすく）
        edit_df["日齢"] = edit_df["日齢"].apply(
            lambda x: str(int(x)) if pd.notna(x) else "")

        # 記録日列を編集可能なDateColumnに変更（新規行追加対応）
        edit_df["記録日"] = pd.to_datetime(edit_df["記録日"]).dt.date

        edited = st.data_editor(
            edit_df,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",          # ← 「+」ボタンで新規行追加可能
            column_config={
                "id":       None,  # id列を非表示
                "記録日":   st.column_config.DateColumn("記録日", width="small"),
                "日齢":     st.column_config.TextColumn("日齢", disabled=True, width="small"),
                "斃死":     st.column_config.NumberColumn("斃死", min_value=0, step=1, width="small"),
                "淘汰":     st.column_config.NumberColumn("淘汰", min_value=0, step=1, width="small"),
                "舎内最高℃": st.column_config.NumberColumn("舎内最高℃", step=0.1, width="small"),
                "舎内最低℃": st.column_config.NumberColumn("舎内最低℃", step=0.1, width="small"),
                "湿度%":    st.column_config.NumberColumn("湿度%", min_value=0.0, max_value=100.0, step=1.0, width="small"),
                "外気最高℃": st.column_config.NumberColumn("外気最高℃", step=0.1, width="small"),
                "外気最低℃": st.column_config.NumberColumn("外気最低℃", step=0.1, width="small"),
                "採食時間min": st.column_config.NumberColumn("採食時間min", step=1.0, width="small"),
                "採食量kg":   st.column_config.NumberColumn("採食量kg", disabled=True, width="small", help="採食時間×搬送係数（自動計算）"),
                "飲水量L":  st.column_config.NumberColumn("飲水量L",  step=0.1, width="small"),
                "納品量kg": st.column_config.NumberColumn("納品量kg", step=10.0, width="small"),
                "飼料銘柄": st.column_config.SelectboxColumn("飼料銘柄", options=brand_names_list, width="medium"),
                "平均体重g": st.column_config.NumberColumn("平均体重g", step=1.0, width="small"),
                "作業日誌": st.column_config.TextColumn("作業日誌", width="large"),
                "担当者":   st.column_config.SelectboxColumn("担当者", options=worker_names_list, width="small"),
            },
            key="list_editor"
        )

        st.caption(f"合計 {len(edit_df)} 件　セルを直接編集・表末「＋」で新規行追加 → 記録日を入力後「一括保存」で日齢は自動計算されます")

        if st.button("💾 一括保存", key="list_save", type="primary"):
            errors  = []
            updated = 0
            inserted = 0

            # 元のIDセット（既存行の判定用）
            original_ids = set(edit_df["id"].dropna().astype(int).tolist())

            for _, row in edited.iterrows():
                row_id = row.get("id")
                rec_date = row.get("記録日")

                # 記録日が未入力の新規行はスキップ
                if pd.isna(rec_date) or rec_date is None:
                    continue

                # 日齢を再計算
                try:
                    rd = rec_date if isinstance(rec_date, date) else date.fromisoformat(str(rec_date))
                except:
                    errors.append(f"日付エラー: {rec_date}")
                    continue

                data = {
                    "flock_house_id":    l_fh_id,
                    "record_date":       str(rd),
                    "mortality_count":   int(row["斃死"]  or 0),
                    "culling_count":     int(row["淘汰"]  or 0),
                    "house_temp_max":    float(row["舎内最高℃"]) if pd.notna(row["舎内最高℃"]) else None,
                    "house_temp_min":    float(row["舎内最低℃"]) if pd.notna(row["舎内最低℃"]) else None,
                    "house_humidity":    float(row["湿度%"])     if pd.notna(row["湿度%"])     else None,
                    "outside_temp_max":  float(row["外気最高℃"]) if pd.notna(row["外気最高℃"]) else None,
                    "outside_temp_min":  float(row["外気最低℃"]) if pd.notna(row["外気最低℃"]) else None,
                    "feed_duration_min":    float(row["採食時間min"])     if pd.notna(row.get("採食時間min"))     and float(row.get("採食時間min") or 0) > 0 else None,
                    "water_intake":      float(row["飲水量L"])   if pd.notna(row["飲水量L"])   and float(row["飲水量L"]  or 0) > 0 else None,
                    "feed_delivery_qty": float(row["納品量kg"])  if pd.notna(row["納品量kg"])  and float(row["納品量kg"] or 0) > 0 else None,
                    "feed_brand_id":     brand_opts.get(row["飼料銘柄"]) if pd.notna(row.get("飼料銘柄")) and row["飼料銘柄"] != "なし" else None,
                    "avg_body_weight":   float(row["平均体重g"]) if pd.notna(row["平均体重g"]) and float(row["平均体重g"] or 0) > 0 else None,
                    "work_log":          str(row["作業日誌"]) if pd.notna(row.get("作業日誌")) and row["作業日誌"] else None,
                    "worker_id":         worker_opts.get(row["担当者"]) if pd.notna(row.get("担当者")) and row["担当者"] != "未選択" else None,
                }

                try:
                    if pd.notna(row_id) and int(row_id) in original_ids:
                        # 既存行 → UPDATE
                        upd("daily_records", "daily_record_id", int(row_id), data)
                        updated += 1
                    else:
                        # 新規行（idがNaN or 元のIDに存在しない）→ INSERT
                        # 日齢は入雛日から自動計算（保存時）
                        insert("daily_records", data)
                        inserted += 1
                except Exception as e:
                    errors.append(f"{rd}: {e}")

            if errors:
                st.error(f"エラー: {'; '.join(errors)}")
            else:
                msg = []
                if updated  > 0: msg.append(f"更新 {updated}件")
                if inserted > 0: msg.append(f"新規追加 {inserted}件")
                st.success(f"✅ {' / '.join(msg)} を保存しました")
                st.rerun()

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
        import matplotlib.font_manager as fm
        import numpy as np

        # 日本語フォント設定（文字化け対策）
        jp_fonts = [f.name for f in fm.fontManager.ttflist
                    if any(k in f.name for k in ["Noto", "IPAex", "IPA", "Hiragino", "Yu Gothic", "Meiryo"])]
        if jp_fonts:
            plt.rcParams["font.family"] = jp_fonts[0]
        else:
            plt.rcParams["font.family"] = "DejaVu Sans"

        # 出荷日齢・横軸範囲
        x_max = int(g_fh_obj.get("planned_shipment_age_days") or 56)
        x_ticks = list(range(0, x_max + 1, 7))
        # Ross308標準データを0〜x_maxまで全日齢で生成
        std_ages  = list(range(0, min(x_max + 1, 57)))
        std_bw    = [get_ross308(a).get("weight_g")      for a in std_ages]
        std_fi    = [get_ross308(a).get("daily_intake_g") for a in std_ages]

        # 入雛羽数（斃死率計算用）
        chick_in_total = (g_fh_obj["chick_in_count"] or 0)

        # ---- 体重グラフ ----
        if item == "体重（実績 vs 標準）":
            fig, ax = plt.subplots(figsize=(10, 4))
            w = df[df["avg_body_weight"].notna()]
            ax.plot(w["日齢"], w["avg_body_weight"], "o-",
                    label="実績体重(g)", color="steelblue", linewidth=1.5)
            ax.plot(std_ages, std_bw, "--",
                    label="Ross308標準(g)", color="orange", alpha=0.8)
            ax.set_ylabel("体重 (g)")
            ax.set_title("体重推移（実績 vs Ross308標準）")

        # ---- 採食量グラフ ----
        elif item == "採食量（実績 vs 標準）":
            fig, ax = plt.subplots(figsize=(10, 4))
            _g_h     = next((h for h in houses if h["house_id"] == g_fh_obj["house_id"]), {})
            _g_hcoef = float(_g_h.get("feed_transfer_coef") or 0)
            _g_ratio_map = {
                b["feed_brand_id"]: float(b.get("transfer_coef_ratio") or 1.0)
                for b in feed_brands
            }
            # 残存羽数を累積計算
            cumulative_loss = 0
            remaining_list  = []
            spare = g_fh_obj["spare_count"] or 0
            total_initial = chick_in_total + spare
            for _, row in df.iterrows():
                cumulative_loss += (row["mortality_count"] or 0) + (row["culling_count"] or 0)
                remaining_list.append(max(total_initial - cumulative_loss, 1))
            df["残存羽数"] = remaining_list

            def per_bird_intake(r):
                if pd.isna(r.get("feed_duration_min")) or _g_hcoef == 0:
                    return None
                ratio     = _g_ratio_map.get(r.get("feed_brand_id"), 1.0)
                intake_kg = float(r["feed_duration_min"]) * _g_hcoef * ratio
                return round(intake_kg * 1000 / r["残存羽数"], 2)

            df["実績採食量_g/羽"] = df.apply(per_bird_intake, axis=1)
            ax.bar(df["日齢"], df["実績採食量_g/羽"].fillna(0.0),
                   label="実績(g/羽)", color="steelblue", alpha=0.7)
            ax.plot(std_ages, std_fi, "--",
                    label="Ross308標準(g/羽)", color="orange", linewidth=1.5)
            ax.set_ylabel("1羽当たり採食量 (g/羽)")
            ax.set_title("採食量推移（実績 vs Ross308標準）")

        # ---- 斃死+淘汰率グラフ ----
        elif item == "斃死+淘汰数":
            fig, ax = plt.subplots(figsize=(10, 4))
            df["斃死淘汰率%"] = (
                (df["mortality_count"].fillna(0) + df["culling_count"].fillna(0))
                / chick_in_total * 100
            ).round(3)
            ax.bar(df["日齢"], df["斃死淘汰率%"], color="tomato", alpha=0.8,
                   label="斃死+淘汰率(%)")
            ax.set_ylabel("斃死+淘汰率 (%)")
            ax.set_title("斃死・淘汰率推移（入雛羽数比）")

        # ---- 温度・湿度グラフ（2軸） ----
        elif item == "温度・湿度":
            fig, ax = plt.subplots(figsize=(10, 4))
            ax2 = ax.twinx()  # 第2軸（湿度用）

            ax.plot(df["日齢"], df["house_temp_max"], "r-",
                    label="舎内最高℃", linewidth=1.5)
            ax.plot(df["日齢"], df["house_temp_min"], "b-",
                    label="舎内最低℃", linewidth=1.5)

            # Ross308推奨温度（体重ベースで標準体重から逆算・簡易）
            # ross_comfort_tempテーブルから近似値を表示
            try:
                comfort_recs = supabase.table("ross_comfort_temp")                     .select("*").order("body_weight_g").execute().data
                if comfort_recs:
                    comfort_ages, comfort_upper = [], []
                    for age in std_ages:
                        bw = get_ross308(age).get("weight_g") or 0
                        # 最近傍の快適温度上限（RH60%基準）を取得
                        closest = min(comfort_recs, key=lambda r: abs((r["body_weight_g"] or 0) - bw))
                        comfort_upper.append(closest.get("rh_60pct_temp_c"))
                    ax.plot(std_ages, comfort_upper, "r--", alpha=0.5,
                            label="快適温度上限(RH60%)", linewidth=1)
            except:
                pass

            ax2.plot(df["日齢"], df["house_humidity"], "g-",
                     label="湿度%", linewidth=1.5, alpha=0.8)
            ax2.set_ylabel("湿度 (%)", color="green")
            ax2.tick_params(axis="y", labelcolor="green")
            ax2.set_ylim(0, 100)

            ax.set_ylabel("温度 (℃)")
            ax.set_title("温度・湿度推移")

            # 凡例を統合
            lines1, labels1 = ax.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax.legend(lines1 + lines2, labels1 + labels2,
                      loc="upper right", fontsize=8)

        ax.set_xlabel("日齢 (日)")
        ax.set_xlim(0, x_max)
        ax.set_xticks(x_ticks)
        ax.grid(True, alpha=0.3)
        if item != "温度・湿度":
            ax.legend()
        st.pyplot(fig)
        plt.close()
    else:
        st.info("記録がありません")
