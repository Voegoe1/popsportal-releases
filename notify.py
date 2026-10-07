"""
PopsPortal -> Discord release-notifier (draait via GitHub Actions).

Leest de openbare WooCommerce Store API uit (geen sleutel nodig: alleen wat
al op de site staat), vergelijkt met de producten die al gepost zijn
(seen_products.json) en post elk nieuw product als los bericht via een
Discord-webhook: titel, status, prijs, tip en productfoto.
Er hoeft niets 24/7 te draaien.
"""

import html
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

import requests

# ---------- Instellingen (GitHub Secrets / Variables) ----------
WC_URL = os.environ["WC_URL"].rstrip("/")
WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

SHOP_NAME = os.getenv("SHOP_NAME") or "PopsPortal"
SHOP_ICON_URL = os.getenv("SHOP_ICON_URL") or None            # logo naast "PopsPortal"
ACCOUNT_URL = os.getenv("ACCOUNT_URL") or f"{WC_URL}/mijn-account/"
TIP_TEXT = os.getenv("TIP_TEXT") or (
    f"[Maak een account aan]({ACCOUNT_URL}) en spaar 5% van je uitgaven "
    "terug in PortalPoints"
)
CATEGORY_ID = os.getenv("WC_CATEGORY_ID") or None             # optioneel: alleen deze categorie
PREORDER_WORDS = [w.strip().lower() for w in
                  (os.getenv("PREORDER_WORDS") or "pre-order,preorder,pre order").split(",")
                  if w.strip()]
CURRENCY = os.getenv("CURRENCY_SYMBOL") or "€"
EMBED_COLOR = int((os.getenv("EMBED_COLOR") or "57F287").lstrip("#"), 16)
UTM = os.getenv("UTM_PARAMS", "utm_source=discord&utm_medium=social&utm_campaign=new_release")
ROLE_ID = os.getenv("DISCORD_ROLE_ID") or None                 # optioneel: rol taggen

STATE_FILE = Path("seen_products.json")
MAX_TRACKED = 5000
PAGES_TO_CHECK = 1          # 1 x 100 nieuwste producten per run
MAX_POSTS_PER_RUN = 100     # veiligheidsrem tegen spam

# Vaste, herkenbare naam: hiermee kun je de bot in Cloudflare toestaan
HEADERS = {
    "User-Agent": "PopsPortalReleaseBot/1.0 (+https://github.com/Voegoe1/popsportal-releases)",
    "Accept": "application/json",
}


# ---------- State ----------
def load_seen() -> set[int] | None:
    """None = eerste run (nog geen state-bestand)."""
    if not STATE_FILE.exists():
        return None
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except (json.JSONDecodeError, OSError):
        print("State-bestand onleesbaar, behandel als eerste run.")
        return None


def save_seen(seen: set[int]) -> None:
    keep = sorted(seen, reverse=True)[:MAX_TRACKED]
    STATE_FILE.write_text(json.dumps(keep))


# ---------- WooCommerce Store API (openbaar) ----------
def fetch_latest_products() -> list[dict]:
    products: list[dict] = []
    for page in range(1, PAGES_TO_CHECK + 1):
        params = {"orderby": "date", "order": "desc", "per_page": 100, "page": page}
        if CATEGORY_ID:
            params["category"] = CATEGORY_ID
        resp = requests.get(
            f"{WC_URL}/wp-json/wc/store/v1/products",
            params=params, headers=HEADERS, timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list):
            raise RuntimeError(f"Onverwacht antwoord van de shop: {str(batch)[:200]}")
        products.extend(batch)
        if len(batch) < 100:
            break
    return products


# ---------- Opmaak ----------
def to_amount(value, minor_unit: int) -> float | None:
    try:
        return int(value) / (10 ** minor_unit)
    except (TypeError, ValueError):
        return None


def format_price(amount: float | None) -> str | None:
    if amount is None:
        return None
    nl = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{CURRENCY} {nl}"


