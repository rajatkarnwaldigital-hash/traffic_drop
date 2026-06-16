import pandas as pd
import requests
import time
import json
import os

# ── CONFIG ──────────────────────────────────────────────────────────────────
SEMRUSH_API_KEY   = "YOUR_SEMRUSH_API_KEY_HERE"
ANTHROPIC_API_KEY = "YOUR_ANTHROPIC_API_KEY_HERE"
REQUEST_TIMEOUT   = 10
DELAY_BETWEEN     = 1
CHECKPOINT_FILE   = "hook_generator_checkpoint.json"

# ── CHECKPOINT HELPERS ───────────────────────────────────────────────────────
def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {}

def save_checkpoint(checkpoint):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint, f, indent=2)

# ── SEMRUSH: TOP KEYWORDS ────────────────────────────────────────────────────
def get_top_keywords(domain, limit=10):
    """
    Fetches top organic keywords. We grab 10 so Claude has more to filter from.
    Cost: 10 units per line returned.
    """
    try:
        url = (
            f"https://api.semrush.com/?type=domain_organic"
            f"&key={SEMRUSH_API_KEY}"
            f"&export_columns=Ph,Po,Nq"
            f"&domain={domain}"
            f"&database=us"
            f"&display_limit={limit}"
            f"&display_sort=nq_desc"
        )
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        lines = r.text.strip().split("\n")
        if len(lines) < 2:
            return []
        keywords = []
        for line in lines[1:]:
            values = line.strip().split(";")
            if len(values) >= 3:
                keywords.append({
                    "keyword" : values[0],
                    "position": values[1],
                    "volume"  : values[2]
                })
        return keywords
    except Exception:
        return []

# ── CLAUDE API: FILTER + HOOK IN ONE CALL ───────────────────────────────────
def generate_hook(company, domain, industry, drop_pct, traffic_then, traffic_now, keywords):
    """
    Single Claude call that:
    1. Filters irrelevant keywords
    2. Generates personalised outreach hook using only relevant ones
    """
    keywords_str = "\n".join(
        f"- {kw['keyword']} (position #{kw['position']}, {kw['volume']} searches/mo)"
        for kw in keywords
    ) if keywords else "- (no keyword data available)"

    prompt = f"""You are an expert SEO outreach specialist writing cold emails on behalf of an SEO agency.

Company: {company}
Website: {domain}
Industry: {industry}
Organic traffic 6 months ago: {traffic_then:,}
Organic traffic now: {traffic_now:,}
Traffic drop: {abs(drop_pct)}% over 6 months

Their top ranking keywords from SEMrush (may include irrelevant ones):
{keywords_str}

Step 1 — Silently filter the keyword list above. Remove any keywords that are clearly unrelated to {company}'s core business or industry. Keep only keywords that genuinely reflect what this company does or sells.

Step 2 — Using the filtered keywords and traffic data, write a 2-3 sentence cold email opening that:
- Sounds like a human wrote it, not AI
- References a specific detail (keyword, traffic number, or ranking)
- Acknowledges the traffic drop naturally without being alarmist
- Creates curiosity and ends in a way that invites a reply
- Does NOT mention your agency or pitch any services
- Does NOT use "Hi there" — use "Hi [First Name]" as placeholder

Write only the final hook. No explanation, no subject line, no sign off."""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 250,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=REQUEST_TIMEOUT
        )
        data = response.json()
        return data["content"][0]["text"].strip()
    except Exception as e:
        return f"ERROR: {str(e)}"

# ── MAIN ─────────────────────────────────────────────────────────────────────
def generate_hooks(input_csv, output_csv):
    df = pd.read_csv(input_csv)
    df = df[df["drop_flagged"] == True].reset_index(drop=True)
    print(f"\n📥 Loaded {len(df)} flagged companies")

    checkpoint = load_checkpoint()
    if checkpoint:
        print(f"🔁 Resuming — {len(checkpoint)} domains already processed\n")
    else:
        print()

    results = []

    for i, row in df.iterrows():
        company      = row.get("company_name", "")
        domain       = str(row.get("domain", "")).strip()
        industry     = str(row.get("industry", ""))
        drop_pct     = float(row.get("traffic_drop_pct", 0))
        traffic_then = int(row.get("traffic_6mo_ago", 0))
        traffic_now  = int(row.get("traffic_now", 0))

        # ── SKIP IF ALREADY PROCESSED ────────────────────────────────────────
        if domain in checkpoint:
            print(f"[{i+1}/{len(df)}] {company} ({domain}) — skipped (checkpoint)")
            results.append(checkpoint[domain])
            continue

        print(f"[{i+1}/{len(df)}] {company} ({domain})")

        # Step 1: get keywords
        print(f"  → fetching keywords...", end=" ")
        keywords = get_top_keywords(domain)
        print(f"got {len(keywords)}")
        time.sleep(DELAY_BETWEEN)

        # Step 2: filter + generate hook in one Claude call
        print(f"  → generating hook...", end=" ")
        hook = generate_hook(company, domain, industry, drop_pct, traffic_then, traffic_now, keywords)
        print(f"done")
        time.sleep(DELAY_BETWEEN)

        print(f"\n  💬 {hook}\n")

        record = {
            "company_name"    : company,
            "domain"          : domain,
            "industry"        : industry,
            "country"         : row.get("country", ""),
            "authority_score" : row.get("authority_score", ""),
            "traffic_6mo_ago" : traffic_then,
            "traffic_now"     : traffic_now,
            "traffic_drop_pct": drop_pct,
            "top_keywords"    : " | ".join(f"{kw['keyword']} (#{kw['position']})" for kw in keywords),
            "outreach_hook"   : hook
        }

        # ── SAVE TO CHECKPOINT IMMEDIATELY ───────────────────────────────────
        checkpoint[domain] = record
        save_checkpoint(checkpoint)

        results.append(record)

    out_df = pd.DataFrame(results)
    out_df.to_csv(output_csv, index=False)

    print(f"{'─'*50}")
    print(f"✅ Hooks generated : {len(out_df)}")
    print(f"💾 Output saved to : {output_csv}")

    # ── CLEAR CHECKPOINT ON SUCCESSFUL COMPLETION ────────────────────────────
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)
        print(f"🧹 Checkpoint cleared")

if __name__ == "__main__":
    generate_hooks(
        input_csv  = "traffic_drop_results.csv",
        output_csv = "outreach_hooks.csv"
    )
