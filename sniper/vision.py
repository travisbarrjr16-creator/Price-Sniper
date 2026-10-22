"""📷 Snap: name what's in a photo (and where it's cheapest), or estimate a plate of food's nutrition.

Uses Claude's vision through the Anthropic API (ANTHROPIC_API_KEY in .env). Without a key it
answers with a clearly labelled sample, like the Demo marketplace, so the screens can be tried.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Literal, Optional

from pydantic import BaseModel, Field

log = logging.getLogger("sniper.vision")

MODEL = "claude-opus-5-5"
MAX_IMAGE_BYTES = 5 * 1024 * 1024          # the API's limit for one image
DAILY_LIMIT_DEFAULT = 60                   # photos per person per day (each one costs a little)
# What the first bytes of each allowed image type look like, so a renamed file can't slip through
MAGIC = {"image/jpeg": (b"\xff\xd8\xff",), "image/png": (b"\x89PNG\r\n\x1a\n",),
         "image/gif": (b"GIF87a", b"GIF89a"), "image/webp": (b"RIFF",)}


class Place(BaseModel):
    store: str = Field(description="Store or site name, e.g. Walmart, Amazon, eBay, Home Depot, Costco, Facebook Marketplace")
    kind: Literal["online", "in_store", "both", "used"]
    price_low: Optional[float] = Field(None, description="Typical low price in USD at this store, if you can estimate it")
    price_high: Optional[float] = None
    note: str = Field(description="One short line: why look here (e.g. 'often cheapest for this brand', 'aisle: hardware')")


class ItemResult(BaseModel):
    name: str = Field(description="What it is, as specific as the photo allows (brand + model if visible)")
    category: str
    description: str = Field(description="One or two plain sentences about what it is / what it's for")
    search_query: str = Field(description="2-7 words to type into a store's search box to find this exact thing")
    confidence: Literal["high", "medium", "low"]
    typical_price_low: Optional[float] = Field(None, description="Typical new price range in USD; null if unknowable")
    typical_price_high: Optional[float] = None
    used_price: Optional[float] = Field(None, description="Typical used/second-hand price in USD, if it's commonly resold")
    where_to_find: list[Place] = Field(description="3-6 places, cheapest-likely first")
    cheapest_bet: str = Field(description="One sentence: where it's most likely cheapest and why (sales timing, used market, store brand…)")
    is_food: bool = Field(description="True if the photo is mainly food or a drink")


class FoodPart(BaseModel):
    name: str
    amount: str = Field(description="Estimated amount, e.g. '8 oz', '1 cup', '2 slices'")
    calories: float
    protein_g: float


class FoodResult(BaseModel):
    name: str = Field(description="Short name for the meal, e.g. 'Ribeye steak with mashed potatoes'")
    serving: str = Field(description="What the numbers cover, e.g. 'the whole plate shown (about 12 oz steak)'")
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: Optional[float] = None
    parts: list[FoodPart] = Field(description="Each food on the plate with its own estimate")
    confidence: Literal["high", "medium", "low"]
    notes: str = Field(description="One short line on what could change the estimate (cooking oil, sauce, cut of meat…)")
    is_food: bool = Field(description="False if the photo isn't food; then the numbers are 0")


ITEM_PROMPT = """Identify the main thing in this photo for a shopper in the United States.
Name it as precisely as the photo allows (read any brand, model or label text). Then say where they can buy it,
listing the places most likely to be cheapest first, with typical US prices you are reasonably sure of.
Include the used market (eBay, Facebook Marketplace, OfferUp…) when the item is commonly resold.
Prices are estimates; leave a price null rather than guess wildly."""

FOOD_PROMPT = """Estimate the nutrition of the food in this photo for a meal log.
Estimate portion sizes from visual cues (plate size, utensils, hands) and cover everything visible on the plate.
Use typical preparation if unknown (e.g. a pan-seared steak with a little oil) and say so in the notes.
Give totals for what is shown, plus a breakdown per food. If it isn't food, set is_food to false and the numbers to 0."""


def configured() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def daily_limit() -> int:
    try:
        return max(0, int(os.getenv("SNIPER_VISION_DAILY", DAILY_LIMIT_DEFAULT)))
    except ValueError:
        return DAILY_LIMIT_DEFAULT


