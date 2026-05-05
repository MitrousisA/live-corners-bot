"""
⚡ Live Corners Bot
====================
Τρέχει κάθε 5 λεπτά κατά τη διάρκεια αγώνων.
Παίρνει live corners, shots, possession από API-Football.
Στέλνει alert στο Telegram αν υπάρχει value.

Πώς λειτουργεί:
1. Βρίσκει όλους τους live αγώνες
2. Παίρνει live στατιστικά (corners, shots κλπ)
3. Παίρνει live odds για corners
4. Ο Claude αναλύει και αποφασίζει
5. Αν υπάρχει value → Telegram alert
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

# Αποθηκεύουμε τα alerts που έχουμε ήδη στείλει (ανά fixture)
# ώστε να μην στέλνουμε το ίδιο alert πολλές φορές
sent_alerts = {}

# ─── API CALLS ───────────────────────────────────────────────────────────────

def get_live_fixtures():
    """Παίρνει όλους τους live αγώνες."""
    try:
        resp = requests.get(
            f"{BASE_URL}/fixtures",
            headers=HEADERS,
            params={"live": "all"},
            timeout=10
        )
        if resp.status_code != 200:
            return []
        fixtures = resp.json().get("response", [])
        # Φιλτράρουμε μόνο τα πρωταθλήματα που μας ενδιαφέρουν
        return [f for f in fixtures if f["league"]["id"] in LEAGUES]
    except:
        return []


def get_fixture_stats(fixture_id):
    """Παίρνει live στατιστικά για έναν αγώνα."""
    try:
        resp = requests.get(
            f"{BASE_URL}/fixtures/statistics",
            headers=HEADERS,
            params={"fixture": fixture_id},
            timeout=10
        )
        if resp.status_code != 200:
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
    except:
        return {}


def get_live_odds(fixture_id):
    """Παίρνει live odds για corners."""
    try:
        resp = requests.get(
            f"{BASE_URL}/odds/live",
            headers=HEADERS,
            params={"fixture": fixture_id},
            timeout=10
        )
        if resp.status_code != 200:
            return "N/A"
        data = resp.json().get("response", [])
        if not data:
            return "N/A"

        # Ψάχνουμε για corner odds
        for fixture_odds in data:
            for bookmaker in fixture_odds.get("bookmakers", []):
                for bet in bookmaker.get("bets", []):
                    if "corner" in bet.get("name", "").lower():
                        values = bet.get("values", [])
                        over_val = next((v for v in values if v.get("value") == "Over" and v.get("main")), None)
                        under_val = next((v for v in values if v.get("value") == "Under" and v.get("main")), None)
                        if over_val and under_val:
                            return f"Over: {over_val.get('odd')} | Under: {under_val.get('odd')} | Line: {over_val.get('handicap')}"
        return "N/A"
    except:
        return "N/A"


# ─── CLAUDE ANALYSIS ──────────────────────────────────────────────────────────

def analyze_with_claude(match_info: dict) -> dict:
    """
    Ο Claude αναλύει τα live δεδομένα και αποφασίζει αν υπάρχει value.
    Επιστρέφει: {"alert": True/False, "message": "..."}
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    home = match_info["home"]
    away = match_info["away"]
    minute = match_info["minute"]
    stats = match_info["stats"]
    odds = match_info["odds"]
    league = match_info["league"]

    # Μορφοποιούμε τα στατιστικά
    home_stats = stats.get(home, {})
    away_stats = stats.get(away, {})

    home_corners   = home_stats.get("Corner Kicks", 0) or 0
    away_corners   = away_stats.get("Corner Kicks", 0) or 0
    home_shots     = home_stats.get("Total Shots", 0) or 0
    away_shots     = away_stats.get("Total Shots", 0) or 0
    home_dangerous = home_stats.get("Dangerous Attacks", 0) or 0
    away_dangerous = away_stats.get("Dangerous Attacks", 0) or 0
    home_possession = home_stats.get("Ball Possession", "N/A")
    away_possession = away_stats.get("Ball Possession", "N/A")

    total_corners = home_corners + away_corners
    minutes_played = int(minute) if str(minute).isdigit() else 45
    corners_per_minute = total_corners / max(minutes_played, 1)
    projected_corners = corners_per_minute * 90

    prompt = f"""Είσαι έμπειρος αναλυτής live ποδοσφαίρου με εξειδίκευση στα corners.

LIVE ΑΓΩΝΑΣ:
  {home} vs {away}
  Πρωτάθλημα: {league}
  Λεπτό: {minute}'

LIVE ΣΤΑΤΙΣΤΙΚΑ:
  Corners: {home} {home_corners} - {away_corners} {away}
  Σύνολο corners: {total_corners}
  Shots: {home} {home_shots} - {away_shots} {away}
  Dangerous Attacks: {home} {home_dangerous} - {away_dangerous} {away}
  Possession: {home} {home_possession} - {away_possession} {away}

ΑΝΑΛΥΣΗ ΡΥΘΜΟΥ:
  Corners/λεπτό: {corners_per_minute:.2f}
  Προβλεπόμενα corners 90': {projected_corners:.1f}

LIVE ODDS CORNERS:
  {odds}

ΑΝΑΛΥΣΕ και απάντησε ΜΟΝΟ σε JSON format:
{{
  "alert": true/false,
  "bet": "Over X.X corners" ή "Under X.X corners" ή "none",
  "confidence": 0-100,
  "reason": "σύντομη αιτιολόγηση 1-2 γραμμές"
}}

ΚΑΝΟΝΕΣ:
- alert=true ΜΟΝΟ αν εμπιστοσύνη ≥ 75%
- Σκέψου: ρυθμό corners, λεπτό αγώνα, odds value
- Αν λεπτό > 75, μόνο Under μπορεί να έχει value
- Αν odds = N/A, alert=false
- Να είσαι ΣΥΝΤΗΡΗΤΙΚΟΣ
"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        import json
        text = message.content[0].text.strip()
        # Καθαρίζουμε το JSON
        text = text.replace("```json", "").replace("```", "").strip()
        result = json.loads(text)
        return result
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
    print("⚡ Live Corners Bot ξεκίνησε!")
    send_telegram("⚡ *Live Corners Bot ξεκίνησε!*\nΠαρακολουθώ live αγώνες για corner value...")

    while True:
        now = datetime.now()
        print(f"\n🔄 {now.strftime('%H:%M:%S')} — Έλεγχος live αγώνων...")

        fixtures = get_live_fixtures()

        if not fixtures:
            print("  Δεν υπάρχουν live αγώνες αυτή τη στιγμή.")
        else:
            print(f"  ✅ {len(fixtures)} live αγώνες βρέθηκαν.")

            for f in fixtures:
                fixture_id = f["fixture"]["id"]
                home = f["teams"]["home"]["name"]
                away = f["teams"]["away"]["name"]
                league = f["league"]["name"]
                minute = f["fixture"]["status"].get("elapsed", 0) or 0
                score_home = f["goals"]["home"] or 0
                score_away = f["goals"]["away"] or 0

                print(f"  📊 {home} {score_home}-{score_away} {away} ({minute}')")

                # Παράλειψη αν είναι πολύ νωρίς (< 20') ή τελείωσε (> 88')
                if minute < 20 or minute > 88:
                    print(f"    ⏭️ Παράλειψη (λεπτό {minute})")
                    continue

                # Παίρνουμε στατιστικά
                stats = get_fixture_stats(fixture_id)
                odds  = get_live_odds(fixture_id)

                match_info = {
                    "home":   home,
                    "away":   away,
                    "league": league,
                    "minute": minute,
                    "stats":  stats,
                    "odds":   odds,
                }

                # Ανάλυση με Claude
                result = analyze_with_claude(match_info)
                print(f"    🤖 Alert: {result.get('alert')} | {result.get('bet')} ({result.get('confidence')}%)")

                # Αν υπάρχει alert και δεν το έχουμε ήδη στείλει
                alert_key = f"{fixture_id}_{result.get('bet')}"
                if result.get("alert") and alert_key not in sent_alerts:
                    sent_alerts[alert_key] = True

                    home_corners = stats.get(home, {}).get("Corner Kicks", 0) or 0
                    away_corners = stats.get(away, {}).get("Corner Kicks", 0) or 0
                    home_shots   = stats.get(home, {}).get("Total Shots", 0) or 0
                    away_shots   = stats.get(away, {}).get("Total Shots", 0) or 0

                    message = f"""⚡ *LIVE CORNER ALERT* — {minute}'
🏆 {league}
⚽ *{home} {score_home}-{score_away} {away}*

📐 Corners: {home_corners} - {away_corners} (Σύνολο: {home_corners + away_corners})
🎯 Shots: {home_shots} - {away_shots}
📊 Odds: {odds}

✅ *Πρόταση: {result.get('bet')}* ({result.get('confidence')}%)
💬 {result.get('reason')}"""

                    send_telegram(message)
                    print(f"    📨 Alert στάλθηκε!")

        # Περιμένουμε 5 λεπτά
        print(f"  ⏳ Επόμενος έλεγχος σε 5 λεπτά...")
        time.sleep(300)


if __name__ == "__main__":
    main()
