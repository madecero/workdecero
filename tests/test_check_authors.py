import unittest

import _paths  # noqa: F401
import check_authors as ca

GOOD = ("madecero", "123+madecero@users.noreply.github.com")
REAL_NAME = ("Pat Rivera", "123+madecero@users.noreply.github.com")
REAL_EMAIL = ("madecero", "pat" + "@" + "mail.test")
OTHER_HANDLE = ("someoneelse", "9+someoneelse@users.noreply.github.com")


def record(author=GOOD, committer=GOOD, sha="a" * 40):
    return (sha,) + author + committer


class Authors(unittest.TestCase):
    def test_allowed_identity(self):
        self.assertEqual(ca.violations([record()]), [])
        self.assertTrue(ca.identity_allowed("madecero", "madecero@users.noreply.github.com"))

    def test_github_may_be_committer_but_not_author(self):
        self.assertEqual(ca.violations([record(committer=ca.GITHUB_COMMITTER)]), [])
        self.assertEqual(ca.violations([record(author=ca.GITHUB_COMMITTER)]), [("a" * 40, "author")])

    def test_real_name_with_noreply_email_is_rejected(self):
        self.assertEqual(ca.violations([record(author=REAL_NAME)]), [("a" * 40, "author")])
        self.assertEqual(ca.violations([record(committer=REAL_NAME)]), [("a" * 40, "committer")])

    def test_real_email_is_rejected(self):
        self.assertEqual(ca.violations([record(author=REAL_EMAIL)]), [("a" * 40, "author")])

    def test_handle_must_match_its_email_and_be_allowed(self):
        self.assertEqual(len(ca.violations([record(author=OTHER_HANDLE)])), 1)
        mismatch = ("madecero", "9+someoneelse@users.noreply.github.com")
        self.assertEqual(len(ca.violations([record(author=mismatch)])), 1)

    def test_report_carries_only_hash_and_role(self):
        bad = ca.violations([record(author=REAL_NAME, committer=REAL_EMAIL, sha="b" * 40)])
        self.assertEqual(bad, [("b" * 40, "author"), ("b" * 40, "committer")])


if __name__ == "__main__":
    unittest.main()
