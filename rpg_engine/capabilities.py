"""Source-bound capability registry: learned status is not active state."""
from __future__ import annotations

import json
import re
from pathlib import Path

PATH = "campaigns/shiki/ref/CAPABILITIES.json"
STATUSES = {"mastered", "experimental", "learning", "retired", "unlearned"}
ACTIVATIONS = {"passive", "declared", "conditional", "autonomous", "unavailable"}
TURN_ID = re.compile(r"s\d+\.(?:text|played)\.\d+")
STABLE_ID = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


def normalize(value):
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _strings(values):
    return isinstance(values, list) and bool(values) and all(
        isinstance(v, str) and bool(v.strip()) for v in values)


def validate(data, root):
    if data.get("schema") != "npr-capabilities/1" or data.get("campaign") != "naruto-shiki":
        raise ValueError("invalid capability index")
    entries, routes = data.get("entries"), data.get("routes")
    if not isinstance(entries, list) or not entries or not isinstance(routes, dict) or not routes:
        raise ValueError("empty capability entries/routes")
    ids, aliases = set(), {}
    root = Path(root).resolve()
    def source_ok(value):
        if TURN_ID.fullmatch(value):
            return True  # archived in game-state, verified separately
        path = (root / value).resolve()
        return value.startswith("campaigns/shiki/") and path.is_relative_to(root) and path.is_file()
    for key, route in routes.items():
        if not _strings(route.get("terms")) or not _strings(route.get("read")) or not _strings(route.get("required")) or not route.get("focus"):
            raise ValueError(f"invalid route {key}")
        if not all(source_ok(x) for x in route["read"]):
            raise ValueError(f"missing route source {key}")
    for row in entries:
        ident = row.get("id", "")
        if not isinstance(ident, str) or not STABLE_ID.fullmatch(ident) or ident in ids:
            raise ValueError("capabilities must have unique stable ids")
        ids.add(ident)
        if any(not isinstance(row.get(k), str) or not row[k].strip()
               for k in ("title", "summary", "requires", "limits")):
            raise ValueError(f"missing description/condition/limit: {ident}")
        if row.get("status") not in STATUSES or row.get("activation") not in ACTIVATIONS:
            raise ValueError(f"invalid capability state: {ident}")
        if (row["status"] == "mastered") == (row["activation"] == "unavailable"):
            raise ValueError(f"availability contradicts status: {ident}")
        if not _strings(row.get("tags")) or set(row["tags"]) - set(routes):
            raise ValueError(f"unknown capability route: {ident}")
        if not _strings(row.get("sources")) or not all(source_ok(x) for x in row["sources"]):
            raise ValueError(f"missing capability source: {ident}")
        if not any(x.startswith("campaigns/shiki/") for x in row["sources"]):
            raise ValueError(f"no accessible canonical reference: {ident}")
        if type(row.get("core")) is not bool or not _strings(row.get("aliases")):
            raise ValueError(f"missing core/aliases: {ident}")
        for alias in row["aliases"]:
            norm = normalize(alias)
            if norm in aliases and aliases[norm] != ident:
                raise ValueError(f"ambiguous capability alias: {alias}")
            aliases[norm] = ident
    return data


def load(root, campaign):
    if campaign not in ("naruto-shiki", "shiki"):
        return None
    path = Path(root) / PATH
    if not path.is_file():
        return None
    return validate(json.loads(path.read_text(encoding="utf-8")), root)


def resolve(data, name):
    """Exact stable ID, title or normalized alias. No substring identity guess."""
    norm = normalize(name)
    matches = [row for row in (data or {}).get("entries", [])
               if norm in {normalize(row["id"]), normalize(row["title"]),
                           *map(normalize, row["aliases"])}]
    return matches[0] if len(matches) == 1 else None


CONTINUATION = re.compile(r"^(?:дальше(?: по игре)?|продолж(?:ай|аем|аю|ить)|"
                          r"что дальше|и дальше|да|нет|угу|хорошо|так|понятно|"
                          r"слушаю|жду|молчу|поехали|go on|continue)[.!? ]*$")
WORD = re.compile(r"[\w]+", re.UNICODE)


def _words(value):
    return WORD.findall(normalize(value))


