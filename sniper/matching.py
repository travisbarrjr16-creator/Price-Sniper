"""Decide whether a listing title is actually the item the user asked for.

Marketplace search is loose: "RTX 4060" returns 4060 Ti cards, laptops, empty
boxes and backplates. This module is the strict second pass.
"""
from __future__ import annotations

import re

from .models import Listing, Watch

# Suffixes that turn one model into a different (usually pricier) one.
VARIANT_SUFFIXES = {
    "ti", "super", "xt", "xtx", "gre", "pro", "max", "plus", "ultra", "mini",
    "lite", "fe",
    "x", "k", "kf", "f", "ks", "g", "x3d",   # CPU models: 7600 vs 7600X, 13600K vs 13600KF, 5800X vs 5800X3D
}
# ...but only when written stuck to the number ("7600X"), not "RTX 4060 x2 fans"
GLUED_SUFFIXES = {"x", "k", "kf", "f", "ks", "g", "x3d"}
# "fe" (Founders Edition) is the same GPU, so it is not a variant in GPU searches ("Galaxy S23 FE" still is).
NON_VARIANT = {"fe"}

# Titles that are almost never the actual item.
JUNK_PHRASES = [
    "box only", "empty box", "for parts", "not working", "broken", "untested",
    "as is", "as-is", "bracket only", "backplate", "fan replacement",
    "replacement fan", "cooler only", "heatsink only", "shroud", "picture of",
    "photo of", "wtb", "wanted", "looking for", "iou", "read description",
    "parts only", "cracked", "no display", "artifacting",
]

_PLUS_EXTRAS = {"free", "bonus", "extra", "box", "cooler", "shipping", "games", "game", "case", "controller"}

# Card features, not accessories, when the title also describes the card itself ("Metal Backplate, 10GB GDDR6X")
_FEATURE_WORDS = {"backplate", "shroud"}
_WHOLE_CARD_RE = re.compile(r"\b(gddr ?\d\w*|\d+gb|graphics card|video card)\b")
_ACCESSORY_RE = re.compile(r"\b(backplate|shroud)s? (for|only)\b|\b(only|just) (the )?(backplate|shroud)\b")

# --- PC parts: a search for one part shouldn't match a whole computer or a bundle -------------
# Which kind of part a search is for, by keywords in the (normalized) search.
_PART_KINDS = {
    "gpu": re.compile(r"\b(rtx|gtx|geforce|radeon|rx \d{3,4}|arc [ab]\d{3})\b"),
    "cpu": re.compile(r"\b(ryzen|threadripper|xeon|core (i|ultra) ?\d|i[3579] \d{4,5}|\d{4,5}x3d)\b"),
    "ram": re.compile(r"\b(ddr ?[345]|dimm|sodimm)\b"),
    "storage": re.compile(r"\b(nvme|ssd|m\.2|hdd)\b"),
    "psu": re.compile(r"\b(psu|power supply|80 plus)\b"),
    "board": re.compile(r"\b(motherboard|[abxzh]\d{3}[em]?)\b"),
}
# Words that mean "this is a whole computer, or several things together".
_CONTAINER_RE = re.compile(
    r"\b(laptop|notebook|gaming pc|desktop replacement|desktop|pre ?built|computer|tower pc|full (pc|build|system)|"
    r"complete (pc|build|system)|pc build|custom build|lot of \d+|\d+ ?(pcs|pieces|units)|lot|bundle|combo|"
    r"all in one|aio pc)\b")
# Phrases that only describe the part, so they don't mean "a whole computer" or "comes with a CPU/GPU":
# "Desktop Processor", "Desktop Memory", "SSD for Laptop & Desktop", "Ryzen 5700G with Radeon Graphics",
# "DDR5 ... for AMD Ryzen / Intel".
_PC = r"(?:laptops?|desktops?|notebooks?|pcs?|computers?|gaming)"
_PART_WORDS = (r"(?:processors?|cpus?|memory|ram|graphics(?: cards?)?|gpus?|video cards?|ssds?|drives?|hard drives?|"
               r"power suppl(?:y|ies)|psus?|motherboards?|kits?|modules?)")
