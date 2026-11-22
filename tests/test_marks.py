import unittest

from rpg_engine import npc_memory

REIKA = ("tamura_reika", {"name": "Тамура Рэйка",
                          "look": "Сухощавая, около 30 лет; надорванная мочка левого уха; левый указательный палец "
                                  "почти не сгибается; въевшиеся чернила на пальцах."})


class MarksTest(unittest.TestCase):
    def test_absent_npc_marks_in_text_ask_who_it_is(self):
        hot = {"scene": {"present": ["shiki", "p4_marked_hunter"]}, "cast": {}}
        turn = {"id": "s10.text.083", "gm": "**Морита Кай:** — Заказ передала женщина. На левом ухе надорвана "
                                            "мочка. Левый указательный палец почти не использовала.",
                "d": {"cast": {"p4_marked_hunter": {"name": "Морита Кай"}}}}
        notes = npc_memory.check_marks(hot, turn, [REIKA])
        self.assertEqual(len(notes), 1)
        self.assertIn("Тамура Рэйка (tamura_reika)", notes[0])

    def test_present_npc_and_plain_looks_are_quiet(self):
        hot = {"scene": {"present": ["shiki", "tamura_reika"]}, "cast": {}}
        turn = {"id": "s10.text.015", "gm": "Рэйка прячет левый указательный палец, почти не сгибая его. "
                                            "У охранника серые глаза и тёмные волосы.", "d": {}}
        self.assertEqual(npc_memory.check_marks(hot, turn, [REIKA]), [])

    def test_new_card_with_someone_elses_marks(self):
        hot = {"scene": {"present": ["shiki"]}, "cast": {}}
        turn = {"id": "s10.text.090", "gm": "Новая посредница.", "d": {"cast": {"post_clerk": {
            "name": "Приёмщица", "look": "Худая; на левом ухе надрыв мочки, будто вырвали серьгу."}}}}
        notes = npc_memory.check_marks(hot, turn, [REIKA])
        self.assertTrue(notes[0].startswith("post_clerk: примета"))


if __name__ == "__main__":
    unittest.main()
