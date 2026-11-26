"""Clinical state is independent of an active fight; no invented measurements."""
from __future__ import annotations

from . import body

KIT = "shiki_medical_kit"
FIELDS = ("where", "severity", "lost", "turn", "bleed", "recovered_at")


def _session(turn):
    try:
        return int(str(turn).split(".")[0][1:])
    except (ValueError, IndexError):
        return None


def context(hot, *, compact=False):
    """Open wounds and residual loss, exact stable IDs, no inference of access.

    The compact live packet includes Shiki, present actors, and injuries from
    the current session (including patients who have been moved elsewhere).
    The on-demand medical route returns all recorded cases.
    """
    injuries = hot.get("injuries") or {}
    states = body.brief(hot)
    present = set((hot.get("scene") or {}).get("present") or [])
    cursor = (hot.get("cursor") or {}).get("id")
    session = _session(cursor)
    patients = {}
    omitted = []
    for who in sorted(set(injuries) | set(states)):
        if body.mode(hot, who) == "dead":
            continue
        wounds = [w for w in injuries.get(who, []) if not w.get("healed")]
        loss = states.get(who, {})
        if not wounds and not loss.get("loss"):
            continue
        recent = session is not None and any(
            _session(w.get("recovered_at") or w.get("turn")) == session for w in wounds)
        if compact and who != "shiki" and who not in present and not recent:
            omitted.append(who)
            continue
        if compact:
            row = {"w": [[w.get(k) for k in FIELDS] for w in wounds]}
        else:
            row = {"wounds": [dict(w) for w in wounds]}
        if loss.get("loss"):
            row["loss"] = {k: loss[k] for k in ("loss", "stage") if k in loss}
        if loss.get("mode") == "exceptional":
            row["physiology"] = {k: loss[k] for k in ("mode", "status_source", "status_note") if k in loss}
        patients[who] = row
    result = {"at": (hot.get("cursor") or {}).get("at"), "patients": patients}
    if compact:
        result["fields"] = list(FIELDS)
        if omitted:
            result["other_ids"] = omitted
        result["rule"] = "null=не установлено; пациент не обязательно рядом. Прочие раны/клиника по HOT и ходу."
    else:
        kit = (hot.get("things") or {}).get(KIT)
        result["kit"] = dict(kit) if isinstance(kit, dict) else {"status": "unknown"}
        result["chakra"] = dict((hot.get("shiki") or {}).get("chakra") or {})
        result["sources"] = ["recovery/HOT.json#injuries", "recovery/HOT.json#body",
                             "recovery/HOT.json#things", "campaigns/shiki/ref/MEDICINE.md"]
    return result