_DESCRIPTIVE_RE = re.compile(
    rf"\b(?:{_PC} ){{1,3}}{_PART_WORDS}\b"                              # "desktop processor", "laptop memory"
    rf"|\b(?:for|in|fits|upgrade|compatible with|works with) (?:your |a |most |any )?{_PC}(?: (?:and |or )?{_PC})*\b"
    rf"|\b(?:pulled|removed|taken|came) (?:out )?(?:of|from) (?:an? |my |the |his )?(?:[a-z0-9]+ ){{0,2}}?(?:{_PC}|pre ?built)\b"
    rf"|\bused in (?:my |a |the )?(?:{_PC} ){{0,2}}(?:{_PC}|pre ?built|build)\b"   # "pulled from a Dell desktop"
    r"|(?<!gaming )\b(?:pcs?|desktops?) (?:and|or) laptops?\b|(?<!gaming )\blaptops? (?:and|or) (?:pcs?|desktops?)\b"  # "PC & laptop", not "Laptop PC"/"Gaming Laptop/PC"
    rf"|\b{_PC} (?:power )?(?:cables?|cords?|adapters?)\b"
    r"|\b(?:amd )?radeon(?: vega| rx vega)?(?: \d{1,4}(?: ?m)?)? graphics\b|\b(?:intel )?(?:uhd|iris xe|integrated) graphics\b"
    r"|\b(?:for|with|compatible with|optimized for|supports?) (?:amd |intel )*(?:ryzen|core|amd|intel|expo|xmp)\b"
    r"|\b(?:amd )?expo\b|\b(?:intel )?xmp(?: \d(?:\.\d)?)?\b")
# For a GPU search, a title that also names a CPU is a PC or a bundle (and the other way around).
_CROSS_KINDS = {"gpu": ("cpu",), "cpu": ("gpu",), "ram": ("gpu", "cpu"), "storage": ("gpu", "cpu")}


# Everyday words people search with ("ram", "gpu") -> what store titles actually say.
# Used for the search words only; matched against the (normalized) listing title.
GENERIC_WORDS = {
    "ram": r"ram|memory(?! card| foam| stick)|ddr ?[2-5]|dimm|sodimm",
    "memory": r"ram|memory(?! card| foam| stick)|ddr ?[2-5]|dimm|sodimm",
    "gpu": r"gpu|graphics card|video card|graphics(?! tablet| drawing| pen)|rtx|gtx|geforce|radeon rx|arc [ab]\d",
    "graphics": r"gpu|graphics(?! tablet| drawing| pen)|video card|rtx|gtx|geforce|radeon rx",
    "cpu": r"cpu|processor|ryzen|core i ?\d|core ultra|xeon|threadripper",
    "processor": r"cpu|processor|ryzen|core i ?\d|core ultra|xeon|threadripper",
    "ssd": r"ssd|nvme|solid state|m\.2",
    "nvme": r"nvme|m\.2",
    "hdd": r"hdd|hard drive|hard disk",
    "psu": r"psu|power supply",
    "mobo": r"mobo|motherboard|mainboard",
    "motherboard": r"motherboard|mobo|mainboard",
    "monitor": r"monitor|display|screen",
    "laptop": r"laptop|notebook",
    "notebook": r"notebook|laptop",
    "tv": r"tv|television|smart tv",
    "headphones": r"headphones?|headset|earbuds?",
    "headset": r"headset|headphones?",
}
# Two-word everyday terms, checked as one ("power supply" ~ "850W PSU")
GENERIC_PHRASES = [
    (("power", "supply"), r"power supply|psu"),
    (("graphics", "card"), GENERIC_WORDS["gpu"]), (("video", "card"), GENERIC_WORDS["gpu"]),
    (("hard", "drive"), GENERIC_WORDS["hdd"]), (("solid", "state"), GENERIC_WORDS["ssd"]),
]
# Real words that show up in listing titles: a search word one letter off from one of these is
# probably a different thing, not a typo ("razer" vs "racer", "gigabyte" vs "gigabit").
COMMON_TITLE_WORDS = {
    "racer", "racing", "gigabit", "gaming", "games", "great", "deals", "board", "cards", "cable", "cables", "stand",
    "mount", "cover", "black", "white", "silver", "light", "power", "super", "ultra", "sound", "smart", "watch",
    "phone", "phones", "screen", "pocket", "tablet", "mouse", "mice", "drive", "drives", "dryer", "laser", "paper",
    "mixer", "music", "tower", "water", "steel", "metal", "glass", "plastic", "wooden", "basic", "ports", "sport",
    "sports", "speed", "sleep", "slime", "shift", "share", "store", "stock", "storm", "strap", "sense", "since",
    "inch", "inches", "pound", "pounds", "pairs", "parts", "party", "lights", "tones", "horse", "house", "honor",
}
# Search words that say which kind of part it is, even without a model ("ram ddr4", "gpu 8gb")
_QUERY_KINDS = {
    "gpu": re.compile(r"\b(gpu|graphics card|video card)\b"),
    "cpu": re.compile(r"\b(cpu|processor)\b"),
    "ram": re.compile(r"\b(ram|memory)(?! card)\b"),
    "storage": re.compile(r"\b(ssd|solid state|hard drive)\b"),
    "psu": re.compile(r"\b(power supply)\b"),
    "board": re.compile(r"\b(mobo)\b"),
}


