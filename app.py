"""
Clash Royale Collection Level — Flask Backend
=============================================
Proxies requests to the official CR API.
"""

import os
import ssl
import urllib.request
import urllib.parse
import urllib.error
import json
from flask import Flask, jsonify, send_from_directory, request
import heapq

app = Flask(__name__, static_folder="static")

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY  = os.environ.get("CR_API_KEY", "").strip()  # set via env var
BASE_URL = "https://proxy.royaleapi.dev/v1"          # RoyaleAPI proxy

# Rarity tables (for upgrade cost planning)
GOLD_COST_BY_NEXT_VISIBLE_LEVEL = {
    2:  {"common": 5},
    3:  {"common": 20},
    4:  {"common": 50, "rare": 50},
    5:  {"common": 150, "rare": 150},
    6:  {"common": 400, "rare": 400},
    7:  {"common": 1_000, "rare": 1_000, "epic": 400},
    8:  {"common": 2_000, "rare": 2_000, "epic": 2_000},
    9:  {"common": 4_000, "rare": 4_000, "epic": 4_000},
    10: {"common": 8_000, "rare": 8_000, "epic": 8_000, "legendary": 5_000},
    11: {"common": 15_000, "rare": 15_000, "epic": 15_000, "legendary": 15_000},
    12: {"common": 25_000, "rare": 25_000, "epic": 25_000, "legendary": 25_000, "champion": 25_000},
    13: {"common": 40_000, "rare": 40_000, "epic": 40_000, "legendary": 40_000, "champion": 40_000},
    14: {"common": 60_000, "rare": 60_000, "epic": 60_000, "legendary": 60_000, "champion": 60_000},
    15: {"common": 90_000, "rare": 90_000, "epic": 90_000, "legendary": 90_000, "champion": 90_000},
    16: {"common": 120_000, "rare": 120_000, "epic": 120_000, "legendary": 120_000, "champion": 120_000},
}

CARD_COUNT_BY_NEXT_VISIBLE_LEVEL = {
    2:  {"common": 2},
    3:  {"common": 4, "rare": 1},
    4:  {"common": 10, "rare": 2},
    5:  {"common": 20, "rare": 4},
    6:  {"common": 50, "rare": 10, "epic": 1},
    7:  {"common": 100, "rare": 20, "epic": 2},
    8:  {"common": 200, "rare": 50, "epic": 4},
    9:  {"common": 400, "rare": 100, "epic": 10, "legendary": 1},
    10: {"common": 800, "rare": 200, "epic": 20, "legendary": 2},
    11: {"common": 1_000, "rare": 300, "epic": 30, "legendary": 4, "champion": 1},
    12: {"common": 1_500, "rare": 400, "epic": 50, "legendary": 6, "champion": 2},
    13: {"common": 2_500, "rare": 550, "epic": 70, "legendary": 9, "champion": 5},
    14: {"common": 3_500, "rare": 750, "epic": 100, "legendary": 12, "champion": 8},
    15: {"common": 5_500, "rare": 1_000, "epic": 130, "legendary": 14, "champion": 11},
    16: {"common": 7_500, "rare": 1_400, "epic": 180, "legendary": 20, "champion": 15},
}

MAX_VISIBLE_LEVEL = 16
INF_TIER_MAX = 10**9

CELEBRATION_TIERS = [
    (20,   200,  500,  1, 0, False),
    (201,  400,  750,  1, 0, False),
    (401,  600,  1000, 1, 0, False),
    (601,  800,  1500, 1, 0, False),
    (801,  1000, 2000, 1, 1, False),
    (1001, 1200, 2500, 1, 2, False),
    (1201, 1400, 3000, 1, 3, False),
    (1401, 1600, 3500, 2, 4, False),
    (1601, 1800, 4000, 2, 5, False),
    (1801, 2000, 4500, 3, 6, False),
    (2001, INF_TIER_MAX, 5000, 3, 6, True),
]

MILESTONE_TARGETS = [1401, 1801, 2001]

# ── SSL fix for macOS ─────────────────────────────────────────────────────────
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = None


def cr_fetch(path: str) -> dict:
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    })
    kwargs = {"timeout": 10}
    if SSL_CONTEXT:
        kwargs["context"] = SSL_CONTEXT
    with urllib.request.urlopen(req, **kwargs) as resp:
        return json.loads(resp.read().decode())


