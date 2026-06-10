import requests
import csv
import json
import time

# ── Config ────────────────────────────────────────────
PASSKEY    = "caStwhUd1zHx4z11vgXT7I53nTyXTngmVpOJ95WGztsKI"
PRODUCT_ID = "5010859059"
LOCALE     = "it_IT"
LIMIT      = 100          # reviews per request (max 100)
OUTPUT_CSV = "douglas_reviews.csv"
# ──────────────────────────────────────────────────────

BASE_URL = "https://api.bazaarvoice.com/data/reviews.json"

all_reviews   = []
offset        = 0
total_results = None

print(f"Starting scrape for product {PRODUCT_ID}...")

while True:
    params = {
        "apiversion":        "5.4",
        "passkey":           PASSKEY,
        "Filter":            f"ProductId:{PRODUCT_ID}",
        "Filter_IsRatingsOnly": "false",
        "Sort":              "SubmissionTime:desc",
        "Limit":             LIMIT,
        "Offset":            offset,
        "locale":            LOCALE,
    }

    resp = requests.get(BASE_URL, params=params, timeout=15)
    print(f"  Page offset={offset} → Status: {resp.status_code}")

    if resp.status_code != 200:
        print(f"  ❌ Error: {resp.text[:200]}")
        break

    data = resp.json()

    # First call — get total
    if total_results is None:
        total_results = data.get("TotalResults", 0)
        print(f"  Total reviews to fetch: {total_results}")

    results = data.get("Results", [])
    if not results:
        break

    for r in results:
        all_reviews.append({
            "author":   r.get("UserNickname", "Anonymous"),
            "rating":   r.get("Rating", ""),
            "title":    r.get("Title", ""),
            "text":     r.get("ReviewText", ""),
            "date":     r.get("SubmissionTime", ""),
            "helpful":  r.get("TotalPositiveFeedbackCount", 0),
            "verified": r.get("BadgesOrder", []),
        })

    print(f"  ✅ Collected {len(all_reviews)} / {total_results}")

    offset += LIMIT
    if offset >= total_results:
        break

    time.sleep(0.5)   # be polite

# ── Save ──────────────────────────────────────────────
if all_reviews:
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_reviews[0].keys())
        writer.writeheader()
        writer.writerows(all_reviews)

    with open("douglas_reviews.json", "w", encoding="utf-8") as f:
        json.dump(all_reviews, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Done! Saved {len(all_reviews)} reviews to {OUTPUT_CSV}")
    print("\nFirst review preview:")
    print(json.dumps(all_reviews[0], indent=2, ensure_ascii=False))
else:
    print("❌ No reviews found")