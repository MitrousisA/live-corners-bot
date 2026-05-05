"""
⚡ Live Corners Bot - Optimized
================================
Βέλτιστη χρήση requests (< 100/ημέρα):

Λογική:
1. 1 request για όλα τα live fixtures (με βασικά events)
2. Statistics ΜΟΝΟ για αγώνες 25'-75' με ενδιαφέρον
3. Odds ΜΟΝΟ αν corners > threshold για το λεπτό
4. Interval: 10 λεπτά

Εκτιμώμενα requests:
- ~6 κύκλοι/ώρα × (1 + 2-3 αγώνες × 2) = ~42 requests/ώρα
- Για 2 ώρες αγώνων/ημέρα: ~84 requests ✅
"""

import os
import time
import requests
import anthropic
from datetime import datetime

# ─── KEYS ────────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
API_FOOTBALL_KEY   = os.environ["RAPIDAPI_KEY"]
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]

BASE_URL = "https://v3.football.api-sports.io"
HEADERS  = {"x-apisports-key": API_FOOTBALL_KEY}

# Πρωταθλήματα που παρακολουθούμε
LEAGUES = {39, 140, 135, 78, 61, 2, 3}

# Αποθήκη αποσταλμένων alerts
sent_alerts = {}

# Counter requests
request_count = 0
request_date  = datetime.now().date()

# ─── REQUEST COUNTER ─────────────────────────────────────────────────────────

def api_get(url, params=None):
    """Κάνει GET request και μετράει τα requests."""
    global request_count, request_date

    # Reset counter κάθε μέρα
    today = datetime.now().date()
    if today != request_date:
        request_count = 0
        request_date  = today

    # Σταματάμε αν πλησιάζουμε το όριο
    if request_count >= 90:
        print(f"  ⚠️ Όριο requests! ({request_count}/100) — Σταματάω για σήμερα.")
        return None

    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        request_count += 1
        print(f"  📡 Request #{request_count}: {url.split('/')[-1]}")
        return resp
    except:
        return None


# ─── CORNER THRESHOLD ────────────────────────────────────────────────────────

def is_interesting(corners_total, minute):
    """
    Ελέγχει αν ο αγώνας είναι ενδιαφέρον για corners ανάλυση.
    Βάσει ρυθμού corners ανά λεπτό.
    """
    if minute < 25 or minute > 75:
        return False

    rate = corners_total / minute  # corners ανά λεπτό
    projected = rate * 90

    # Ενδιαφέρον αν προβλέπονται > 8 ή < 7 corners
    if projected > 8.5 or projected < 6.5:
        return True

    # Ή αν υπάρχουν ήδη πολλά corners νωρίς
    if minute < 40 and corners_total >= 5:
        return True
    if minute < 60 and corners_total >= 8:
        return True

    return False


# ─── API CALLS ───────────────────────────────────────────────────────────────

def get_live_fixtures():
    """
    Παίρνει live fixtures ΜΕ τα events included.
    1 request που δίνει: score, status, events (γκολ, κάρτες κλπ).
    """
    resp = api_get(f"{BASE_URL}/fixtures", {"live": "all"})
    if not resp or resp.status_code != 200:
        return []

    fixtures = resp.json().get("response", [])
    # Φιλτράρουμε μόνο τα πρωταθλήματα μας
    return [f for f in fixtures if f["league"]["id"] in LEAGUES]


def get_fixture_stats(fixture_id):
    """Παίρνει στατιστικά — καλείται ΜΟΝΟ για ενδιαφέροντες αγώνες."""
    resp = api_get(f"{BASE_URL}/fixtures/statistics", {"fixture": fixture_id})
    if not resp or resp.status_code != 200:
        return {}

    data = resp.json().get("response", [])
    if not data:
        return {}

    stats = {}
    for team_data in data:
        team_name = team_data["team"]["name"]
        team_stats = {}
        for stat in team_data.get("statistics", []):
            team_stats[stat["type"]] = stat["value"]
        stats[team_name] = team_stats
    return stats