def _matches(text_words, term, *, prefix=False):
    """Token boundaries avoid 'меч' in 'замечаю' or 'рана' in 'охрана'."""
    needle = _words(term)
    if not needle:
        return False
    for i in range(len(text_words) - len(needle) + 1):
        candidate = text_words[i:i + len(needle)]
        if candidate[:-1] == needle[:-1] and (
                candidate[-1] == needle[-1] or prefix and candidate[-1].startswith(needle[-1])):
            return True
    return False


def _domains(data, text):
    words = _words(text)
    matched = []
    routes = set()
    for row in data.get("entries") or []:
        if any(_matches(words, alias) for alias in [row["id"], row["title"], *row["aliases"]]):
            matched.append(row["id"])
            routes.update(row["tags"])
    for key, row in (data.get("routes") or {}).items():
        if any(_matches(words, term, prefix=True) for term in row["terms"]):
            routes.add(key)
    return [key for key in data["routes"] if key in routes], matched


def for_request(data, player, *, scene=None, active_fight=False):
    """Current explicit domains win; bare continuations use verified scene context.

    A route is a retrieval hint, not a claim that the action is legal or known.
    """
    text = normalize(player).strip("# *")
    routes, matched = _domains(data, text)
    continuation = not text or bool(CONTINUATION.fullmatch(text))
    fallback = []
    if continuation and scene:
        # Deliberately exclude private 'unread' and old player requests.
        scene_text = " ".join(str(scene.get(k) or "") for k in ("situation", "positions", "pressure"))
        fallback, _ = _domains(data, scene_text)
        routes = list(dict.fromkeys(routes + fallback))
    if active_fight and "combat" not in routes:
        routes.append("combat")
    routes = [key for key in data["routes"] if key in routes]
    selected = [row for row in data.get("entries") or [] if set(row["tags"]) & set(routes)]
    selected.sort(key=lambda row: (row["id"] not in matched, not row["core"], row["id"]))
    read = list(dict.fromkeys(path for key in routes for path in data["routes"][key]["read"]))
    required = list(dict.fromkeys(field for key in routes for field in data["routes"][key].get("required", [])))
    return {"routes": routes, "entries": selected, "matched": matched,
            "basis": "scene_continuation" if continuation else "current_request",
            "scene_routes": fallback, "read": read, "required": required,
            "unavailable": [row["id"] for row in selected if row["activation"] == "unavailable"]}


def scene_hint(data, hot):
    """Small present-scene anchor, not a prediction of the next request."""
    scene = hot.get("scene") or {}
    plan = for_request(data, "", scene=scene, active_fight=bool((hot.get("_fight") or {}).get("active")))
    return {"as_of": scene.get("as_of"), "routes": plan["routes"],
            "source": "recovery/HOT.json#scene",
            "rule": "Для продолжения; явная заявка важнее. Активный бой всегда."}


SOURCE_BASE = "campaigns/shiki/"


def _short_source(value):
    return value.removeprefix(SOURCE_BASE)


def packet(root, campaign, hot=None):
    data = load(root, campaign)
    if not data:
        return None
    return {"rule": "Темы по текущей заявке объединяй; «дальше» — scene_hint. Прочитанное не перечитывай. Проверяй состояние и неизвестные факты по источнику; реестр не даёт знания NPC.",
            "source_base": SOURCE_BASE,
            "registry": PATH,
            "scene_hint": scene_hint(data, hot or {}),
            "routes": {key: {"read": [_short_source(x) for x in row["read"]],
                             "required": row["required"]} for key, row in data["routes"].items()},
            "items": [{"id": row["id"], "tags": row["tags"], "fact": row["summary"],
                       "sources": [_short_source(x) for x in row["sources"]],
                       "activation": row["activation"],
                       **({"status": row["status"]} if row["status"] != "mastered" else {})}
                      for row in data["entries"] if row["core"]],
            "catalog": [{"id": row["id"], "title": row["title"], "tags": row["tags"],
                         **({"status": row["status"], "activation": row["activation"]}
                            if row["activation"] == "unavailable" else
                            {"activation": row["activation"]} if row["activation"] != "declared" else {})}
                        for row in data["entries"] if not row["core"]],
            "catalog_rule": "Без status — освоено, без activation — по заявке. Условия в реестре. К путям ref/ и SHIKI.md добавь source_base; ID ходов неизменны."}
