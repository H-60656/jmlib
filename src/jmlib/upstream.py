"""Upstream album-id lookup for import: candidates only, never a decision.

SCOPE (read this before calling anything here):

* Only a **user-triggered** import/resolve step may call :func:`search`.  Nothing
  in this module runs at server startup, on a timer, or as a side effect of
  indexing, and no archive byte is ever uploaded — only a title keyword is sent.
* A lookup produces **candidates with a score and a reason**.  It never picks
  one: ``auto_ok`` is a UI hint for "this candidate is unambiguous", and the
  actual Aid is only whatever a human confirms.
* No download happens here.  Fetching images is ``jmlib download``'s job.

Offline-testability is deliberate: :func:`search` accepts an injected ``client``
so the whole module can be contracted without touching the network.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
from pathlib import Path

from jmlib import paths

SCHEMA = "jmlib.upstream.v1"
CACHE_TTL_SECONDS = 24 * 3600
MIN_INTERVAL_SECONDS = 1.5
DEFAULT_LIMIT = 8

# Upstream answers a long decorated string ("001-[汉化组] 标题 [中国翻译] [DL版]")
# with popular books that merely share a token, so the *first* non-empty answer
# is worthless.  Query variants are therefore all tried, best answer wins, and
# only a variant good enough to auto-ok stops the loop early.
MAX_VARIANTS_TRIED = 3

# A candidate this close to the query, and this far ahead of the runner-up, is
# unambiguous enough to pre-select in the UI.  It is still never applied alone.
AUTO_OK_SCORE = 0.85
AUTO_OK_MARGIN = 0.08

# Small traditional -> simplified map (kept tiny on purpose: no third-party
# dependency, and only the characters that actually show up in these titles).
_TRAD_SIMP = {
    "級": "级",
    "話": "话",
    "巻": "卷",
    "體": "体",
    "們": "们",
    "個": "个",
    "這": "这",
    "學": "学",
    "園": "园",
    "樂": "乐",
    "樣": "样",
    "說": "说",
    "讓": "让",
    "時": "时",
    "間": "间",
    "對": "对",
    "開": "开",
    "關": "关",
    "見": "见",
    "現": "现",
    "實": "实",
    "應": "应",
    "會": "会",
    "動": "动",
    "過": "过",
    "還": "还",
    "麼": "么",
    "發": "发",
    "髮": "发",
    "裡": "里",
    "後": "后",
    "從": "从",
    "舊": "旧",
    "親": "亲",
    "愛": "爱",
    "戀": "恋",
    "慾": "欲",
    "處": "处",
    "場": "场",
    "頭": "头",
    "臉": "脸",
    "濕": "湿",
    "腳": "脚",
    "媽": "妈",
    "嗎": "吗",
    "兒": "儿",
    "麗": "丽",
    "與": "与",
    "為": "为",
    "無": "无",
    "眾": "众",
    "車": "车",
    "週": "周",
    "總": "总",
}

_BRACKET_RE = re.compile(r"\[[^\[\]]*\]|\([^()]*\)|（[^（）]*）|【[^【】]*】|〔[^〔〕]*〕")
_NOISE_RE = re.compile(
    r"(中国翻译|中国语|中文|汉化组|漢化組|个人汉化|個人漢化|汉化|漢化|无修正|無修正"
    r"|DL版|DL|中国語|生肉|全彩|重制|重置)",
    re.I,
)
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+")
_EXT_RE = re.compile(r"\.(zip|rar|7z|jpg|jpeg|png|webp|gif|bmp)$", re.I)
_PUNCT_RE = re.compile(r"[\s\u3000!-/:-@\[-`{-~。、，．·！？…—～「」『』：；（）【】]+")

# The old archive names every volume as ``001-<title>`` (also ``2_``, ``03.``).
# The number is a volume marker, not part of the work's name, and upstream has
# never heard of it: leaving it in costs a token on every honest match.
_VOLUME_PREFIX_RE = re.compile(r"^\d{1,3}\s*[-_.、]\s*")


def _to_simplified(text: str) -> str:
    return "".join(_TRAD_SIMP.get(ch, ch) for ch in text)


def _strip_brackets(text: str, rounds: int = 3) -> str:
    """Drop bracketed decorations, repeating so nested groups unwind fully.

    One pass is not enough: ``[グループ (作者)]`` contains a complete ``(...)``
    inside a complete ``[...]``, so removing the inner pair first would leave an
    orphan ``]`` in the query string.  Looping until stable keeps the query free
    of bracket debris, which upstream would otherwise search for literally.
    """
    out = text or ""
    for _ in range(rounds):
        stripped = _BRACKET_RE.sub(" ", out)
        if stripped == out:
            break
        out = stripped
    return out


def normalize(text: str) -> str:
    """Lowercased, punctuation-free, simplified-folded form used as a cache key."""
    out = _EXT_RE.sub("", text or "")
    out = _to_simplified(out)
    out = _PUNCT_RE.sub(" ", out.lower())
    return re.sub(r"\s+", " ", out).strip()


def clean_title(text: str) -> str:
    """A directory/zip name reduced to the work's own title.

    Drops the extension, HTML debris (``&nbsp;``), the ``001-`` volume prefix and
    bracketed group/publisher tags, then collapses whitespace.  What remains is
    what a person would type into a search box.
    """
    out = html.unescape(_EXT_RE.sub("", (text or "").strip()))
    out = _VOLUME_PREFIX_RE.sub("", out)
    out = _strip_brackets(out)
    out = _NOISE_RE.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip(" _-·、")


def variants(title: str) -> list[str]:
    """Query strings for one directory name, most specific first.

    The cleaned title comes first: it is what upstream actually indexes, and the
    decorated original is kept only as a fallback for the rare upstream album
    that genuinely carries the group tag in its name.
    """
    stem = _EXT_RE.sub("", (title or "").strip())
    if not stem:
        return []
    cleaned = clean_title(stem)

    def add(bucket: list[str], value: str) -> None:
        value = re.sub(r"\s+", " ", value).strip()
        if len(value) < 2:
            return
        if value not in bucket:
            bucket.append(value)

    out: list[str] = []
    add(out, cleaned)
    add(out, _to_simplified(cleaned))
    longest = max(_CJK_RE.findall(cleaned) or [""], key=len, default="")
    add(out, longest)
    # Last resorts: the title plus its decorated variants, for albums whose own
    # name includes the group/publisher tag.
    add(out, _strip_brackets(html.unescape(stem)))
    add(out, _to_simplified(html.unescape(stem)))
    return out[:6]


def _bigrams(text: str) -> set[str]:
    compact = text.replace(" ", "")
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def _core(text: str) -> str:
    """Normalized title with local naming noise removed.

    Directory names carry group/publisher/quality decorations ("[猫瓶瓶汉化组]",
    "中国翻译", "DL版") and an ``001-`` volume prefix that upstream album names do
    not repeat.  Leaving them in would count them as *missing* query tokens on
    every honest match, so the score would punish correct candidates.
    Comparison happens on the core name.
    """
    return normalize(clean_title(text or ""))


_VOLUME_IN_TEXT_RE = re.compile(
    r"^\s*(\d{1,3})\s*[-_.、]|(?:第|第\s*)?\s*([0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*[话話巻卷集部]"
)


def _volume_of(text: str) -> str:
    """The volume/chapter marker a name claims, or '' when it claims none."""
    m = _VOLUME_IN_TEXT_RE.search(text or "")
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def score(query: str, candidate: dict) -> dict:
    """Similarity of one candidate to the query, 0..1, plus the reasons why.

    Conservative by design: a wrong match imports the wrong book, so the score
    rewards exact overlap heavily and the length penalty keeps "same author,
    different work" from looking like a match.  Volume numbers count like any
    other token, which is what separates "vol 1" from "vol 2" of one series —
    and a *contradicting* volume marker is penalised outright, because matching
    volume 1 of a work against volume 5 of the same work is the most damaging
    near-miss this resolves against.
    """
    raw_name = candidate.get("name") or candidate.get("title") or ""
    q = _core(query) or normalize(query)
    name = _core(raw_name) or normalize(raw_name)
    if not q or not name:
        return {"score": 0.0, "hits": ["空标题，无法比较"]}

    qb, nb = _bigrams(q), _bigrams(name)
    overlap = len(qb & nb) / max(len(qb), 1)  # recall of query bigrams
    dice = len(qb & nb) / max(len(qb) + len(nb), 1) * 2
    bigram = 0.45 * overlap + 0.35 * dice

    q_tokens = [t for t in q.split(" ") if len(t) >= 2]
    contained = sum(1 for t in q_tokens if t and t in name)
    token = (contained / len(q_tokens)) if q_tokens else (1.0 if q in name else 0.0)

    length_penalty = min(abs(len(q) - len(name)) / max(len(q), len(name), 1), 1.0)
    length = 1.0 - length_penalty

    author = ""
    tags = candidate.get("tags") or []
    author_hit = 0.0
    for raw in [author, *(tags if isinstance(tags, (list, tuple)) else [])]:
        text = normalize(str(raw))
        if text and len(text) >= 2 and text in q:
            author_hit = 1.0
            break

    total = 0.45 * bigram + 0.25 * token + 0.15 * length + 0.15 * author_hit
    hits = [
        f"标题 bigram 重合 {overlap:.2f}",
        f"词元包含 {contained}/{len(q_tokens) or 1}",
        f"长度接近度 {length:.2f}",
    ]

    # One name wholly inside the other is strong evidence: upstream titles are
    # routinely the local title plus a group tag, or minus a subtitle the local
    # folder never had.  Pure bigram overlap cannot see that.
    if q and name and (q in name or name in q):
        shorter, longer = (q, name) if len(q) <= len(name) else (name, q)
        ratio = len(shorter) / max(len(longer), 1)
        boost = 0.55 * ratio
        total = max(total, min(0.95, boost + 0.35))
        hits.append(f"一方标题包含另一方（{ratio:.2f}）")

    q_vol, c_vol = _volume_of(query), _volume_of(raw_name)
    if q_vol and c_vol and q_vol != c_vol:
        total *= 0.5
        hits.append(f"卷号冲突 {q_vol} vs {c_vol}")

    if author_hit:
        hits.append("作者/标签在目录名中出现")
    if q == name:
        hits.append("规范化后完全同名")
        total = 1.0
    return {"score": round(min(total, 1.0), 4), "hits": hits}


def rank(query: str, candidates: list[dict], limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Score and sort candidates; each keeps its score and reasons."""
    scored = []
    for cand in candidates:
        s = score(query, cand)
        scored.append({**cand, "score": s["score"], "hits": s["hits"]})
    scored.sort(key=lambda c: (-c["score"], str(c.get("id") or "")))
    return scored[:limit]