def get_live_corner_odds(fixture_id):
    """Παίρνει live corner odds — καλείται ΜΟΝΟ αν αγώνας είναι ενδιαφέρων."""
    resp = api_get(f"{BASE_URL}/odds/live", {"fixture": fixture_id})
    if not resp or resp.status_code != 200:
        return "N/A"

    data = resp.json().get("response", [])
    if not data:
        return "N/A"

    for fixture_odds in data:
        for bookmaker in fixture_odds.get("bookmakers", []):
            for bet in bookmaker.get("bets", []):
                if "corner" in bet.get("name", "").lower():
                    values = bet.get("values", [])
                    over_val  = next((v for v in values if v.get("value") == "Over"  and v.get("main")), None)
                    under_val = next((v for v in values if v.get("value") == "Under" and v.get("main")), None)
                    if over_val and under_val:
                        return f"Over {over_val.get('handicap')}: {over_val.get('odd')} | Under: {under_val.get('odd')}"
    return "N/A"


# ─── CLAUDE ANALYSIS ──────────────────────────────────────────────────────────

def analyze_with_claude(match_info: dict) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    home = match_info["home"]
    away = match_info["away"]
    minute = match_info["minute"]
    stats = match_info["stats"]
    odds = match_info["odds"]
    league = match_info["league"]
    score_home = match_info["score_home"]
    score_away = match_info["score_away"]

    home_stats = stats.get(home, {})
    away_stats = stats.get(away, {})

    home_corners    = home_stats.get("Corner Kicks", 0) or 0
    away_corners    = away_stats.get("Corner Kicks", 0) or 0
    home_shots      = home_stats.get("Total Shots", 0) or 0
    away_shots      = away_stats.get("Total Shots", 0) or 0
    home_dangerous  = home_stats.get("Dangerous Attacks", 0) or 0
    away_dangerous  = away_stats.get("Dangerous Attacks", 0) or 0
    home_possession = home_stats.get("Ball Possession", "N/A")
    away_possession = away_stats.get("Ball Possession", "N/A")

    total_corners = home_corners + away_corners
    corners_per_minute = total_corners / max(int(minute), 1)
    projected_corners  = round(corners_per_minute * 90, 1)

    prompt = f"""Είσαι έμπειρος αναλυτής live ποδοσφαίρου με εξειδίκευση στα corners.

LIVE ΑΓΩΝΑΣ: {home} {score_home}-{score_away} {away} | {league} | {minute}'

ΣΤΑΤΙΣΤΙΚΑ:
  Corners: {home} {home_corners} - {away_corners} {away} (Σύνολο: {total_corners})
  Shots: {home_shots} - {away_shots}
  Dangerous Attacks: {home_dangerous} - {away_dangerous}
  Possession: {home_possession} - {away_possession}

ΑΝΑΛΥΣΗ:
  Ρυθμός: {corners_per_minute:.2f} corners/λεπτό
  Προβλεπόμενα στο 90': {projected_corners}

LIVE ODDS: {odds}

Απάντησε ΜΟΝΟ σε JSON:
{{"alert": true/false, "bet": "Over X.X" ή "Under X.X" ή "none", "confidence": 0-100, "reason": "1-2 γραμμές"}}

ΚΑΝΟΝΕΣ:
- alert=true ΜΟΝΟ αν confidence ≥ 75%
- Αν odds=N/A → alert=false
- Λεπτό > 70': μόνο Under έχει νόημα
- Να είσαι ΣΥΝΤΗΡΗΤΙΚΟΣ
"""

    try:
        import json
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        text = message.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except:
        return {"alert": False, "bet": "none", "confidence": 0, "reason": "Error"}


# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": "Markdown"
        }, timeout=10)
    except:
        pass


# ─── MAIN LOOP ────────────────────────────────────────────────────────────────

