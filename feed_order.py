"""
飼料発注管理 - 農場単位での一括発注（予定配送ベース）
daily_sheet.py で登録した予定配送を集約してメール/FAX送信
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from supabase import create_client, Client

st.set_page_config(page_title="飼料発注管理", layout="wide")

st.markdown("""
<style>
h1 { font-size: 1.1rem !important; margin-bottom: 0.2rem !important; }
h3, h4 { font-size: 0.95rem !important; margin-bottom: 0.2rem !important; }
.block-container { padding-top: 0.5rem !important; max-width: 100% !important; }
.stNumberInput label, .stSelectbox label, .stDateInput label { font-size: 0.78rem !important; }
.stAlert { font-size: 0.82rem !important; padding: 0.3rem 0.6rem !important; }
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

farm_map   = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts  = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map  = {h["house_id"]: h.get("house_name","") for h in houses}
brand_map  = {b["feed_brand_id"]: b["brand_name"] for b in feed_brands}
ln_map     = {ln["lot_number_id"]: ln for ln in lot_numbers}

# ----------------------------------------------------------
# UI
# ----------------------------------------------------------
st.title("🚛 飼料発注管理")

tab1, tab2, tab3 = st.tabs(["📦 一括発注", "📜 発注履歴", "📧 メール設定"])

