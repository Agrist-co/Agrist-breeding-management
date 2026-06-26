"""
ブロイラー飼養管理システム - 飼料発注予測・メール送信
複数鶏舎のタンク残量を自動計算し、発注タイミングと発注量を予測
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from supabase import create_client, Client

st.set_page_config(page_title="飼料発注予測", layout="wide")

st.markdown("""
<style>
h1 { font-size: 1.1rem !important; margin-bottom: 0.2rem !important; }
h3 { font-size: 0.95rem !important; margin-bottom: 0.2rem !important; }
.block-container { padding-top: 0.5rem !important; max-width: 100% !important; }
.stNumberInput label, .stSelectbox label, .stDateInput label { font-size: 0.78rem !important; }
.stAlert { font-size: 0.82rem !important; padding: 0.3rem 0.6rem !important; }
/* 警告色 */
.warn-red   { color: #c0392b; font-weight: 700; }
.warn-orange{ color: #e67e22; font-weight: 700; }
.warn-green { color: #27ae60; font-weight: 600; }
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

# ----------------------------------------------------------
# マスタ取得
# ----------------------------------------------------------
farms        = fetch("farms",        "farm_id")
houses       = fetch("houses",       "house_id")
lot_numbers  = fetch("lot_numbers",  "lot_number_id")
flock_houses = fetch("flock_houses", "flock_house_id")
feed_brands  = fetch("feed_brands",  "feed_brand_id")
ross308      = fetch("ross308_standard", "day")
comfort_temp = fetch("ross_comfort_temp", "body_weight_g")
correction   = fetch("feed_correction_factors", "id")

farm_map   = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts  = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map  = {h["house_id"]: h for h in houses}
ln_map     = {ln["lot_number_id"]: ln["lot_number"] for ln in lot_numbers}
brand_map  = {b["feed_brand_id"]: b["brand_name"] for b in feed_brands}
ross_dict  = {(r["sex"], r["day"]): r for r in ross308}

def get_ross308(age):
    return ross_dict.get(("as_hatched", max(0, min(int(age), 56))), {})

def get_env_correction(house_temp_avg, humidity, body_weight_g):
    """体重・温湿度から採食量補正係数を算出"""
    if not comfort_temp or house_temp_avg is None:
        return 1.0
    # 最近傍の快適温度レコードを取得
    closest = min(comfort_temp, key=lambda r: abs((r["body_weight_g"] or 0) - body_weight_g))
    # RHに応じた快適温度上限を線形補間
    rh = humidity or 60.0
    if rh <= 40:
        upper = closest["rh_40pct_temp_c"]
    elif rh <= 50:
        t40, t50 = closest["rh_40pct_temp_c"], closest["rh_50pct_temp_c"]
        upper = t40 + (t50 - t40) * (rh - 40) / 10
    elif rh <= 60:
        t50, t60 = closest["rh_50pct_temp_c"], closest["rh_60pct_temp_c"]
        upper = t50 + (t60 - t50) * (rh - 50) / 10
    else:
        t60, t70 = closest["rh_60pct_temp_c"], closest["rh_70pct_temp_c"]
        upper = t60 + (t70 - t60) * (min(rh, 70) - 60) / 10

    excess = house_temp_avg - upper
    if excess <= 0:
        return 1.00
    elif excess <= 3:
        return 0.95
    elif excess <= 6:
        return 0.88
    else:
        return 0.78

def calc_weighted_correction(flock_house_id, n=5):
    """直近N回の発注補正率から加重平均を算出（新しいほど重み大）"""
    details = supabase.table("feed_order_details")         .select("feed_correction_factor, created_at")         .eq("flock_house_id", flock_house_id)         .not_.is_("feed_correction_factor", "null")         .order("created_at", desc=True)         .limit(n).execute().data

    if not details:
        return 1.0

    # 加重平均: 直近=N, 2回前=N-1, ...
    total_weight = 0
    weighted_sum = 0.0
    for i, d in enumerate(details):
        weight = len(details) - i  # 直近ほど重み大
        factor = float(d["feed_correction_factor"] or 1.0)
        weighted_sum += factor * weight
        total_weight += weight

    return round(weighted_sum / total_weight, 4) if total_weight > 0 else 1.0


def calc_tank_status(fh):
    """鶏舎のタンク状況を計算"""
    fh_id    = fh["flock_house_id"]
    house    = house_map.get(fh["house_id"], {})
    h_coef   = float(house.get("feed_transfer_coef") or 0)

    # 日次記録を取得
    recs = supabase.table("daily_records") \
        .select("*") \
        .eq("flock_house_id", fh_id) \
        .order("record_date").execute().data

    # タンク残量 = 初回納入量 + Σ納品量 − Σ採食量
    tank = float(fh.get("initial_feed_delivery_qty") or 0)
    total_intake = 0.0
    daily_intakes = []

    for r in recs:
        # 納品量を加算
        tank += float(r.get("feed_delivery_qty") or 0)
        # 採食量を計算（採食時間 × 搬送係数 × 銘柄補正率）
        dur = r.get("feed_duration_min")
        if dur and h_coef > 0:
            brand_id  = r.get("feed_brand_id")
            brand_obj = next((b for b in feed_brands if b["feed_brand_id"] == brand_id), {}) if brand_id else {}
            ratio     = float(brand_obj.get("transfer_coef_ratio") or 1.0)
            intake_kg = float(dur) * h_coef * ratio
            tank -= intake_kg
            total_intake += intake_kg
            daily_intakes.append(intake_kg)

    # 直近7日の日平均採食量
    recent = daily_intakes[-7:] if len(daily_intakes) >= 7 else daily_intakes
    avg_daily = sum(recent) / len(recent) if recent else 0.0

    # 残存羽数
    chick_in   = fh["chick_in_count"] or 0
    spare      = fh["spare_count"]    or 0
    total_mort = sum(r.get("mortality_count") or 0 for r in recs)
    total_cull = sum(r.get("culling_count")   or 0 for r in recs)
    remaining  = chick_in + spare - total_mort - total_cull

    # 現在日齢
    chick_date = date.fromisoformat(fh["chick_in_date"])
    today_age  = (date.today() - chick_date).days

    # 最新の環境データ（直近3日平均）
    recent_recs = recs[-3:] if recs else []
    if recent_recs:
        avg_temp = sum(
            ((r.get("house_temp_max") or 0) + (r.get("house_temp_min") or 0)) / 2
            for r in recent_recs if r.get("house_temp_max")
        ) / max(len([r for r in recent_recs if r.get("house_temp_max")]), 1)
        avg_hum = sum(r.get("house_humidity") or 60 for r in recent_recs) / len(recent_recs)
    else:
        avg_temp = None
        avg_hum  = 60.0

    # 採食量補正率（加重平均）を取得
    feed_corr  = float(fh.get("feed_correction_factor") or 1.0)

    # 予測採食量（Ross308標準 × 環境補正 × 採食量補正率）
    ross       = get_ross308(today_age)
    std_intake = ross.get("daily_intake_g") or 0
    body_wt    = ross.get("weight_g") or 1000
    env_factor = get_env_correction(avg_temp, avg_hum, body_wt)
    pred_daily = std_intake / 1000 * remaining * env_factor * feed_corr if std_intake > 0 else avg_daily

    # 在庫日数（実績or予測採食量で計算）
    use_daily  = avg_daily if avg_daily > 0 else pred_daily
    stock_days = round(tank / use_daily, 1) if use_daily > 0 else 99.9

    return {
        "flock_house_id":   fh_id,
        "house_name":       house_map.get(fh["house_id"], {}).get("house_name", ""),
        "tank_number":      house.get("tank_number", "-"),
        "tank_capacity":    float(house.get("tank_capacity") or 0),
        "remaining":        remaining,
        "today_age":        today_age,
        "planned_age":      fh.get("planned_shipment_age_days") or 56,
        "tank_remaining":   round(tank, 2),
        "avg_daily":        round(avg_daily, 2),
        "pred_daily":       round(pred_daily, 2),
        "use_daily":        round(use_daily, 2),
        "env_factor":       env_factor,
        "feed_corr":        feed_corr,
        "stock_days":       stock_days,
        "h_coef":           h_coef,
        "status":           fh.get("status", ""),
    }

# ----------------------------------------------------------
# UI
# ----------------------------------------------------------
st.title("🚛 飼料発注予測")

tab1, tab2 = st.tabs(["📊 発注予測", "📧 メール設定"])

# ==========================================================
# タブ1: 発注予測
# ==========================================================
with tab1:
    if not farms:
        st.warning("農場マスタを登録してください")
        st.stop()

    # ---- パラメータ設定 ----
    st.markdown("#### ⚙️ 発注パラメータ")
    p1, p2, p3, p4 = st.columns(4)
    with p1:
        sel_farm    = st.selectbox("農場", list(farm_opts.keys()), key="fo_farm")
        sel_farm_id = farm_opts[sel_farm]
    with p2:
        lead_time = st.number_input("リードタイム（日）",
            min_value=1, max_value=14, value=3, step=1, key="fo_lead")
    with p3:
        order_qty = st.number_input("基本発注量（kg）",
            min_value=0.0, step=500.0, value=5000.0, key="fo_qty")
    with p4:
        order_date = st.date_input("発注予定日",
            value=date.today(), key="fo_date")

    delivery_date = order_date + timedelta(days=lead_time)
    st.caption(f"納品予定日: **{delivery_date}**（発注日 + {lead_time}日）")

    # ---- 鶏舎別タンク状況を計算 ----
    farm_lns = [ln for ln in lot_numbers if ln["farm_id"] == sel_farm_id]
    farm_fhs = [fh for fh in flock_houses
                if any(ln["lot_number_id"] == fh["lot_number_id"] for ln in farm_lns)
                and fh["status"] == "育成中"]

    if not farm_fhs:
        st.info("育成中の鶏舎がありません")
        st.stop()

    st.markdown("#### 🏗️ 鶏舎別タンク状況")

    with st.spinner("タンク残量を計算中..."):
        statuses = [calc_tank_status(fh) for fh in farm_fhs]

    # 一覧表示
    rows = []
    for s in statuses:
        remaining_age = max(s["planned_age"] - s["today_age"], 0)
        # 在庫警告判定
        if s["stock_days"] <= lead_time:
            warn = "🔴 要発注"
        elif s["stock_days"] <= lead_time + 3:
            warn = "🟡 注意"
        else:
            warn = "🟢 余裕"

        rows.append({
            "鶏舎":           s["house_name"],
            "タンクNo":       s["tank_number"],
            "日齢":           s["today_age"],
            "残日齢":         remaining_age,
            "残存羽数":       f"{s['remaining']:,}",
            "タンク残量kg":   f"{s['tank_remaining']:.1f}",
            "日均採食kg":     f"{s['use_daily']:.1f}",
            "環境補正":       f"{s['env_factor']:.2f}",
            "採食補正":       f"{s['feed_corr']:.4f}",
            "在庫日数":       f"{s['stock_days']:.1f}",
            "状況":           warn,
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # ---- 発注が必要な鶏舎をチェック ----
    need_order = [s for s in statuses if s["stock_days"] <= lead_time + 3]
    safe       = [s for s in statuses if s["stock_days"] >  lead_time + 3]

    if need_order:
        st.warning(f"⚠️ {len(need_order)}棟が発注推奨（在庫{lead_time + 3}日以内）")

    # ---- 発注内容の設定 ----
    st.markdown("#### 📋 発注内容")

    # 発注する鶏舎を選択
    all_house_names = [s["house_name"] for s in statuses]
    default_sel = [s["house_name"] for s in need_order]
    sel_houses = st.multiselect(
        "発注対象鶏舎（複数選択可）",
        all_house_names,
        default=default_sel,
        key="fo_houses"
    )

    # 発注量を鶏舎ごとに設定
    order_details = []
    if sel_houses:
        st.markdown("##### 鶏舎別発注量")
        cols = st.columns(min(len(sel_houses), 4))
        total_order = 0.0
        for i, house_nm in enumerate(sel_houses):
            s = next(st_  for st_ in statuses if st_["house_name"] == house_nm)
            with cols[i % 4]:
                qty = st.number_input(
                    f"{house_nm}（在庫{s['stock_days']:.1f}日）",
                    min_value=0.0, value=order_qty, step=500.0,
                    key=f"fo_qty_{house_nm}")
                # 飼料銘柄選択
                brand_names_list = ["未指定"] + [b["brand_name"] for b in feed_brands]
                brand_sel = st.selectbox(f"{house_nm} 銘柄",
                    brand_names_list, key=f"fo_brand_{house_nm}")
                # 納品直前の実測タンク残量入力
                actual_tank = st.number_input(
                    f"{house_nm} 実測残量(kg)",
                    min_value=0.0,
                    value=float(s["tank_remaining"]),
                    step=10.0,
                    key=f"fo_actual_{house_nm}",
                    help="納品直前の実際のタンク残量を入力（補正率の計算に使用）")
                # 補正率を計算して表示
                calc_tank  = s["tank_remaining"]
                if calc_tank > 0:
                    new_corr = round(actual_tank / calc_tank, 4)
                    prev_corr = s["feed_corr"]
                    corr_color = "🟢" if 0.95 <= new_corr <= 1.05 else ("🟡" if 0.9 <= new_corr <= 1.1 else "🔴")
                    st.caption(
                        f"計算残量: {calc_tank:.1f}kg　"
                        f"今回補正率: {corr_color} **{new_corr:.4f}**　"
                        f"現在適用値: {prev_corr:.4f}")
                else:
                    new_corr = 1.0
                order_details.append({
                    "house_name":          house_nm,
                    "flock_house_id":      s["flock_house_id"],
                    "order_qty":           qty,
                    "brand_name":          brand_sel,
                    "tank_remaining":      s["tank_remaining"],
                    "actual_tank":         actual_tank,
                    "new_corr":            new_corr,
                    "daily_consumption":   s["use_daily"],
                    "stock_days":          s["stock_days"],
                })
                total_order += qty

        st.divider()

        # ---- 発注サマリ ----
        st.markdown("#### 📦 発注サマリ")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("発注棟数",   f"{len(sel_houses)} 棟")
        s2.metric("合計発注量", f"{total_order:,.0f} kg")
        s3.metric("発注予定日", str(order_date))
        s4.metric("納品予定日", str(delivery_date))

        # 発注書プレビュー
        st.markdown("#### 📄 発注書プレビュー")
        preview_lines = [
            f"【飼料発注】{sel_farm}",
            f"発注日: {order_date}　納品希望日: {delivery_date}",
            f"",
        ]
        for d in order_details:
            brand_str = f"（{d['brand_name']}）" if d["brand_name"] != "未指定" else ""
            preview_lines.append(
                f"  {d['house_name']}: {d['order_qty']:,.0f} kg {brand_str}"
                f"  ※タンク残: {d['tank_remaining']:.1f}kg / 在庫{d['stock_days']:.1f}日"
            )
        preview_lines += [
            f"",
            f"合計: {total_order:,.0f} kg",
            f"",
            f"よろしくお願いいたします。",
            f"{sel_farm}",
        ]
        preview_text = "\n".join(preview_lines)
        st.text_area("発注書", value=preview_text, height=200, key="fo_preview")

        # ---- 発注登録 ----
        st.divider()
        bc1, bc2 = st.columns([1, 4])
        with bc1:
            if st.button("💾 発注を登録", type="primary", key="fo_save"):
                try:
                    # feed_ordersに登録
                    res = supabase.table("feed_orders").insert({
                        "farm_id":         sel_farm_id,
                        "order_date":      str(order_date),
                        "delivery_date":   str(delivery_date),
                        "lead_time_days":  lead_time,
                        "total_order_qty": total_order,
                        "status":          "発注済",
                    }).execute()
                    order_id = res.data[0]["order_id"]

                    # feed_order_detailsに明細登録 + 補正率を更新
                    for d in order_details:
                        brand_id = next(
                            (b["feed_brand_id"] for b in feed_brands
                             if b["brand_name"] == d["brand_name"]), None)
                        supabase.table("feed_order_details").insert({
                            "order_id":               order_id,
                            "flock_house_id":         d["flock_house_id"],
                            "feed_brand_id":          brand_id,
                            "order_qty":              d["order_qty"],
                            "tank_remaining":         d["tank_remaining"],
                            "actual_tank_remaining":  d["actual_tank"],
                            "calc_tank_remaining":    d["tank_remaining"],
                            "feed_correction_factor": d["new_corr"],
                            "daily_consumption":      d["daily_consumption"],
                            "stock_days":             d["stock_days"],
                        }).execute()

                        # flock_housesの補正率を加重平均で更新
                        new_weighted = calc_weighted_correction(d["flock_house_id"], n=5)
                        # 今回の値を含めた再計算（最新履歴が反映された後）
                        supabase.table("flock_houses").update({
                            "feed_correction_factor": new_weighted
                        }).eq("flock_house_id", d["flock_house_id"]).execute()

                    st.session_state["fo_order_id"]    = order_id
                    st.session_state["fo_preview_text"] = preview_text
                    st.session_state["fo_total"]        = total_order
                    st.success(f"✅ 発注を登録しました（発注ID: {order_id}）")
                except Exception as e:
                    st.error(f"登録エラー: {e}")

        with bc2:
            # メール送信セクション
            if "fo_order_id" in st.session_state:
                st.markdown("#### 📧 メール送信")
                email_settings = fetch("email_settings", "setting_id")
                farm_settings  = [es for es in email_settings
                                  if es["farm_id"] == sel_farm_id or es["farm_id"] is None]

                if not farm_settings:
                    st.warning("メール設定がありません。「📧 メール設定」タブで登録してください。")
                else:
                    setting_opts = {es["setting_name"]: es for es in farm_settings}
                    sel_setting  = st.selectbox("送信先設定", list(setting_opts.keys()), key="fo_email_sel")
                    es           = setting_opts[sel_setting]

                    to_addr  = st.text_input("宛先", value=es["to_address"] or "", key="fo_to")
                    cc_addr  = st.text_input("CC",  value=es["cc_address"]  or "", key="fo_cc")
                    subject  = st.text_input("件名",
                        value=f"【飼料発注】{sel_farm} {order_date}",
                        key="fo_subject")
                    body     = st.text_area("本文",
                        value=st.session_state.get("fo_preview_text", ""),
                        height=180, key="fo_body")

                    if st.button("📧 メール送信", key="fo_send", type="primary"):
                        try:
                            import smtplib
                            from email.mime.text import MIMEText
                            from email.mime.multipart import MIMEMultipart

                            smtp_host = st.secrets.get("smtp", {}).get("host", "")
                            smtp_port = int(st.secrets.get("smtp", {}).get("port", 587))
                            smtp_user = st.secrets.get("smtp", {}).get("user", "")
                            smtp_pass = st.secrets.get("smtp", {}).get("password", "")

                            if not smtp_host:
                                st.error("SMTP設定がありません。Secretsに[smtp]セクションを追加してください。")
                            else:
                                msg = MIMEMultipart()
                                msg["From"]    = smtp_user
                                msg["To"]      = to_addr
                                if cc_addr:
                                    msg["Cc"] = cc_addr
                                msg["Subject"] = subject
                                msg.attach(MIMEText(body, "plain", "utf-8"))

                                recipients = [a.strip() for a in to_addr.split(",")]
                                if cc_addr:
                                    recipients += [a.strip() for a in cc_addr.split(",")]

                                with smtplib.SMTP(smtp_host, smtp_port) as server:
                                    server.starttls()
                                    server.login(smtp_user, smtp_pass)
                                    server.sendmail(smtp_user, recipients, msg.as_string())

                                st.success(f"✅ メールを送信しました → {to_addr}")
                        except Exception as e:
                            st.error(f"送信エラー: {e}")

    # ---- 発注履歴 ----
    st.divider()
    st.markdown("#### 📜 発注履歴")
    orders = supabase.table("feed_orders") \
        .select("*").eq("farm_id", sel_farm_id) \
        .order("order_date", desc=True).limit(10).execute().data
    if orders:
        df_ord = pd.DataFrame(orders)[
            ["order_id","order_date","delivery_date","total_order_qty","status","remarks"]]
        df_ord.columns = ["発注ID","発注日","納品予定日","合計量kg","状況","備考"]
        st.dataframe(df_ord, use_container_width=True, hide_index=True)
    else:
        st.info("発注履歴がありません")

# ==========================================================
# タブ2: メール設定
# ==========================================================
with tab2:
    st.markdown("#### 📧 メール送信先設定")

    email_settings = fetch("email_settings", "setting_id")
    mode = st.radio("操作", ["新規登録", "編集", "削除"], horizontal=True, key="es_mode")

    col1, col2 = st.columns([1, 1])
    with col1:
        if mode == "新規登録":
            st.markdown("##### ➕ 新規登録")
            es_farm    = st.selectbox("農場（任意・未選択は全農場共通）",
                ["全農場共通"] + list(farm_opts.keys()), key="es_farm")
            es_name    = st.text_input("設定名（例: ○○飼料会社）", key="es_name")
            es_to      = st.text_input("宛先メールアドレス（複数はカンマ区切り）", key="es_to")
            es_cc      = st.text_input("CC（任意）", key="es_cc")
            es_subject = st.text_input("件名テンプレート", key="es_subject",
                value="【飼料発注】{farm_name} {order_date}")
            es_body    = st.text_area("本文テンプレート", key="es_body",
                value="お世話になります。\n以下の通り飼料を発注します。\n\n{order_details}\n\n合計: {total_qty}kg\n\nよろしくお願いいたします。",
                height=120)

            if st.button("登録", key="es_add", type="primary"):
                if not es_name or not es_to:
                    st.error("設定名と宛先は必須です")
                else:
                    try:
                        supabase.table("email_settings").insert({
                            "farm_id":          farm_opts.get(es_farm) if es_farm != "全農場共通" else None,
                            "setting_name":     es_name,
                            "to_address":       es_to,
                            "cc_address":       es_cc or None,
                            "subject_template": es_subject,
                            "body_template":    es_body,
                            "is_active":        True,
                        }).execute()
                        st.success(f"✅ 「{es_name}」を登録しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

        elif mode == "編集" and email_settings:
            st.markdown("##### ✏️ 編集")
            sel_es_id = st.selectbox("編集する設定",
                [es["setting_id"] for es in email_settings],
                format_func=lambda x: next(es["setting_name"] for es in email_settings if es["setting_id"] == x),
                key="es_edit_id")
            t = next(es for es in email_settings if es["setting_id"] == sel_es_id)
            new_name = st.text_input("設定名", value=t["setting_name"], key="es_edit_name")
            new_to   = st.text_input("宛先", value=t["to_address"] or "", key="es_edit_to")
            new_cc   = st.text_input("CC",   value=t["cc_address"]  or "", key="es_edit_cc")
            new_active = st.checkbox("有効", value=t["is_active"], key="es_edit_active")
            if st.button("更新", key="es_update", type="primary"):
                try:
                    supabase.table("email_settings").update({
                        "setting_name": new_name,
                        "to_address":   new_to,
                        "cc_address":   new_cc or None,
                        "is_active":    new_active,
                    }).eq("setting_id", sel_es_id).execute()
                    st.success("✅ 更新しました")
                    st.rerun()
                except Exception as e:
                    st.error(f"更新エラー: {e}")

        elif mode == "削除" and email_settings:
            st.markdown("##### 🗑️ 削除")
            del_es_id = st.selectbox("削除する設定",
                [es["setting_id"] for es in email_settings],
                format_func=lambda x: next(es["setting_name"] for es in email_settings if es["setting_id"] == x),
                key="es_del_id")
            if st.button("削除", key="es_del", type="primary"):
                try:
                    supabase.table("email_settings").delete() \
                        .eq("setting_id", del_es_id).execute()
                    st.success("✅ 削除しました")
                    st.rerun()
                except Exception as e:
                    st.error(f"削除エラー: {e}")

    with col2:
        st.markdown("##### 📋 登録済み設定一覧")
        if email_settings:
            df_es = pd.DataFrame(email_settings)[
                ["setting_id","setting_name","to_address","cc_address","is_active"]]
            df_es.columns = ["ID","設定名","宛先","CC","有効"]
            st.dataframe(df_es, use_container_width=True, hide_index=True)
        else:
            st.info("まだ設定がありません")

    st.divider()
    st.markdown("#### 🔧 SMTP設定（Secretsに追加）")
    st.code("""
# Streamlit Cloud の Secrets に以下を追加してください
[smtp]
host     = "smtp.gmail.com"        # GmailのSMTPサーバー
port     = 587
user     = "your@gmail.com"        # 送信元アドレス
password = "your_app_password"     # Googleアプリパスワード
""", language="toml")
    st.caption("Gmailの場合は「アプリパスワード」を使用してください（2段階認証が必要）")
