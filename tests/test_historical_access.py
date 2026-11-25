import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rpg_engine import acquaint, echo, history_access, npc_memory, presence, drama, talk


class HistoricalAccessTest(unittest.TestCase):
    def test_shared_surname_and_mentions_are_not_acquaintance(self):
        names = {"a": presence._entry(["Сэйдзи Минасэ"]), "b": presence._entry(["Рэи Минасэ"])}
        rows = [{"id": f"s08.text.{i:03}", "gm": "Сэйдзи Минасэ упомянут в документе."} for i in range(1, 8)]
        rows += [{"id": "s08.text.008", "gm": "**Минасэ:** — Документ.", "d": {"scene": {"present": ["b"]}}}]
        met = acquaint.index(None, names, rows)
        self.assertEqual(acquaint.level(met, "a", 9), (0, []))
        self.assertEqual(met["b"]["spoke"], ["s08.text.008"])
        self.assertNotIn("s08.text.008", met.get("a", {}).get("spoke", []))

    def test_gap_does_not_inherit_presence_and_remote_label_is_not_meeting(self):
        names = {"a": presence._entry(["Сэйдзи Минасэ"])}
        rows = [{"id": "s08.text.001", "gm": "", "d": {"scene": {"present": ["a"]}}},
                {"id": "s08.text.003", "gm": "**Сэйдзи Минасэ, по рации:** — Слышу.", "d": {}}]
        met = acquaint.index(None, names, rows)
        self.assertEqual(met["a"]["seen"], ["s08.text.001"])
        self.assertEqual(met["a"]["spoke"], [])

    def test_history_own_speech_does_not_include_other_voice(self):
        names = {"a": presence._entry(["Сэйдзи Минасэ"]), "b": presence._entry(["Рэи Минасэ"])}
        text = "**Сэйдзи Минасэ:** — Контракт подписан.\n\n**Рэи Минасэ:** — Тайный код."
        self.assertEqual(history_access.own_speech(text, "a", "s08.text.001", names, [], {"a", "b"}),
                         ["Контракт подписан."])
        self.assertEqual(history_access.own_speech(text, "a", "s08.text.001", names, [], {"b"}), [])

    def test_absent_npc_private_patch_does_not_receive_global_line(self):
        h = {"scene": {"present": []}, "cast": {"a": {"name": "А"}}}
        turn = {"id": "s14.text.024", "at": "0010-04-11T21:31:00", "line": "Чужая тайна"}
        npc_memory.apply(h, turn, {"a": {"affect": {"feel": "злость", "hours": 1}}})
        self.assertNotIn("memory", h["cast"]["a"])
        turn["d"] = {"knowledge_events": [{"actor": "a", "claim": "Письмо пришло", "source": "s14.text.023",
                                            "channel": "message"}]}
        npc_memory.apply(h, turn, {})
        episode = h["cast"]["a"]["memory"][-1]
        self.assertEqual(episode["what"], "Письмо пришло")
        self.assertEqual(episode["sources"][0]["channel"], "message")
        self.assertNotIn("Чужая тайна", str(episode))

    def test_same_session_ambiguity_not_resolved_by_popularity(self):
        store = {"a": {"name": "Сэйдзи Минасэ", "sources": ["s08.text.001", "s08.text.002"]},
                 "b": {"name": "Рэи Минасэ", "sources": ["s08.text.003"]}}
        self.assertIsNone(talk.Resolver(store)("Минасэ", 8))

    def test_old_witness_without_name_gets_pointer_not_global_knowledge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "archive/session-008-turns.jsonl"
            path.parent.mkdir()
            row = {"id": "s08.text.001", "at": "0006-10-01T00:00:00",
                   "gm": "Контракт и водосборник. Тайная мысль другого человека."}
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n")
            manifest = root / "archive/heuristic-recall/manifest.json"
            manifest.parent.mkdir()
            manifest.write_text("{}")
            state = {"session": 14, "cursor": {"id": "s14.text.023", "at": "0010-04-11T21:30:00"},
                     "scene": {"present": ["a"], "situation": "Контракт и водосборник"},
                     "cast": {"a": {"name": "Сэйдзи Минасэ"}}}
            idx = {"records": [{"id": row["id"], "kind": "played", "source": str(path.relative_to(root)),
                                "at": row["at"], "terms": " ".join(echo._stems(row["gm"]))}], "df": {}}
            idx["records"] += [{"id": f"s07.text.{i:03}", "kind": "other"} for i in range(60)]
            with patch("rpg_engine.heuristic_history.load", return_value=idx), \
                 patch("rpg_engine.meaning.search", return_value=[]), \
                 patch("rpg_engine.npc_memory.load_episodes", return_value=[{"turn": row["id"], "ch": "witnessed"}]):
                result = echo.present_past(root, state)
            self.assertEqual(len(result["a"]), 1)
            self.assertIn("присутствовал", result["a"][0])
            self.assertNotIn("Тайная мысль", result["a"][0])
