"""Powerball & Mega Millions — history, frequency stats, and number pickers.

Pulls official draw history from New York State's open-data API (Socrata), then
offers several ways to choose a line:

  * pure random          — a fair, uniform draw (the honest baseline)
  * hot / frequency       — weight each ball by how often it has hit
  * overdue / cold        — weight each ball by how long since it last hit
  * balanced / optimized  — reject-sample until the line matches the historical
                            shape of real winners (sum range + odd/even mix)

IMPORTANT HONESTY NOTE (surfaced in the UI too): lottery draws are independent
and uniform. No past-frequency method changes your odds of winning the jackpot.
The one real, mathematical edge is *combinatorial*: picking uncommon numbers
(e.g. above 31, avoiding patterns) doesn't help you win, but it lowers the chance
you'd *split* a jackpot with other players who picked birthdays and patterns.
"""

import random
import datetime

import requests

UA = {"User-Agent": "PropEdge/1.0 (personal lottery stats)"}

GAMES = {
    "Powerball": {
        "white_max": 69,
        "white_count": 5,
        "special_max": 26,
        "special_name": "Powerball",
        "special_color": "#FF4D4D",
        "url": "https://data.ny.gov/resource/d6yy-54nr.json",
        "special_field": None,          # special is the last token of winning_numbers
        "draw_days": ["Mon", "Wed", "Sat"],
        "matrix_start": "2015-10-07",   # current 5/69 + 1/26 matrix
    },
    "Mega Millions": {
        "white_max": 70,
        "white_count": 5,
        "special_max": 24,
        "special_name": "Mega Ball",
        "special_color": "#FFC400",
        "url": "https://data.ny.gov/resource/5xaw-6ayf.json",
        "special_field": "mega_ball",   # special is its own column
        "draw_days": ["Tue", "Fri"],
        "matrix_start": "2017-10-31",   # 5/70 white-ball matrix (Mega Ball cap later cut 25->24)
    },
}


def fetch_draws(game, limit=500):
    """Return recent draws newest-first as [{date, white:[...], special:int, weekday}].

    `limit` caps how many recent draws to pull; results are filtered to the
    current game matrix so frequencies aren't polluted by retired number ranges.
    """
    g = GAMES[game]
    params = {"$limit": limit, "$order": "draw_date DESC"}
    r = requests.get(g["url"], params=params, headers=UA, timeout=20)
    r.raise_for_status()
    start = g["matrix_start"]
    out = []
    for row in r.json():
        date_s = (row.get("draw_date") or "")[:10]
        if not date_s or date_s < start:
            continue
        nums = (row.get("winning_numbers") or "").split()
        try:
            nums = [int(n) for n in nums]
        except ValueError:
            continue
        if g["special_field"]:
            white = nums
            try:
                special = int(row.get(g["special_field"]))
            except (TypeError, ValueError):
                continue
        else:
            if len(nums) < g["white_count"] + 1:
                continue
            white = nums[: g["white_count"]]
            special = nums[g["white_count"]]
        # keep only balls inside the current matrix (drops e.g. retired Mega Ball 25)
        if len(white) != g["white_count"]:
            continue
        if any(not (1 <= w <= g["white_max"]) for w in white):
            continue
        if not (1 <= special <= g["special_max"]):
            continue
        d = datetime.date.fromisoformat(date_s)
        out.append({"date": d, "white": sorted(white), "special": special,
                    "weekday": d.strftime("%a")})
    return out


# --- frequency / overdue -------------------------------------------------

def white_frequency(draws, game):
    counts = {i: 0 for i in range(1, GAMES[game]["white_max"] + 1)}
    for d in draws:
        for w in d["white"]:
            counts[w] += 1
    return counts


def special_frequency(draws, game):
    counts = {i: 0 for i in range(1, GAMES[game]["special_max"] + 1)}
    for d in draws:
        counts[d["special"]] += 1
    return counts


def overdue_gaps(draws, game, kind="white"):
    """Draws-since-last-seen for every ball (newest draw = gap 0). Higher = colder."""
    mx = GAMES[game]["white_max"] if kind == "white" else GAMES[game]["special_max"]
    gaps = {i: len(draws) for i in range(1, mx + 1)}  # never seen -> max gap
    for idx, d in enumerate(draws):  # draws are newest-first
        balls = d["white"] if kind == "white" else [d["special"]]
        for b in balls:
            if gaps[b] == len(draws):
                gaps[b] = idx
    return gaps


