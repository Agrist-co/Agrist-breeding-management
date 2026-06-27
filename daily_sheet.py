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
    active_brs   = [b for b in feed_brands if b.get("is_active")]
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

    # 0日齢: 初回投入量を起点として登録
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
            s_tank = combined_tank[s]["actual_tank"]
            e_tank = combined_tank[e]["actual_tank"]

            # 区間内の途中納品量（s+1〜e-1のみ、e日の納品は含めない）
            # e日の納品は実測残量計測後の投入なので次区間の起点に含まれる
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

    # 0日齢: 初回投入量でタンク満載（予定発注列には出さない）
    pred_tank[0]   = first_qty
    real_tank[0]   = first_qty
    event_notes[0] = f"初回投入: {first_qty:.0f}kg"
    day0_feed    = df.loc[0, "act_feed_kg"]
    evening_pred = first_qty - day0_feed

    def get_order_note_for_day(day, qty):
        cur = get_brand_for_age(day, active_brs)
        if cur is None:
            return f"{qty:,.0f}kg"
        age_to = cur.get("age_to_days")
        if age_to is None:
            return f"{cur['brand_name']} {qty:,.0f}kg"
        total_remain = max(shipping_age - day, 1)
        pre_days  = min(age_to - day, total_remain)
        pre_ratio = pre_days / total_remain
        pre_qty   = round(qty * pre_ratio / 500) * 500
        fin_qty   = qty - pre_qty
        if fin_qty <= 0 or pre_ratio >= 0.95:
            return f"{cur['brand_name']} {qty:,.0f}kg"
        nxt = get_brand_for_age(age_to + 1, active_brs)
        nxt_name = nxt["brand_name"] if nxt else "仕上"
        return f"{cur['brand_name']} {pre_qty:,.0f}kg ＋ {nxt_name} {fin_qty:,.0f}kg"

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

_header_box_html = f"""<div class="header-box">
  <span><b>農場</b>: <span class="header-val">{sel_farm}</span></span>&nbsp;&nbsp;
  <span><b>ロット</b>: <span class="header-val">{sel_ln['lot_number']}</span></span>&nbsp;&nbsp;
  <span><b>鶏舎</b>: <span class="header-val">{house_map.get(sel_fh['house_id'],'')}</span></span>&nbsp;&nbsp;
  <span><b>タンクNo</b>: <span class="header-val">{tank_number}</span></span>&nbsp;&nbsp;
  <span><b>タンク容量</b>: <span class="header-val">{tank_capacity} kg</span></span>&nbsp;&nbsp;
  <span><b>搬送係数</b>: <span class="header-val">{house_coef} kg/min</span></span>
  <br>
  <span><b>入雛日</b>: <span class="header-val">{chick_in_date}</span></span>&nbsp;&nbsp;
  <span><b>入雛羽数</b>: <span class="header-val">{chick_in_count:,} 羽</span></span>&nbsp;&nbsp;
  <span><b>スペア</b>: <span class="header-val">{spare_count:,} 羽</span></span>&nbsp;&nbsp;
  <span><b>出荷日齢</b>: <span class="header-val">{planned_age} 日</span></span>&nbsp;&nbsp;
  <span><b>残存羽数</b>: <span class="header-val">{remaining:,} 羽</span></span>
</div>"""
st.markdown(_header_box_html, unsafe_allow_html=True)

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

for age in range(0, planned_age + 1):
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
# タブ1: 日次入力シート
# ----------------------------------------------------------
# ----------------------------------------------------------
# セクション1: 日次入力シート
# ----------------------------------------------------------
st.markdown("---")
st.markdown("### 📝 日次入力シート")
display_cols = [
    "日令","月日",
    "斃死","淘汰","合計","残羽数",
    "舎内最高℃","舎内最低℃","湿度%","外気最高℃","外気最低℃",
    "平均体重g","標準体重g",
    "採食時間min","採食量kg","採食係数%","標準採食g",
    "納品量kg","飼料銘柄",
    "作業日誌","担当者",
]

df_disp = df_all[display_cols].copy()