# ── Calculation helpers ───────────────────────────────────────────────────────
def is_merge_tactics_ruler(card: dict) -> bool:
    text = " ".join(str(card.get(k, "")) for k in ("name", "type", "key", "id")).lower()
    return "ruler" in text or "merge tactics" in text or "autochess" in text

def visible_level(card: dict) -> int:
    api_level = int(card.get("level", 0) or 0)
    api_max_level = int(card.get("maxLevel", MAX_VISIBLE_LEVEL) or MAX_VISIBLE_LEVEL)

    if api_level <= 0:
        return 0

    return api_level + (MAX_VISIBLE_LEVEL - api_max_level)


def form_bonus(card: dict) -> int:
    evo_level = int(card.get("evolutionLevel", 0) or 0)

    # Conservative model:
    # 0 = no owned special form
    # 1/2 = one owned Evolution/Hero form
    # 3 = both forms, if the API represents it that way
    if evo_level == 3:
        return 10
    if evo_level > 0:
        return 5
    return 0

def compute_collection_level(cards: list[dict]) -> int:
    total = 0
    for card in cards:
        if is_merge_tactics_ruler(card):
            continue

        total += visible_level(card)
        total += form_bonus(card)

    return total


def get_celebration_tier(cl: int) -> dict | None:
    for tier in CELEBRATION_TIERS:
        lo, hi, *_ = tier
        if lo <= cl <= hi:
            return serialize_tier(*tier)
    return None


def get_next_celebration_tier(cl: int) -> dict | None:
    for tier in CELEBRATION_TIERS:
        lo, *_ = tier
        if lo > cl:
            return serialize_tier(*tier)
    return None

def get_next_tier_target(cl: int) -> int | None:
    for lo, hi, gems, chests, banners, skin in CELEBRATION_TIERS:
        if lo > cl:
            return lo
    return None

