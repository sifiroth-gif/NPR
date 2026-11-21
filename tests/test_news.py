import unittest

from rpg_engine import news, nextpack, plot, will


def _event(npc, outcome, *, status="fulfilled", at="0010-03-01T12:00:00", **extra):
    return {"npc": npc, "name": extra.pop("name", npc), "plan": f"{npc}_plan", "at": at, "status": status,
            "outcome": outcome, "turn": "s10.text.005", "shown": 0, **extra}


def _hot(turn="s10.text.010", at="0010-03-02T10:00:00", happened=(), place="Сторожка"):
    return {"cursor": {"id": turn, "at": at, "place": place}, "scene": {"present": ["shiki"]},
            "_will": {"happened": list(happened)}, "_psyche": {"minagi_zen": {"dread": {"stage": "1"}}},
            "_plot": {"events": {}}}


DRY = "Получит перечень расхождений, но не восстановит скрытые цепочки распоряжений."


class WeightTest(unittest.TestCase):
    def test_canon_and_people_first_paperwork_last(self):
        hot = _hot()
        canon = _event("akatsuki", "Дейдара и Сасори унесли Кадзекагэ Гаару.", canon=True, name="Акацуки")
        known = _event("minagi_zen", "Минаги Дзэн продал адрес сторожки людям в сером.", status="partial",
                       name="Минаги Дзэн")
        paper = _event("kusa_head", DRY, name="Глава Кусагакуре")
        self.assertTrue(news.dry(DRY))
        self.assertFalse(news.dry("Минаги Дзэн продал адрес сторожки людям в сером."))
        self.assertGreater(news.weight(hot, canon), news.weight(hot, known))
        self.assertGreater(news.weight(hot, known), news.weight(hot, paper))
        self.assertLess(news.weight(hot, paper), news.MIN_WEIGHT)

    def test_offscreen_shows_weighty_and_counts_the_rest(self):
        texts = [(8, "канон")] + [(2, f"событие {i}") for i in range(7)] + [(-3, "бумага")]
        shown = news.offscreen({}, texts)
        self.assertEqual(shown[0], "канон")
        self.assertEqual(len(shown), news.TOP + 1)
        self.assertIn("ещё 4", shown[-1])


class StepTest(unittest.TestCase):
    def test_jump_offers_news_then_detects_delivery(self):
        hot = _hot(happened=[_event("kusa_head", DRY, name="Глава Кусагакуре"),
                             _event("akatsuki", "Дейдара и Сасори унесли Кадзекагэ Гаару.", canon=True,
                                    name="Акацуки")])
        news.step(hot, [{"id": "s10.text.009", "gm": "Тихо."}])          # первая свёртка: точка отсчёта
        hot["cursor"].update(id="s10.text.010", at="0010-03-03T10:00:00")
        item = news.step(hot, [{"id": "s10.text.010", "gm": "Прошли сутки."}])
        self.assertIn("Гаару", item["must"])
        self.assertEqual(item["why"], "скачок времени")
        hot["cursor"].update(id="s10.text.011", at="0010-03-03T10:05:00")
        news.step(hot, [{"id": "s10.text.011", "gm": "Возница сплюнул: в Песке, говорят, Кадзекагэ унесли."}])
        state = hot["_will"]["news"]
        self.assertEqual((state["offered"], state["delivered"]), (1, 1))
        self.assertNotIn("current", state)
        self.assertEqual(hot["_will"]["happened"][1]["news"], "delivered")

    def test_undelivered_news_is_dropped_after_hold(self):
        hot = _hot(happened=[_event("minagi_zen", "Минаги Дзэн продал адрес сторожки.", status="failed",
                                    name="Минаги Дзэн")])
        news.step(hot, [{"id": "s10.text.010", "gm": "."}])
        hot["cursor"].update(at="0010-03-04T10:00:00")
        self.assertIsNotNone(news.step(hot, [{"id": "s10.text.010", "gm": "."}]))
        for n in range(11, 11 + news.HOLD):
            hot["cursor"].update(id=f"s10.text.{n:03}", at=f"0010-03-04T{n:02}:00:00")
            news.step(hot, [{"id": f"s10.text.{n:03}", "gm": "Шики молча работал."}])
        self.assertEqual(hot["_will"]["news"]["missed"], 1)

    def test_dense_scene_keeps_news_until_hours_pass(self):
        hot = _hot(happened=[_event("minagi_zen", "Минаги Дзэн продал адрес сторожки.", status="failed",
                                    name="Минаги Дзэн")])
        news.step(hot, [{"id": "s10.text.010", "gm": "."}])
        hot["cursor"].update(at="0010-03-04T10:00:00")
        news.step(hot, [{"id": "s10.text.010", "gm": "."}])
        for n in range(11, 11 + news.HOLD + 2):   # плотная сцена: минуты, не часы
            hot["cursor"].update(id=f"s10.text.{n:03}", at=f"0010-03-04T10:{n:02}:00")
            news.step(hot, [{"id": f"s10.text.{n:03}", "gm": "Испытание продолжается."}])
        self.assertEqual(hot["_will"]["news"]["missed"], 0)
        self.assertIn("current", hot["_will"]["news"])

    def test_long_scene_lets_the_world_in(self):
        hot = _hot()
        for n in range(1, news.LONG + 1):
            hot["cursor"]["id"] = f"s10.text.{n:03}"
            item = news.step(hot, [{"id": f"s10.text.{n:03}", "gm": "Операция."}])
        self.assertIn("мир войдёт", item["must"])


class ScheduleTest(unittest.TestCase):
    def test_world_dated_chapter_opens_itself(self):
        hot = _hot(at="0010-03-10T12:00:00")
        hot["_plot"]["events"] = {
            "ch09": {"id": "ch09", "kind": "chapter", "status": "active", "title": "Цепь приказа"},
            "ch13": {"id": "ch13", "kind": "chapter", "status": "queued", "title": "Смерть Кадзекагэ",
                     "starts": "0010-03-12T03:00:00"}}
        notes = plot.schedule(hot)
        self.assertIn("Через 39 ч", notes[0]["text"])
        hot["cursor"]["at"] = "0010-03-12T05:00:00"
        notes = plot.schedule(hot)
        self.assertEqual(hot["_plot"]["events"]["ch13"]["status"], "active")
        self.assertIn("Прежняя «Цепь приказа»", notes[0]["text"])


class PlanAndNextTest(unittest.TestCase):
    def test_paperwork_plan_gets_a_note(self):
        hot = _hot()
        turn = {"id": "s10.text.010", "d": {"cast": {"kusa_head": {"intent": {"audit": {"if_unopposed": DRY}}}}}}
        notes = will.review(hot, [turn])
        self.assertTrue(any("исход — бумага" in n["text"] for n in notes))

    def test_news_leads_must(self):
        hot = _hot()
        pack = nextpack.build(hot, {"cursor": {"id": "s10.text.010"}}, {}, [], news={"must": "Весть мира: X"})
        self.assertEqual(pack["must"][0], "Весть мира: X")


if __name__ == "__main__":
    unittest.main()