# ==========================================================
# タブ1: 一括発注
# ==========================================================
with tab1:
    if not farms:
        st.warning("農場マスタを登録してください")
        st.stop()

    # ---- 農場・期間選択 ----
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_farm    = st.selectbox("農場", list(farm_opts.keys()), key="fo_farm")
        sel_farm_id = farm_opts[sel_farm]
    with c2:
        lead_time = st.number_input("リードタイム（日）",
            min_value=1, max_value=14, value=3, step=1, key="fo_lead")
    with c3:
        range_from = st.date_input("発注範囲（開始）",
            value=date.today(), key="fo_from")
    with c4:
        range_to = st.date_input("発注範囲（終了）",
            value=date.today() + timedelta(days=14), key="fo_to")

    # ---- 農場内の育成中鶏舎を取得 ----
    farm_ln_ids = {ln["lot_number_id"] for ln in lot_numbers if ln["farm_id"] == sel_farm_id}
    farm_fhs    = [fh for fh in flock_houses
                   if fh["lot_number_id"] in farm_ln_ids and fh.get("status") == "育成中"]

    if not farm_fhs:
        st.info("育成中の鶏舎がありません")
        st.stop()

    # ---- 予定配送を取得（order_id=NULLの予定データ） ----
    fh_ids = [fh["flock_house_id"] for fh in farm_fhs]

    all_details = []
    for fh_id in fh_ids:
        details = supabase.table("feed_order_details") \
            .select("*") \
            .eq("flock_house_id", fh_id) \
            .is_("order_id", "null") \
            .eq("status", "予定") \
            .order("delivery_date").execute().data
        for d in details:
            fh = next((f for f in farm_fhs if f["flock_house_id"] == fh_id), {})
            ln = ln_map.get(fh.get("lot_number_id"), {})
            d["house_name"] = house_map.get(fh.get("house_id"), "")
            d["lot_number"]  = ln.get("lot_number", "")
            all_details.append(d)

    if not all_details:
        st.info("予定配送が登録されていません。各鶏舎の発注予測画面で「予定配送を登録・更新」を実行してください。")
        st.stop()

    # ---- 期間フィルタ ----
    df_all = pd.DataFrame(all_details)
    df_all["delivery_date_dt"] = pd.to_datetime(df_all["delivery_date"]).dt.date
    df_sel = df_all[
        (df_all["delivery_date_dt"] >= range_from) &
        (df_all["delivery_date_dt"] <= range_to)
    ].copy()

    if df_sel.empty:
        st.info(f"指定期間（{range_from}〜{range_to}）に予定配送がありません")
    else:
        # ---- 予定配送一覧 ----
        st.markdown("#### 📋 予定配送一覧")
        disp = df_sel[[
            "house_name","lot_number","delivery_date","day_age","order_qty","event_notes","pred_tank"
        ]].copy()
        disp.columns = ["鶏舎","ロット","納品予定日","日齢","発注量kg","発注内容","予測残量kg"]
        total_qty = df_sel["order_qty"].sum()
        st.dataframe(disp, use_container_width=True, hide_index=True)
        st.markdown(f"**対象: {len(df_sel)}件　合計: {total_qty:,.0f} kg**")

        # ---- 発注書プレビュー ----
        st.markdown("#### 📄 発注書プレビュー")
        order_date    = st.date_input("発注日", value=date.today(), key="fo_order_date")
        preview_lines = [
            f"【飼料発注】{sel_farm}",
            f"発注日: {order_date}",
            "",
        ]
        for _, r in df_sel.sort_values("delivery_date").iterrows():
            preview_lines.append(
                f"  納品予定日: {r['delivery_date']}（{r['house_name']} 日齢{int(r['day_age'] or 0)}日）"
                f"　{r['order_qty']:,.0f} kg　{r['event_notes'] or ''}"
            )
        preview_lines += [
            "",
            f"合計: {total_qty:,.0f} kg",
            "",
            "よろしくお願いいたします。",
            sel_farm,
        ]
        preview_text = "\n".join(preview_lines)
        body_text = st.text_area("発注書", value=preview_text, height=250, key="fo_preview")

        # ---- 発注登録・送信 ----
        st.divider()
        bc1, bc2 = st.columns([1, 3])

        with bc1:
            if st.button("💾 発注確定登録", type="primary", key="fo_save"):
                try:
                    # feed_ordersにヘッダー登録
                    res = supabase.table("feed_orders").insert({
                        "farm_id":         sel_farm_id,
                        "order_date":      str(order_date),
                        "delivery_date":   str(range_from),
                        "lead_time_days":  lead_time,
                        "total_order_qty": float(total_qty),
                        "status":          "発注済",
                    }).execute()
                    order_id = res.data[0]["order_id"]

                    # 予定配送をorder_idで更新（発注済みに変更）
                    for detail_id in df_sel["detail_id"].tolist():
                        supabase.table("feed_order_details").update({
                            "order_id": order_id,
                            "status":   "発注済",
                        }).eq("detail_id", detail_id).execute()

                    st.session_state["fo_order_id"]    = order_id
                    st.session_state["fo_preview_text"] = body_text
                    st.session_state["fo_total"]        = total_qty
                    st.success(f"✅ 発注を登録しました（発注ID: {order_id}）")
                    st.rerun()
                except Exception as e:
                    st.error(f"登録エラー: {e}")

        with bc2:
            if "fo_order_id" in st.session_state:
                st.markdown("#### 📧 メール送信")
                email_settings = fetch("email_settings", "setting_id")
                farm_settings  = [es for es in email_settings
                                  if es.get("farm_id") == sel_farm_id or es.get("farm_id") is None]

                if not farm_settings:
                    st.warning("メール設定がありません。「📧 メール設定」タブで登録してください。")
                else:
                    setting_opts = {es["setting_name"]: es for es in farm_settings}
                    sel_setting  = st.selectbox("送信先設定",
                        list(setting_opts.keys()), key="fo_email_sel")
                    es      = setting_opts[sel_setting]
                    to_addr = st.text_input("宛先", value=es.get("to_address",""), key="fo_to")
                    cc_addr = st.text_input("CC",  value=es.get("cc_address",""),  key="fo_cc")
                    subject = st.text_input("件名",
                        value=f"【飼料発注】{sel_farm} {order_date}", key="fo_subject")
                    body    = st.text_area("本文",
                        value=st.session_state.get("fo_preview_text",""),
                        height=200, key="fo_body")

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
                                st.error("SMTP設定がありません")
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

