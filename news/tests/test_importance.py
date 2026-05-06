from django.test import SimpleTestCase

from news.models import Franchise, Source, SourceType, TrustType
from news.services.importance import calculate_importance


class ImportanceTests(SimpleTestCase):
    def test_high_importance_caps_at_100(self):
        source = Source(name="Official", slug="official", source_type=SourceType.RSS, trust_type=TrustType.OFFICIAL)
        franchise = Franchise(name="Zelda", slug="zelda", priority=90)

        score = calculate_importance(
            source=source,
            title="Nintendo Direct reveals Switch 2 Zelda release date trailer",
            franchises=[franchise],
            tags=["direct", "switch2", "release_date", "trailer"],
        )

        self.assertEqual(score, 100)

    def test_sale_from_press_is_lower_importance(self):
        source = Source(name="Press", slug="press", source_type=SourceType.RSS, trust_type=TrustType.PRESS)

        score = calculate_importance(source=source, title="Nintendo eShop sale starts today", tags=["sale"])

        self.assertLess(score, 60)
        self.assertGreaterEqual(score, 30)

