"""Create a fresh setting or export a portable, empty copy of the engine."""

import json
from pathlib import Path
import shutil
import tempfile

TEMPLATE = {
    "id": "new-campaign",
    "title": "Название кампании",
    "start_at": "0001-01-01T09:00:00",
    "location": "Стартовая локация",
    "player": {"id": "player", "name": "Имя персонажа"},
    "rules": {},
    "npcs": [],
    "operations": [],
}


def scaffold(destination, *, portable=False):
    destination = Path(destination).absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix=".npr-setting-", dir=destination.parent) as working:
        stage = Path(working) / "setting"
        stage.mkdir()
        (stage / "campaign.json").write_text(json.dumps(TEMPLATE, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
        pack = {"campaign_id": TEMPLATE["id"], "rules": [], "pin_facts": [], "anchors": [],
                "session_files": "sessions/SESSION_{session:03d}.json"}
        (stage / "scene.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
        (stage / "sessions").mkdir()
        if portable:
            shutil.copytree(root / "rpg_engine", stage / "rpg_engine",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            (stage / "docs").mkdir()
            for name in ("LONG_CAMPAIGN.md", "COMBAT_MECHANICS.md"):
                shutil.copyfile(root / "docs" / name, stage / "docs" / name)
            (stage / "README.md").write_text(
                "# Движок длительной текстовой RPG\n\n"
                "Заполните campaign.json и укажите тот же ID в scene.json.\n\n"
                "    python -m rpg_engine init campaign.json\n"
                "    python -m rpg_engine gm-packet --pack scene.json\n\n"
                "Команды, механика и подготовка сцен описаны в [руководстве](docs/LONG_CAMPAIGN.md).\n",
                encoding="utf-8")
        # reserve the destination atomically and avoid replacing another
        # setting if two scaffold requests target the same path
        destination.mkdir()
        try:
            for item in stage.iterdir():
                item.rename(destination / item.name)
        except OSError:
            shutil.rmtree(destination)
            raise
    return {"path": str(destination), "portable": portable, "configuration": str(destination / "campaign.json")}