def decode_image(data: str, media_type: str) -> bytes:
    """The photo as bytes, or ValueError with a message people can act on."""
    if media_type not in MAGIC:
        raise ValueError("Use a JPEG, PNG, WebP or GIF photo")
    if data.startswith("data:"):              # tolerate a full data: URL
        data = data.split(",", 1)[-1]
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        raise ValueError("That photo didn't come through, try again")
    if not raw:
        raise ValueError("That photo is empty")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("That photo is too big (5 MB max)")
    if not raw.startswith(MAGIC[media_type]) or (media_type == "image/webp" and raw[8:12] != b"WEBP"):
        raise ValueError("That file isn't a photo the app can read")
    return raw


class VisionError(Exception):
    """Something people can read: shown as the error message."""


async def analyze(raw: bytes, media_type: str, mode: str, client=None) -> dict:
    """mode 'item' -> ItemResult, 'food' -> FoodResult, as a dict with 'demo': bool."""
    if mode not in ("item", "food"):
        raise ValueError("mode must be item or food")
    if client is None and not configured():
        return {**(SAMPLE_FOOD if mode == "food" else SAMPLE_ITEM), "demo": True}
    import anthropic                          # only needed once a key is set
    client = client or anthropic.AsyncAnthropic(timeout=90, max_retries=2)
    schema, prompt = (FoodResult, FOOD_PROMPT) if mode == "food" else (ItemResult, ITEM_PROMPT)
    try:
        resp = await client.beta.messages.parse(
            model=os.getenv("SNIPER_VISION_MODEL", MODEL),
            max_tokens=8000,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_format=schema,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.standard_b64encode(raw).decode()}},
                {"type": "text", "text": prompt},
            ]}],
        )
    except anthropic.AuthenticationError:
        log.warning("vision: ANTHROPIC_API_KEY was rejected")
        raise VisionError("The photo service key on this server isn't working (ask the person who runs the app)")
    except anthropic.RateLimitError:
        raise VisionError("The photo service is busy, try again in a minute")
    except anthropic.BadRequestError as e:
        log.warning("vision: bad request (%s)", getattr(e, "message", e))
        raise VisionError("That photo couldn't be read, try another one")
    except anthropic.APIStatusError as e:
        log.warning("vision: API error %s", e.status_code)
        raise VisionError("The photo service had a problem, try again")
    except anthropic.APIConnectionError:
        raise VisionError("Couldn't reach the photo service, check the server's internet connection")
    if resp.stop_reason == "refusal":
        raise VisionError("The photo service wouldn't describe that photo")
    if resp.parsed_output is None:
        raise VisionError("Couldn't make sense of that photo, try a clearer one")
    return {**resp.parsed_output.model_dump(), "demo": False}


SAMPLE_ITEM = ItemResult(
    name="Sample: Stanley Quencher H2.0 40 oz Tumbler", category="Kitchen & drinkware",
    description="A sample answer. Add ANTHROPIC_API_KEY to .env and the app will name what's really in your photo.",
    search_query="Stanley Quencher 40 oz", confidence="high", typical_price_low=35, typical_price_high=45, used_price=22,
    where_to_find=[
        Place(store="Facebook Marketplace", kind="used", price_low=18, price_high=28, note="Lots of lightly used ones locally"),
        Place(store="Walmart", kind="both", price_low=35, price_high=45, note="Often has colors on rollback"),
        Place(store="Target", kind="both", price_low=35, price_high=45, note="Exclusive colors; Circle deals"),
        Place(store="Amazon", kind="online", price_low=35, price_high=50, note="Check the seller is Stanley"),
        Place(store="Dick's Sporting Goods", kind="in_store", price_low=35, price_high=45, note="Usually stocked in store"),
    ],
    cheapest_bet="Used on Facebook Marketplace is cheapest; new, watch Walmart and Target sales.", is_food=False,
).model_dump()

SAMPLE_FOOD = FoodResult(
    name="Sample: Grilled ribeye steak", serving="About 10 oz cooked steak with a pat of butter",
    calories=780, protein_g=62, carbs_g=0, fat_g=58, fiber_g=0,
    parts=[FoodPart(name="Ribeye steak", amount="10 oz cooked", calories=730, protein_g=62),
           FoodPart(name="Butter", amount="1 tsp", calories=35, protein_g=0),
           FoodPart(name="Seasoning", amount="pinch", calories=15, protein_g=0)],
    confidence="medium", notes="A sample answer. Add ANTHROPIC_API_KEY to .env to analyze your own photos.", is_food=True,
).model_dump()
