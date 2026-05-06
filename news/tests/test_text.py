from django.test import TestCase

from news.models import Franchise
from news.services.classifier import detect_franchises
from news.services.text import contains_hangul, extract_sentences, normalize_title, tokenize_topic


class TextTests(TestCase):
    def test_extract_sentences(self):
        text = "첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다. 네 번째 문장입니다."

        self.assertEqual(len(extract_sentences(text, limit=2)), 2)

    def test_contains_hangul(self):
        self.assertTrue(contains_hangul("닌텐도 스위치"))
        self.assertFalse(contains_hangul("Nintendo Switch"))

    def test_tokenize_topic_removes_common_words(self):
        tokens = tokenize_topic("Nintendo Switch new Zelda release date")

        self.assertIn("zelda", tokens)
        self.assertNotIn("nintendo", tokens)

    def test_franchise_alias_matching(self):
        Franchise.objects.create(name="Zelda", slug="zelda", aliases=["The Legend of Zelda", "젤다"], priority=90)

        matches = detect_franchises("젤다 신작 발표", "")

        self.assertEqual([match.slug for match in matches], ["zelda"])

    def test_normalize_title_keeps_korean_terms(self):
        self.assertEqual(normalize_title("닌텐도 다이렉트: 신작 발표!"), "닌텐도 다이렉트 신작 발표")

