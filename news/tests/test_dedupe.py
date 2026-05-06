from django.test import SimpleTestCase

from news.services.dedupe import canonicalize_url, content_hash_for, normalized_title


class DedupeTests(SimpleTestCase):
    def test_canonicalize_url_removes_tracking_and_trailing_slash(self):
        url = "HTTPS://Example.COM/News/Article/?utm_source=x&b=2&a=1&fbclid=abc"

        self.assertEqual(canonicalize_url(url), "https://example.com/News/Article?a=1&b=2")

    def test_normalized_title_makes_switch2_variants_comparable(self):
        first = normalized_title("Nintendo Switch 2: Direct rumor!")
        second = normalized_title("Switch2 Direct rumor")

        self.assertEqual(first, "switch2 direct rumor")
        self.assertEqual(second, "switch2 direct rumor")

    def test_content_hash_uses_normalized_title_and_canonical_url(self):
        canonical_url = "https://example.com/news"

        self.assertEqual(
            content_hash_for("Nintendo Switch 2 news", canonical_url),
            content_hash_for("Switch2 news", canonical_url),
        )

