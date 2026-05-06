from __future__ import annotations

from django.core.management.base import BaseCommand

from news.models import Franchise, Language, Region, Source, SourceType, TrustType, TRUST_BASE_SCORE


NINTENDO_TITLE_KEYWORDS = [
    "Nintendo",
    "Switch",
    "Switch 2",
    "Direct",
    "Mario",
    "Zelda",
    "Pokemon",
    "Pokémon",
    "Metroid",
    "Kirby",
    "Donkey Kong",
    "Splatoon",
    "Xenoblade",
    "Fire Emblem",
    "Rhythm Heaven",
    "Tomodachi",
    "닌텐도",
    "스위치",
    "마리오",
    "젤다",
    "포켓몬",
]


SOURCES = [
    {
        "name": "한국닌텐도 News",
        "slug": "nintendo-kr-news",
        "url": "https://www.nintendo.com/kr/news",
        "source_type": SourceType.HTML,
        "trust_type": TrustType.OFFICIAL,
        "region": Region.KR,
        "language": Language.KO,
        "config": {
            "item_selector": ".ncmn-softUnit--list",
            "title_selector": ".ncmn-softUnit__name",
            "link_selector": "a[href]",
            "date_selector": ".ncmn-softUnit__release",
            "url_include_patterns": ["/kr/news/article/", "/kr/event/", "/kr/switch/", "/kr/movie/", "/kr/interview/", "/kr/guide/"],
            "reject_url_patterns": ["/kr/search", "/kr/news/rss", "/category/", "/tag/", "/all-games"],
        },
    },
    {
        "name": "한국닌텐도 발매 스케줄",
        "slug": "nintendo-kr-schedule",
        "url": "https://www.nintendo.com/kr/schedule",
        "source_type": SourceType.HTML,
        "trust_type": TrustType.OFFICIAL,
        "region": Region.KR,
        "language": Language.KO,
        "config": {
            "url_include_patterns": ["/kr/schedule", "/kr/software"],
            "reject_url_patterns": ["/kr/search", "/kr/news/rss", "/category/", "/tag/"],
            "quality_allow_title_patterns": [r".+"],
        },
    },
    {
        "name": "Nintendo US What's New",
        "slug": "nintendo-us-whats-new",
        "url": "https://www.nintendo.com/us/whatsnew/",
        "source_type": SourceType.HTML,
        "trust_type": TrustType.OFFICIAL,
        "region": Region.US,
        "language": Language.EN,
        "config": {
            "embedded_json_selector": "script#__NEXT_DATA__",
            "embedded_json_item_type": "NewsArticle",
            "embedded_json_title_fields": ["headline", "name", "title"],
            "embedded_json_url_fields": ['url({"relative":true})'],
            "embedded_json_summary_fields": ['body.text({"characterLimit":250})'],
            "embedded_json_date_fields": ["publishDate"],
            "url_include_patterns": ["/us/whatsnew/"],
            "reject_url_patterns": ["/us/search", "/us/store", "/news/rss", "/all-games", "/nintendo-direct"],
            "title_exclude_exact": ["Skip to main content"],
        },
    },
    {
        "name": "Nintendo UK News",
        "slug": "nintendo-uk-news",
        "url": "https://www.nintendo.com/en-gb/News/News-Updates-11145.html",
        "source_type": SourceType.HTML,
        "trust_type": TrustType.OFFICIAL,
        "region": Region.EU,
        "language": Language.EN,
        "config": {
            "item_selector": ".overview-news-element, li.page-list-group-item",
            "title_selector": "h2 a, h3 a, .news-list-title a, .page-title a, .news-list-title, .page-title",
            "link_selector": "a[href]",
            "date_selector": ".date, .page-data",
            "summary_selector": ".news-list-info p:not(.date):not(.news-list-title), .strapline",
            "thumbnail_selector": "img",
            "url_include_patterns": ["/en-gb/News/"],
            "url_exclude_patterns": ["/Support/", "/Search/", "/feed", "/rss"],
            "reject_url_patterns": ["/Support/", "/Search/", "/category/", "/tag/", "/all-games", "/nintendo-direct"],
        },
    },
    {
        "name": "Nintendo Official YouTube Korea",
        "slug": "nintendo-youtube-kr",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCRCK5FCJtomQT3b88jXI_DA",
        "source_type": SourceType.YOUTUBE_RSS,
        "trust_type": TrustType.OFFICIAL,
        "region": Region.KR,
        "language": Language.KO,
        "enabled": False,
        "config": {
            "channel_id": "UCRCK5FCJtomQT3b88jXI_DA",
            "handle": "@nintendo_kr",
            "channel_url": "https://www.youtube.com/@nintendo_kr",
            "reject_url_patterns": ["/playlist", "/shorts"],
            "note": "공식 채널 ID는 기록해 두었지만 현재 YouTube RSS endpoint가 404를 반환하므로 기본 비활성입니다.",
        },
    },
    {
        "name": "Gematsu",
        "slug": "gematsu",
        "url": "https://www.gematsu.com/feed",
        "source_type": SourceType.RSS,
        "trust_type": TrustType.PRESS,
        "region": Region.GLOBAL,
        "language": Language.EN,
        "config": {
            "title_include_keywords": NINTENDO_TITLE_KEYWORDS,
            "title_exclude_keywords": ["podcast", "interview roundup"],
            "reject_url_patterns": ["/category/", "/tag/", "/platform/", "/games/", "/feed"],
            "title_exclude_exact": ["News RSS", "All games"],
        },
    },
    {
        "name": "Nintendo Life",
        "slug": "nintendo-life",
        "url": "https://www.nintendolife.com/feeds/latest",
        "source_type": SourceType.RSS,
        "trust_type": TrustType.PRESS,
        "region": Region.GLOBAL,
        "language": Language.EN,
        "config": {
            "title_include_keywords": NINTENDO_TITLE_KEYWORDS,
            "title_exclude_keywords": ["soapbox", "talking point", "poll"],
            "reject_url_patterns": ["/guides/", "/forums/", "/games/", "/news/rss", "/feeds/", "/all-games"],
            "title_exclude_exact": ["News RSS", "Tips and tricks", "All Nintendo Switch games"],
        },
    },
    {
        "name": "VGC Nintendo",
        "slug": "vgc-nintendo",
        "url": "https://www.videogameschronicle.com/platforms/nintendo/",
        "source_type": SourceType.HTML,
        "trust_type": TrustType.PRESS,
        "region": Region.GLOBAL,
        "language": Language.EN,
        "config": {
            "item_selector": "article.vgc-post--post",
            "title_selector": ".headline a",
            "link_selector": ".headline a",
            "date_selector": "time",
            "summary_selector": ".strapline",
            "thumbnail_selector": "img",
            "thumbnail_attr": "data-src",
            "url_include_patterns": ["/news/", "/features/"],
            "url_exclude_patterns": ["/feed", "/rss", "/category/", "/tag/", "/platforms/"],
            "reject_url_patterns": ["/platforms/", "/category/", "/tag/", "/page/", "/news/rss", "/features/rss", "/reviews/rss"],
            "title_include_keywords": NINTENDO_TITLE_KEYWORDS,
            "title_exclude_keywords": ["VGC Live", "Patreon", "tickets are on sale"],
            "title_exclude_exact": ["News RSS", "Features RSS", "Reviews RSS", "RSS Feed"],
        },
    },
    {
        "name": "GamingLeaksAndRumours Reddit RSS",
        "slug": "gaming-leaks-rumours-reddit",
        "url": "https://www.reddit.com/r/GamingLeaksAndRumours/search.rss?q=Nintendo%20OR%20Switch%20OR%20Direct&restrict_sr=1&sort=new",
        "source_type": SourceType.REDDIT_RSS,
        "trust_type": TrustType.RUMOR,
        "region": Region.GLOBAL,
        "language": Language.EN,
        "config": {
            "title_include_keywords": ["Nintendo", "Switch", "Switch 2", "Direct", "Mario", "Zelda", "Pokemon", "Pokémon"],
            "title_exclude_keywords": ["weekly discussion", "megathread", "job listing", "hiring"],
            "reject_url_patterns": ["/search", "/wiki", "/about"],
            "title_exclude_exact": ["News RSS"],
        },
    },
]