def by_weekday(draws, game, top=6):
    """Per draw-day: the hottest white balls and special ball."""
    out = {}
    for day in GAMES[game]["draw_days"]:
        day_draws = [d for d in draws if d["weekday"] == day]
        if not day_draws:
            continue
        wf = white_frequency(day_draws, game)
        sf = special_frequency(day_draws, game)
        out[day] = {
            "n": len(day_draws),
            "white": sorted(wf, key=wf.get, reverse=True)[:top],
            "special": max(sf, key=sf.get) if any(sf.values()) else None,
        }
    return out


def sum_stats(draws):
    sums = sorted(sum(d["white"]) for d in draws)
    if not sums:
        return None
    return {
        "min": sums[0], "max": sums[-1],
        "p10": _pct(sums, 10), "p50": _pct(sums, 50), "p90": _pct(sums, 90),
        "avg": round(sum(sums) / len(sums), 1),
    }


def _pct(sorted_vals, q):
    if not sorted_vals:
        return 0
    return sorted_vals[int(q / 100 * (len(sorted_vals) - 1))]


# --- pickers -------------------------------------------------------------

def _weighted_sample(weights, k):
    """Pick k distinct keys from {key: weight} proportional to weight (no replacement)."""
    pop = list(weights.keys())
    w = [max(0.0001, weights[p]) for p in pop]
    chosen = []
    for _ in range(k):
        total = sum(w)
        r = random.uniform(0, total)
        upto = 0.0
        for i, wi in enumerate(w):
            upto += wi
            if upto >= r:
                chosen.append(pop.pop(i))
                w.pop(i)
                break
    return sorted(chosen)


def pick_random(game):
    g = GAMES[game]
    white = sorted(random.sample(range(1, g["white_max"] + 1), g["white_count"]))
    return white, random.randint(1, g["special_max"])


def pick_hot(game, draws):
    """Frequency-weighted: balls that hit more get a proportionally bigger shot."""
    g = GAMES[game]
    wf = white_frequency(draws, game)
    sf = special_frequency(draws, game)
    wf = {k: v + 1 for k, v in wf.items()}        # +1 smoothing so nothing is impossible
    sf = {k: v + 1 for k, v in sf.items()}
    white = _weighted_sample(wf, g["white_count"])
    special = _weighted_sample(sf, 1)[0]
    return white, special


def pick_overdue(game, draws):
    """Cold/overdue-weighted: the longer a ball's been missing, the heavier its weight."""
    g = GAMES[game]
    wg = overdue_gaps(draws, game, "white")
    sg = overdue_gaps(draws, game, "special")
    wg = {k: v + 1 for k, v in wg.items()}
    sg = {k: v + 1 for k, v in sg.items()}
    white = _weighted_sample(wg, g["white_count"])
    special = _weighted_sample(sg, 1)[0]
    return white, special


def pick_balanced(game, draws, attempts=4000):
    """Reject-sample uniform lines until one matches the shape of real winners:
    its white-ball sum lands in the historical 10th–90th percentile band and the
    odd/even split isn't all-odd or all-even. Falls back to pure random."""
    g = GAMES[game]
    stats = sum_stats(draws)
    lo, hi = (stats["p10"], stats["p90"]) if stats else (0, 10 ** 9)
    for _ in range(attempts):
        white = sorted(random.sample(range(1, g["white_max"] + 1), g["white_count"]))
        if not (lo <= sum(white) <= hi):
            continue
        odds = sum(1 for x in white if x % 2)
        if odds in (0, g["white_count"]):          # avoid all-odd / all-even
            continue
        return white, random.randint(1, g["special_max"])
    return pick_random(game)


PICKERS = {
    "🎲 Pure random": "random",
    "🔥 Hot (frequency-weighted)": "hot",
    "❄️ Overdue (cold)": "overdue",
    "⚖️ Balanced (math-optimized)": "balanced",
}


# --- prize tiers & ticket checker ----------------------------------------