# ==========================================================
# タブ2: 発注履歴
# ==========================================================
with tab2:
    st.markdown("#### 📜 発注履歴")

    if not farms:
        st.stop()

    sel_farm_h    = st.selectbox("農場", list(farm_opts.keys()), key="fh_farm")
    sel_farm_id_h = farm_opts[sel_farm_h]

    orders = supabase.table("feed_orders") \
        .select("*").eq("farm_id", sel_farm_id_h) \
        .order("order_date", desc=True).limit(20).execute().data

    if orders:
        df_ord = pd.DataFrame(orders)[[
            "order_id","order_date","delivery_date","total_order_qty","status","remarks"]]
        df_ord.columns = ["発注ID","発注日","納品予定日","合計量kg","状況","備考"]
        st.dataframe(df_ord, use_container_width=True, hide_index=True)

        # 選択した発注の明細を表示
        sel_ord_id = st.selectbox("発注IDを選択して明細を確認",
            [o["order_id"] for o in orders],
            format_func=lambda x: f"発注ID {x} ({next(o['order_date'] for o in orders if o['order_id']==x)})",
            key="fh_ord_id")

        details = supabase.table("feed_order_details") \
            .select("*").eq("order_id", sel_ord_id).execute().data
        if details:
            df_det = pd.DataFrame(details)
            df_det["鶏舎"] = df_det["flock_house_id"].apply(
                lambda x: house_map.get(
                    next((fh["house_id"] for fh in flock_houses if fh["flock_house_id"]==x), None), ""))
            df_det["銘柄"] = df_det["feed_brand_id"].map(brand_map).fillna("-")
            disp_det = df_det[["鶏舎","delivery_date","day_age","order_qty","event_notes","銘柄","pred_tank","status"]]
            disp_det.columns = ["鶏舎","納品予定日","日齢","発注量kg","発注内容","銘柄","予測残量kg","状況"]
            st.dataframe(disp_det, use_container_width=True, hide_index=True)
    else:
        st.info("発注履歴がありません")

# ==========================================================
# タブ3: メール設定
# ==========================================================
with tab3:
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

            if st.button("登録", key="es_add", type="primary"):
                if not es_name or not es_to:
                    st.error("設定名と宛先は必須です")
                else:
                    try:
                        supabase.table("email_settings").insert({
                            "farm_id":      farm_opts.get(es_farm) if es_farm != "全農場共通" else None,
                            "setting_name": es_name,
                            "to_address":   es_to,
                            "cc_address":   es_cc or None,
                            "is_active":    True,
                        }).execute()
                        st.success(f"✅ 「{es_name}」を登録しました")
                        st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

        elif mode == "編集" and email_settings:
            sel_es_id = st.selectbox("編集する設定",
                [es["setting_id"] for es in email_settings],
                format_func=lambda x: next(es["setting_name"] for es in email_settings if es["setting_id"]==x),
                key="es_edit_id")
            t = next(es for es in email_settings if es["setting_id"] == sel_es_id)
            new_name = st.text_input("設定名", value=t["setting_name"], key="es_edit_name")
            new_to   = st.text_input("宛先",   value=t.get("to_address",""), key="es_edit_to")
            new_cc   = st.text_input("CC",     value=t.get("cc_address",""), key="es_edit_cc")
            if st.button("更新", key="es_update", type="primary"):
                try:
                    supabase.table("email_settings").update({
                        "setting_name": new_name,
                        "to_address":   new_to,
                        "cc_address":   new_cc or None,
                    }).eq("setting_id", sel_es_id).execute()
                    st.success("✅ 更新しました")
                    st.rerun()
                except Exception as e:
                    st.error(f"更新エラー: {e}")

        elif mode == "削除" and email_settings:
            del_es_id = st.selectbox("削除する設定",
                [es["setting_id"] for es in email_settings],
                format_func=lambda x: next(es["setting_name"] for es in email_settings if es["setting_id"]==x),
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
            df_es = pd.DataFrame(email_settings)[[
                "setting_id","setting_name","to_address","cc_address"]]
            df_es.columns = ["ID","設定名","宛先","CC"]
            st.dataframe(df_es, use_container_width=True, hide_index=True)
        else:
            st.info("まだ設定がありません")

    st.divider()
    st.markdown("#### 🔧 SMTP設定（Secretsに追加）")
    st.code("""
[smtp]
host     = "smtp.gmail.com"
port     = 587
user     = "your@gmail.com"
password = "your_app_password"
""", language="toml")