def part_kind(query: str) -> str | None:
    q = normalize(query)
    return (next((k for k, rx in _PART_KINDS.items() if rx.search(q)), None)
            or next((k for k, rx in _QUERY_KINDS.items() if rx.search(q)), None))


def whole_system_reason(title: str, query: str) -> str | None:
    """Why this listing is a computer/bundle rather than the part searched for (None if it's the part)."""
    kind = part_kind(query)
    if not kind:
        return None                     # only for PC-part searches: "computer" is fine in a Raspberry Pi search
    title = re.sub(r"\s*[&/]\s*", " and ", title)          # "PC & laptop" / "PC/laptop" -> "pc and laptop"
    t, q = _DESCRIPTIVE_RE.sub(" ", normalize(title)), normalize(query)
    m = _CONTAINER_RE.search(t)
    if m and not _CONTAINER_RE.search(q):
        return f"not just the part ('{m.group(0)}')"
    for other in _CROSS_KINDS.get(kind, ()):
        if _PART_KINDS[other].search(t) and not _PART_KINDS[other].search(q):
            return f"includes a {other.upper()} (PC or bundle)"
    return None


_UNIT_RE = re.compile(r"(\d+)\s+(gb|tb|mb|mhz|ghz|hz|in|inch|mm|w)\b")
# "2x16GB" / "2 x 16 GB" -> "2x16" (kits, multipacks)
# (a kit count is 1-2 digits, so "7600X 6-core" stays the 7600X CPU)
_KIT_RE = re.compile(r"\b(\d{1,2}) ?x ?(\d+)(?:gb|tb|mb)?\b")
# units don't matter when comparing words: "16" == "16gb", "6400" == "6400mhz"
_CORE_RE = re.compile(r"^(\d+(?:\.\d+)?|\d+x\d+)(gb|tb|mb|mhz|ghz|hz|mt|w|mm|in|inch)$")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, glue numbers to their units ("8 GB" -> "8gb")."""
    t = re.sub(r"(?<=\d)\+", " plus", text.lower())       # "80+" = "80 plus", "S24+" = "S24 plus"
    t = re.sub(r"[^a-z0-9.]+", " ", t)
    t = _UNIT_RE.sub(r"\1\2", t)
    # split letter/number runs so "rtx4060ti" -> "rtx 4060 ti"
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)
    # ...but keep units glued back together, and kits like "2 x 16gb" as one word
    t = _UNIT_RE.sub(r"\1\2", t)
    t = _KIT_RE.sub(r"\1x\2", t)
    t = re.sub(r"\bx 3 d\b", "x3d", t)                     # "5800X3D" -> "5800 x3d", not the "5800X"
    return re.sub(r"\s+", " ", t).strip()


def _raw_words(text: str) -> list[str]:
    """Words as written, without splitting letters from numbers ("ddr5" stays "ddr5")."""
    t = re.sub(r"[^a-z0-9.]+", " ", re.sub(r"(?<=\d)\+", " plus", text.lower()))
    t = _KIT_RE.sub(r"\1x\2", _UNIT_RE.sub(r"\1\2", t))
    return t.split()


def _core(word: str) -> str:
    return _CORE_RE.sub(r"\1", word)


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def _typo_distance(a: str, b: str, limit: int) -> int:
    """Edits between two words (a swapped pair of letters counts as one), stopping early past `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def close_spelling(word: str, candidates) -> bool:
    """A misspelled name word ("corsiar", "nvidea", "rysen") that is clearly one of `candidates`.

    Only plain letter words of 4+ letters: numbers and model codes must match exactly (4060 is not
    4070), and short words like "pro"/"max"/"ti" decide the model, so they're never guessed.
    """
    # 5+ letters only: short names are too easy to mix up (dell/deal, sony/song, ipad/ipod, omen/open)
    if len(word) < 5 or not word.isalpha() or word in VARIANT_SUFFIXES:
        return False
    limit = 1 if len(word) < 9 else 2
    for c in candidates:
        if (not c.isalpha() or c in VARIANT_SUFFIXES or c in COMMON_TITLE_WORDS
                or abs(len(c) - len(word)) > limit or c[0] != word[0]):
            continue                       # people rarely get the first letter wrong
        if _typo_distance(word, c, limit) <= limit:
            return True
    return False


def missing_query_word(text: str, query: str) -> str | None:
    """The first search word that isn't in `text`, or None if they're all there.

    A word matches if it appears as written ("ddr5", "6400mhz" ~ "6400"), or if its letter/number
    parts appear right next to each other ("rtx4060" ~ "RTX 4060"). Parts scattered around the
    title don't count, so "DDR5" doesn't match "DDR4 ... Z5". Everyday words count when the title
    says the same thing ("ram" ~ "DDR4 Desktop Memory"), and name words may be misspelled a little.
    """
    norm = normalize(text)
    split = [_core(w) for w in norm.split()]
    raw = {_core(w) for w in _raw_words(text)} | set(split)
    qwords = _raw_words(query)
    for phrase, pattern in GENERIC_PHRASES:          # "power supply" counts as one term
        n = len(phrase)
        for i in range(len(qwords) - n + 1):
            if tuple(qwords[i:i + n]) == phrase and re.search(rf"(?<![a-z0-9])({pattern})(?![a-z0-9])", norm):
                qwords = qwords[:i] + qwords[i + n:]
                break
    for word in qwords:
        if _core(word) in raw:
            continue
        parts = [_core(p) for p in normalize(word).split()]
        n = len(parts)
        if parts and any(split[i:i + n] == parts for i in range(len(split) - n + 1)):
            continue
        if word in GENERIC_WORDS and re.search(rf"(?<![a-z0-9])({GENERIC_WORDS[word]})(?![a-z0-9])", norm):
            continue
        if close_spelling(word, raw):
            continue
        return word
    return None


def _has_term(norm_title: str, term: str) -> bool:
    nt = normalize(term)
    if not nt:
        return True
    return re.search(rf"(?<![a-z0-9]){re.escape(nt)}(?![a-z0-9])", norm_title) is not None


def match(listing: Listing, watch: Watch) -> tuple[bool, str | None]:
    """Return (matches, reason_if_rejected)."""
    title = normalize(listing.title)
    title_tokens = title.split()
    query_tokens = tokens(watch.query)

    if (word := missing_query_word(listing.title, watch.query)) is not None:
        return False, f"missing '{word}'"

    for term in watch.required_terms:
        if normalize(term) and not _has_term(title, term):
            return False, f"missing required '{term}'"

    for term in watch.excluded_terms:
        if normalize(term) and _has_term(title, term):     # a blank term must not exclude everything
            return False, f"excluded '{term}'"

    for phrase in JUNK_PHRASES:
        if _has_term(title, phrase) and not _has_term(normalize(watch.query), phrase):
            if phrase in _FEATURE_WORDS and _WHOLE_CARD_RE.search(title) and not _ACCESSORY_RE.search(title):
                continue
            return False, f"junk '{phrase}'"

    if watch.strict_variants:
        if why := whole_system_reason(listing.title, watch.query):
            return False, why
        query_set = set(query_tokens)
        is_gpu = part_kind(watch.query) == "gpu"      # "FE" = Founders Edition (same card); on phones "S23 FE" is another model
        for i, tok in enumerate(title_tokens):
            # "4070 Super" when you searched "4070"; also "15 Pro Max" when you searched "15 Pro"
            if (tok.isdigit() or tok in VARIANT_SUFFIXES) and tok in query_set and i + 1 < len(title_tokens):
                nxt = title_tokens[i + 1]
                if nxt in GLUED_SUFFIXES and not re.search(rf"(?<![a-z0-9]){tok}{nxt}", listing.title.lower()):
                    continue
                low = listing.title.lower()
                if (not tok.isdigit() and re.search(rf"(?<![a-z0-9]){tok}\s*[,;.|]\s*{nxt}(?![a-z0-9])", low)
                        and not re.search(rf"(?<![a-z0-9]){tok}[\s-]+{nxt}(?![a-z0-9])", low)):
                    continue        # "iPhone 15 Pro, Max battery health": a comma between, not "Pro Max"
                if (nxt == "plus" and i + 2 < len(title_tokens) and title_tokens[i + 2] in _PLUS_EXTRAS
                        and (tok in GLUED_SUFFIXES or re.search(rf"(?<![a-z0-9]){tok}\s+\+", low))):
                    continue        # "7600X Plus free cooler", "RTX 4060 + free game" (not "iPhone 15 Plus case", "S24+")
                if nxt in VARIANT_SUFFIXES and not (nxt in NON_VARIANT and is_gpu) and nxt not in query_set:
                    return False, f"variant '{tok} {nxt}'"

    if listing.condition not in watch.conditions:
        return False, f"condition '{listing.condition}'"

    if listing.is_auction and not watch.include_auctions:
        return False, "auction"

    if (
        listing.local_pickup
        and watch.radius_miles
        and listing.distance_miles is not None
        and listing.distance_miles > watch.radius_miles
    ):
        return False, "outside radius"

    return True, None
