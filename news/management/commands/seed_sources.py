from __future__ import annotations

from django.core.management.base import BaseCommand

from news.models import Franchise, Language, Region, Source, SourceType, TrustType, TRUST_BASE_SCORE


SOURCES = [
    {
        "name": "한국닌텐도 News",
        "slug": "nintendo-kr-news",
        "url": "https://www.nintendo.com/kr/news",
        "source_type": SourceType.HTML,
        "trust_type": TrustType.OFFICIAL,
        "region": Region.KR,
        "language": Language.KO,
        "config": {"url_include_patterns": ["/kr/news"]},
    },
    {
        "name": "한국닌텐도 발매 스케줄",
        "slug": "nintendo-kr-schedule",
        "url": "https://www.nintendo.com/kr/schedule",
        "source_type": SourceType.HTML,
        "trust_type": TrustType.OFFICIAL,
        "region": Region.KR,
        "language": Language.KO,
        "config": {"url_include_patterns": ["/kr/schedule", "/kr/software"]},
    },
    {
        "name": "Nintendo US What's New",
        "slug": "nintendo-us-whats-new",
        "url": "https://www.nintendo.com/us/whatsnew/",
        "source_type": SourceType.HTML,
        "trust_type": TrustType.OFFICIAL,
        "region": Region.US,
        "language": Language.EN,
        "config": {"url_include_patterns": ["/us/whatsnew", "/us/store/products", "/us/news"]},
    },
    {
        "name": "Nintendo UK News",
        "slug": "nintendo-uk-news",
        "url": "https://www.nintendo.com/en-gb/News/News-Updates-11145.html",
        "source_type": SourceType.HTML,
        "trust_type": TrustType.OFFICIAL,
        "region": Region.EU,
        "language": Language.EN,
        "config": {"url_include_patterns": ["/News/", "/Games/"]},
    },
    {
        "name": "Nintendo Official YouTube Korea",
        "slug": "nintendo-youtube-kr",
        "url": "",
        "source_type": SourceType.YOUTUBE_RSS,
        "trust_type": TrustType.OFFICIAL,
        "region": Region.KR,
        "language": Language.KO,
        "enabled": False,
        "config": {"channel_id": "", "note": "YouTube 채널 ID를 입력하면 RSS 수집을 켤 수 있습니다."},
    },
    {
        "name": "Gematsu",
        "slug": "gematsu",
        "url": "https://www.gematsu.com/feed",
        "source_type": SourceType.RSS,
        "trust_type": TrustType.PRESS,
        "region": Region.GLOBAL,
        "language": Language.EN,
    },
    {
        "name": "Nintendo Life",
        "slug": "nintendo-life",
        "url": "https://www.nintendolife.com/feeds/latest",
        "source_type": SourceType.RSS,
        "trust_type": TrustType.PRESS,
        "region": Region.GLOBAL,
        "language": Language.EN,
    },
    {
        "name": "VGC Nintendo",
        "slug": "vgc-nintendo",
        "url": "https://www.videogameschronicle.com/platforms/nintendo/",
        "source_type": SourceType.HTML,
        "trust_type": TrustType.PRESS,
        "region": Region.GLOBAL,
        "language": Language.EN,
        "config": {"url_include_patterns": ["/news/", "/platforms/nintendo/"]},
    },
    {
        "name": "GamingLeaksAndRumours Reddit RSS",
        "slug": "gaming-leaks-rumours-reddit",
        "url": "https://www.reddit.com/r/GamingLeaksAndRumours/search.rss?q=Nintendo%20OR%20Switch%20OR%20Direct&restrict_sr=1&sort=new",
        "source_type": SourceType.REDDIT_RSS,
        "trust_type": TrustType.RUMOR,
        "region": Region.GLOBAL,
        "language": Language.EN,
    },
]

FRANCHISES = [
    ("Mario", "mario", ["Mario", "Super Mario", "마리오", "マリオ"], 90),
    ("Zelda", "zelda", ["Zelda", "The Legend of Zelda", "젤다", "ゼルダ"], 90),
    ("Pokémon", "pokemon", ["Pokemon", "Pokémon", "포켓몬", "ポケモン"], 90),
    ("Metroid", "metroid", ["Metroid", "메트로이드", "メトロイド"], 80),
    ("Animal Crossing", "animal-crossing", ["Animal Crossing", "동물의 숲", "どうぶつの森"], 80),
    ("Splatoon", "splatoon", ["Splatoon", "스플래툰", "スプラトゥーン"], 80),
    ("Kirby", "kirby", ["Kirby", "커비", "カービィ"], 75),
    ("Fire Emblem", "fire-emblem", ["Fire Emblem", "파이어 엠블렘"], 70),
    ("Xenoblade", "xenoblade", ["Xenoblade", "제노블레이드"], 70),
    ("Donkey Kong", "donkey-kong", ["Donkey Kong", "동키콩"], 75),
    ("Rhythm Heaven", "rhythm-heaven", ["Rhythm Heaven", "리듬 세상", "リズム天国"], 70),
]


class Command(BaseCommand):
    help = "Create or update default Nintendo Watch sources and franchises."

    def handle(self, *args, **options):
        source_created = 0
        source_updated = 0
        for data in SOURCES:
            defaults = {
                "name": data["name"],
                "url": data.get("url", ""),
                "source_type": data["source_type"],
                "trust_type": data["trust_type"],
                "region": data["region"],
                "language": data["language"],
                "base_score": TRUST_BASE_SCORE.get(data["trust_type"], 5),
                "enabled": data.get("enabled", True),
                "poll_interval_minutes": data.get("poll_interval_minutes", 60),
                "config": data.get("config", {}),
            }
            _source, created = Source.objects.update_or_create(slug=data["slug"], defaults=defaults)
            source_created += int(created)
            source_updated += int(not created)

        franchise_created = 0
        franchise_updated = 0
        for name, slug, aliases, priority in FRANCHISES:
            _franchise, created = Franchise.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "aliases": aliases, "priority": priority},
            )
            franchise_created += int(created)
            franchise_updated += int(not created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Sources: {source_created} created, {source_updated} updated. "
                f"Franchises: {franchise_created} created, {franchise_updated} updated."
            )
        )

