from django.test import SimpleTestCase

from news.models import Source, SourceType, TrustType
from news.services.classifier import classify_item


class ClassifierTests(SimpleTestCase):
    def test_official_direct_classification(self):
        source = Source(name="Official", slug="official", source_type=SourceType.RSS, trust_type=TrustType.OFFICIAL)

        result = classify_item(source, "Nintendo Direct announced for Switch 2", "")

        self.assertEqual(result.trust_label, "official")
        self.assertEqual(result.category, "direct")
        self.assertIn("direct", result.tags)
        self.assertIn("switch2", result.tags)

    def test_rumor_leak_classification(self):
        source = Source(name="Rumor", slug="rumor", source_type=SourceType.RSS, trust_type=TrustType.RUMOR)

        result = classify_item(source, "Insider says new Zelda leaked", "")

        self.assertEqual(result.trust_label, "rumor")
        self.assertEqual(result.category, "leak")
        self.assertIn("leak", result.tags)