def main():
    print("⚡ Live Corners Bot ξεκίνησε! (Optimized)")
    send_telegram("⚡ *Live Corners Bot ξεκίνησε!*\nΠαρακολουθώ live αγώνες για corner value...")

    while True:
        now = datetime.now()
        print(f"\n🔄 {now.strftime('%H:%M:%S')} — Έλεγχος (Requests σήμερα: {request_count}/100)")

        # Αν φτάσαμε το όριο, περιμένουμε μέχρι αύριο
        if request_count >= 90:
            print("  ⚠️ Όριο requests — Κοιμάμαι μέχρι αύριο...")
            time.sleep(3600)
            continue

        # 1. Παίρνουμε live fixtures (1 request)
        fixtures = get_live_fixtures()

        if not fixtures:
            print("  Δεν υπάρχουν live αγώνες.")
        else:
            print(f"  ✅ {len(fixtures)} live αγώνες.")

            for f in fixtures:
                fixture_id = f["fixture"]["id"]
                home       = f["teams"]["home"]["name"]
                away       = f["teams"]["away"]["name"]
                league     = f["league"]["name"]
                minute     = f["fixture"]["status"].get("elapsed", 0) or 0
                score_home = f["goals"]["home"] or 0
                score_away = f["goals"]["away"] or 0

                # Παίρνουμε corners από τα events του fixture
                events = f.get("events", [])
                home_id = f["teams"]["home"]["id"]
                home_corners = sum(1 for e in events if e.get("type") == "subst" and False)  # placeholder
                # Τα corners δεν είναι στα events — χρειάζονται statistics

                # Πρώτα ελέγχουμε αν αξίζει να κάνουμε statistics request
                # Βάσει λεπτού μόνο για αρχή
                if minute < 25 or minute > 75:
                    print(f"  ⏭️ {home} vs {away} — Παράλειψη (λεπτό {minute})")
                    continue

                print(f"  📊 {home} {score_home}-{score_away} {away} ({minute}') — Παίρνω στατιστικά...")

                # 2. Statistics request (μόνο για αγώνες στο σωστό λεπτό)
                stats = get_fixture_stats(fixture_id)
                if not stats:
                    continue

                home_corners = stats.get(home, {}).get("Corner Kicks", 0) or 0
                away_corners = stats.get(away, {}).get("Corner Kicks", 0) or 0
                total_corners = home_corners + away_corners

                # Ελέγχουμε αν είναι ενδιαφέρον
                if not is_interesting(total_corners, minute):
                    print(f"    Corners: {total_corners} στο {minute}' — Δεν είναι ενδιαφέρον")
                    continue

                print(f"    ⭐ Ενδιαφέρον! Corners: {total_corners} στο {minute}' — Παίρνω odds...")

                # 3. Odds request (μόνο για ενδιαφέροντες αγώνες)
                odds = get_live_corner_odds(fixture_id)

                match_info = {
                    "home":       home,
                    "away":       away,
                    "league":     league,
                    "minute":     minute,
                    "score_home": score_home,
                    "score_away": score_away,
                    "stats":      stats,
                    "odds":       odds,
                }

                # 4. Claude ανάλυση
                result = analyze_with_claude(match_info)
                print(f"    🤖 {result.get('bet')} ({result.get('confidence')}%) — Alert: {result.get('alert')}")

                # 5. Telegram alert αν υπάρχει value
                alert_key = f"{fixture_id}_{result.get('bet')}"
                if result.get("alert") and alert_key not in sent_alerts:
                    sent_alerts[alert_key] = True

                    home_shots    = stats.get(home, {}).get("Total Shots", 0) or 0
                    away_shots    = stats.get(away, {}).get("Total Shots", 0) or 0
                    home_poss     = stats.get(home, {}).get("Ball Possession", "N/A")
                    away_poss     = stats.get(away, {}).get("Ball Possession", "N/A")
                    projected     = round((total_corners / max(minute, 1)) * 90, 1)

                    message = f"""⚡ *LIVE CORNER ALERT* — {minute}'
🏆 {league}
⚽ *{home} {score_home}-{score_away} {away}*

📐 Corners: {home_corners} - {away_corners} (Σύνολο: {total_corners})
🎯 Shots: {home_shots} - {away_shots}
🔵 Possession: {home_poss} - {away_poss}
📈 Προβλεπόμενα 90': {projected}
💰 Odds: {odds}

✅ *Πρόταση: {result.get('bet')}* ({result.get('confidence')}%)
💬 {result.get('reason')}"""

                    send_telegram(message)
                    print(f"    📨 Alert στάλθηκε!")

        # 10 λεπτά αναμονή
        print(f"  ⏳ Επόμενος έλεγχος σε 10 λεπτά... (Requests: {request_count}/100)")
        time.sleep(600)


if __name__ == "__main__":
    main()