# (white_matches, special_matched) -> (label, dollar_amount or None for jackpot)
PRIZES = {
    "Powerball": {
        (5, True): ("Jackpot 🎉", None), (5, False): ("$1,000,000", 1_000_000),
        (4, True): ("$50,000", 50_000), (4, False): ("$100", 100),
        (3, True): ("$100", 100), (3, False): ("$7", 7),
        (2, True): ("$7", 7), (1, True): ("$4", 4), (0, True): ("$4", 4),
    },
    "Mega Millions": {
        (5, True): ("Jackpot 🎉", None), (5, False): ("$1,000,000", 1_000_000),
        (4, True): ("$10,000", 10_000), (4, False): ("$500", 500),
        (3, True): ("$200", 200), (3, False): ("$10", 10),
        (2, True): ("$10", 10), (1, True): ("$5", 5), (0, True): ("$2", 2),
    },
}


def prize_for(game, white_match, special_match):
    """(label, amount) for a match count, or None if it wins nothing."""
    return PRIZES[game].get((white_match, bool(special_match)))


def validate_ticket(game, white, special):
    """Return an error string if the ticket is illegal, else None."""
    g = GAMES[game]
    white = [w for w in white if w]
    if len(white) != g["white_count"]:
        return f"Pick exactly {g['white_count']} white balls."
    if len(set(white)) != len(white):
        return "White balls must be distinct."
    if any(not (1 <= w <= g["white_max"]) for w in white):
        return f"White balls must be between 1 and {g['white_max']}."
    if not special or not (1 <= special <= g["special_max"]):
        return f"{g['special_name']} must be between 1 and {g['special_max']}."
    return None


def check_ticket(game, white, special, draws):
    """Score a ticket against every draw in `draws` and profile its numbers."""
    err = validate_ticket(game, white, special)
    if err:
        return {"valid": False, "error": err}

    white_set = set(white)
    wins, tier_counts, total, jackpots, best = [], {}, 0.0, 0, None
    for d in draws:
        wm = len(white_set & set(d["white"]))
        sm = special == d["special"]
        p = prize_for(game, wm, sm)
        if not p:
            continue
        label, amount = p
        rec = {"date": d["date"], "white_match": wm, "special_match": sm, "label": label,
               "amount": amount}
        wins.append(rec)
        tier_counts[label] = tier_counts.get(label, 0) + 1
        if amount:
            total += amount
        else:
            jackpots += 1
        # "best" = most white matches, then special
        if best is None or (wm, sm) > (best["white_match"], best["special_match"]):
            best = rec

    # number profile vs history
    wf = white_frequency(draws, game)
    sf = special_frequency(draws, game)
    ranked = sorted(wf, key=wf.get, reverse=True)
    rank_of = {n: i + 1 for i, n in enumerate(ranked)}
    ball_freq = [{"num": n, "hits": wf[n], "rank": rank_of[n]} for n in sorted(white)]
    sp_ranked = sorted(sf, key=sf.get, reverse=True)
    sp_rank = sp_ranked.index(special) + 1 if special in sp_ranked else None

    ss = sum_stats(draws)
    s = sum(white)
    odds = sum(1 for x in white if x % 2)

    return {
        "valid": True, "error": None,
        "draws_checked": len(draws),
        "wins": wins, "best": best, "tier_counts": tier_counts,
        "total_amount": total, "jackpots": jackpots,
        "profile": {
            "sum": s,
            "sum_band": (ss["p10"], ss["p90"]) if ss else None,
            "in_band": bool(ss and ss["p10"] <= s <= ss["p90"]),
            "odds": odds, "evens": len(white) - odds,
            "ball_freq": ball_freq,
            "special": {"num": special, "hits": sf.get(special, 0), "rank": sp_rank,
                        "of": len(sp_ranked)},
            "white_of": len(ranked),
        },
    }


def generate(game, strategy, draws, lines=1):
    """Produce `lines` picks with the chosen strategy. Returns [(white, special), ...]."""
    fn = {
        "random": lambda: pick_random(game),
        "hot": lambda: pick_hot(game, draws),
        "overdue": lambda: pick_overdue(game, draws),
        "balanced": lambda: pick_balanced(game, draws),
    }[strategy]
    return [fn() for _ in range(max(1, lines))]