def decide(scored: list[dict]) -> dict:
    """Whether the top candidate is unambiguous enough to pre-select (never to apply)."""
    if not scored:
        return {"auto_ok": False, "reason": "没有候选"}
    top = scored[0]["score"]
    second = scored[1]["score"] if len(scored) > 1 else 0.0
    margin = top - second
    if top >= AUTO_OK_SCORE and margin >= AUTO_OK_MARGIN:
        return {"auto_ok": True, "reason": f"最高分 {top:.2f}，领先 {margin:.2f}"}
    if top < AUTO_OK_SCORE:
        return {"auto_ok": False, "reason": f"最高分仅 {top:.2f}，需人工判断"}
    return {"auto_ok": False, "reason": f"候选接近（差 {margin:.2f}），需人工判断"}


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #


def _cache_path() -> Path:
    return paths.UPSTREAM_CACHE


def load_cache(path: Path | None = None) -> dict:
    target = Path(path) if path is not None else _cache_path()
    if not target.exists():
        return {"schemaVersion": SCHEMA, "entries": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schemaVersion": SCHEMA, "entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"schemaVersion": SCHEMA, "entries": {}}
    return data


def save_cache(cache: dict, path: Path | None = None) -> Path:
    target = Path(path) if path is not None else _cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".json.part")
    part.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(part, target)
    return target


