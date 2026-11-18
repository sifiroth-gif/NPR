"""Regression probes for bounded source-preserving GM brief assembly."""
import copy
import json
import unittest

from rpg_engine import live_scene, scene_packet


def size(packet):
    return len(json.dumps(packet, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def campaign():
    at = "0006-10-03T03:09:00"
    names = {"doctor": "Кадзиура", "nurse": "Дежурная", "recipient": "Реципиент"}
    actors = {}
    for key, name in names.items():
        actors[key] = {
            "name": name, "role": "Сотрудник реанимации" if key != "recipient" else "Пациент",
            "wants": "Обеспечить исход клинического случая, сохраняя полномочия и доказательства",
            "fears": "Внезапная остановка дыхания и потеря данных",
            "leverage": "Истории болезни, наблюдение и согласования",
            "voice": "Профессиональная речь без объяснения очевидного",
            "state": "Достоверное текущее клиническое положение. " * 13,
            "knows": [f"Сведение {i} получено в предыдущем наблюдении с ограниченной достоверностью. " * 2
                      for i in range(12)],
            "memory": [{"turn": f"s08.text.{i:03}", "what": "Сыгранный факт " * 9}
                       for i in range(1, 9)],
            "hides": "Личное намерение не равно публичному знанию о ходе лечения.",
        }
    threads = {f"thread_{i}": {
        "state": "Сохранённый прежний результат с источником и неизвестными последствиями. " * 8,
        "turn": f"s0{4 + i // 8}.text.{i + 1:03}"
    } for i in range(24)}
    threads["patient"] = {"state": "Повреждение дыхательных путей, лечение продолжается.",
                          "turn": "s08.text.011"}
    threads["supply"] = {"state": "Поставки ещё не проверены.",
                         "turn": "s08.text.011"}
    events = {
        "main": {"id": "main", "kind": "main", "title": "Чужая ткань",
                 "question": "Кто распоряжается чужой тканью и судьбами людей?",
                 "endings": ["Установлен источник", "Ответственность распределилась", "Доказательства утрачены"]},
        "chapter": {"id": "chapter", "kind": "chapter", "parent": "main", "title": "Исследование тканей",
                    "question": "Как определить происхождение поражения?",
                    "status": "active", "scenes": 1,
                    "endings": ["Диагноз подтверждён", "Часть признаков установлена",
                                "Возможность проверки утрачена", "Ничего не выяснили"],
                    "notes": ["У пациента есть дыхательная недостаточность"] * 4,
                    "threads": ["patient", "supply"], "limit": {"scenes": 12}}
    }
    for i in range(8):
        events[f"side_{i}"] = {
            "id": f"side_{i}", "kind": "side", "title": f"Старая линия {i}", "status": "queued",
            "question": "Каким будет исход старой линии?",
            "endings": ["Одна развязка", "Другая развязка", "Провал", "Ничего"],
            "trigger": "Подтверждённая реакция независимой стороны с проверяемыми последствиями " * 2,
            "limit": {"scenes": 4},
        }
    clocks = {
        f"clock_{i}": {"owner": f"faction_{i}", "what": "Самостоятельная проверка",
                       "due": f"0006-10-03T0{i + 4}:15:00",
                       "signs": "Официальная запись", "if_unopposed": "Поступит сообщение"}
        for i in range(4)
    }
    h = {
        "cursor": {"id": "s08.text.011", "at": at, "place": "Больница",
                   "date": "Вторник, 3 октября", "time": "03:09"},
        "scene": {"present": ["shiki", *actors],
                  "situation": "Реципиент под вентиляцией, имплантированный образец ещё не изучен.",
                  "positions": "Врач и пациент у кровати, другие медики в отделении.",
                  "pressure": "Пациент зависит от поддержки, результат исследования неизвестен."},
        "last": {"player": "Я готов исследовать образец", "gm": "Имплантация завершилась."},
        "shiki": {"body": "Свежая зашитая рана левого плеча; имплантирован биоптат."},
        "cast": actors,
        "threads": threads,
        "offscreen": {"truth": {}, "clocks": clocks},
        "_plot": {"events": events},
        "_checks": [{"turn": "s08.text.011", "kind": "review",
                     "text": "Check source reference"} for _ in range(12)],
    }
    turns = []
    for i in range(1, 12):
        label = "Кадзиура" if i % 2 else "Дежурная"
        turns.append({
            "id": f"s08.text.{i:03}", "at": "0006-10-03T03:00:00",
            "place": "Больница", "player": "Смотрю",
            "line": f"{label} сообщил о состоянии пациента и документах {i}.",
            "gm": (f"{label} объяснил результат источника {i}. " * 45),
        })
    return h, turns


class ScenePacketBudgetTests(unittest.TestCase):
    def test_large_campaign_keeps_clocks_sources_and_chapter(self):
        hot, turns = campaign()
        original = copy.deepcopy(hot)
        packet = live_scene.build(hot, turns)
        self.assertEqual(hot, original, "projection mutated live canon")
        self.assertLessEqual(size(packet), live_scene.MAX_BRIEF_BYTES,
                             "L0 exceeds hard byte budget")
        self.assertEqual({c["id"] for c in packet["world"]["clocks"]},
                         set(hot["offscreen"]["clocks"]))
        self.assertEqual(packet["plot"]["active"][0]["id"], "chapter")
        self.assertEqual(packet["plot"]["active"][0]["endings"],
                         hot["_plot"]["events"]["chapter"]["endings"])
        self.assertEqual(packet["plot"]["source"], "recovery/GM_ONLY.json:plot")
        self.assertFalse(packet.get("over_budget", False))

    def test_keeps_played_source_pointers_when_excerpts_shorten(self):
        hot, turns = campaign()
        packet = live_scene.build(hot, turns)
        self.assertTrue(packet["source_passages"])
        self.assertTrue(packet["source_passages_truncated"])
        self.assertTrue(all(v["source"].startswith("archive/") for v in
                            packet["source_passages"].values()))
        for actor in ("doctor", "nurse"):
            refs = packet["present_npcs"][actor]["prior_disclosures"]
            self.assertGreaterEqual(len(refs), 2)
            self.assertTrue(all(v.get("turn") and v.get("sha256") for v in refs))
        self.assertEqual(packet["world"]["thread_sources"], "memory/INDEX.md")

    def test_has_source_ref_for_omitted_queues_and_skips_duplicate_dynamics(self):
        hot, turns = campaign()
        packet = live_scene.build(hot, turns)
        self.assertEqual(len(packet["plot"]["queued"]), 8)
        self.assertTrue(all("endings" not in x for x in packet["plot"]["queued"]))
        self.assertEqual(packet["npc_dynamics"]["source"],
                         "recovery/GM_ONLY.json:psyche")
        self.assertTrue(all(not x["present"] for x in packet["npc_dynamics"]["actors"]))
        self.assertIn("checks_ref", packet)
        self.assertLessEqual(packet["budget"]["target_bytes"],
                             packet["budget"]["hard_bytes"])

    def test_documented_limits_match_runtime_constants(self):
        from pathlib import Path
        docs = (Path(__file__).resolve().parents[1] / "docs/LIVE_SCENE_PIPELINE.md")
        prose = docs.read_text(encoding="utf-8")
        self.assertIn("TARGET_BRIEF_BYTES = 32000", prose)
        self.assertIn("MAX_BRIEF_BYTES = 48000", prose)
        self.assertEqual(live_scene.TARGET_BRIEF_BYTES, scene_packet.TARGET_BYTES)
        self.assertEqual(live_scene.MAX_BRIEF_BYTES, scene_packet.HARD_BYTES)

    def test_middle_disclosure_ids_remain_recoverable(self):
        hot, turns = campaign()
        packet = live_scene.build(hot, turns)
        for actor_id in ("doctor", "nurse"):
            record = packet["present_npcs"][actor_id]
            if "prior_disclosure_ids" in record:
                self.assertGreater(len(record["prior_disclosure_ids"]),
                                   len(record["prior_disclosures"]))
                self.assertTrue(all(value.startswith("s08.text.")
                                    for value in record["prior_disclosure_ids"]))

    def test_oversized_mandatory_data_is_explicit_not_deleted(self):
        input_packet = {
            "scene": {"situation": "X" * 60000},
            "world": {"clocks": [{"id": "deadline", "due": "0006-10-03T03:20:00"}]},
            "plot": {"main": None, "active": [], "queued": []},
            "present_npcs": {}, "source_passages": {
                "s08.text.001": {"gm_excerpt": "A", "source": "archive/session-008-turns.jsonl"}},
            "last": {"gm_tail": "A"}, "checks": [],
        }
        packet = scene_packet.compact(input_packet, {}, target_bytes=1000, hard_bytes=1500)
        self.assertTrue(packet["over_budget"])
        self.assertIn("deadline", [c["id"] for c in packet["world"]["clocks"]])
        self.assertIn("s08.text.001", packet["source_passages"])
        self.assertEqual(input_packet["source_passages"]["s08.text.001"]["gm_excerpt"], "A")


if __name__ == "__main__":
    unittest.main()
