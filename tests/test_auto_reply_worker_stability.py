import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
WORKER = SCRIPTS / "auto-reply-worker.py"


def load_worker(name: str):
    spec = importlib.util.spec_from_file_location(name, WORKER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LaughterSelectionTests(unittest.TestCase):
    def setUp(self):
        self.worker = load_worker("auto_reply_laughter_selection_test")

    def sanitize(self, draft: str) -> str:
        return self.worker._sanitize_outbound_reaction(
            draft, "그건 좀 세네", laughter_allowed=True
        )

    def test_allowed_four_run_reply_passes_through_unchanged(self):
        for _ in range(20):
            self.assertEqual(self.sanitize("ㅋㅋㅋㅋ 그건 좀"), "ㅋㅋㅋㅋ 그건 좀")

    def test_same_draft_always_sanitizes_to_the_same_reply(self):
        outputs = {self.sanitize("좋네 ㅋㅋㅋ") for _ in range(20)}
        self.assertEqual(len(outputs), 1, outputs)

    def test_exact_triple_laughter_still_varies_across_drafts(self):
        drafts = [f"{word} ㅋㅋㅋ" for word in (
            "좋네", "그렇지", "맞아", "대박", "역시", "아 진짜",
            "그니까", "오호", "인정", "웃기네", "그럴 줄", "어쩐지",
        )]
        lengths = set()
        for draft in drafts:
            reply = self.sanitize(draft)
            run = reply.rsplit(" ", 1)[-1]
            self.assertRegex(run, r"\Aㅋ{3,}\Z")
            lengths.add(len(run))
        self.assertGreater(len(lengths), 1, "every draft kept exactly three ㅋ")


if __name__ == "__main__":
    unittest.main()
