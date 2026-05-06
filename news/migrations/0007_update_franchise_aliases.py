from __future__ import annotations

from django.db import migrations


FRANCHISES = [
    ("Mario", "mario", ["Mario", "Super Mario", "마리오", "슈퍼 마리오", "Mario Kart", "마리오 카트", "マリオ"], 90),
    ("Zelda", "zelda", ["The Legend of Zelda", "Zelda", "젤다의 전설", "젤다", "야숨", "왕눈", "ゼルダ"], 90),
    ("Pokémon", "pokemon", ["Pokémon", "Pokemon", "Pocket Monsters", "포켓몬스터", "포켓몬", "ポケモン"], 90),
    ("Metroid", "metroid", ["Metroid", "메트로이드", "メトロイド"], 80),
    ("Animal Crossing", "animal-crossing", ["Animal Crossing", "동물의 숲", "동숲", "모동숲", "どうぶつの森"], 80),
    ("Splatoon", "splatoon", ["Splatoon", "스플래툰", "スプラトゥーン"], 80),
    ("Kirby", "kirby", ["Kirby", "별의 커비", "커비", "カービィ"], 75),
    ("Fire Emblem", "fire-emblem", ["Fire Emblem", "파이어 엠블렘"], 70),
    ("Xenoblade", "xenoblade", ["Xenoblade", "Xenoblade Chronicles", "제노블레이드", "제노블레이드 크로니클스"], 70),
    ("Donkey Kong", "donkey-kong", ["Donkey Kong", "DK", "동키콩"], 75),
    ("Rhythm Heaven", "rhythm-heaven", ["Rhythm Heaven", "리듬 천국", "리듬천국", "리듬 세상", "리듬세상", "リズム天国"], 70),
]


def forwards(apps, schema_editor):
    Franchise = apps.get_model("news", "Franchise")
    for name, slug, aliases, priority in FRANCHISES:
        Franchise.objects.filter(slug=slug).update(name=name, aliases=aliases, priority=priority)


class Migration(migrations.Migration):
    dependencies = [
        ("news", "0006_archive_existing_boilerplate_items"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
