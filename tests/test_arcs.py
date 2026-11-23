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
                                            "why": "Шики сжёг печать Бётоном", "shown": False})
        self.assertIn("Покажи игроку эту причину", arcs.must(hot, ARCS))
        self.assertNotIn("nope", canon)
        self.assertIn("Расхождение с каноном «Пять печатей»", arcs.must(hot, ARCS))
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


ORDERED = [{"id": "k", "title": "Казекагэ", "from": "0010-03-12T03:00:00", "until": "0010-03-18T00:00:00",
            "beats": [
                {"id": "extract", "step": 1, "offscreen": True, "fixed": True, "due": "0010-03-15T10:00:00",
                 "title": "Шукаку извлечён", "canon": "Гаара мёртв."},
                {"id": "seals", "step": 2, "due": "0010-03-15T10:30:00", "title": "Пять печатей",
                 "canon": "Печати сняты.", "keys": ["печат", "гай"]},
                {"id": "deidara", "step": 3, "due": "0010-03-15T11:00:00", "title": "Дейдара улетает",
                 "canon": "Погоня.", "keys": ["дейдар", "птиц"]},
                {"id": "sasori", "step": 3, "due": "0010-03-15T11:00:00", "title": "Сасори", "canon": "Бой.",
                 "keys": ["сасори", "хируко"]}]}]


class CanonOrderTest(unittest.TestCase):
    def test_later_beat_after_due_closes_earlier_offscreen(self):
        hot = state("0010-03-15T10:05:00")
        arcs.track(hot, turn("s11.text.043", "0010-03-15T10:05:00", "Гай снимает печать."), ORDERED)
        canon = hot["_plot"]["canon"]
        self.assertEqual(canon["seals"]["status"], "started")
        self.assertEqual(canon["extract"]["status"], "offscreen")
        self.assertIn("Шукаку извлечён: Гаара мёртв.", arcs.view(hot, ORDERED)["happened"])

    def test_arriving_before_due_is_a_divergence_not_an_error(self):
        # Игрок, 23.11.2026: «Гаара жив, потому что вход разрушили раньше его смерти» — так и надо было ответить.
        hot = state("0010-03-15T08:29:00")
        notes = arcs.track(hot, turn("s11.text.043", "0010-03-15T08:29:00", "Гай снимает печать."), ORDERED)
        self.assertNotIn("extract", hot["_plot"]["canon"])
        self.assertIn("Расхождение с каноном по времени", notes[0]["text"])
        arcs.track(hot, turn("s11.text.071", "0010-03-15T08:44:00", "Гаара жив.",
                             {"arc": {"extract": "changed: Шики разрушил вход до конца ритуала — Гаара жив"}}), ORDERED)
        self.assertIn("Шики разрушил вход", arcs.must(hot, ORDERED))
        arcs.track(hot, turn("s11.text.072", "0010-03-15T08:45:00", "…"), ORDERED)
        self.assertEqual(hot["_plot"]["canon"]["extract"]["shown"], "s11.text.072")

    def test_mention_is_not_the_outcome(self):
        # s11.text.052: «птица Дейдары» в пещере закрыла погоню за 17 ходов до неё.
        hot = state("0010-03-15T08:35:00")
        arcs.track(hot, turn("s11.text.052", "0010-03-15T08:35:00", "Дейдара сидел на глиняной птице."), ORDERED)
        self.assertEqual(hot["_plot"]["canon"]["deidara"]["status"], "started")
        self.assertTrue(any(x.startswith("[deidara] (идёт") for x in arcs.view(hot, ORDERED)["next"]))
        arcs.track(hot, turn("s11.text.054", "0010-03-15T08:37:00", "Хвост Хируко. Сасори ударил."), ORDERED)
        self.assertEqual(hot["_plot"]["canon"]["deidara"]["status"], "started")   # тот же шаг — параллельно


class DeliveryTest(unittest.TestCase):
    BEATS = [{"id": "k", "title": "К", "from": "0010-03-12T03:00:00", "until": "0010-03-18T00:00:00", "beats": [
        {"id": "extract", "step": 1, "offscreen": True, "due": "0010-03-15T10:00:00", "title": "Шукаку извлечён",
         "canon": "Гаара мёртв.", "sign": "гаар\\w*[^.]{0,60}(?:мёртв|не дыш)"},
        {"id": "seals", "step": 2, "due": "0010-03-15T10:30:00", "title": "Печати", "canon": "Сняты.",
         "keys": ["печат", "гай"]}]}]

    def test_happened_must_until_shown(self):
        # s11.text.071: игрок не знал о смерти Гаары — нигде не было сказано.
        hot = state("0010-03-15T10:05:00")
        arcs.track(hot, turn("s11.text.043", "0010-03-15T10:05:00", "Гай снимает печать."), self.BEATS)
        self.assertIn("Случилось без Шики: «Шукаку извлечён»", arcs.must(hot, self.BEATS))
        arcs.track(hot, turn("s11.text.044", "0010-03-15T10:07:00", "Плита рухнула."), self.BEATS)
        self.assertIn("так и не дошло до игрока (1 ход.)", arcs.must(hot, self.BEATS))
        arcs.track(hot, turn("s11.text.045", "0010-03-15T10:09:00",
                             "Чувства отшельника нашли Гаару: тело не дышит."), self.BEATS)
        self.assertEqual(hot["_plot"]["canon"]["extract"]["told"], "s11.text.045")
        self.assertNotIn("Случ", arcs.must(hot, self.BEATS) or "")

    def test_gm_can_mark_told(self):
        hot = state("0010-03-15T10:05:00")
        arcs.advance(hot, self.BEATS)
        arcs.track(hot, turn("s11.text.050", "0010-03-15T10:06:00", "…", {"arc": {"extract": "told"}}), self.BEATS)
        self.assertEqual(hot["_plot"]["canon"]["extract"]["told"], "s11.text.050")