def get_next_milestone(cl: int) -> int:
    if cl < 20:   return 20
    if cl < 1500: return ((cl // 10) + 1) * 10
    return ((cl // 5) + 1) * 5

def serialize_tier(lo, hi, gems, chests, banners, skin) -> dict:
    return {
        "min": lo,
        "max": hi,
        "label": f"{lo}+" if hi >= INF_TIER_MAX else f"{lo}–{hi}",
        "gems": gems,
        "chests": chests,
        "banners": banners,
        "tower_skin": skin,
    }


def get_upgrade_cost(rarity: str, next_visible_level: int) -> int | None:
    return GOLD_COST_BY_NEXT_VISIBLE_LEVEL.get(next_visible_level, {}).get(rarity)

def get_card_count_required(rarity: str, next_visible_level: int) -> int | None:
    return CARD_COUNT_BY_NEXT_VISIBLE_LEVEL.get(next_visible_level, {}).get(rarity)

def upgrade_plan(cards: list[dict], target_cl: int, current_cl: int) -> dict:
    needed = target_cl - current_cl

    if needed <= 0:
        return {
            "needed": 0,
            "already_reached": True,
            "plan": [],
            "total_gold": 0,
            "shortfall": 0,
        }

    # Heap entries:
    # (gold_cost, current_visible_level, card_name, unique_index, card_copy)
    #
    # We use a copy of the minimal card state so the optimizer can simulate
    # repeated upgrades without mutating the API response.
    heap = []

    for idx, card in enumerate(cards):
        if is_merge_tactics_ruler(card):
            continue

        rarity = str(card.get("rarity", "common")).lower()
        cur_visible = visible_level(card)

        if cur_visible <= 0 or cur_visible >= MAX_VISIBLE_LEVEL:
            continue

        next_visible = cur_visible + 1
        gold = get_upgrade_cost(rarity, next_visible)

        if gold is None:
            continue

        heapq.heappush(heap, (
            gold,
            cur_visible,
            str(card.get("name") or ""),
            idx,
            {
                "name": card.get("name"),
                "rarity": rarity,
                "visible_level": cur_visible,
                "count": int(card.get("count", 0) or 0),
            }
        ))

    plan = []
    total_gold = 0
    remaining = needed

    while heap and remaining > 0:
        gold, cur_visible, name, idx, state = heapq.heappop(heap)

        next_visible = state["visible_level"] + 1

        # Safety check in case an old heap entry ever becomes stale.
        if next_visible != cur_visible + 1:
            continue
        
        cards_needed = get_card_count_required(state["rarity"], next_visible)
        copies_owned = int(state.get("count", 0) or 0)

        if cards_needed is None:
            cards_needed = 0

        wildcards_or_extra_needed = max(0, cards_needed - copies_owned)

        plan.append({
            "name": state["name"],
            "rarity": state["rarity"],
            "from_level": state["visible_level"],
            "to_level": next_visible,
            "upgrades": 1,
            "gold": gold,
            "copies_owned": copies_owned,
            "copies_needed": cards_needed,
            "wildcards_or_extra_needed": wildcards_or_extra_needed,
        })

        total_gold += gold
        remaining -= 1

        # Simulate that this card has now been upgraded once.
        state["visible_level"] = next_visible
        state["count"] = max(0, copies_owned - cards_needed)

        # Reinsert the same card if it still has another possible upgrade.
        if state["visible_level"] < MAX_VISIBLE_LEVEL:
            following_level = state["visible_level"] + 1
            following_gold = get_upgrade_cost(state["rarity"], following_level)

            if following_gold is not None:
                heapq.heappush(heap, (
                    following_gold,
                    state["visible_level"],
                    str(state["name"] or ""),
                    idx,
                    state,
                ))

    return {
        "needed": needed,
        "already_reached": False,
        "plan": plan,
        "total_gold": total_gold,
        "shortfall": max(0, remaining),
    }


# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/api/player/<path:tag>")
def get_player(tag: str):
    if not API_KEY:
        return jsonify({"error": "Server has no API key configured."}), 500

    tag = tag if tag.startswith("#") else f"#{tag}"
    encoded = urllib.parse.quote(tag)

    try:
        data = cr_fetch(f"/players/{encoded}")
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode())
        return jsonify({"error": body.get("reason", str(e))}), e.code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    cards = data.get("cards", [])
    cl = compute_collection_level(cards)
    
    next_tier_target = get_next_tier_target(cl)

    counted_cards = [
        c for c in cards
        if not is_merge_tactics_ruler(c)
    ]

    total_upgrades_left = sum(
        max(0, MAX_VISIBLE_LEVEL - visible_level(c))
        for c in counted_cards
    )

    maxed = sum(
        1 for c in counted_cards
        if visible_level(c) >= MAX_VISIBLE_LEVEL
    )
    
    forms_owned = sum(
        2 if int(c.get("evolutionLevel", 0) or 0) == 3
        else 1 if int(c.get("evolutionLevel", 0) or 0) > 0
        else 0
        for c in counted_cards
    )

    next_tier_plan = (
        upgrade_plan(counted_cards, next_tier_target, cl)
        if next_tier_target is not None
        else None
    )

    targets = sorted(set(
        [t for t in MILESTONE_TARGETS if t > cl] +
        ([next_tier_target] if next_tier_target is not None else [])
    ))

    plans = {
        str(t): upgrade_plan(counted_cards, t, cl)
        for t in targets
    }

    return jsonify({
        "tag":              data.get("tag"),
        "name":             data.get("name"),
        "trophies":         data.get("trophies"),
        "collection_level": cl,
        "cards_total":      len(counted_cards),
        "cards_maxed":      maxed,
        "forms_owned":      forms_owned,
        "evolutions":       forms_owned,    
        "upgrades_left":    total_upgrades_left,
        "next_milestone":   get_next_milestone(cl),
        "celebration_tier": get_celebration_tier(cl),
        "next_tier":        get_next_celebration_tier(cl),
        "all_tiers": [serialize_tier(*tier) for tier in CELEBRATION_TIERS],
        "plans": plans,  # fixed targets: 1401, 1801, 2001
        "next_tier_target": next_tier_target,
        "next_tier_plan": next_tier_plan,
    })


# ── Serve frontend ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    if not API_KEY:
        print("⚠  Warning: CR_API_KEY not set. Set it with:")
        print('   export CR_API_KEY="your_token_here"')
    app.run(debug=True, port=5000)
