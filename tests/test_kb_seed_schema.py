from __future__ import annotations

import unittest

import knowledge_base


class KnowledgeBaseSeedSchemaTests(unittest.TestCase):
    def test_seed_precedents_have_no_confidence_or_recovery_duration(self):
        for record in knowledge_base.SEED_RECORDS:
            with self.subTest(kb_id=record["kb_id"]):
                summary = record["summary"].lower()
                self.assertNotIn("recovery_seconds", record)
                self.assertNotIn("confidence", summary)
                self.assertNotIn("conf ", summary)
                self.assertNotRegex(summary, r"resolved .+ in \d+(?:\.\d+)?s")
                self.assertIn(record["fault_class"], summary)
                self.assertIn(record["action_taken"], record["summary"])


if __name__ == "__main__":
    unittest.main()
