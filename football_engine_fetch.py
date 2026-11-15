#!/usr/bin/env python3
"""
football_engine_fetch.py : récupère des DONNÉES RÉELLES pour Bi Both et les croise entre sources.

Sources utilisées (API officielles, avec TES clés) :
  - API-Football  (https://www.api-football.com)   variable d'environnement APIFOOTBALL_KEY
  - football-data.org (https://www.football-data.org) variable d'environnement FOOTBALL_DATA_KEY  (optionnelle, sert à croiser)
SofaScore, FotMob et Flashscore n'ont pas d'API publique : les aspirer viole leurs conditions d'utilisation, donc ce script ne le fait pas.

Règles : rien n'est inventé. Un match n'est gardé que si les scores existent ; si deux sources donnent des scores
différents, le match est écrit dans "conflicts" et NE DOIT PAS servir aux calculs. xG et mi-temps ne sont écrits que si l'API les fournit.

Usage :
  export APIFOOTBALL_KEY=...            # obligatoire
  export FOOTBALL_DATA_KEY=...          # optionnel (croisement)
  python3 football_engine_fetch.py --intl --out data/football_data.json            # sans clé : résultats internationaux publics
  python3 football_engine_fetch.py --intl --days-back 30 --days-ahead 3 --xg --out data/football_data.json   # avec API-Football
Puis, dans le site : Profil > Données & moteur > Importer football_data.json
NB : script écrit d'après la documentation officielle des API ; teste d'abord avec --days-back 2 (le plan gratuit d'API-Football est limité en requêtes/jour).
"""
import argparse, json, os, sys, time, datetime as dt
import urllib.request, urllib.parse

AF = "https://v3.football.api-sports.io"
FD = "https://api.football-data.org/v4"
INTL = os.environ.get("RESULTS_URL", "https://raw.githubusercontent.com/martj42/international_results/master/results.csv")

def fetch_intl(out_dir):
    """Télécharge les résultats internationaux réels (dépôt public martj42/international_results, licence CC0).
    Contrôles : colonnes attendues, nombre minimal de lignes, scores présents. En cas de doute, RIEN n'est écrit."""
    import csv, io
    with urllib.request.urlopen(INTL, timeout=60) as r:
        txt = r.read().decode("utf-8-sig")
    rd = csv.DictReader(io.StringIO(txt))
    need = {"date", "home_team", "away_team", "home_score", "away_score", "neutral"}
    if not need.issubset(set(rd.fieldnames or [])):
        sys.exit("Format inattendu pour results.csv : colonnes manquantes, données NON écrites.")
    rows = [r for r in rd if r["date"] >= "2016-01-01" and r["home_score"] not in ("", "NA") and r["away_score"] not in ("", "NA")]
    if len(rows) < 1000:
        sys.exit("Trop peu de lignes (%d) : données NON écrites." % len(rows))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "results.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rd.fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(len(rows), "matchs réels ->", path, "| dernier match :", max(r["date"] for r in rows))

