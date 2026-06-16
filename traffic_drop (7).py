import pandas as pd
import requests
import time
import json
import os
from datetime import datetime
from dateutil.relativedelta import relativedelta

# ── CONFIG ──────────────────────────────────────────────────────────────────
SEMRUSH_API_KEY = "YOUR_SEMRUSH_API_KEY_HERE"
DROP_THRESHOLD  = 20
REQUEST_TIMEOUT = 8
DELAY_BETWEEN   = 1
CHECKPOINT_FILE = "traffic_drop_checkpoint.json"

# ── DATE SETUP ───────────────────────────────────────────────────────────────
now            = datetime.now()
current_month  = (now - relativedelta(months=1)).strftime("%Y%m15")
old_month      = (now - relativedelta(months=7)).strftime("%Y%m15")

# ── CHECKPOINT HELPERS ───────────────────────────────────────────────────────
def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {}

def save_checkpoint(checkpoint):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint, f, indent=2)

# ── HELPERS ──────────────────────────────────────────────────────────────────
def get_traffic_history(domain):
    """
    Fetches traffic history for a domain.
    display_limit=8 gives us ~8 months of data — enough to cover
    current month + 7 months back, with a small buffer.
    Cost: 10 units per line returned → max 80 units per domain (down from 240).
    """
    try:
        url = (
            f"https://api.semrush.com/?type=domain_rank_history"
            f"&key={SEMRUSH_API_KEY}"
            f"&export_columns=Ot,Dt"
            f"&domain={domain}"
            f"&database=us"
            f"&display_limit=8"  # reduced from 24 — saves ~67% API units
        )
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        lines = r.text.strip().split("\n")
        if len(lines) < 2:
            return {}
        history = {}
        for line in lines[1:]:
            values = line.strip().split(";")
            if len(values) >= 2:
                traffic = int(values[0]) if values[0].isdigit() else None
                date    = values[1].strip()
                if traffic is not None and date:
                    history[date] = traffic
        return history
    except Exception:
        return {}

def calculate_drop(traffic_now, traffic_then):
    if traffic_now is None or traffic_then is None:
        return None
    if traffic_then == 0:
        return None
    return round(((traffic_now - traffic_then) / traffic_then) * 100, 1)

# ── MAIN ─────────────────────────────────────────────────────────────────────
def detect_drops(input_csv, output_csv):
    df = pd.read_csv(input_csv)
    df = df[df["qualified"] == True].reset_index(drop=True)

    print(f"\n📥 Loaded {len(df)} qualified companies")
    print(f"📅 Comparing traffic: up to {old_month} → {current_month}")

    checkpoint = load_checkpoint()
    if checkpoint:
        print(f"🔁 Resuming — {len(checkpoint)} domains already processed\n")
    else:
        print()

    results = []

    for i, row in df.iterrows():
        company = row.get("company_name", "")
        domain  = str(row.get("domain", "")).strip()

        # ── SKIP IF ALREADY PROCESSED ────────────────────────────────────────
        if domain in checkpoint:
            print(f"[{i+1}/{len(df)}] {company} ({domain}) — skipped (checkpoint)")
            results.append(checkpoint[domain])
            continue

        print(f"[{i+1}/{len(df)}] {company} ({domain})", end=" ... ")

        history = get_traffic_history(domain)
        time.sleep(DELAY_BETWEEN)

        traffic_now  = history.get(current_month)

        # ── FALLBACK: if no data from 6 months ago, use earliest available ──
        traffic_then     = history.get(old_month)
        comparison_month = old_month
        using_fallback   = False

        if traffic_then is None and history:
            # Company doesn't have 6 months of history — use oldest available
            oldest_date  = min(history.keys())
            traffic_then = history[oldest_date]
            comparison_month = oldest_date
            using_fallback   = True

        drop_pct = calculate_drop(traffic_now, traffic_then)
        flagged  = drop_pct is not None and drop_pct <= -DROP_THRESHOLD

        fallback_note = f" [fallback: using {comparison_month}]" if using_fallback else ""

        if drop_pct is None:
            print(f"⚠️  no data (available months: {list(history.keys())[:3]})")
        elif flagged:
            print(f"🚨 DROP {drop_pct}% (then:{traffic_then} now:{traffic_now}){fallback_note}")
        else:
            print(f"✅ stable {drop_pct}% (then:{traffic_then} now:{traffic_now}){fallback_note}")

        record = {
            "company_name"      : company,
            "domain"            : domain,
            "industry"          : row.get("industry", ""),
            "country"           : row.get("country", ""),
            "authority_score"   : row.get("authority_score", ""),
            "traffic_6mo_ago"   : traffic_then,
            "traffic_now"       : traffic_now,
            "traffic_drop_pct"  : drop_pct,
            "drop_flagged"      : flagged,
            "comparison_month"  : comparison_month,   # which month was used as baseline
            "used_fallback"     : using_fallback,     # True if < 6 months of history
        }

        # ── SAVE TO CHECKPOINT IMMEDIATELY ───────────────────────────────────
        checkpoint[domain] = record
        save_checkpoint(checkpoint)

        results.append(record)

    out_df = pd.DataFrame(results)
    out_df.to_csv(output_csv, index=False)

    total      = len(out_df)
    flagged    = int(out_df["drop_flagged"].sum())
    no_data    = int(out_df["traffic_drop_pct"].isna().sum())
    stable     = total - flagged - no_data
    fallbacks  = int(out_df["used_fallback"].sum()) if "used_fallback" in out_df.columns else 0

    print(f"\n{'─'*50}")
    print(f"🚨 Drop flagged (≥{DROP_THRESHOLD}%) : {flagged}")
    print(f"✅ Stable                : {stable}")
    print(f"⚠️  No data               : {no_data}")
    print(f"🔄 Used fallback baseline: {fallbacks}")
    print(f"📊 Total                 : {total}")
    print(f"\n💾 Output saved to: {output_csv}")

    # ── CLEAR CHECKPOINT ON SUCCESSFUL COMPLETION ────────────────────────────
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        print(f"🧹 Checkpoint cleared")

if __name__ == "__main__":
    detect_drops(
        input_csv  = "qualified_companies.csv",
        output_csv = "traffic_drop_results.csv"
    )