# 今日の行をハイライト（スタイリングは data_editor では非対応のため目印列で代用）
edited = st.data_editor(
    df_disp,
    use_container_width=True,
    hide_index=True,
    num_rows="fixed",
    height=57 * 35 + 38,
    column_config={
        "日令":       st.column_config.NumberColumn("日令",    disabled=True, width=40),
        "月日":       st.column_config.TextColumn(  "月日",    disabled=True, width=55),
        "斃死":       st.column_config.NumberColumn("斃死",    min_value=0, step=1, width=50),
        "淘汰":       st.column_config.NumberColumn("淘汰",    min_value=0, step=1, width=50),
        "合計":       st.column_config.NumberColumn("合計",    disabled=True, width=50),
        "残羽数":     st.column_config.NumberColumn("残羽数",  disabled=True, width=65),
        "舎内最高℃": st.column_config.NumberColumn("舎内最高℃", step=0.1, width=70),
        "舎内最低℃": st.column_config.NumberColumn("舎内最低℃", step=0.1, width=70),
        "湿度%":      st.column_config.NumberColumn("湿度%",   min_value=0.0, max_value=100.0, step=1.0, width=55),
        "外気最高℃": st.column_config.NumberColumn("外気最高℃", step=0.1, width=70),
        "外気最低℃": st.column_config.NumberColumn("外気最低℃", step=0.1, width=70),
        "平均体重g":  st.column_config.NumberColumn("平均体重g", step=1.0, width=70),
        "標準体重g":  st.column_config.NumberColumn("標準体重g", disabled=True, width=70),
        "採食時間min":st.column_config.NumberColumn("採食時間\nmin", step=1.0, width=65),
        "採食量kg":   st.column_config.NumberColumn("採食量kg", disabled=True, width=65),
        "採食係数%":  st.column_config.NumberColumn("採食係数%", disabled=True, width=65),
        "標準採食g":  st.column_config.NumberColumn("標準採食g", disabled=True, width=65),
        "納品量kg":   st.column_config.NumberColumn("納品量kg", step=100.0, width=65),
        "飼料銘柄":   st.column_config.SelectboxColumn("飼料銘柄", options=brand_names, width=100),
        "作業日誌":   st.column_config.TextColumn(  "作業日誌", width=150),
        "担当者":     st.column_config.SelectboxColumn("担当者", options=worker_names, width=80),
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
            pd.notna(row["斃死"])      and row["斃死"]      != 0,
            pd.notna(row["淘汰"])      and row["淘汰"]      != 0,
            pd.notna(row["舎内最高℃"]),
            pd.notna(row["採食時間min"]) and row["採食時間min"] != 0,
            pd.notna(row["納品量kg"])  and row["納品量kg"]  != 0,
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
# セクション2: 発注予測
# ----------------------------------------------------------
st.markdown("---")
st.markdown("### 🚛 発注予測")
st.markdown("#### ⚙️ 発注パラメータ")
fp1, fp2 = st.columns(2)
with fp1:
    fc_std_qty   = st.number_input("配送単位（kg）",
        min_value=0.0, value=4000.0, step=500.0, key="fc_std_qty")
with fp2:
    fc_min_alert = st.number_input("最低残量アラート（kg）",
        min_value=0.0, value=200.0, step=50.0, key="fc_min_alert")
fc_lead_time = 0  # リードタイムなし（出荷日齢まで全予測）

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

    # ---- デバッグ情報 ----
    with st.expander("🔍 デバッグ情報", expanded=False):
        today_day_dbg = (date.today() - chick_in_date).days
        first_qty_dbg = float(sel_fh.get("initial_feed_delivery_qty") or 0)
        st.write(f"**今日の日齢**: {today_day_dbg}日 / **出荷日齢**: {planned_age}日")
        st.write(f"**初回投入量**: {first_qty_dbg:,.0f} kg")
        st.write(f"**配送単位**: {fc_std_qty:,.0f} kg / **最低残量アラート**: {fc_min_alert:,.0f} kg")
        st.write(f"**補正係数**: {float(sel_fh.get('feed_correction_factor') or 1.0):.3f}")
        st.write("**adj_dict（実測残量・確定発注）**:", adj_dict)
        # 区間ごとの補正率計算を表示
        for ck_d_str in sorted(adj_dict.keys(), key=lambda x: int(x)):
            ck_d = int(ck_d_str)
            ck_v = adj_dict[ck_d_str]
            if ck_v.get("actual_tank") is not None and ck_d > 0:
                tank_val = float(ck_v["actual_tank"])
                std_sum  = df_fc.loc[df_fc["day"] < ck_d, "std_feed_kg"].sum()
                consumed = first_qty_dbg - tank_val
                rate_calc = consumed / std_sum if std_sum > 0 else 0
                st.write(f"日齢0→{ck_d}: 消費={consumed:.0f}kg / 標準合計={std_sum:.1f}kg = **補正率{rate_calc:.4f}**")
        rate_chk = df_fc[["day","date_str","adj_rate","act_feed_kg","std_feed_kg","pred_tank","delivery_kg"]].copy()
        rate_chk.columns = ["日齢","月日","補正率","予測採食","標準採食","予測残量","発注量"]
        st.dataframe(rate_chk.round(4), use_container_width=True, hide_index=True)

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
    height=56 * 35 + 38,
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

    # ---- Step7: 予定配送一覧（出荷日齢まで全発注計画） ----
    st.markdown("#### 📋 予定配送一覧（出荷日齢まで）")
    active_brs  = [b for b in feed_brands if b.get("is_active")]
    order_plan  = df_fc[df_fc["delivery_kg"] > 0].copy()

    if order_plan.empty:
        st.info("出荷日齢まで発注不要です")
    else:
        order_plan = order_plan.copy()
        order_plan["納品予定日_dt"] = order_plan["date_obj"]
        order_plan["納品予定日"]    = order_plan["date_str"]
        order_plan["日齢"]          = order_plan["day"].astype(int)
        order_plan["発注量kg"]      = order_plan["delivery_kg"].round(0)
        order_plan["発注種別"]      = order_plan["event_notes"]  # 銘柄名＋数量
        order_plan["タンク残量kg"]  = order_plan["pred_tank"].round(0)
        order_plan["採食累計kg"]    = order_plan["cum_feed_kg"].round(0)
        order_plan["配送累計kg"]    = order_plan["cum_delivery_kg"].round(0)
        order_plan["使用銘柄"]      = order_plan["日齢"].apply(
            lambda d: (get_brand_for_age(d, active_brs) or {}).get("brand_name", "-"))

        # 合計行を追加
        total_row = {
            "納品予定日": "【合計】",
            "日齢":       "",
            "発注量kg":   order_plan["発注量kg"].sum(),
            "発注種別":   "",
            "タンク残量kg": "",
            "採食累計kg": "",
            "配送累計kg": "",
            "使用銘柄":   "",
        }
        disp_order = order_plan[[
            "納品予定日","日齢","発注量kg","発注種別","タンク残量kg","採食累計kg"
        ]].copy()
        st.dataframe(disp_order, use_container_width=True, hide_index=True)
        total_qty = order_plan["発注量kg"].sum()
        st.caption(f"合計発注量: **{total_qty:,.0f} kg** / {len(order_plan)}回")

        # ---- Step8: 発注日範囲を指定して発注 ----
        st.markdown("#### 📤 発注日範囲指定・発注登録")
        st.caption("発注したい範囲（納品予定日）を指定して、その期間の発注を一括登録します")

        # date_objはdatetime.date型なのでそのまま使用
        min_date = order_plan["納品予定日_dt"].min()
        max_date = order_plan["納品予定日_dt"].max()
        if hasattr(min_date, "date"): min_date = min_date.date()
        if hasattr(max_date, "date"): max_date = max_date.date()

        dr1, dr2 = st.columns(2)
        with dr1:
            range_from = st.date_input("発注範囲（開始）",
                value=min_date, min_value=min_date, max_value=max_date,
                key="fc_range_from")
        with dr2:
            range_to = st.date_input("発注範囲（終了）",
                value=min(min_date + timedelta(days=14), max_date),
                min_value=min_date, max_value=max_date,
                key="fc_range_to")

        # 対象発注を絞り込み
        _dt_as_date = order_plan["納品予定日_dt"].apply(
            lambda x: x if isinstance(x, date) else x.date())
        sel_orders = order_plan[
            (_dt_as_date >= range_from) &
            (_dt_as_date <= range_to)
        ]

        if sel_orders.empty:
            st.info("指定した期間に発注はありません")
        else:
            sel_total = sel_orders["発注量kg"].sum()
            st.markdown(f"**対象: {len(sel_orders)}回　合計: {sel_total:,.0f} kg**")
            st.dataframe(
                sel_orders[["納品予定日","日齢","発注量kg","発注種別"]],
                use_container_width=True, hide_index=True)

            # 発注書プレビュー
            farm_id_fc = next(
                (ln["farm_id"] for ln in lot_numbers
                 if ln["lot_number_id"] == sel_fh["lot_number_id"]), None)
            farm_nm_fc = farm_map.get(farm_id_fc, "")
            lines = [
                f"【飼料発注】{farm_nm_fc}",
                f"発注日: {date.today()}",
                "",
            ]
            for _, r in sel_orders.iterrows():
                lines.append(
                    f"  納品予定日: {r['納品予定日']}（日齢{r['日齢']}日）"
                    f"　合計 {r['発注量kg']:,.0f} kg"
                    f"　内訳: {r['発注種別']}")
            lines += ["", f"合計: {sel_total:,.0f} kg", "", "よろしくお願いいたします。", farm_nm_fc]
            preview_text = "\n".join(lines)
            st.text_area("発注書プレビュー", value=preview_text, height=200, key="fc_preview")

            # 登録・送信ボタン
            bc1, bc2 = st.columns([1, 3])
            with bc1:
                if st.button("💾 発注登録", type="primary", key="fc_order_save"):
                    try:
                        for _, r in sel_orders.iterrows():
                            brand_id = next(
                                (b["feed_brand_id"] for b in feed_brands
                                 if b["brand_name"] == r["使用銘柄"]), None)
                            res_ord = supabase.table("feed_orders").insert({
                                "farm_id":         farm_id_fc,
                                "order_date":      str(date.today()),
                                "delivery_date":   str(r["納品予定日_dt"].date()),
                                "lead_time_days":  0,
                                "total_order_qty": float(r["発注量kg"]),
                                "status":          "発注済",
                            }).execute()
                            order_id = res_ord.data[0]["order_id"]
                            supabase.table("feed_order_details").insert({
                                "order_id":        order_id,
                                "flock_house_id":  sel_fh_id,
                                "feed_brand_id":   brand_id,
                                "order_qty":       float(r["発注量kg"]),
                                "tank_remaining":  float(r["タンク残量kg"]),
                                "stock_days":      None,
                            }).execute()
                        st.session_state["fc_msg"] = f"✅ {len(sel_orders)}件の発注を登録しました"
                        st.session_state["fc_preview"] = preview_text
                        st.rerun()
                    except Exception as e:
                        st.error(f"登録エラー: {e}")

            with bc2:
                # 登録後のメール送信
                if "fc_msg" in st.session_state:
                    st.success(st.session_state.pop("fc_msg"))
                    email_settings = fetch("email_settings", "setting_id")
                    if email_settings:
                        setting_opts = {es["setting_name"]: es for es in email_settings}
                        sel_es = st.selectbox("送信先",
                            list(setting_opts.keys()), key="fc_email_sel")
                        es = setting_opts[sel_es]
                        to_addr = st.text_input("宛先", value=es["to_address"] or "", key="fc_to")
                        subject = st.text_input("件名",
                            value=f"【飼料発注】{farm_nm_fc} {date.today()}", key="fc_subj")
                        body = st.text_area("本文",
                            value=st.session_state.get("fc_preview", ""),
                            height=150, key="fc_body")
                        if st.button("📧 メール送信", key="fc_send", type="primary"):
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
                                    msg["Subject"] = subject
                                    msg.attach(MIMEText(body, "plain", "utf-8"))
                                    with smtplib.SMTP(smtp_host, smtp_port) as server:
                                        server.starttls()
                                        server.login(smtp_user, smtp_pass)
                                        server.sendmail(smtp_user, [to_addr], msg.as_string())
                                    st.success(f"✅ 送信完了 → {to_addr}")
                            except Exception as e:
                                st.error(f"送信エラー: {e}")
                    else:
                        st.warning("メール設定がありません")

except Exception as e:
    st.error(f"発注予測エラー: {e}")
    import traceback
    st.code(traceback.format_exc())
