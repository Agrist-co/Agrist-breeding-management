"""
ブロイラー飼養管理システム - 日次記録入力
1日1レコード・現場での毎日の入力画面
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from supabase import create_client, Client

st.set_page_config(page_title="日次記録", layout="wide")

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

def fetch_filter(table, col, val, order=None):
    q = supabase.table(table).select("*").eq(col, val)
    if order:
        q = q.order(order)
    return q.execute().data

def insert(table, data):
    return supabase.table(table).insert(data).execute()

def update(table, id_col, id_val, data):
    return supabase.table(table).update(data).eq(id_col, id_val).execute()

# ----------------------------------------------------------
# マスタデータ取得
# ----------------------------------------------------------
farms        = fetch("farms",        "farm_id")
houses       = fetch("houses",       "house_id")
lot_numbers  = fetch("lot_numbers",  "lot_number_id")
flock_houses = fetch("flock_houses", "flock_house_id")
feed_brands  = fetch("feed_brands",  "feed_brand_id")
workers      = fetch("workers",      "worker_id")
ross308      = fetch("ross308_standard", "day")

farm_map    = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts   = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map   = {h["house_id"]: h["house_name"] for h in houses}
ln_map      = {ln["lot_number_id"]: ln["lot_number"] for ln in lot_numbers}
brand_map   = {b["feed_brand_id"]: b["brand_name"] for b in feed_brands}
brand_opts  = {b["brand_name"]: b["feed_brand_id"] for b in feed_brands}
worker_map  = {w["worker_id"]: w["worker_name"] for w in workers}
worker_opts = {w["worker_name"]: w["worker_id"] for w in workers}

# Ross308標準データをdictに変換（日齢→データ）
ross_dict = {
    (r["sex"], r["day"]): r
    for r in ross308
}

def get_ross308(age_days: int, sex: str = "as_hatched") -> dict:
    """日齢からRoss308標準値を取得（範囲外は端値を返す）"""
    age = max(0, min(age_days, 56))
    return ross_dict.get((sex, age), {})

def get_remaining_count(fh: dict, up_to_date: date) -> tuple:
    """指定日までの残存羽数を計算（スペア込み・スペア抜き）"""
    records = supabase.table("daily_records") \
        .select("mortality_count, culling_count") \
        .eq("flock_house_id", fh["flock_house_id"]) \
        .lte("record_date", str(up_to_date)) \
        .execute().data
    total_mort    = sum(r["mortality_count"] or 0 for r in records)
    total_cull    = sum(r["culling_count"]   or 0 for r in records)
    chick_in      = fh["chick_in_count"] or 0
    spare         = fh["spare_count"]    or 0
    total_remain  = chick_in + spare - total_mort - total_cull
    perf_remain   = chick_in            - total_mort - total_cull
    return total_remain, perf_remain, total_mort, total_cull

# ----------------------------------------------------------
# UI
# ----------------------------------------------------------
st.title("📝 日次記録")

tab1, tab2, tab3 = st.tabs(["➕ 入力・編集", "📋 記録一覧", "📈 推移グラフ"])

# ==========================================================
# タブ1: 入力・編集
# ==========================================================
with tab1:

    if not farms:
        st.warning("農場マスタを登録してください")
        st.stop()

    # ---- 対象選択 ----
    st.subheader("🔍 対象選択")
    col_s1, col_s2, col_s3, col_s4 = st.columns(4)

    with col_s1:
        sel_farm    = st.selectbox("農場", list(farm_opts.keys()), key="dr_farm")
        sel_farm_id = farm_opts[sel_farm]

    with col_s2:
        farm_lns = [ln for ln in lot_numbers if ln["farm_id"] == sel_farm_id and ln["is_active"]]
        if not farm_lns:
            st.warning("ロット番号を登録してください")
            st.stop()
        sel_ln_id = st.selectbox("ロット番号",
            [ln["lot_number_id"] for ln in farm_lns],
            format_func=lambda x: ln_map[x],
            key="dr_ln")

    with col_s3:
        lot_fhs = [fh for fh in flock_houses if fh["lot_number_id"] == sel_ln_id]
        if not lot_fhs:
            st.warning("鶏舎割当を登録してください")
            st.stop()
        sel_fh_id = st.selectbox("鶏舎",
            [fh["flock_house_id"] for fh in lot_fhs],
            format_func=lambda x: house_map.get(
                next(fh["house_id"] for fh in lot_fhs if fh["flock_house_id"] == x), ""),
            key="dr_fh")

    with col_s4:
        record_date = st.date_input("記録日", value=date.today(), key="dr_date")

    # 選択した鶏舎情報
    sel_fh        = next(fh for fh in lot_fhs if fh["flock_house_id"] == sel_fh_id)
    chick_in_date = date.fromisoformat(sel_fh["chick_in_date"])
    age_days      = (record_date - chick_in_date).days

    # 残存羽数計算（記録日の前日までの累計）
    prev_date = record_date - timedelta(days=1)
    total_rem, perf_rem, total_mort, total_cull = get_remaining_count(sel_fh, prev_date)

    # Ross308標準値
    ross = get_ross308(age_days)

    # ---- 現況サマリ ----
    st.divider()
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("日齢", f"{age_days} 日")
    m2.metric("残存羽数（管理用）", f"{total_rem:,} 羽")
    m3.metric("残存羽数（成績用）", f"{perf_rem:,} 羽")
    m4.metric("累計斃死+淘汰", f"{total_mort + total_cull:,} 羽")
    m5.metric("Ross308標準体重", f"{ross.get('weight_g', '-')} g" if ross else "-")

    # ---- 既存レコードの確認 ----
    existing = supabase.table("daily_records") \
        .select("*") \
        .eq("flock_house_id", sel_fh_id) \
        .eq("record_date", str(record_date)) \
        .execute().data
    existing_record = existing[0] if existing else None

    if existing_record:
        st.info(f"📌 {record_date} の記録が既に存在します。内容を編集できます。")

    # ---- 入力フォーム ----
    st.divider()
    st.subheader("📝 入力フォーム")

    def v(key, default=None):
        """既存レコードがあればその値、なければデフォルト値を返す"""
        if existing_record and existing_record.get(key) is not None:
            return existing_record[key]
        return default

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 🐔 羽数")
        mortality = st.number_input("斃死数",  min_value=0, value=int(v("mortality_count", 0)), step=1, key="dr_mort")
        culling   = st.number_input("淘汰数",  min_value=0, value=int(v("culling_count",   0)), step=1, key="dr_cull")

        # 本日の残存羽数（入力値を反映）
        today_total = total_rem  - mortality - culling
        today_perf  = perf_rem  - mortality - culling
        st.info(f"本日の残存羽数　管理用: **{today_total:,} 羽**　成績用: **{today_perf:,} 羽**")

        st.markdown("#### 🌡️ 舎内環境")
        house_temp_max = st.number_input("舎内最高温度（℃）",
            min_value=-10.0, max_value=50.0,
            value=float(v("house_temp_max", 25.0)), step=0.1, key="dr_ht_max")
        house_temp_min = st.number_input("舎内最低温度（℃）",
            min_value=-10.0, max_value=50.0,
            value=float(v("house_temp_min", 20.0)), step=0.1, key="dr_ht_min")
        house_humidity = st.number_input("湿度（%）",
            min_value=0.0, max_value=100.0,
            value=float(v("house_humidity", 60.0)), step=1.0, key="dr_hum")

        # Ross308快適温度との比較
        avg_temp = (house_temp_max + house_temp_min) / 2
        st.caption(f"舎内平均温度: **{avg_temp:.1f}℃**")

        st.markdown("#### 🌤️ 外気")
        outside_temp_max = st.number_input("外気最高温度（℃）",
            min_value=-20.0, max_value=45.0,
            value=float(v("outside_temp_max", 20.0)), step=0.1, key="dr_ot_max")
        outside_temp_min = st.number_input("外気最低温度（℃）",
            min_value=-20.0, max_value=45.0,
            value=float(v("outside_temp_min", 15.0)), step=0.1, key="dr_ot_min")

    with col2:
        st.markdown("#### 🌽 飼料・飲水")
        feed_intake   = st.number_input("採食量（kg）",
            min_value=0.0, value=float(v("feed_intake", 0.0)), step=10.0, key="dr_fi")
        water_intake  = st.number_input("飲水量（L）",
            min_value=0.0, value=float(v("water_intake", 0.0)), step=10.0, key="dr_wi")

        # Ross308標準採食量との比較
        if ross and ross.get("daily_intake_g"):
            std_intake_kg = ross["daily_intake_g"] / 1000 * today_total
            diff = feed_intake - std_intake_kg
            diff_pct = (diff / std_intake_kg * 100) if std_intake_kg > 0 else 0
            color = "🟢" if abs(diff_pct) <= 5 else ("🔴" if diff_pct < -5 else "🟡")
            st.caption(
                f"Ross308標準採食量: **{std_intake_kg:.1f} kg**　"
                f"{color} 差: **{diff:+.1f} kg**（{diff_pct:+.1f}%）"
            )

        # 飼料納品
        feed_delivery = st.number_input("飼料納品量（kg）",
            min_value=0.0, value=float(v("feed_delivery_qty", 0.0)), step=100.0, key="dr_fd",
            help="納品がない日は0のままでOK")

        if feed_brands:
            brand_names = ["なし"] + list(brand_opts.keys())
            current_brand = brand_map.get(v("feed_brand_id"), "なし")
            sel_brand = st.selectbox("納品飼料銘柄", brand_names,
                index=brand_names.index(current_brand) if current_brand in brand_names else 0,
                key="dr_brand")
        else:
            sel_brand = "なし"
            st.caption("飼料銘柄マスタが未登録です")

        st.markdown("#### ⚖️ 平均体重")
        has_weight = st.checkbox("本日体重測定あり",
            value=v("avg_body_weight") is not None, key="dr_has_weight")
        avg_weight = None
        if has_weight:
            avg_weight = st.number_input("平均体重（g）",
                min_value=0.0,
                value=float(v("avg_body_weight", ross.get("weight_g", 0) or 0)),
                step=10.0, key="dr_weight")
            # Ross308標準体重との比較
            if ross and ross.get("weight_g"):
                diff_w = avg_weight - ross["weight_g"]
                diff_w_pct = (diff_w / ross["weight_g"] * 100) if ross["weight_g"] > 0 else 0
                color_w = "🟢" if abs(diff_w_pct) <= 5 else ("🔴" if diff_w_pct < -5 else "🟡")
                st.caption(
                    f"Ross308標準体重: **{ross['weight_g']} g**　"
                    f"{color_w} 差: **{diff_w:+.0f} g**（{diff_w_pct:+.1f}%）"
                )

        st.markdown("#### 📋 作業記録")
        work_log = st.text_area("作業日誌", value=v("work_log", ""), key="dr_log", height=100)

        if workers:
            worker_names = ["未選択"] + list(worker_opts.keys())
            current_worker = worker_map.get(v("worker_id"), "未選択")
            sel_worker = st.selectbox("作業担当者", worker_names,
                index=worker_names.index(current_worker) if current_worker in worker_names else 0,
                key="dr_worker")
        else:
            sel_worker = "未選択"
            st.caption("担当者マスタが未登録です")

    # ---- 保存ボタン ----
    st.divider()
    if st.button("💾 保存", key="btn_save", type="primary"):
        data = {
            "flock_house_id":   sel_fh_id,
            "record_date":      str(record_date),
            "mortality_count":  mortality,
            "culling_count":    culling,
            "house_temp_max":   house_temp_max,
            "house_temp_min":   house_temp_min,
            "house_humidity":   house_humidity,
            "outside_temp_max": outside_temp_max,
            "outside_temp_min": outside_temp_min,
            "feed_intake":      feed_intake if feed_intake > 0 else None,
            "water_intake":     water_intake if water_intake > 0 else None,
            "feed_delivery_qty": feed_delivery if feed_delivery > 0 else None,
            "feed_brand_id":    brand_opts.get(sel_brand) if sel_brand != "なし" else None,
            "avg_body_weight":  avg_weight,
            "work_log":         work_log or None,
            "worker_id":        worker_opts.get(sel_worker) if sel_worker != "未選択" else None,
        }
        try:
            if existing_record:
                update("daily_records", "daily_record_id",
                       existing_record["daily_record_id"], data)
                st.success(f"✅ {record_date} の記録を更新しました（日齢: {age_days}日）")
            else:
                insert("daily_records", data)
                st.success(f"✅ {record_date} の記録を保存しました（日齢: {age_days}日）")
            st.rerun()
        except Exception as e:
            st.error(f"保存エラー: {e}")

# ==========================================================
# タブ2: 記録一覧
# ==========================================================
with tab2:
    st.subheader("📋 記録一覧")

    if not farms:
        st.stop()

    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        list_farm    = st.selectbox("農場", list(farm_opts.keys()), key="list_farm")
        list_farm_id = farm_opts[list_farm]
    with col_f2:
        list_lns = [ln for ln in lot_numbers if ln["farm_id"] == list_farm_id]
        if not list_lns:
            st.info("ロット番号がありません")
            st.stop()
        list_ln_id = st.selectbox("ロット番号",
            [ln["lot_number_id"] for ln in list_lns],
            format_func=lambda x: ln_map[x],
            key="list_ln")
    with col_f3:
        list_fhs = [fh for fh in flock_houses if fh["lot_number_id"] == list_ln_id]
        if not list_fhs:
            st.info("鶏舎割当がありません")
            st.stop()
        list_fh_id = st.selectbox("鶏舎",
            [fh["flock_house_id"] for fh in list_fhs],
            format_func=lambda x: house_map.get(
                next(fh["house_id"] for fh in list_fhs if fh["flock_house_id"] == x), ""),
            key="list_fh")

    records = supabase.table("daily_records") \
        .select("*") \
        .eq("flock_house_id", list_fh_id) \
        .order("record_date") \
        .execute().data

    if records:
        list_fh_obj = next(fh for fh in list_fhs if fh["flock_house_id"] == list_fh_id)
        chick_date  = date.fromisoformat(list_fh_obj["chick_in_date"])

        df = pd.DataFrame(records)
        df["日齢"]   = df["record_date"].apply(
            lambda d: (date.fromisoformat(d) - chick_date).days)
        df["銘柄"]   = df["feed_brand_id"].map(brand_map).fillna("-")
        df["担当者"] = df["worker_id"].map(worker_map).fillna("-")

        cols = ["record_date", "日齢", "mortality_count", "culling_count",
                "house_temp_max", "house_temp_min", "house_humidity",
                "outside_temp_max", "outside_temp_min",
                "feed_intake", "water_intake", "feed_delivery_qty",
                "銘柄", "avg_body_weight", "work_log", "担当者"]
        df = df[cols]
        df.columns = ["記録日", "日齢", "斃死", "淘汰",
                      "舎内最高℃", "舎内最低℃", "湿度%",
                      "外気最高℃", "外気最低℃",
                      "採食量kg", "飲水量L", "納品量kg",
                      "飼料銘柄", "平均体重g", "作業日誌", "担当者"]
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"合計 {len(df)} 件")
    else:
        st.info("記録がありません")

# ==========================================================
# タブ3: 推移グラフ
# ==========================================================
with tab3:
    st.subheader("📈 推移グラフ")

    if not farms:
        st.stop()

    col_g1, col_g2, col_g3 = st.columns(3)
    with col_g1:
        g_farm    = st.selectbox("農場", list(farm_opts.keys()), key="g_farm")
        g_farm_id = farm_opts[g_farm]
    with col_g2:
        g_lns = [ln for ln in lot_numbers if ln["farm_id"] == g_farm_id]
        if not g_lns:
            st.stop()
        g_ln_id = st.selectbox("ロット番号",
            [ln["lot_number_id"] for ln in g_lns],
            format_func=lambda x: ln_map[x],
            key="g_ln")
    with col_g3:
        g_fhs = [fh for fh in flock_houses if fh["lot_number_id"] == g_ln_id]
        if not g_fhs:
            st.stop()
        g_fh_id = st.selectbox("鶏舎",
            [fh["flock_house_id"] for fh in g_fhs],
            format_func=lambda x: house_map.get(
                next(fh["house_id"] for fh in g_fhs if fh["flock_house_id"] == x), ""),
            key="g_fh")

    g_records = supabase.table("daily_records") \
        .select("*") \
        .eq("flock_house_id", g_fh_id) \
        .order("record_date") \
        .execute().data

    if g_records:
        g_fh_obj   = next(fh for fh in g_fhs if fh["flock_house_id"] == g_fh_id)
        g_chick_dt = date.fromisoformat(g_fh_obj["chick_in_date"])

        df = pd.DataFrame(g_records)
        df["日齢"] = df["record_date"].apply(
            lambda d: (date.fromisoformat(d) - g_chick_dt).days)

        # Ross308標準値を追加
        df["標準採食量_g"] = df["日齢"].apply(
            lambda a: get_ross308(a).get("daily_intake_g"))
        df["標準体重_g"]   = df["日齢"].apply(
            lambda a: get_ross308(a).get("weight_g"))

        graph_item = st.selectbox("グラフ項目",
            ["体重（実績 vs 標準）", "採食量（実績 vs 標準）", "斃死+淘汰数", "温度・湿度"],
            key="g_item")

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm
        plt.rcParams["font.family"] = "DejaVu Sans"

        fig, ax = plt.subplots(figsize=(10, 4))

        if graph_item == "体重（実績 vs 標準）":
            w_df = df[df["avg_body_weight"].notna()]
            ax.plot(w_df["日齢"], w_df["avg_body_weight"], "o-", label="実績体重(g)", color="steelblue")
            ax.plot(df["日齢"], df["標準体重_g"], "--", label="Ross308標準(g)", color="orange", alpha=0.7)
            ax.set_ylabel("Body Weight (g)")
            ax.legend()

        elif graph_item == "採食量（実績 vs 標準）":
            ax.bar(df["日齢"], df["feed_intake"], label="実績採食量(kg)", color="steelblue", alpha=0.7)
            std_kg = df["標準採食量_g"].apply(
                lambda g: (g / 1000 * g_fh_obj["chick_in_count"]) if g else None)
            ax.plot(df["日齢"], std_kg, "--", label="Ross308標準(kg)", color="orange")
            ax.set_ylabel("Feed Intake (kg)")
            ax.legend()

        elif graph_item == "斃死+淘汰数":
            df["斃死+淘汰"] = df["mortality_count"] + df["culling_count"]
            ax.bar(df["日齢"], df["斃死+淘汰"], color="tomato", alpha=0.8)
            ax.set_ylabel("Count")

        elif graph_item == "温度・湿度":
            ax.plot(df["日齢"], df["house_temp_max"], "r-",  label="舎内最高℃")
            ax.plot(df["日齢"], df["house_temp_min"], "b-",  label="舎内最低℃")
            ax.plot(df["日齢"], df["house_humidity"], "g--", label="湿度%", alpha=0.7)
            ax.set_ylabel("Temp(℃) / Humidity(%)")
            ax.legend()

        ax.set_xlabel("Age (days)")
        ax.grid(True, alpha=0.3)
        st.pyplot(fig)
        plt.close()
    else:
        st.info("記録がありません")
