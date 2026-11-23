"""Канон Naruto внутри главы: события по сроку и порядку (поправка игрока 23.11.2026, s11.text.041–042)."""
import unittest

from rpg_engine import arcs, nextpack

ARCS = [{"id": "kazekage", "title": "Спасение Кадзекагэ", "from": "0010-03-12T03:00:00", "until": "0010-03-18T00:00:00",
         "beats": [
             {"id": "guy", "offscreen": True, "due": "0010-03-14T10:00:00", "title": "Команда Гая",
              "canon": "Цунадэ отправляет команду Гая."},
             {"id": "itachi", "due": "0010-03-15T08:00:00", "title": "Подставной Итачи", "where": "подступы",
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
