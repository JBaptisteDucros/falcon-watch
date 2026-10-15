#!/usr/bin/env python3
"""
Falcon 8X watcher (Aerobask) -> notifications Telegram.

Principe : toutes les INTERVAL secondes, le script télécharge chaque page
de la liste PAGES. Il t'alerte si :
  - un mot-clé de sortie apparaît (KEYWORDS) alors qu'il n'y était pas avant,
  - ou (optionnel) si le contenu de la page change.

Installation :  pip install requests
Lancement    :  python falcon_watch.py
Test Telegram:  python falcon_watch.py --test

Configuration Telegram (une seule fois) :
  1. Sur Telegram, parle à @BotFather -> /newbot -> récupère le TOKEN.
  2. Envoie un message quelconque à ton nouveau bot.
  3. Ouvre https://api.telegram.org/bot<TOKEN>/getUpdates et repère "chat":{"id":...}.
  4. Mets les deux valeurs en variables d'environnement :
       export TELEGRAM_TOKEN="123456:ABC..."
       export TELEGRAM_CHAT_ID="123456789"
"""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

# ----------------------------------------------------------------- CONFIG --

# A ADAPTER : colle ici les URLs exactes (page du store, fil du forum
# X-Plane.org, actualités Aerobask, site Dassault...).
PAGES = [
    {"name": "Dassault Store (X-Plane)",
     "url": "https://www.thestore-dassault-aviation.com/en/simulation/x-plane",
     "alert_on_change": True},
    {"name": "Aerobask (page Falcon 8X)",
     "url": "https://aerobask.com/falcon_8x.php",
     "alert_on_change": True},
    {"name": "X-Plane.Org Store (Aerobask)",
     "url": "https://store.x-plane.org/Aerobask_c_151.html",
     "alert_on_change": True},
    # Forum : pas d'alerte à chaque changement, mais un récap dédié dans le rapport de 22h.
    {"name": "X-Plane.Org Forum",
     "url": "https://forums.x-plane.org",
     "alert_on_change": False,
     "forum": True},
]

# Mots-clés (insensibles à la casse) qui indiquent une sortie / mise en vente.
KEYWORDS = [
    r"falcon\s*8x.{0,200}(available|released|buy|add to cart|order|beta|keys)",
    r"(available|released|buy|add to cart|order|beta|keys).{0,200}falcon\s*8x",
    r"limited beta",
]

INTERVAL = 300  # secondes entre deux passages (5 min, reste raisonnable)
STATE_FILE = Path("falcon_watch_state.json")
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ------------------------------------------------------------------- CODE --


def notify(text: str) -> None:
    print(f"[ALERTE] {text}")
    if not (TOKEN and CHAT_ID):
        print("  (Telegram non configuré : TELEGRAM_TOKEN / TELEGRAM_CHAT_ID)")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": False},
            timeout=15,
        )
    except requests.RequestException as e:
        print(f"  Erreur Telegram : {e}")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def clean_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def keyword_hit(text: str):
    for pattern in KEYWORDS:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return m.group(0)[:200]
    return None


def check_page(page: dict, state: dict) -> None:
    url = page["url"]
    try:
        # Durée totale limitée à 30 s par page (évite les sites qui répondent au compte-gouttes).
        deadline = time.time() + 30
        r = requests.get(url, headers=HEADERS, timeout=(10, 10), stream=True)
        r.raise_for_status()
        chunks = []
        for chunk in r.iter_content(chunk_size=8192):
            chunks.append(chunk)
            if time.time() > deadline:
                raise requests.RequestException("délai total dépassé (30 s)")
        body = b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")
    except requests.RequestException as e:
        print(f"[{time.strftime('%H:%M:%S')}] {page['name']} : erreur ({e})")
        return

    text = clean_text(body)
    digest = hashlib.sha256(text.encode()).hexdigest()
    hit = keyword_hit(text)
    prev = state.get(url)  # None = premier passage

    if prev is None:
        # Premier passage : on enregistre l'état de référence sans alerter,
        # sauf si un mot-clé est déjà présent.
        state[url] = {"hash": digest, "hit": bool(hit)}
        if hit:
            notify(f"Falcon 8X : mot-clé déjà présent sur {page['name']}\n«{hit}»\n{url}")
        print(f"[{time.strftime('%H:%M:%S')}] {page['name']} : état initial enregistré")
        return

    if hit and not prev["hit"]:
        notify(f"Falcon 8X : annonce détectée sur {page['name']} !\n«{hit}»\n{url}")
    elif page.get("alert_on_change") and digest != prev["hash"]:
        notify(f"Changement détecté sur {page['name']}\n{url}")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] {page['name']} : rien de nouveau")

    state[url] = {"hash": digest, "hit": bool(hit)}