def cache_get(
    query: str, *, cache: dict | None = None, now: float | None = None, ttl: int = CACHE_TTL_SECONDS
) -> dict | None:
    data = cache if cache is not None else load_cache()
    entry = (data.get("entries") or {}).get(normalize(query))
    if not entry:
        return None
    age = (now if now is not None else time.time()) - float(entry.get("fetched_at") or 0)
    if age > ttl:
        return None
    result = entry.get("result")
    return result if isinstance(result, dict) else None


def cache_put(query: str, result: dict, *, cache: dict | None = None, now: float | None = None) -> dict:
    data = cache if cache is not None else load_cache()
    data.setdefault("schemaVersion", SCHEMA)
    entries = data.setdefault("entries", {})
    entries[normalize(query)] = {
        "query": query,
        "fetched_at": now if now is not None else time.time(),
        "result": result,
    }
    return data


# --------------------------------------------------------------------------- #
# live search
# --------------------------------------------------------------------------- #

_last_call_at = 0.0


def _default_client(option: Path | None = None):
    """Build a jmcomic client lazily; only reached on a real user-triggered search."""
    import jmcomic

    if option is not None:
        opt = jmcomic.create_option_by_file(str(option))
    else:
        opt = jmcomic.JmModuleConfig.option_class().construct(
            __import__("copy").deepcopy(jmcomic.JmModuleConfig.option_default_dict())
        )
    return opt.new_jm_client()