def get(url, headers, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("  ! requête échouée :", url, e, file=sys.stderr)
        return None

def af(path, key, params):
    time.sleep(0.4)  # respecte les limites de débit
    j = get(AF + path, {"x-apisports-key": key}, params)
    return (j or {}).get("response") or []

def fd(path, key, params):
    time.sleep(6.5)  # plan gratuit : 10 requêtes/minute
    return get(FD + path, {"X-Auth-Token": key}, params) or {}

def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def xg_of(fx_id, key):
    """xG fournis par API-Football (type 'expected_goals') ; None si absent."""
    out = {}
    for t in af("/fixtures/statistics", key, {"fixture": fx_id}):
        for s in t.get("statistics", []):
            if s.get("type") == "expected_goals" and s.get("value") not in (None, ""):
                try:
                    out[t["team"]["name"]] = float(s["value"])
                except (TypeError, ValueError):
                    pass
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-back", type=int, default=7)
    ap.add_argument("--days-ahead", type=int, default=3)
    ap.add_argument("--xg", action="store_true", help="1 requête de plus par match terminé")
    ap.add_argument("--odds", action="store_true", help="cotes 1X2 des matchs à venir (1 requête par match)")
    ap.add_argument("--injuries", action="store_true", help="blessures des matchs à venir (1 requête par match)")
    ap.add_argument("--intl", action="store_true", help="télécharge les résultats internationaux publics (sans clé)")
    ap.add_argument("--out", default="football_data.json")
    a = ap.parse_args()
    if a.intl:
        fetch_intl(os.path.dirname(a.out) or ".")
    key = os.environ.get("APIFOOTBALL_KEY")
    if not key:
        if a.intl:
            print("Pas de clé API-Football : seules les données internationales publiques ont été mises à jour.")
            return
        sys.exit("APIFOOTBALL_KEY manquante.")
    fdkey = os.environ.get("FOOTBALL_DATA_KEY")
    today = dt.date.today()
    done, up = {}, []
    for k in range(-a.days_back, a.days_ahead + 1):
        day = today + dt.timedelta(days=k)
        for f in af("/fixtures", key, {"date": day.isoformat()}):
            st = f["fixture"]["status"]["short"]
            h, w = f["teams"]["home"]["name"], f["teams"]["away"]["name"]
            g = f["goals"]
            if st in ("FT", "AET", "PEN") and num(g["home"]) is not None and num(g["away"]) is not None:
                ht = f["score"].get("halftime") or {}
                row = {"d": day.isoformat(), "h": h, "a": w, "gh": num(g["home"]), "ga": num(g["away"]),
                       "hth": num(ht.get("home")), "hta": num(ht.get("away")),
                       "comp": f["league"]["name"], "src": ["api-football"], "_id": f["fixture"]["id"]}
                done[(row["d"], h, w)] = row
            elif st in ("NS", "TBD") and k >= 0:
                up.append({"d": day.isoformat(), "h": h, "a": w, "comp": f["league"]["name"], "_id": f["fixture"]["id"]})
        print(day, "ok")
    conflicts = []
    if fdkey:  # croisement avec une 2e source
        j = fd("/matches", fdkey, {"dateFrom": (today - dt.timedelta(days=min(a.days_back, 10))).isoformat(),
                                   "dateTo": today.isoformat(), "status": "FINISHED"})
        for m in j.get("matches", []):
            ft = (m.get("score") or {}).get("fullTime") or {}
            d = m["utcDate"][:10]
            for (dd, h, w), row in done.items():
                if dd == d and h[:5].lower() == m["homeTeam"]["name"][:5].lower() and w[:5].lower() == m["awayTeam"]["name"][:5].lower():
                    if (num(ft.get("home")), num(ft.get("away"))) == (row["gh"], row["ga"]):
                        row["src"].append("football-data.org")
                    else:
                        conflicts.append({"match": [d, h, w], "api-football": [row["gh"], row["ga"]],
                                          "football-data.org": [ft.get("home"), ft.get("away")]})
                        row["conflict"] = True
    if a.xg:
        for row in done.values():
            x = xg_of(row["_id"], key)
            if row["h"] in x and row["a"] in x:
                row["xh"], row["xa"] = x[row["h"]], x[row["a"]]
    for u in up:
        if a.odds:
            for o in af("/odds", key, {"fixture": u["_id"]}):
                for bk in o.get("bookmakers", []):
                    for bet in bk.get("bets", []):
                        if bet.get("name") == "Match Winner":
                            v = {x["value"]: float(x["odd"]) for x in bet["values"]}
                            if all(k in v for k in ("Home", "Draw", "Away")):
                                u["odds"] = {"h": v["Home"], "d": v["Draw"], "a": v["Away"], "book": bk["name"]}
                                break
                    if "odds" in u:
                        break
                if "odds" in u:
                    break
        if a.injuries:
            u["injuries"] = [{"team": i["team"]["name"], "player": i["player"]["name"], "type": i["player"].get("type")}
                             for i in af("/injuries", key, {"fixture": u["_id"]})]
    matches = [{k: v for k, v in r.items() if not k.startswith("_") and k != "conflict" and v is not None}
               for r in done.values() if not r.get("conflict")]
    json.dump({"generated": dt.datetime.utcnow().isoformat() + "Z", "matches": matches,
               "upcoming": [{k: v for k, v in u.items() if not k.startswith("_")} for u in up],
               "conflicts": conflicts}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(len(matches), "matchs terminés,", len(up), "à venir,", len(conflicts), "conflits ->", a.out)

if __name__ == "__main__":
    main()
