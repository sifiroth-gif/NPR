import unittest
from pathlib import Path
from rpg_engine import fight, fusion, hot, will, overlay, capabilities

class FusionTests(unittest.TestCase):
    def base(self):
        return {"shiki":{"chakra":{"value":40.3,"max":100,"at":"0010-04-12T11:10:00"}}}
    def merged(self):
        h=self.base()
        h["shiki"]["fusion_profile"]={"id":fusion.PROFILE}
        fight._chakra(h,{"set":514.3},0,"s14.text.046","0010-04-12T11:10:00",[])
        return h
    def test_reserve_and_regen(self):
        h=self.merged()
        self.assertEqual(h["shiki"]["chakra"]["value"],514.3)
        self.assertEqual(h["shiki"]["chakra"]["max"],600)
        fight._regen(h,"0010-04-12T12:10:00")
        self.assertEqual(h["shiki"]["chakra"]["value"],524.3)
        fight._chakra(h,{"spend":[{"what":"S","rank":"S"}]},0,"s14.text.047","0010-04-12T12:10:00",[])
        self.assertEqual(h["shiki"]["chakra"]["value"],514.3)
        fight._regen(h,"0010-04-13T12:10:00")
        self.assertEqual(h["shiki"]["chakra"]["value"],600)
    def test_historical(self):
        h=self.base()
        fight._regen(h,"0010-04-12T12:10:00")
        self.assertEqual(h["shiki"]["chakra"]["value"],46.3)
        self.assertEqual(h["shiki"]["chakra"]["max"],100)
        self.assertFalse(fusion.active(h))
    def test_retired_pact_and_autonomy(self):
        h=self.merged()
        h["cursor"]={"at":"0010-04-12T11:10:00","id":"s14.text.046"}
        h["pact"]={"bound_at":"0006-09-27T06:30:19","stage":6,"value":100,"events":[]}
        h["shiki"]["nemera_chakra"]={"value":474}
        h["_psyche"]={"nemera":{"intent":{"old":{"status":"committed","due":"0010-04-12T12:00:00"}}}}
        hot.refresh(h)
        self.assertEqual(h["pact"]["status"],"terminated")
        self.assertNotIn("nemera_chakra",h["shiki"])
        self.assertEqual(will.agendas(h),[])
        self.assertEqual(fight.nemera_turn(h,{"gm":"тишина"}),[])
        self.assertEqual(hot.pact_state(h["pact"],"0011-01-01T00:00:00"),h["pact"])
    def test_overlay(self):
        h=self.merged()
        packet={"cursor":"s14.text.046","next_id":"s14.text.047","fusion_profile":{"id":fusion.PROFILE},
                "chakra":h["shiki"]["chakra"],"lag":{"at":"0010-04-12T11:10:00","chakra_at":"0010-04-12T11:10:00"}}
        t={"schema":"npr-turn/2","id":"s14.text.047","at":"0010-04-12T12:10:00","place":"Рьючидо",
           "player":"жду","gm":"Час прошёл.","line":"Час прошёл.","review":{"predecessor":"s14.text.046"},"d":{"scene":{}}}
        p=overlay.project(packet,[{"turn":t,"sha":"confirmed"}])
        self.assertEqual(p["chakra"]["value"],524.3)
        self.assertEqual(p["chakra"]["max"],600)
    def test_registry_is_selected_by_current_profile(self):
        root=Path(__file__).resolve().parents[1]
        old=capabilities.load(root,"shiki")
        new=capabilities.load(root,"shiki",self.merged())
        self.assertEqual(capabilities.resolve(old,"совместный покров")["status"],"learning")
        self.assertEqual(capabilities.resolve(new,"совместный покров")["status"],"retired")
        self.assertEqual(capabilities.resolve(new,"собственные отростки")["status"],"mastered")
        self.assertEqual(capabilities.resolve(new,"чакра немеры")["status"],"retired")
        self.assertIn("FUSION_CAPABILITIES",capabilities.packet(root,"shiki",self.merged())["registry"])
