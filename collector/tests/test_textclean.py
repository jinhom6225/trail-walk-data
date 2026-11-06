import unittest

from collector.textclean import clean_text
from collector.tests.support import real_courses, real_routes


class CleanTextTest(unittest.TestCase):
    def test_br_becomes_newline(self):
        self.assertEqual(clean_text("가<br>나<BR/>다<br />라"), "가\n나\n다\n라")

    def test_tags_removed_entities_unescaped_spaces_collapsed(self):
        self.assertEqual(clean_text('<p><span style=font-size: 11pt;>a&nbsp;&amp;\t\t b</span></p>'), "a & b")

    def test_paragraphs_become_lines_and_empty_lines_drop(self):
        self.assertEqual(clean_text("<p>&nbsp;</p><p>첫 줄</p><p>둘째 줄</p>"), "첫 줄\n둘째 줄")

    def test_none_and_non_strings(self):
        self.assertEqual(clean_text(None), "")
        self.assertEqual(clean_text(19), "19")

    def test_less_than_sign_in_text_is_kept(self):
        self.assertEqual(clean_text("3 < 5 그리고 a<b"), "3 < 5 그리고 a<b")
        self.assertEqual(clean_text("1 &lt; 2"), "1 < 2")

    def test_real_route_themedescs(self):
        text = clean_text(real_routes()[0]["themedescs"])
        self.assertTrue(text.startswith("'남파랑길'은 '남쪽의 쪽빛 바다와 함께 걷는 길'이라는 뜻으로"), text)
        for bad in ("<", ">", "\t", "&nbsp;", "\xa0", "  "):
            self.assertNotIn(bad, text)
        self.assertEqual(len(text.splitlines()), 2)

    def test_real_course_summary(self):
        text = clean_text(real_courses()[0]["crsSummary"])
        lines = text.splitlines()
        self.assertEqual(lines[0], "- 해파랑길 시종점인 오륙도 해맞이 공원에서부터 부산 중구 부산대교까지 이어지는 구간")
        self.assertEqual(lines[-1], "- 부산 갈맷길 3-1, 3-2코스가 중첩됨")
        self.assertNotIn("<br>", text)

    def test_all_real_text_fields_are_clean(self):
        for item in real_courses():
            for name in ("crsSummary", "crsContents", "crsTourInfo", "travelerinfo", "sigun", "crsKorNm"):
                text = clean_text(item[name])
                self.assertNotRegex(text, r"<\s*/?\s*[A-Za-z]", (item["crsIdx"], name))
                self.assertNotIn("\t", text)
                self.assertEqual(text, text.strip())


if __name__ == "__main__":
    unittest.main()