def fetch_body(url: str) -> str:
    deadline = time.time() + 30
    r = requests.get(url, headers=HEADERS, timeout=(10, 10), stream=True)
    r.raise_for_status()
    chunks = []
    for chunk in r.iter_content(chunk_size=8192):
        chunks.append(chunk)
        if time.time() > deadline:
            raise requests.RequestException("délai total dépassé (30 s)")
    return b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")


def daily_report() -> None:
    """Message récapitulatif : état de chaque page, sans modifier l'état mémorisé."""
    lines = ["Rapport Falcon 8X (22h)"]
    problems = 0
    for page in PAGES:
        try:
            text = clean_text(fetch_body(page["url"]))
            hit = keyword_hit(text)
            if page.get("forum"):
                # Récap spécial forum : extraits autour de "Falcon 8X" / "Aerobask".
                snippets, seen = [], set()
                for m in re.finditer(r"falcon\s*8x|aerobask", text, flags=re.IGNORECASE):
                    snip = text[max(0, m.start() - 50): m.end() + 70].strip()
                    key = snip[:40].lower()
                    if key not in seen:
                        seen.add(key)
                        snippets.append(snip)
                    if len(snippets) >= 5:
                        break
                lines.append("")
                lines.append(f"FORUM ({page['name']}) :")
                if snippets:
                    lines.append(f"{len(snippets)} mention(s) Falcon 8X / Aerobask sur la page :")
                    lines.extend(f"  . ...{s}..." for s in snippets)
                else:
                    lines.append("  aucune mention de Falcon 8X ou d'Aerobask sur la page d'accueil.")
                if hit:
                    lines.append(f"  mot-clé de sortie détecté : «{hit[:80]}»")
                lines.append("")
            elif hit:
                lines.append(f"- {page['name']} : mot-clé détecté «{hit[:80]}»")
            else:
                lines.append(f"- {page['name']} : OK, rien de nouveau")
        except requests.RequestException as e:
            problems += 1
            lines.append(f"- {page['name']} : INACCESSIBLE ({e})")
    lines.append("")
    lines.append("Le bot tourne bien." if problems == 0
                 else f"Le bot tourne, mais {problems} page(s) injoignable(s).")
    notify("\n".join(lines))


def main() -> None:
    if "--test" in sys.argv:
        notify("Test : le bot Falcon 8X fonctionne.")
        return

    if "--daily" in sys.argv:
        # Les crons GitHub sont en UTC : on lance à 20h et 21h UTC et on ne
        # garde que l'exécution qui tombe à 22h à Paris (heure d'été ou d'hiver).
        from datetime import datetime
        from zoneinfo import ZoneInfo
        if "--force" in sys.argv or datetime.now(ZoneInfo("Europe/Paris")).hour == 22:
            daily_report()
        else:
            print("Pas 22h à Paris : rapport ignoré.")
        return

    state = load_state()
    if "--once" in sys.argv:
        # Mode GitHub Actions : un seul passage, puis on quitte.
        for page in PAGES:
            check_page(page, state)
        save_state(state)
        return

    print(f"Surveillance de {len(PAGES)} page(s), toutes les {INTERVAL}s. Ctrl+C pour arrêter.")
    while True:
        for page in PAGES:
            check_page(page, state)
            save_state(state)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