FRANCHISES = [
    ("마리오", "mario", ["Mario", "Super Mario", "마리오", "슈퍼 마리오", "Mario Kart", "마리오 카트", "マリオ"], 90),
    ("젤다의 전설", "zelda", ["The Legend of Zelda", "Zelda", "젤다의 전설", "젤다", "야숨", "왕눈", "ゼルダ"], 90),
    ("포켓몬", "pokemon", ["Pokémon", "Pokemon", "Pocket Monsters", "포켓몬스터", "포켓몬", "ポケモン"], 90),
    ("메트로이드", "metroid", ["Metroid", "메트로이드", "メトロイド"], 80),
    ("동물의 숲", "animal-crossing", ["Animal Crossing", "동물의 숲", "동숲", "모동숲", "どうぶつの森"], 80),
    ("스플래툰", "splatoon", ["Splatoon", "스플래툰", "スプラトゥーン"], 80),
    ("별의 커비", "kirby", ["Kirby", "별의 커비", "커비", "カービィ"], 75),
    ("파이어 엠블렘", "fire-emblem", ["Fire Emblem", "파이어 엠블렘"], 70),
    ("제노블레이드", "xenoblade", ["Xenoblade", "Xenoblade Chronicles", "제노블레이드", "제노블레이드 크로니클스"], 70),
    ("동키콩", "donkey-kong", ["Donkey Kong", "DK", "동키콩"], 75),
    ("리듬 세상", "rhythm-heaven", ["Rhythm Heaven", "리듬 천국", "리듬천국", "리듬 세상", "리듬세상", "リズム天国"], 70),
]


class Command(BaseCommand):
    help = "Create or update default Nintendo Watch sources and game type names."

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
                f"Game types: {franchise_created} created, {franchise_updated} updated."
            )
        )
