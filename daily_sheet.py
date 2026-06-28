"""
ブロイラー飼養管理システム - Excel風日次入力シート（PC専用）
日齢0〜出荷日齢の全行を一覧表示・直接入力
"""

import streamlit as st
import pandas as pd
import numpy as np
import math
from datetime import date, timedelta
from supabase import create_client, Client

st.set_page_config(page_title="日次入力シート", layout="wide")

st.markdown("""
<style>
h1 { font-size: 1.1rem !important; margin-bottom: 0.2rem !important; }
.block-container { padding-top: 0.5rem !important; padding-bottom: 0.3rem !important; }
.stSelectbox label, .stNumberInput label { font-size: 0.75rem !important; }

/* ヘッダー情報ボックス（少し大きく） */
.header-box {
    background: #f0f4f8;
    border: 1px solid #c8d6e5;
    border-radius: 6px;
    padding: 8px 14px;
    margin-bottom: 6px;
    font-size: 0.95rem;
    line-height: 2.0;
}
.header-box b { color: #1a5276; font-size: 0.88rem; }
.header-val { color: #111; font-weight: 700; font-size: 0.98rem; }

/* data_editorの列ヘッダー（表題）を小さく */
div[data-testid="stDataFrameResizable"] th,
div[data-testid="stDataFrameResizable"] [data-testid="column-header-cell"] {
    font-size: 0.68rem !important;
    padding: 2px 4px !important;
    line-height: 1.2 !important;
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

# ----------------------------------------------------------
# マスタ取得
# ----------------------------------------------------------
farms        = fetch("farms",        "farm_id")
houses       = fetch("houses",       "house_id")
lot_numbers  = fetch("lot_numbers",  "lot_number_id")
flock_houses = fetch("flock_houses", "flock_house_id")
feed_brands  = fetch("feed_brands",  "feed_brand_id")
workers      = fetch("workers",      "worker_id")
ross308      = fetch("ross308_standard", "day")
comfort_temp = fetch("ross_comfort_temp", "body_weight_g")
ross308      = fetch("ross308_standard", "day")

farm_map    = {f["farm_id"]:  f["farm_name"]  for f in farms}
farm_opts   = {f["farm_name"]: f["farm_id"]   for f in farms}
house_map   = {h["house_id"]: h["house_name"] for h in houses}
tank_no_map = {h["house_id"]: h.get("tank_number", "-") for h in houses}
# flock_house_id → tank_number のマップ
fh_tank_map = {
    fh["flock_house_id"]: tank_no_map.get(fh["house_id"], "-")
    for fh in flock_houses
}
ln_map      = {ln["lot_number_id"]: ln["lot_number"] for ln in lot_numbers}
brand_map   = {b["feed_brand_id"]: b["brand_name"]   for b in feed_brands}
brand_opts  = {b["brand_name"]: b["feed_brand_id"]   for b in feed_brands}
worker_map  = {w["worker_id"]: w["worker_name"]       for w in workers}
worker_opts = {w["worker_name"]: w["worker_id"]       for w in workers}
ross_dict   = {(r["sex"], r["day"]): r                for r in ross308}

def get_ross308(age):
    max_day = max(k[1] for k in ross_dict.keys()) if ross_dict else 56
    return ross_dict.get(("as_hatched", max(0, min(int(age), max_day))), {})

def get_brand_for_age(age_days, active_brands):
    matched = [b for b in active_brands
               if (b.get("age_from_days") or 0) <= age_days
               and (b.get("age_to_days") is None or age_days <= b.get("age_to_days"))]
    return max(matched, key=lambda b: b.get("age_from_days") or 0) if matched else None

def get_env_correction(house_temp_avg, humidity, body_weight_g):
    if not comfort_temp or house_temp_avg is None:
        return 1.0
    closest = min(comfort_temp, key=lambda r: abs((r["body_weight_g"] or 0) - body_weight_g))
    rh = humidity or 60.0
    if rh <= 40:   upper = closest["rh_40pct_temp_c"]
    elif rh <= 50: upper = closest["rh_40pct_temp_c"] + (closest["rh_50pct_temp_c"] - closest["rh_40pct_temp_c"]) * (rh - 40) / 10
    elif rh <= 60: upper = closest["rh_50pct_temp_c"] + (closest["rh_60pct_temp_c"] - closest["rh_50pct_temp_c"]) * (rh - 50) / 10
    else:          upper = closest["rh_60pct_temp_c"] + (closest["rh_70pct_temp_c"] - closest["rh_60pct_temp_c"]) * (min(rh, 70) - 60) / 10
    excess = house_temp_avg - upper
    if excess <= 0:   return 1.00
    elif excess <= 3: return 0.95
    elif excess <= 6: return 0.88
    else:             return 0.78

def run_feed_forecast(fh, recs, house_coef, std_qty, min_alert, lead_time, adj_dict=None):
    """出荷日齢までのタンク残量・発注予測を実行"""
    chick_date   = date.fromisoformat(fh["chick_in_date"])
    shipping_age = fh.get("planned_shipment_age_days") or 56
    chick_in     = fh["chick_in_count"] or 0
    spare        = fh["spare_count"]    or 0
    total_birds  = chick_in + spare
    active_brs   = [b for b in feed_brands if b.get("is_active") not in (None, False, 0, "false", "0", "")]
    weighted_corr = float(fh.get("feed_correction_factor") or 1.0)

    # 初回投入量: fh設定値 → 0日齢daily_records納品量 → adj_dict[0] の順で取得
    first_qty = float(fh.get("initial_feed_delivery_qty") or 0)
    if first_qty == 0:
        chick_date_tmp = date.fromisoformat(fh["chick_in_date"])
        day0_rec = next(
            (r for r in recs
             if (date.fromisoformat(r["record_date"]) - chick_date_tmp).days == 0),
            None)
        if day0_rec and day0_rec.get("feed_delivery_qty"):
            first_qty = float(day0_rec["feed_delivery_qty"])
    if first_qty == 0 and adj_dict and 0 in adj_dict and (adj_dict[0] or {}).get("delivered"):
        first_qty = float(adj_dict[0]["delivered"])

    # 前期/中期/仕上の銘柄ステージ（発注内容の表示用のみに使用）
    brand_stages = sorted(active_brs, key=lambda b: b.get("age_from_days") or 0)

    # 日次記録をday→recordに変換
    rec_by_day = {}
    for r in recs:
        d = (date.fromisoformat(r["record_date"]) - chick_date).days
        rec_by_day[d] = r

    # 環境データ（直近3日平均）
    recent = recs[-3:] if recs else []
    avg_temp, avg_hum = None, 60.0
    if recent:
        temps = [((r.get("house_temp_max") or 0) + (r.get("house_temp_min") or 0)) / 2
                 for r in recent if r.get("house_temp_max")]
        if temps: avg_temp = sum(temps) / len(temps)
        hums = [r.get("house_humidity") or 60 for r in recent]
        avg_hum = sum(hums) / len(hums)

    # 実績確定日（実測タンク残量がある発注明細）
    order_details = supabase.table("feed_order_details")         .select("*, feed_orders(delivery_date)")         .eq("flock_house_id", fh["flock_house_id"])         .not_.is_("actual_tank_remaining", "null")         .execute().data
    act_dict = {}
    for od in order_details:
        dd = (od.get("feed_orders") or {}).get("delivery_date")
        if dd:
            day = (date.fromisoformat(dd) - chick_date).days
            act_dict[day] = {
                "actual_tank": float(od["actual_tank_remaining"]),
                "delivered":   float(od["order_qty"] or 0),
            }

    # adj_dictのactual_tank: ユーザー入力による実測残量補正（Supabaseとは独立）
    adj_tank_map = {
        int(day): float(v["actual_tank"])
        for day, v in (adj_dict or {}).items()
        if v.get("actual_tank") is not None and int(day) > 0  # 0日齢はfirst_qty固定
    }
    # 調整発注辞書（未来日の発注量を上書き）
    adj_delivery_map = {
        int(day): float(v["delivered"])
        for day, v in (adj_dict or {}).items()
        if v.get("delivered") and float(v["delivered"]) > 0
    }

    # 全日齢の標準採食量（環境補正＋加重平均補正込み）
    # 出荷前日18時給餌停止: 出荷前日を0.75掛け、出荷日は行自体を含めない
    days = list(range(0, shipping_age))  # 出荷日(shipping_age)は含まない
    std_feed = []
    for d in days:
        r   = get_ross308(d)
        std = (r.get("daily_intake_g") or 0) * total_birds / 1000
        env = get_env_correction(avg_temp, avg_hum, r.get("weight_g") or 1000)
        kg  = std * env * weighted_corr
        if d == shipping_age - 1: kg *= 0.75  # 出荷前日18時給餌停止
        std_feed.append(kg)

    df = pd.DataFrame({"day": days, "std_feed_kg": std_feed})
    df["date_obj"] = [chick_date + timedelta(days=d) for d in days]
    df["date_str"] = [d.strftime("%m/%d") for d in df["date_obj"]]

    # 区間補正率
    # act_dict（Supabase）とadj_tank_map（手入力）を統合してタンク残量確定点を作成
    adj_rates   = np.ones(len(df))
    actual_feed = df["std_feed_kg"].values.copy()

    # 日次記録がある日の実績採食を先にactual_feedに反映
    # （補正率計算の分母を「予測残量ベースの消費量」と一致させるため）
    for d, r in rec_by_day.items():
        if d in day_to_idx if False else True:  # day_to_idxはまだ未定義なのでrecで直接
            pass
    # ※day_to_idxは後で定義するため、ここではrec_by_dayをインデックスで直接処理
    for idx_pre, day_val in enumerate(days):
        r_pre = rec_by_day.get(day_val, {})
        if r_pre and house_coef > 0 and r_pre.get("feed_duration_min"):
            bid_pre  = r_pre.get("feed_brand_id")
            bobj_pre = next((b for b in feed_brands if b["feed_brand_id"] == bid_pre), {})                        if bid_pre else get_brand_for_age(day_val, active_brs) or {}
            ratio_pre = float(bobj_pre.get("transfer_coef_ratio") or 1.0)
            actual_feed[idx_pre] = float(r_pre["feed_duration_min"]) * house_coef * ratio_pre

    # 統合タンク残量辞書: 0日齢(first_qty)を起点に、act_dict→adj_tank_mapで上書き
    combined_tank = {}

    # 0日齢: initial_feed_delivery_qtyを起点として登録
    combined_tank[0] = {
        "actual_tank": first_qty,
        "delivered":   0,
    }
    # Supabase確定値
    for d, v in act_dict.items():
        if v.get("actual_tank") is not None:
            combined_tank[d] = {
                "actual_tank": v["actual_tank"],
                "delivered":   v.get("delivered", 0),
            }
    # 手入力実測残量（Supabaseを上書き、0日齢は除外）
    for d, tank_val in adj_tank_map.items():
        if d == 0:
            continue  # 0日齢はfirst_qtyで固定
        combined_tank[d] = {
            "actual_tank": tank_val,
            "delivered":   adj_delivery_map.get(d, 0),
        }

    # day→indexの逆引き辞書
    day_to_idx = {int(df.loc[i, "day"]): i for i in range(len(df))}

    sorted_act  = sorted(combined_tank.keys())
    latest_rate = weighted_corr

    if len(sorted_act) > 1:
        for i in range(len(sorted_act) - 1):
            s, e = sorted_act[i], sorted_act[i+1]
            # s_tank = 実測残量 + s日の納品量（実測後に投入される分）
            s_tank_raw = combined_tank[s]["actual_tank"]
            s_delivery = combined_tank[s].get("delivered", 0)
            s_tank     = s_tank_raw + s_delivery
            e_tank     = combined_tank[e]["actual_tank"]

            # 区間内の途中納品量（s+1〜e-1のみ、e日の納品は次区間の起点に含まれる）
            delivered_between = 0.0
            for dd in range(s + 1, e):
                if dd in combined_tank:
                    delivered_between += combined_tank[dd].get("delivered", 0)
                elif dd in rec_by_day:
                    delivered_between += float(rec_by_day[dd].get("feed_delivery_qty") or 0)

            consumed = s_tank + delivered_between - e_tank
            # 区間s〜e-1の実際の採食量合計
            # actual_feed: 日次記録がある日は実績値、ない日は標準値（補正率1.0）
            actual_cons = sum(
                actual_feed[day_to_idx[d]]
                for d in range(s, e) if d in day_to_idx
            )
            rate = consumed / actual_cons if actual_cons > 0 else 1.0
            latest_rate = rate
            for d in range(s, e):
                if d in day_to_idx:
                    idx = day_to_idx[d]
                    adj_rates[idx]   = rate
                    actual_feed[idx] = df.loc[idx, "std_feed_kg"] * rate

    # 最後の実測残量日以降（last_act含む）: 最新区間補正率を継続適用
    last_act = sorted_act[-1] if sorted_act else 0
    for d in df["day"]:
        if d >= last_act and d in day_to_idx:
            idx = day_to_idx[d]
            actual_feed[idx] = df.loc[idx, "std_feed_kg"] * latest_rate
            adj_rates[idx]   = latest_rate

    df["adj_rate"]    = adj_rates
    df["act_feed_kg"] = actual_feed

    # ----------------------------------------------------------
    # タンク残量シミュレーション
    # 発注ルール:
    #   タンク残量 ≦ min_alert(200kg) になったら配送単位(std_qty)で発注
    #   繰り返し条件: 予測採食量累計 - 予測配送量累計 > 配送単位
    #     → 再度配送単位で発注
    #   終了条件: 予測採食量累計 - 予測配送量累計 ≦ 配送単位
    #     → 最終発注量 = 予測採食量累計 - 予測配送量累計（端数）
    # ----------------------------------------------------------
    pred_tank   = np.zeros(len(df))
    real_tank   = np.full(len(df), np.nan)
    delivery_kg = np.zeros(len(df))
    event_notes = [""] * len(df)

    # 前期銘柄の総必要量（0日齢〜前期終了日の標準採食合計）
    _cur_brand_at0 = get_brand_for_age(0, active_brs)
    _cur_age_to    = _cur_brand_at0.get("age_to_days") if _cur_brand_at0 else None
    _pre_total_need = df.loc[
        df["day"].between(0, _cur_age_to if _cur_age_to is not None else shipping_age - 1),
        "std_feed_kg"
    ].sum() if _cur_brand_at0 else 0.0

    # 前期発注累計（初回投入 + 以降の前期分発注）を追跡
    _pre_delivered = [0.0]  # リストで可変参照

    def get_order_note_for_day(day, qty):
        """前期残量ベースで銘柄を決定する"""
        cur = get_brand_for_age(day, active_brs)
        if cur is None:
            return f"{qty:,.0f}kg"
        age_to = cur.get("age_to_days")

        # 既に仕上フェーズ（前期銘柄の期間を過ぎている）
        if age_to is None:
            return f"{cur['brand_name']} {qty:,.0f}kg"

        # 前期の残り必要量
        pre_remaining = max(_pre_total_need - _pre_delivered[0], 0.0)

        if pre_remaining <= 0:
            # 前期は満足済み → 全量仕上
            nxt = get_brand_for_age(age_to + 1, active_brs)
            nxt_name = nxt["brand_name"] if nxt else "仕上"
            return f"{nxt_name} {qty:,.0f}kg"
        elif pre_remaining >= qty:
            # 全量前期で足りる
            _pre_delivered[0] += qty
            return f"{cur['brand_name']} {qty:,.0f}kg"
        else:
            # 前期残り分＋仕上分に分割（100kg単位で四捨五入→1000kg単位）
            pre_qty = round(pre_remaining / 1000) * 1000
            pre_qty = max(min(pre_qty, qty), 0)
            nxt_qty = qty - pre_qty
            _pre_delivered[0] += pre_qty
            nxt = get_brand_for_age(age_to + 1, active_brs)
            nxt_name = nxt["brand_name"] if nxt else "仕上"
            if nxt_qty <= 0 or pre_qty <= 0:
                # 片方が0の場合は単独表示
                if pre_qty <= 0:
                    return f"{nxt_name} {qty:,.0f}kg"
                return f"{cur['brand_name']} {qty:,.0f}kg"
            return f"{cur['brand_name']} {pre_qty:,.0f}kg ＋ {nxt_name} {nxt_qty:,.0f}kg"

    # 0日齢: flock_houses.initial_feed_delivery_qtyを初回投入量として使用
    delivery_kg[0] = first_qty
    pred_tank[0]   = first_qty
    real_tank[0]   = first_qty
    # 0日齢は前期銘柄のみ、初回投入分を前期発注累計に加算
    _day0_brand = get_brand_for_age(0, active_brs)
    _day0_brand_name = _day0_brand["brand_name"] if _day0_brand else "前期"
    event_notes[0] = f"{_day0_brand_name} {first_qty:,.0f}kg" if first_qty > 0 else ""
    _pre_delivered[0] += first_qty  # 初回投入分を前期累計に加算
    day0_feed    = df.loc[0, "act_feed_kg"]
    evening_pred = first_qty - day0_feed

    for d in range(1, len(df)):
        daily_feed   = df.loc[d, "act_feed_kg"]
        r            = rec_by_day.get(d, {})

        # pred_tank[d] = 前日evening_predを引き継ぐ（朝のタンク残量）
        pred_tank[d] = evening_pred

        # ── 実測残量補正: adj_tank_mapに値があればpred_tank(朝残量)を実測値で上書き ──
        # 実測残量 = 納品直前の朝タンク残量 → pred_tankを補正し、採食後evening_predへ
        if d in adj_tank_map:
            real_tank[d] = adj_tank_map[d]
            pred_tank[d] = adj_tank_map[d]

        # ── 優先1: Supabase実測タンク残量あり（feed_order_details確定値） ──
        if d in act_dict and act_dict[d].get("actual_tank") is not None:
            real_tank[d]   = act_dict[d]["actual_tank"]
            pred_tank[d]   = real_tank[d]
            delivery_kg[d] = act_dict[d].get("delivered", 0)
            event_notes[d] = f"実績: 残{real_tank[d]:.0f}kg 納品{delivery_kg[d]:.0f}kg"
            evening_pred   = real_tank[d] + delivery_kg[d] - daily_feed

        # ── 優先2: 調整発注（確定発注・手動上書き） ──
        elif d in adj_delivery_map:
            adj_qty        = adj_delivery_map[d]
            delivery_kg[d] = adj_qty
            event_notes[d] = f"[確定] {get_order_note_for_day(d, adj_qty)}"
            # 採食は日次記録があれば実績、なければ標準値
            if r and house_coef > 0 and r.get("feed_duration_min"):
                bid   = r.get("feed_brand_id")
                bobj  = next((b for b in feed_brands if b["feed_brand_id"] == bid), {})                         if bid else get_brand_for_age(d, active_brs) or {}
                ratio = float(bobj.get("transfer_coef_ratio") or 1.0)
                ri    = float(r["feed_duration_min"]) * house_coef * ratio
                actual_feed[d] = ri
                evening_pred   = pred_tank[d] + adj_qty - ri
            else:
                evening_pred   = pred_tank[d] + adj_qty - daily_feed

        # ── 優先3: 日次記録あり（採食時間 or 納品量） ──
        elif r and (r.get("feed_duration_min") or r.get("feed_delivery_qty")):
            dt = float(r.get("feed_delivery_qty") or 0)
            if house_coef > 0 and r.get("feed_duration_min"):
                bid   = r.get("feed_brand_id")
                bobj  = next((b for b in feed_brands if b["feed_brand_id"] == bid), {})                         if bid else get_brand_for_age(d, active_brs) or {}
                ratio = float(bobj.get("transfer_coef_ratio") or 1.0)
                ri    = float(r["feed_duration_min"]) * house_coef * ratio
                actual_feed[d] = ri
                evening_pred   = pred_tank[d] + dt - ri
            else:
                evening_pred   = pred_tank[d] + dt - daily_feed
            if dt > 0:
                delivery_kg[d] = dt
                event_notes[d] = f"納品: {dt:.0f}kg"

        # ── 優先4: 予測発注計算（タンク残量ベース） ──
        else:
            oq = 0.0
            if pred_tank[d] <= min_alert:
                # 出荷日までの残り採食量合計
                future_need = df.loc[d:, "std_feed_kg"].sum()
                cur_tank    = pred_tank[d]
                # 今回の発注後に出荷日まで持つか判定
                # 持たない → 配送単位で発注
                # 持つ → 端数発注（最終）
                if future_need - cur_tank <= 0:
                    pass  # 発注不要
                elif future_need - (cur_tank + std_qty) <= 0:
                    # 1配送単位で出荷日まで足りる → 端数のみ
                    oq = round((future_need - cur_tank) / 100) * 100
                    oq = max(oq, 100)
                    event_notes[d] = f"最終: {get_order_note_for_day(d, oq)}"
                else:
                    # 配送単位で発注
                    oq = std_qty
                    event_notes[d] = get_order_note_for_day(d, oq)
                delivery_kg[d] = oq
            evening_pred = pred_tank[d] + delivery_kg[d] - daily_feed

    df["pred_tank"]       = pred_tank
    df["real_tank"]       = real_tank
    df["delivery_kg"]     = delivery_kg
    df["event_notes"]     = event_notes
    df["act_feed_kg"]     = actual_feed
    df["cum_feed_kg"]     = df["act_feed_kg"].cumsum()
    df["cum_delivery_kg"] = df["delivery_kg"].cumsum()  # delivery_kg[0]=first_qty含む
    return df
ross_dict   = {(r["sex"], r["day"]): r for r in ross308}

def get_ross308(age):
    max_day = max(k[1] for k in ross_dict.keys()) if ross_dict else 56
    return ross_dict.get(("as_hatched", max(0, min(int(age), max_day))), {})

# ----------------------------------------------------------
# 対象選択
# ----------------------------------------------------------
st.title("📋 ブロイラー飼養管理 - 入力・発注予測")
tab1, tab2 = st.tabs(["📝 日次入力・発注予測", "🚛 発注"])


with tab1:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        sel_farm    = st.selectbox("農場", list(farm_opts.keys()), key="s_farm")
        sel_farm_id = farm_opts[sel_farm]
    with c2:
        farm_lns = [ln for ln in lot_numbers if ln["farm_id"] == sel_farm_id and ln["is_active"]]
        if not farm_lns:
            st.warning("ロット番号を登録してください")
            st.stop()
        sel_ln_id = st.selectbox("ロット番号",
            [ln["lot_number_id"] for ln in farm_lns],
            format_func=lambda x: ln_map[x], key="s_ln")
    with c3:
        lot_fhs = [fh for fh in flock_houses if fh["lot_number_id"] == sel_ln_id]
        if not lot_fhs:
            st.warning("鶏舎割当を登録してください")
            st.stop()
        sel_fh_id = st.selectbox("鶏舎",
            [fh["flock_house_id"] for fh in lot_fhs],
            format_func=lambda x: house_map.get(
                next(fh["house_id"] for fh in lot_fhs if fh["flock_house_id"] == x), ""),
            key="s_fh")
    with c4:
        # 入雛日：選択した鶏舎に紐づく入雛日を表示（同一鶏舎で複数回転ある場合も対応）
        sel_fh_tmp    = next(fh for fh in lot_fhs if fh["flock_house_id"] == sel_fh_id)
        default_chick = sel_fh_tmp.get("chick_in_date", "")
        # 同一ロット・同一鶏舎の複数入雛日がある場合に備えてリスト化
        chick_dates = sorted(set(
            fh["chick_in_date"] for fh in lot_fhs
            if fh["flock_house_id"] == sel_fh_id
            or (fh["house_id"] == sel_fh_tmp["house_id"] and fh["lot_number_id"] == sel_ln_id)
        ))
        sel_chick_date = st.selectbox("入雛日",
            chick_dates,
            index=chick_dates.index(default_chick) if default_chick in chick_dates else 0,
            key="s_chick_date")
        # 入雛日で絞り込んだflock_houseを確定
        sel_fh_id = next(
            (fh["flock_house_id"] for fh in lot_fhs
             if fh["flock_house_id"] == sel_fh_id
             and fh["chick_in_date"] == sel_chick_date),
            sel_fh_id
        )

    sel_fh    = next(fh for fh in lot_fhs if fh["flock_house_id"] == sel_fh_id)
    sel_house = next((h for h in houses if h["house_id"] == sel_fh["house_id"]), {})
    sel_ln    = next(ln for ln in lot_numbers if ln["lot_number_id"] == sel_ln_id)

    # ----------------------------------------------------------
    # 上部ヘッダー情報（DB自動取得）
    # ----------------------------------------------------------
    chick_in_date   = date.fromisoformat(sel_fh["chick_in_date"])
    chick_in_count  = sel_fh["chick_in_count"] or 0
    spare_count     = sel_fh["spare_count"]    or 0
    planned_age     = sel_fh["planned_shipment_age_days"] or 56
    today_age       = (date.today() - chick_in_date).days
    house_coef      = float(sel_house.get("feed_transfer_coef") or 0)
    tank_capacity   = sel_house.get("tank_capacity") or "-"
    tank_number     = sel_house.get("tank_number")   or "-"

    # 残存羽数計算
    all_recs = supabase.table("daily_records") \
        .select("mortality_count,culling_count") \
        .eq("flock_house_id", sel_fh_id).execute().data
    total_mort = sum(r["mortality_count"] or 0 for r in all_recs)
    total_cull = sum(r["culling_count"]   or 0 for r in all_recs)
    remaining  = chick_in_count + spare_count - total_mort - total_cull

    st.dataframe(pd.DataFrame([{
        "タンクNo":        tank_number,
        "タンク容量(kg)":  tank_capacity,
        "搬送係数(kg/min)": house_coef,
        "入雛日":          str(chick_in_date),
        "入雛羽数":        f"{chick_in_count:,}",
        "スペア羽数":      f"{spare_count:,}",
        "出荷日齢":        f"{planned_age}日",
    }]), use_container_width=True, hide_index=True)


    # ----------------------------------------------------------
    # 既存の日次記録を取得
    # ----------------------------------------------------------
    existing_recs = supabase.table("daily_records") \
        .select("*") \
        .eq("flock_house_id", sel_fh_id) \
        .order("record_date").execute().data

    rec_by_date = {r["record_date"]: r for r in existing_recs}

    # ----------------------------------------------------------
    # 全日齢分のDataFrameを構築（日齢0〜出荷日齢）
    # ----------------------------------------------------------
    brand_names  = [""] + list(brand_opts.keys())
    worker_names = [""] + list(worker_opts.keys())

    rows = []
    cum_mort = 0
    cum_cull = 0

    for age in range(0, planned_age):  # 出荷日齢は含まない
        rec_date = chick_in_date + timedelta(days=age)
        rec      = rec_by_date.get(str(rec_date), {})
        ross     = get_ross308(age)

        mort     = rec.get("mortality_count") or 0
        cull     = rec.get("culling_count")   or 0
        cum_mort += mort
        cum_cull += cull
        total_loss   = cum_mort + cum_cull
        remain_count = chick_in_count + spare_count - total_loss

        # 採食量計算
        duration = rec.get("feed_duration_min")
        brand_id = rec.get("feed_brand_id")
        brand_nm = brand_map.get(brand_id, "") if brand_id else ""
        brand_obj = next((b for b in feed_brands if b["feed_brand_id"] == brand_id), {}) if brand_id else {}
        ratio    = float(brand_obj.get("transfer_coef_ratio") or 1.0)
        if duration and house_coef > 0:
            intake_kg = round(float(duration) * house_coef * ratio, 2)
            coef_pct  = round(ratio * 100, 1)
        else:
            intake_kg = None
            coef_pct  = None

        # タンク残量（今後計算予定・現時点は直接入力）
        rows.append({
            "日令":        age,
            "月日":        rec_date.strftime("%m/%d"),
            "斃死":        mort if rec else None,
            "淘汰":        cull if rec else None,
            "合計":        total_loss,      # 入雛日からの累計（斃死+淘汰）
            "残羽数":      remain_count,    # 入雛羽数+スペア−累計損耗
            "舎内最高℃":  rec.get("house_temp_max"),
            "舎内最低℃":  rec.get("house_temp_min"),
            "湿度%":       rec.get("house_humidity"),
            "外気最高℃":  rec.get("outside_temp_max"),
            "外気最低℃":  rec.get("outside_temp_min"),
            "平均体重g":   rec.get("avg_body_weight"),
            "標準体重g":   ross.get("weight_g"),
            "採食時間min": duration,
            "採食量kg":    intake_kg,
            "採食係数%":   coef_pct,
            "標準採食g":   ross.get("daily_intake_g"),
            "納品量kg":    rec.get("feed_delivery_qty"),
            "飼料銘柄":    brand_nm,
            "作業日誌":    rec.get("work_log") or "",
            "担当者":      worker_map.get(rec.get("worker_id"), "") if rec.get("worker_id") else "",
            "_date":       str(rec_date),
            "_id":         rec.get("daily_record_id"),
        })

    df_all = pd.DataFrame(rows)

    # ----------------------------------------------------------
    # ----------------------------------------------------------
    display_cols = [
        "日令","月日",
        "斃死","淘汰","合計",
        "舎内最高℃","舎内最低℃","湿度%","外気最高℃","外気最低℃",
        "平均体重g","標準体重g",
        "採食時間min","採食量kg",
        "飼料銘柄",
        "作業日誌",
    ]

    df_disp = df_all[display_cols].copy()

    # 保存前に斃死+淘汰の累計を事前計算してdisplayに反映
    _mort_cum = 0
    _cull_cum = 0
    for i in range(len(df_disp)):
        _mort_cum += int(df_disp.at[i, "斃死"] or 0)
        _cull_cum += int(df_disp.at[i, "淘汰"] or 0)
        df_disp.at[i, "合計"] = _mort_cum + _cull_cum

    edited = st.data_editor(
        df_disp,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        height=(planned_age + 1) * 28 + 38,
        column_order=display_cols,
        column_config={
            "日令":       st.column_config.NumberColumn("日令",      disabled=True, width=40),
            "月日":       st.column_config.TextColumn(  "月日",      disabled=True, width=55),
            "斃死":       st.column_config.NumberColumn("斃死",      min_value=0, step=1, width=50),
            "淘汰":       st.column_config.NumberColumn("淘汰",      min_value=0, step=1, width=50),
            "合計":       st.column_config.NumberColumn("合計",      disabled=True, width=50),
            "舎内最高℃": st.column_config.NumberColumn("舎内最高℃", step=0.1, width=70),
            "舎内最低℃": st.column_config.NumberColumn("舎内最低℃", step=0.1, width=70),
            "湿度%":      st.column_config.NumberColumn("湿度%",     min_value=0.0, max_value=100.0, step=1.0, width=55),
            "外気最高℃": st.column_config.NumberColumn("外気最高℃", step=0.1, width=70),
            "外気最低℃": st.column_config.NumberColumn("外気最低℃", step=0.1, width=70),
            "平均体重g":  st.column_config.NumberColumn("平均体重g", step=1.0, width=70),
            "標準体重g":  st.column_config.NumberColumn("標準体重g", disabled=True, width=70),
            "採食時間min":st.column_config.NumberColumn("採食時間\nmin", step=1.0, width=65),
            "採食量kg":   st.column_config.NumberColumn("採食量kg",  disabled=True, width=65),
            "飼料銘柄":   st.column_config.SelectboxColumn("飼料銘柄", options=brand_names, width=100),
            "作業日誌":   st.column_config.TextColumn(  "作業日誌",  width=150),
        },
        key=f"sheet_editor_{sel_fh_id}"
    )

    # ----------------------------------------------------------
    # 保存処理
    # ----------------------------------------------------------
    # 保存結果メッセージを表示（rerun後も保持）
    if "sheet_msg" in st.session_state:
        msg_type, msg_text = st.session_state.pop("sheet_msg")
        if msg_type == "success":
            st.success(msg_text)
        else:
            st.error(msg_text)

    st.caption(f"合計 {len(df_disp)} 行（日齢0〜{planned_age}日）　入力後「💾 一括保存」を押してください")

    if st.button("💾 一括保存", type="primary", key="sheet_save"):
        updated  = 0
        inserted = 0
        skipped  = 0
        errors   = []

        for i, row in edited.iterrows():
            orig     = df_all.iloc[i]
            rec_date = orig["_date"]
            rec_id   = orig["_id"]

            # 斃死・淘汰・環境・採食時間・納品量のどれかが入力されていれば保存対象
            has_data = any([
                pd.notna(row["斃死"])        and row["斃死"]        != 0,
                pd.notna(row["淘汰"])        and row["淘汰"]        != 0,
                pd.notna(row["舎内最高℃"]),
                pd.notna(row["採食時間min"]) and row["採食時間min"] != 0,
                bool(row.get("作業日誌")),
            ])

            if not has_data:
                skipped += 1
                continue

            # 銘柄補正率
            brand_nm = row.get("飼料銘柄") or ""
            brand_id = brand_opts.get(brand_nm) if brand_nm else None
            brand_obj2 = next((b for b in feed_brands if b["feed_brand_id"] == brand_id), {}) if brand_id else {}
            ratio2   = float(brand_obj2.get("transfer_coef_ratio") or 1.0)

            data = {
                "flock_house_id":    sel_fh_id,
                "record_date":       rec_date,
                "mortality_count":   int(row["斃死"]  or 0),
                "culling_count":     int(row["淘汰"]  or 0),
                "house_temp_max":    float(row["舎内最高℃"]) if pd.notna(row["舎内最高℃"]) else None,
                "house_temp_min":    float(row["舎内最低℃"]) if pd.notna(row["舎内最低℃"]) else None,
                "house_humidity":    float(row["湿度%"])      if pd.notna(row["湿度%"])      else None,
                "outside_temp_max":  float(row["外気最高℃"]) if pd.notna(row["外気最高℃"]) else None,
                "outside_temp_min":  float(row["外気最低℃"]) if pd.notna(row["外気最低℃"]) else None,
                "avg_body_weight":   float(row["平均体重g"])  if pd.notna(row["平均体重g"])  and float(row["平均体重g"] or 0) > 0 else None,
                "feed_duration_min": float(row["採食時間min"]) if pd.notna(row["採食時間min"]) and float(row["採食時間min"] or 0) > 0 else None,
                "feed_delivery_qty": float(row["納品量kg"])   if pd.notna(row["納品量kg"])   and float(row["納品量kg"]  or 0) > 0 else None,
                "feed_brand_id":     brand_id,
                "work_log":          str(row["作業日誌"]) if pd.notna(row.get("作業日誌")) and row["作業日誌"] else None,
                "worker_id":         worker_opts.get(row["担当者"]) if pd.notna(row.get("担当者")) and row["担当者"] else None,
            }

            try:
                if rec_id and pd.notna(rec_id):
                    supabase.table("daily_records").update(data) \
                        .eq("daily_record_id", int(rec_id)).execute()
                    updated += 1
                else:
                    supabase.table("daily_records").insert(data).execute()
                    inserted += 1
            except Exception as e:
                errors.append(f"日齢{orig['日令']}: {e}")

        if errors:
            st.session_state["sheet_msg"] = ("error", f"エラー: {'; '.join(errors[:3])}")
        else:
            msg = []
            if updated  > 0: msg.append(f"更新 {updated}件")
            if inserted > 0: msg.append(f"新規 {inserted}件")
            if skipped  > 0: msg.append(f"未入力スキップ {skipped}件")
            st.session_state["sheet_msg"] = ("success", f"✅ {' / '.join(msg)}")
        st.rerun()

    # ----------------------------------------------------------
    # タブ2: 発注予測（直接編集→自動再計算）
    # ----------------------------------------------------------
    # ----------------------------------------------------------
    # ----------------------------------------------------------

    # ---- 発注予測シミュレーション ----
    st.markdown("---")
    st.markdown("### 🚛 発注予測")
    fc_std_qty   = 4000.0   # 配送単位（kg）
    fc_min_alert = 200.0    # 最低残量アラート（kg）
    fc_lead_time = 0

    st.caption("💡 表の「実測残量kg」「調整発注kg」を直接入力すると自動再計算されます")

    try:
        fc_recs = supabase.table("daily_records")             .select("*")             .eq("flock_house_id", sel_fh_id)             .order("record_date").execute().data

        today_day_fc = (date.today() - chick_in_date).days

        # ---- Step1: adj_dictを読み込む（セッション保持） ----
        adj_key = f"adj_dict_{sel_fh_id}"
        if adj_key not in st.session_state:
            st.session_state[adj_key] = {}
        adj_dict = st.session_state[adj_key]

        # ---- Step2: 発注予測を実行（adj_dictを反映） ----
        df_fc = run_feed_forecast(
            sel_fh, fc_recs, house_coef,
            fc_std_qty, fc_min_alert, fc_lead_time,
            adj_dict=adj_dict)

        # ---- Step4: 編集可能なシミュレーション表 ----
        st.markdown("#### 📊 タンク残量シミュレーション（直接編集→自動再計算）")

        # 編集用DataFrameを構築
        # 実測残量・調整発注量は編集可能、それ以外は読み取り専用
        # adj_dictから調整発注値を取得
        adj_delivery = {day: v.get("delivered") for day, v in adj_dict.items() if v.get("delivered")}

        edit_df = pd.DataFrame({
            "日齢":       df_fc["day"].astype(int),
            "月日":       df_fc.apply(
                lambda r: f"◀{r['date_str']}" if r["day"] == today_day_fc else r["date_str"],
                axis=1),
            "採食kg":     df_fc["act_feed_kg"].round(1),
            "標準採食kg": df_fc["std_feed_kg"].round(1),
            "採食累計kg": df_fc["cum_feed_kg"].round(0),
            "補正率":     df_fc["adj_rate"].round(3),
            "予測残量kg": df_fc["pred_tank"].round(0),
            "実測残量kg": df_fc.apply(
                lambda row: (
                    float(adj_dict[int(row["day"])]["actual_tank"])
                    if int(row["day"]) in adj_dict and adj_dict[int(row["day"])].get("actual_tank") is not None
                    else (float(row["real_tank"]) if not (isinstance(row["real_tank"], float) and np.isnan(row["real_tank"])) else None)
                ), axis=1),
            "予定発注kg": df_fc["delivery_kg"].apply(
                lambda x: float(x) if x > 0 else None),   # 自動計算・表示専用
            "調整発注kg": df_fc["day"].apply(
                lambda d: adj_delivery.get(int(d))),       # 手動入力・空欄=予定発注を使用
            "発注内容":   df_fc["event_notes"],
        })

        edited_fc = st.data_editor(
            edit_df,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
        height=(planned_age + 1) * 28 + 38,
            column_config={
                "日齢":       st.column_config.NumberColumn("日齢",       disabled=True, width=42),
                "月日":       st.column_config.TextColumn(  "月日",       disabled=True, width=60),
                "採食kg":     st.column_config.NumberColumn("採食kg\n(予測)",  disabled=True, width=65),
                "標準採食kg": st.column_config.NumberColumn("標準採食kg\n(Ross308)", disabled=True, width=85,
                    help="Ross308標準値×羽数÷1000（環境・補正なし）"),
                "採食累計kg": st.column_config.NumberColumn("採食累計kg", disabled=True, width=78),
                "補正率":     st.column_config.NumberColumn("補正率",     disabled=True, width=55),
                "予測残量kg": st.column_config.NumberColumn("予測残量kg", disabled=True, width=78),
                "実測残量kg": st.column_config.NumberColumn("実測残量kg",
                    min_value=0.0, step=10.0, width=85,
                    help="実測タンク残量を入力→予測残量の補正に反映"),
                "予定発注kg": st.column_config.NumberColumn("予定発注kg", disabled=True, width=78,
                    help="自動計算による予定発注量"),
                "調整発注kg": st.column_config.NumberColumn("調整発注kg",
                    min_value=0.0, step=500.0, width=85,
                    help="空欄=予定発注を使用。変更する場合のみ入力→自動再計算"),
                "発注内容":   st.column_config.TextColumn("発注内容（銘柄＋数量）",
                    disabled=True, width=250),
            },
            key=f"fc_editor_{sel_fh_id}"
        )

        # ---- Step5: 変更を検知してadj_dictを更新→自動再計算 ----
        new_adj = {}
        for i, row in edited_fc.iterrows():
            day   = int(row["日齢"])
            entry = {}

            # 実測残量: 0日齢以外を保存（0日齢はfirst_qty固定）
            new_real = row.get("実測残量kg")
            if new_real is not None and pd.notna(new_real) and day > 0:
                entry["actual_tank"] = float(new_real)

            # 調整発注量: 入力があれば保存、空欄=自動予測に戻す
            new_del = row.get("調整発注kg")
            if new_del is not None and pd.notna(new_del) and float(new_del) > 0:
                entry["delivered"] = float(new_del)

            if entry:
                new_adj[day] = entry

        # adj_dictが変わったらsession_stateに保存→自動再計算
        if new_adj != adj_dict:
            st.session_state[adj_key] = new_adj
            st.rerun()

        # ---- Step6: 調整内容のリセット ----
        if adj_dict:
            adj_count = len([v for v in adj_dict.values()
                             if v.get("actual_tank") or v.get("delivered")])
            st.caption(f"✏️ 調整中: {adj_count}件の変更あり")
            if st.button("🔄 調整をリセット", key="fc_reset"):
                st.session_state[adj_key] = {}
                st.rerun()

        # ---- Step7: order_plan構築（発注タブで使用） ----
        active_brs  = [b for b in feed_brands if b.get("is_active") not in (None, False, 0, "false", "0", "")]
        order_plan  = df_fc[df_fc["delivery_kg"] > 0].copy()
        if not order_plan.empty:
            order_plan["納品予定日_dt"] = order_plan["date_obj"]
            order_plan["納品予定日"]    = order_plan["date_str"]
            order_plan["日齢"]          = order_plan["day"].astype(int)
            order_plan["発注量kg"]      = order_plan["delivery_kg"].round(0)
            order_plan["発注種別"]      = order_plan["event_notes"]
            order_plan["タンク残量kg"]  = order_plan["pred_tank"].round(0)
            order_plan["採食累計kg"]    = order_plan["cum_feed_kg"].round(0)

        # ---- Step8: 予定配送の保存・更新ボタン ----
        if not order_plan.empty:
            if st.button("💾 予定配送を保存・更新", type="primary", key="fc_order_save"):
                try:
                    supabase.table("feed_order_details") \
                        .delete() \
                        .eq("flock_house_id", sel_fh_id) \
                        .is_("order_id", "null") \
                        .execute()
                    for _, r in order_plan.iterrows():
                        dt = r["納品予定日_dt"]
                        if hasattr(dt, "date"):
                            dt = dt.date()
                        supabase.table("feed_order_details").insert({
                            "order_id":       None,
                            "flock_house_id": sel_fh_id,
                            "feed_brand_id":  None,
                            "order_qty":      float(r["発注量kg"]),
                            "tank_remaining": float(r["タンク残量kg"]),
                            "delivery_date":  str(dt),
                            "day_age":        int(r["日齢"]),
                            "event_notes":    str(r["発注種別"]),
                            "pred_tank":      float(r["タンク残量kg"]),
                            "status":         "予定",
                        }).execute()
                    st.success(f"✅ {len(order_plan)}件の予定配送を保存しました")
                except Exception as e:
                    st.error(f"保存エラー: {e}")


    except Exception as e:
        st.error(f"発注予測エラー: {e}")
        import traceback
        st.code(traceback.format_exc())

# ==========================================================
# タブ2: 一括発注（農場単位）
# ==========================================================
with tab2:
    st.markdown("### 🚛 発注")

    # ---- 農場選択（先に農場を選んで予定配送の日付範囲を取得） ----
    o_farm    = st.selectbox("農場", list(farm_opts.keys()), key="o_farm")
    o_farm_id = farm_opts[o_farm]

    # ---- 農場内育成中の鶏舎を取得・表示 ----
    o_farm_ln_ids = {ln["lot_number_id"] for ln in lot_numbers if ln["farm_id"] == o_farm_id}
    o_farm_fhs    = [fh for fh in flock_houses
                     if fh["lot_number_id"] in o_farm_ln_ids and fh.get("status") == "育成中"]

    if not o_farm_fhs:
        st.info("育成中の鶏舎がありません")
    else:
        import pandas as pd
        # ---- 全予定配送を取得して日付範囲を確定 ----
        o_fh_ids  = [fh["flock_house_id"] for fh in o_farm_fhs]
        o_details = []
        for fh_id in o_fh_ids:
            rows = supabase.table("feed_order_details") \
                .select("*") \
                .eq("flock_house_id", fh_id) \
                .order("delivery_date").execute().data
            # 予定・発注済みを両方含める（order_idなし or あり）
            rows = [r for r in rows if r.get("delivery_date")]
            for row in rows:
                fh_obj = next((f for f in o_farm_fhs if f["flock_house_id"] == fh_id), {})
                ln_obj = next((ln for ln in lot_numbers if ln["lot_number_id"] == fh_obj.get("lot_number_id")), {})
                row["house_name"] = house_map.get(fh_obj.get("house_id"), "")
                row["lot_number"] = ln_obj.get("lot_number", "")
                o_details.append(row)

        # 予定配送の日付範囲を取得してデフォルト値に使用
        if o_details:
            _all_dates = [date.fromisoformat(r["delivery_date"]) for r in o_details if r.get("delivery_date")]
            _min_date  = min(_all_dates) if _all_dates else date.today()
            _max_date  = max(_all_dates) if _all_dates else date.today() + timedelta(days=14)
        else:
            _min_date = date.today()
            _max_date = date.today() + timedelta(days=14)

        # ---- 期間・発注日選択 ----
        # セッション初期化（農場が変わったらリセット）
        _farm_change_key = f"o_farm_prev"
        if st.session_state.get(_farm_change_key) != o_farm_id:
            st.session_state["o_from_val"] = _min_date
            st.session_state["o_to_val"]   = _max_date
            st.session_state[_farm_change_key] = o_farm_id

        oc1, oc2, oc3 = st.columns(3)
        with oc1:
            o_from = st.date_input("納品範囲（開始）",
                value=st.session_state.get("o_from_val", _min_date), key="o_from")
            st.session_state["o_from_val"] = o_from
        with oc2:
            o_to   = st.date_input("納品範囲（終了）",
                value=st.session_state.get("o_to_val", _max_date), key="o_to")
            st.session_state["o_to_val"] = o_to
        with oc3:
            o_order_date = st.date_input("発注日", value=date.today(), key="o_order_date")


        if not o_details:
            st.info("予定配送が登録されていません。タブ1の発注予測でシミュレーションを実行してください。")
        else:
            df_od = pd.DataFrame(o_details)
            df_od["delivery_date_dt"] = pd.to_datetime(df_od["delivery_date"]).dt.date
            df_sel = df_od[
                (df_od["delivery_date_dt"] >= o_from) &
                (df_od["delivery_date_dt"] <= o_to)
            ].copy()


            if df_sel.empty:
                st.info(f"指定期間（{o_from}〜{o_to}）に予定配送がありません")
            else:
                o_total = df_sel["order_qty"].sum()
                st.markdown(f"**対象: {len(df_sel)}件　合計: {o_total:,.0f} kg**")

                # 発注一覧表示（納品予定日昇順）
                disp_sel = df_sel[["delivery_date","house_name","order_qty","event_notes","status"]].copy()
                disp_sel.columns = ["納品予定日","鶏舎","発注量kg","発注内容","状況"]
                disp_sel = disp_sel.sort_values("納品予定日").reset_index(drop=True)
                st.dataframe(disp_sel, use_container_width=True, hide_index=True)

                st.divider()
                obc1, obc2 = st.columns([1, 3])

                with obc1:
                    if st.button("💾 発注確定登録", type="primary", key="o_save"):
                        try:
                            res = supabase.table("feed_orders").insert({
                                "farm_id":         o_farm_id,
                                "order_date":      str(o_order_date),
                                "delivery_date":   str(o_from),
                                "lead_time_days":  0,
                                "total_order_qty": float(o_total),
                                "status":          "発注済",
                            }).execute()
                            o_order_id = res.data[0]["order_id"]
                            for detail_id in df_sel["detail_id"].tolist():
                                supabase.table("feed_order_details").update({
                                    "order_id": o_order_id,
                                    "status":   "発注済",
                                }).eq("detail_id", detail_id).execute()
                            # 発注書テキスト生成
                            o_lines = [
                                f"【飼料発注】{o_farm}",
                                f"発注日: {o_order_date}",
                                "",
                                f"{'納品予定日':<12}{'タンクNo':<10}{'発注量(kg)':<12}{'発注内容'}",
                                "-" * 60,
                            ]
                            for _, r in df_sel.sort_values("delivery_date").iterrows():
                                tank_no = fh_tank_map.get(r.get("flock_house_id"), "-")
                                o_lines.append(
                                    f"{r['delivery_date']:<12}{str(tank_no):<10}"
                                    f"{r['order_qty']:>8,.0f}kg    {r['event_notes'] or ''}")
                            o_lines += ["-" * 60, f"{'合計':<22}{o_total:>8,.0f}kg", "", "よろしくお願いいたします。", o_farm]
                            o_order_text_new = "\n".join(o_lines)
                            st.session_state["o_order_id"]   = o_order_id
                            st.session_state["o_order_text"] = o_order_text_new
                            st.success(f"✅ 発注登録完了（ID: {o_order_id}）")
                        except Exception as e:
                            st.error(f"登録エラー: {e}")

                with obc2:
                    # ---- 発注書プレビュー（発注確定後に表示） ----
                    if st.session_state.get("o_order_text"):
                        st.markdown("#### 📄 発注書プレビュー")
                        o_body_text = st.text_area("発注書（編集可）",
                            value=st.session_state.get("o_order_text", ""),
                            height=250, key="o_body")
                    else:
                        o_body_text = ""

                    if st.session_state.get("o_order_id"):
                        st.markdown("#### 📤 送信")
                        send_method = st.radio("送信方法",
                            ["📧 メール", "🖨️ 印刷"],
                            horizontal=True, key="o_send_method")

                        if send_method == "📧 メール":
                            o_email_settings = supabase.table("email_settings").select("*").execute().data
                            o_farm_es = [es for es in o_email_settings
                                         if es.get("farm_id") == o_farm_id or es.get("farm_id") is None]
                            if not o_farm_es:
                                st.warning("メール設定がありません")
                            else:
                                o_setting_opts = {es["setting_name"]: es for es in o_farm_es}
                                o_es      = o_setting_opts[st.selectbox("送信先設定",
                                    list(o_setting_opts.keys()), key="o_email_sel")]
                                o_to_addr = st.text_input("宛先", value=o_es.get("to_address",""), key="o_to_addr")
                                o_cc_addr = st.text_input("CC",  value=o_es.get("cc_address",""),  key="o_cc")
                                o_subject = st.text_input("件名",
                                    value=f"【飼料発注】{o_farm} {o_order_date}", key="o_subject")
                                if st.button("📧 メール送信", key="o_send_email", type="primary"):
                                    try:
                                        import smtplib
                                        from email.mime.text import MIMEText
                                        from email.mime.multipart import MIMEMultipart
                                        smtp_host = st.secrets.get("smtp",{}).get("host","")
                                        smtp_port = int(st.secrets.get("smtp",{}).get("port",587))
                                        smtp_user = st.secrets.get("smtp",{}).get("user","")
                                        smtp_pass = st.secrets.get("smtp",{}).get("password","")
                                        if not smtp_host:
                                            st.error("SMTP設定がありません")
                                        else:
                                            msg = MIMEMultipart()
                                            msg["From"]    = smtp_user
                                            msg["To"]      = o_to_addr
                                            if o_cc_addr: msg["Cc"] = o_cc_addr
                                            msg["Subject"] = o_subject
                                            msg.attach(MIMEText(o_body_text, "plain", "utf-8"))
                                            rcpts = [a.strip() for a in o_to_addr.split(",")]
                                            if o_cc_addr:
                                                rcpts += [a.strip() for a in o_cc_addr.split(",")]
                                            with smtplib.SMTP(smtp_host, smtp_port) as sv:
                                                sv.starttls()
                                                sv.login(smtp_user, smtp_pass)
                                                sv.sendmail(smtp_user, rcpts, msg.as_string())
                                            st.success(f"✅ メール送信完了 → {o_to_addr}")
                                    except Exception as e:
                                        st.error(f"送信エラー: {e}")

                        else:  # 印刷
                            # テーブル形式のHTML発注書を生成
                            _rows_html = ""
                            for _, r in df_sel.sort_values("delivery_date").iterrows():
                                _tank_no = fh_tank_map.get(r.get("flock_house_id"), "-")
                                _rows_html += f"""<tr>
                                    <td>{r['delivery_date']}</td>
                                    <td class="tank">{_tank_no}</td>
                                    <td style="text-align:right">{r['order_qty']:,.0f}</td>
                                    <td class="notes">{r['event_notes'] or ''}</td>
                                </tr>"""
                            _print_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>発注書</title>
<style>
  body {{ font-family: 'Source Sans Pro', 'Noto Sans JP', sans-serif; font-size: 12pt; padding: 20mm; }}
  h2 {{ font-size: 14pt; margin-bottom: 4px; }}
  p {{ margin: 2px 0; font-size: 11pt; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th {{ background: #f0f0f0; border: 1px solid #999; padding: 6px 8px; font-size: 11pt; text-align: left; white-space: nowrap; }}
  th.tank {{ width: 60px; }}
  td {{ border: 1px solid #999; padding: 5px 8px; white-space: nowrap; overflow: hidden; font-size: 11pt; }}
  td.tank {{ width: 60px; }}
  td.notes {{ font-size: 11pt; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 250px; }}
  tr {{ height: 24px; max-height: 24px; }}
  .total {{ font-weight: bold; text-align: right; padding: 6px 8px; border-top: 2px solid #333; }}
  .footer {{ margin-top: 20px; font-size: 11pt; }}
  @media print {{ button {{ display: none; }} }}
</style>
</head><body>
<h2>【飼料発注】{o_farm}</h2>
<p>発注日: {o_order_date}</p>
<table>
  <thead><tr>
    <th>納品予定日</th><th class="tank">タンクNo</th><th style="text-align:right">発注量(kg)</th><th>発注内容</th>
  </tr></thead>
  <tbody>{_rows_html}</tbody>

</table>
<div class="footer"><p>よろしくお願いいたします。</p><p>{o_farm}</p></div>
<br>
<button onclick="window.print()" style="padding:8px 20px;font-size:12pt;cursor:pointer;">🖨️ 印刷</button>
</body></html>"""
                            # data: URIでUTF-8を直接渡す（文字化け防止）
                            import urllib.parse
                            _encoded = urllib.parse.quote(_print_html, safe='')
                            st.components.v1.html(f"""
<button onclick="
  var w=window.open('','_blank');
  w.document.open('text/html','replace');
  w.document.write(decodeURIComponent('{_encoded}'));
  w.document.close();
  setTimeout(function(){{w.focus();}},300);
" style="background:#1f77b4;color:white;border:none;padding:10px 24px;border-radius:4px;cursor:pointer;font-size:14px;">
🖨️ 発注書を印刷プレビュー
</button>""", height=55)
