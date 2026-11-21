import json
import tempfile
import unittest
import zlib
from pathlib import Path

from rpg_engine import heuristic_history, meaning, recall_request
from rpg_engine.heuristic_history import stem, tokens

ROOT = Path(__file__).resolve().parents[1]
SAME = {stem("измена"): stem("предательство"), stem("изменил"): stem("предательство"),
        stem("предал"): stem("предательство")}


class FakeEncoder:
    """Мешок основ в 384 измерениях; «измена» и «предательство» — один смысл."""

    def __init__(self):
        self.calls = 0

    def __call__(self, texts):
        self.calls += len(texts)
        out = []
        for text in texts:
            vector = [0.0] * 384
            for word in tokens(text.split(": ", 1)[-1]):
                base = stem(word)
                base = SAME.get(base, base)
                vector[zlib.crc32(base.encode()) % 384] += 1.0
            out.append(vector)
        return out


TURNS = [
    ("s06.text.001", "Шики вошёл в чайную у моста. Хозяйка налила чай и молча ушла.", "Чайная у моста."),
    ("s06.text.002", "Кайто признался: он продал маршрут каравана людям в сером, это было предательство.",
     "Кайто признался в предательстве."),
    ("s06.text.003", "Дождь шёл всю ночь, Шики чинил ремень сумки.", "Ночь, дождь, ремень."),
    ("s06.text.004", "Сэйна осмотрела рану и наложила швы при свече.", "Сэйна зашила рану."),
]


def _state():
    state = Path(tempfile.mkdtemp())
    (state / "archive").mkdir()
    (state / "recovery").mkdir()
    lines = [json.dumps({"id": i, "kind": "played", "at": f"0007-01-0{n}T10:00:00", "player": "Иду.",
                         "gm": gm, "line": line}, ensure_ascii=False)
             for n, (i, gm, line) in enumerate(TURNS, 1)]
    (state / "archive/session-006-turns.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (state / "recovery/HOT.json").write_text(json.dumps({"cursor": {"id": "s07.text.010"}}), encoding="utf-8")
    heuristic_history.save(ROOT, state)
    return state


class MeaningTest(unittest.TestCase):
    def setUp(self):
        meaning._MATRIX.clear()
        self.state = _state()
        self.enc = FakeEncoder()

    def test_pieces_and_packing(self):
        self.assertEqual(meaning.pieces("коротко"), ["коротко"])
        long = " ".join(["слово"] * 1500)
        parts = meaning.pieces(long)
        self.assertEqual(len(parts), meaning.PIECES)
        self.assertTrue(all(len(p) <= meaning.CHUNK for p in parts))
        self.assertTrue(parts[-1].endswith("слово"))
        back = meaning._unpack(meaning._pack([0.5, -0.25, 0.0]))
        self.assertEqual(back, [127, -64, 0])

    def test_off_without_model_and_incremental_update(self):
        self.assertEqual(meaning.update(ROOT, self.state, enc=None)["status"], "off")
        first = meaning.update(ROOT, self.state, enc=self.enc, budget=100)
        self.assertEqual(first["status"], "updated")
        self.assertGreaterEqual(first["encoded"], len(TURNS))
        stored = (self.state / meaning.FOLDER / "s006.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x)["id"] for x in stored], [t[0] for t in TURNS])
        calls = self.enc.calls
        again = meaning.update(ROOT, self.state, enc=self.enc, budget=100)
        self.assertEqual((again["status"], again["encoded"]), ("current", 0))
        self.assertEqual(self.enc.calls, calls, "готовые векторы не перекодируются")

    def test_budget_leaves_rest_for_next_fold(self):
        part = meaning.update(ROOT, self.state, enc=self.enc, budget=2)
        self.assertGreater(part["missing"], 0)
        rest = meaning.update(ROOT, self.state, enc=self.enc, budget=1000)
        self.assertEqual(rest["missing"], 0)

    def test_search_finds_by_meaning_without_shared_word(self):
        meaning.update(ROOT, self.state, enc=self.enc, budget=1000)
        words = heuristic_history.query(heuristic_history.load(
            self.state / "archive/heuristic-recall/manifest.json"), "измена", limit=5)["matches"]
        self.assertFalse([m for m in words if m["id"] == "s06.text.002"], "по словам не найти")
        found = meaning.search(self.state, "измена", enc=self.enc, limit=3)
        self.assertEqual(found[0][0], "s06.text.002")


class RecallRequestTest(unittest.TestCase):
    def setUp(self):
        meaning._MATRIX.clear()
        self.state = _state()
        self.enc = FakeEncoder()
        meaning.update(ROOT, self.state, enc=self.enc, budget=1000)
        meaning._LOADED["fake"] = self.enc

    def tearDown(self):
        meaning._LOADED.pop("fake", None)

    def test_request_answered_once_and_shown_while_fresh(self):
        (self.state / recall_request.REQUEST).write_text(
            json.dumps({"q": ["кто совершил измену", "Сэйна зашила рану"]}, ensure_ascii=False), encoding="utf-8")
        hot = {"cursor": {"id": "s07.text.010"}}
        import os
        os.environ[meaning.ENV] = "fake"
        try:
            answer = recall_request.handle(ROOT, self.state, hot)
        finally:
            os.environ.pop(meaning.ENV)
        self.assertEqual(answer["mode"], "слова+смысл")
        first = answer["answers"][0]["hits"][0]
        self.assertEqual(first["id"], "s06.text.002")
        self.assertIn("смысл", first["how"])
        self.assertEqual({x["why"] for x in first["linked"]} & {"до", "после"}, {"до", "после"})
        self.assertEqual(answer["answers"][1]["hits"][0]["id"], "s06.text.004")
        request = json.loads((self.state / recall_request.REQUEST).read_text(encoding="utf-8"))
        self.assertEqual(request["done"], "s07.text.010")
        self.assertIsNone(recall_request.handle(ROOT, self.state, hot), "отвеченный запрос не повторяется")
        shown = recall_request.view(self.state, {"cursor": {"id": "s07.text.012"}})
        self.assertEqual(shown["answers"][0]["top"][0]["id"], "s06.text.002")
        self.assertLessEqual(len(shown["answers"][0]["top"]), recall_request.NEXT_HITS)
        self.assertIsNone(recall_request.view(self.state, {"cursor": {"id": "s07.text.020"}}))

    def test_words_only_without_model_and_bad_request(self):
        answer = recall_request.ask(ROOT, self.state, "Кайто продал маршрут каравана")
        self.assertEqual(answer["mode"], "слова")
        self.assertEqual(answer["hits"][0]["id"], "s06.text.002")
        (self.state / recall_request.REQUEST).write_text('{"q": []}', encoding="utf-8")
        bad = recall_request.handle(ROOT, self.state, {"cursor": {"id": "s07.text.010"}})
        self.assertIn("пустой вопрос", bad["error"])
        self.assertIn("error", recall_request.view(self.state, {"cursor": {"id": "s07.text.010"}}))


if __name__ == "__main__":
    unittest.main()