def album_info(aid: str, *, client=None, throttle: bool = True) -> dict:
    """Public metadata for one album — title, tags, authors.  No images.

    Same rules as :func:`search`: user-triggered only, one call, spaced by the
    shared throttle, and failures come back as a state instead of raising.  Used
    to backfill tags without downloading a single page.
    """
    global _last_call_at

    base = {"ok": False, "aid": str(aid), "title": "", "tags": [], "authors": [], "error": ""}
    if client is None:
        try:
            client = _default_client()
        except Exception as exc:
            return {**base, "error": f"{type(exc).__name__}: {exc}"}

    if throttle:
        wait = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call_at)
        if wait > 0:
            time.sleep(wait)

    try:
        _last_call_at = time.monotonic()
        album = client.get_album_detail(str(aid))
    except Exception as exc:
        return {**base, "error": f"{type(exc).__name__}: {exc}"}

    tags = [str(t).strip() for t in (getattr(album, "tags", None) or []) if str(t).strip()]
    authors: list[str] = []
    for attr in ("authors", "author", "authoroname"):
        value = getattr(album, attr, None)
        if isinstance(value, str) and value.strip():
            authors.append(value.strip())
        elif isinstance(value, (list, tuple, set)):
            authors.extend(str(v).strip() for v in value if str(v).strip())
    # De-duplicate while keeping the first occurrence's order.  A set membership
    # test is the mechanism, but the side effect must not live inside a
    # comprehension — write the two steps out.
    unique: list[str] = []
    seen: set[str] = set()
    for author in authors:
        if author not in seen:
            seen.add(author)
            unique.append(author)
    authors = unique

    return {
        "ok": True,
        "aid": str(getattr(album, "album_id", None) or getattr(album, "id", None) or aid),
        "title": str(getattr(album, "name", "") or "").strip(),
        "tags": tags,
        "authors": authors,
        "error": "",
    }