def price_text(product: dict) -> str:
    prices = product.get("prices") or {}
    unit = int(prices.get("currency_minor_unit") or 2)
    rng = prices.get("price_range") or {}
    if rng.get("min_amount"):
        low = format_price(to_amount(rng.get("min_amount"), unit))
        return f"vanaf {low}" if low else "Prijs volgt"

    price = format_price(to_amount(prices.get("price"), unit))
    regular = format_price(to_amount(prices.get("regular_price"), unit))
    if price and product.get("on_sale") and regular and regular != price:
        return f"~~{regular}~~ **{price}**"
    return price or "Prijs volgt"


def status_text(product: dict) -> str:
    availability = product.get("stock_availability") or {}
    shown = html.unescape(str(availability.get("text") or "")).lower()
    labels = " ".join(
        (c.get("name", "") + " " + c.get("slug", ""))
        for c in (product.get("categories") or []) + (product.get("tags") or [])
    ).lower()
    if any(w in shown or w in labels for w in PREORDER_WORDS):
        return "Pre-order"
    if product.get("is_on_backorder") or "backorder" in str(availability.get("class", "")):
        return "Back-order"
    if product.get("is_in_stock"):
        return "In stock"
    return "Uitverkocht"


def with_utm(url: str) -> str:
    if not UTM:
        return url
    parts = urlsplit(url)
    query = parse_qsl(parts.query) + parse_qsl(UTM)
    return urlunsplit(parts._replace(query=urlencode(query)))


def build_message(product: dict) -> dict:
    name = html.unescape(product.get("name") or "Nieuw product")
    link = with_utm(product["permalink"])

    author = {"name": SHOP_NAME, "url": WC_URL}
    if SHOP_ICON_URL:
        author["icon_url"] = SHOP_ICON_URL

    embed = {
        "author": author,
        "title": name[:256],
        "url": link,
        "color": EMBED_COLOR,
        "fields": [
            {"name": "Status", "value": status_text(product), "inline": False},
            {"name": "Prijs", "value": price_text(product), "inline": False},
            {"name": "Tip", "value": TIP_TEXT[:1024], "inline": False},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    images = product.get("images") or []
    if images and images[0].get("src"):
        embed["image"] = {"url": images[0]["src"]}

    content = name[:1900]
    if ROLE_ID:
        content = f"<@&{ROLE_ID}> {content}"

    return {
        "content": content,
        "embeds": [embed],
        "allowed_mentions": {"roles": [ROLE_ID] if ROLE_ID else []},
    }


# ---------- Discord ----------
def post_to_discord(message: dict) -> None:
    for _ in range(5):
        resp = requests.post(
            WEBHOOK_URL, params={"wait": "true"}, json=message, timeout=30,
        )
        if resp.status_code == 429:  # rate limit: wachten en opnieuw
            wait = float(resp.json().get("retry_after", 2))
            time.sleep(wait + 0.5)
            continue
        if resp.status_code >= 400:
            raise RuntimeError(f"Discord gaf {resp.status_code}: {resp.text[:300]}")
        return
    raise RuntimeError("Discord blijft rate-limiten, later opnieuw.")


# ---------- Main ----------
def main() -> int:
    seen = load_seen()

    try:
        products = fetch_latest_products()
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        hint = ""
        if code == 403:
            hint = (" -> De shop (waarschijnlijk Cloudflare) blokkeert GitHub. "
                    "Sta de bot toe in Cloudflare, zie de handleiding.")
        print(f"Shop ophalen mislukt ({code}){hint}")
        return 1
    except Exception as exc:
        print(f"Shop ophalen mislukt: {exc}")
        return 1

    if seen is None:
        save_seen({p["id"] for p in products})
        print(f"Eerste run: {len(products)} bestaande producten gemarkeerd, niets gepost.")
        return 0

    new = [p for p in products if p["id"] not in seen]
    new.reverse()  # oudste eerst
    print(f"{len(products)} producten gecheckt, {len(new)} nieuw.")

    exit_code = 0
    for product in new[:MAX_POSTS_PER_RUN]:
        try:
            post_to_discord(build_message(product))
        except Exception as exc:
            print(f"Posten mislukt, volgende run opnieuw: {exc}")
            exit_code = 1
            break
        seen.add(product["id"])
        print(f"Gepost: {html.unescape(product.get('name') or '')} [{status_text(product)}]")
        time.sleep(1.2)

    save_seen(seen)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
