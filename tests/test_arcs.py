"""Канон Naruto внутри главы: события по сроку и порядку (поправка игрока 23.11.2026, s11.text.041–042)."""
import unittest

from rpg_engine import arcs, nextpack, will

ARCS = [{"id": "kazekage", "title": "Спасение Кадзекагэ", "from": "0010-03-12T03:00:00", "until": "0010-03-18T00:00:00",
         "beats": [
             {"id": "guy", "offscreen": True, "due": "0010-03-14T10:00:00", "title": "Команда Гая",
              "canon": "Цунадэ отправляет команду Гая."},
             {"id": "itachi", "due": "0010-03-15T08:00:00", "title": "Подставной Итачи", "where": "подступы",
              "goal": "задержать отряд до конца извлечения",
              "canon": "Итачи задерживает отряд.", "keys": ["итачи"], "before": "внутр\\w* пещер"},
             {"id": "barrier", "due": "0010-03-15T10:30:00", "title": "Пять печатей", "canon": "Гай снимает печати.",
              "keys": ["гай", "печат"]}]}]


def state(at):
    return {"cursor": {"id": "s11.text.042", "at": at}}


def turn(tid, at, gm, d=None):
    return {"id": tid, "at": at, "place": "Ущелье", "gm": gm, "d": d or {}}


class ArcsTest(unittest.TestCase):
    def test_offscreen_happens_by_due_and_shows_once(self):
        hot = state("0010-03-15T08:23:00")
        self.assertEqual(arcs.advance(hot, ARCS), ["guy"])
        self.assertEqual(arcs.advance(hot, ARCS), [])
        view = arcs.view(hot, ARCS)
        self.assertEqual(view["happened"], ["Команда Гая: Цунадэ отправляет команду Гая."])
        self.assertTrue(view["next"][0].startswith("[itachi] Подставной Итачи — 15.03 08:00, просрочено на 23 мин"))
        hot["cursor"]["at"] = "0010-03-16T09:00:00"
        self.assertNotIn("happened", arcs.view(hot, ARCS))

    def test_overdue_beat_near_shiki_is_a_must(self):
        hot = state("0010-03-15T08:23:00")
        self.assertIn("Подставной Итачи", arcs.must(hot, ARCS))
        self.assertIsNone(arcs.must(state("0010-03-15T07:00:00"), ARCS))

    def test_skipping_canon_is_flagged(self):
        hot = state("0010-03-15T08:23:00")
        notes = arcs.track(hot, turn("s11.text.043", "0010-03-15T09:00:00", "Отряд вошёл внутрь пещеры."), ARCS)
        self.assertEqual(notes[0]["kind"], "arc")
        self.assertIn("Подставной Итачи", notes[0]["text"])

    def test_played_by_keys_and_by_record(self):
        hot = state("0010-03-15T08:23:00")
        arcs.track(hot, turn("s11.text.043", "0010-03-15T08:40:00", "**Итачи Учиха:** — Наруто-кун."), ARCS)
        arcs.track(hot, turn("s11.text.044", "0010-03-15T09:00:00", "Тишина.",
                             {"arc": {"barrier": "changed: Шики сжёг печать Бётоном", "nope": "played"}}), ARCS)
        canon = hot["_plot"]["canon"]
        self.assertEqual(canon["itachi"]["status"], "played")
        self.assertEqual(canon["barrier"], {"status": "changed", "turn": "s11.text.044",
                                            "why": "Шики сжёг печать Бётоном"})
        self.assertNotIn("nope", canon)
        self.assertIsNone(arcs.must(hot, ARCS))
        self.assertEqual(arcs.track(hot, turn("s11.text.045", "0010-03-15T09:10:00", "внутри пещеры"), ARCS), [])

    def test_route_or_lateness_does_not_cancel_canon(self):
        hot = state("0010-03-15T08:23:00")
        why = "changed: Шики и отряд шли другим маршрутом, точка перехвата осталась в стороне"
        notes = arcs.track(hot, turn("s11.text.043", "0010-03-15T08:29:00", "Тишина у печатей.",
                                     {"arc": {"itachi": why}}), ARCS)
        self.assertIn("не принято", notes[0]["text"])
        self.assertEqual(hot["_plot"]["canon"]["itachi"], {"late": 1})
        item = arcs.must(hot, ARCS)
        self.assertTrue(item.startswith("Канон опаздывает на 1 ход(а): «Подставной Итачи» открывает этот ход"))
        self.assertIn("задержать отряд до конца извлечения", item)
        self.assertIn("сорвать действия Шики", item)
        arcs.track(hot, turn("s11.text.044", "0010-03-15T08:40:00", "Гай снимает талисман.",
                             {"arc": {"itachi": "changed: Шики убил его Шитоном до перехвата"}}), ARCS)
        self.assertEqual(hot["_plot"]["canon"]["itachi"]["status"], "changed")

    def test_old_evasive_change_is_reverted(self):
        hot = state("0010-03-15T08:40:00")
        hot["_plot"] = {"canon": {"itachi": {"status": "changed", "why": "отряд шёл по изменённому маршруту"}}}
        notes = arcs.revalidate(hot, ARCS)
        self.assertEqual(len(notes), 1)
        self.assertIsNotNone(arcs.must(hot, ARCS))
        self.assertFalse(arcs.valid_change("Сражения за Шики не было"))
        self.assertTrue(arcs.valid_change("Немера сожгла печать до прихода Итачи"))

    def test_npc_plan_is_not_closed_by_a_missed_meeting(self):
        hot = {"cursor": {"id": "s11.text.044", "at": "0010-03-15T08:40:00"}, "cast": {"baki": {"name": "Баки"}}}
        t = {"id": "s11.text.044", "d": {"cast": {"baki": {"intent": {"meet": {
            "status": "abandoned", "outcome": "Не застал Шики, разминулись у ворот"}}}}}}
        notes = [n for n in will.review(hot, [t]) if "разминкой" in n["text"]]
        self.assertEqual(len(notes), 1)

    def test_arc_outside_window_is_silent(self):
        self.assertEqual(arcs.view(state("0010-02-01T00:00:00"), ARCS), {})
        self.assertEqual(arcs.view(state("0010-04-01T00:00:00"), ARCS), {})

    def test_next_pack_puts_canon_first(self):
        hot = {"cursor": {"id": "s11.text.042"}, "scene": {"present": []}}
        pack = nextpack.build(hot, {"cursor": {"id": "s11.text.042"}}, {"checks": []}, [],
                              extra={"arc": {"next": ["[itachi] …"]}, "arc_must": "Канон: Итачи"})
        self.assertEqual(pack["must"][0], "Канон: Итачи")
        self.assertEqual(pack["arc"]["next"], ["[itachi] …"])


if __name__ == "__main__":
    unittest.main()