def _classify_failure(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if any(k in text for k in ("403", "429", "forbidden", "too many", "blocked", "cloudflare")):
        return "blocked"
    return "error"


def _extract(page) -> list[dict]:
    """Candidates out of a ``JmSearchPage`` (or any iterable of (id, payload))."""
    out: list[dict] = []
    seen: set[str] = set()

    def push(aid, payload) -> None:
        aid = str(aid) if aid is not None else ""
        if not aid or aid in seen:
            return
        seen.add(aid)
        if isinstance(payload, dict):
            name = payload.get("name") or payload.get("title") or ""
            tags = payload.get("tags") or []
        else:
            name = str(payload)
            tags = []
        tags = [str(t) for t in tags] if isinstance(tags, (list, tuple, set)) else [str(tags)]
        out.append({"id": aid, "name": name, "tags": tags})

    single = getattr(page, "album", None)
    if single is not None:
        push(
            getattr(single, "album_id", None) or getattr(single, "id", None),
            {"name": getattr(single, "name", ""), "tags": getattr(single, "tags", [])},
        )
        return out

    for item in page or []:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            push(item[0], item[1])
        elif isinstance(item, dict):
            push(item.get("id") or item.get("aid"), item)
        else:
            push(
                getattr(item, "album_id", None) or getattr(item, "id", None),
                {"name": getattr(item, "name", ""), "tags": getattr(item, "tags", [])},
            )
    return out


def search(
    title: str,
    *,
    limit: int = DEFAULT_LIMIT,
    client=None,
    cache: dict | None = None,
    cache_path: Path | None = None,
    now: float | None = None,
    force: bool = False,
    throttle: bool = True,
    max_variants: int = MAX_VARIANTS_TRIED,
) -> dict:
    """Look up candidates for one directory name.

    Returns a structured result — never raises for a network/upstream problem::

        {"ok": bool, "query": str, "variant": str, "candidates": [...],
         "auto_ok": bool, "reason": str, "cached": bool,
         "failure": None | "timeout" | "blocked" | "empty" | "error",
         "error": str}

    Every variant is tried and the *best* answer wins, because upstream answers a
    decorated query with unrelated popular books: returning the first non-empty
    page would hand back a confident-looking wrong book.  The loop stops early
    once a variant is good enough to auto-accept, so the common case still costs
    one request.

    ``client`` and ``cache`` are injectable so contracts never touch the network
    or the real cache file.
    """
    global _last_call_at

    base = {
        "ok": False,
        "query": title,
        "variant": "",
        "candidates": [],
        "auto_ok": False,
        "reason": "",
        "cached": False,
        "failure": None,
        "error": "",
    }

    tried = variants(title)
    if not tried:
        return {**base, "reason": "目录名里没有可用作搜索的标题", "failure": "empty"}

    if not force:
        hit = cache_get(title, cache=cache, now=now) or cache_get(tried[0], cache=cache, now=now)
        if hit is not None:
            return {**hit, "cached": True}

    if client is None:
        try:
            client = _default_client()
        except Exception as exc:  # offline / unconfigured: a state, not a crash
            return {**base, "reason": "无法创建上游客户端", "failure": "error", "error": f"{type(exc).__name__}: {exc}"}

    last_error = ""
    best: dict | None = None
    for variant in tried[: max(1, max_variants)]:
        if throttle:
            wait = MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call_at)
            if wait > 0:
                time.sleep(wait)
        try:
            _last_call_at = time.monotonic()
            page = client.search_site(search_query=variant, page=1)
        except Exception as exc:
            failure = _classify_failure(exc)
            last_error = f"{type(exc).__name__}: {exc}"
            if failure in ("timeout", "blocked"):
                # A blocked/throttled upstream is not a per-variant problem: stop
                # rather than retry three times into the same wall.
                return {**base, "variant": variant, "reason": "上游不可用", "failure": failure, "error": last_error}
            continue

        raw = _extract(page)
        if not raw:
            continue
        ranked = rank(title, raw, limit=limit)
        verdict = decide(ranked)
        candidate = {
            "ok": True,
            "query": title,
            "variant": variant,
            "candidates": ranked,
            "auto_ok": verdict["auto_ok"],
            "reason": verdict["reason"],
            "cached": False,
            "failure": None,
            "error": last_error,
        }
        if verdict["auto_ok"]:
            best = candidate
            break
        if best is None or ranked[0]["score"] > best["candidates"][0]["score"]:
            best = candidate

    if best is None:
        return {**base, "variant": tried[0], "reason": "上游没有返回任何结果", "failure": "empty", "error": last_error}

    data = cache_put(title, best, cache=cache, now=now)
    if cache is None:
        try:
            save_cache(data, cache_path)
        except OSError:
            pass  # a cache that cannot be written must not fail the lookup
    return best
