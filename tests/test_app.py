import math
import tempfile
import unittest
from pathlib import Path

from app import Database, DomainError, seed_demo


class GeodeticFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.epoch = seed_demo(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_adjust_review_publish_and_compare(self):
        result = self.db.adjust(self.epoch, "alice", "editor")
        self.assertLess(result["residual_rms"], 0.02)
        self.db.transition(self.epoch, "alice", "editor", "submit")
        for obs in self.db.list_observations(self.epoch):
            self.db.confirm_observation(self.epoch, obs["id"], "bob", "reviewer")
        for point in {r["point"] for r in self.db.results(self.epoch)}:
            self.db.confirm_result(self.epoch, point, "bob", "reviewer")
        approved = self.db.transition(self.epoch, "bob", "reviewer", "approve")
        self.assertEqual(approved["status"], "approved")
        published = self.db.transition(self.epoch, "bob", "reviewer", "publish")
        self.assertEqual(published["status"], "published")
        self.assertTrue(self.db.results(self.epoch))
        self.assertTrue(any(item["action"] == "epoch.publish" for item in self.db.audit(self.epoch)))

    def test_duplicate_and_role_conflict_are_rejected(self):
        with self.assertRaisesRegex(DomainError, "疑似重复"):
            self.db.add_observation(self.epoch, "alice", "distance", "A", "B", 100.0)
        with self.assertRaises(DomainError) as cm:
            self.db.adjust(self.epoch, "bob", "viewer")
        self.assertEqual(cm.exception.status, 403)

    def test_single_return_keeps_confirmed_records_locked(self):
        self.db.adjust(self.epoch, "alice", "editor")
        self.db.transition(self.epoch, "alice", "editor", "submit")
        observations = self.db.list_observations(self.epoch)
        target = observations[-1]
        for obs in observations:
            if obs["id"] != target["id"]:
                self.db.confirm_observation(self.epoch, obs["id"], "bob", "reviewer")
        returned = self.db.return_observation(self.epoch, target["id"], "方向偏差超限", "bob", "reviewer")
        self.assertEqual(returned["review_status"], "returned")
        self.assertEqual(returned["return_reason"], "方向偏差超限")
        self.assertEqual(self.db.get_epoch(self.epoch)["status"], "draft")
        statuses = {o["id"]: o["review_status"] for o in self.db.list_observations(self.epoch)}
        self.assertEqual(statuses[target["id"]], "returned")
        self.assertTrue(all(v == "confirmed" for k, v in statuses.items() if k != target["id"]))
        # confirmed observations stay locked even while the epoch is back in draft
        with self.assertRaisesRegex(DomainError, "已确认"):
            self.db.revise_observation(self.epoch, observations[0]["id"], "alice", "editor", value=1.0)
        # a return must carry a reason
        with self.assertRaisesRegex(DomainError, "退回必须填写原因"):
            self.db.return_observation(self.epoch, target["id"], "  ", "bob", "reviewer")

    def test_revise_readjust_and_targeted_rereview_then_publish(self):
        self.db.adjust(self.epoch, "alice", "editor")
        self.db.transition(self.epoch, "alice", "editor", "submit")
        observations = self.db.list_observations(self.epoch)
        target = next(o for o in observations if o["kind"] == "height_difference" and o["p1"] == "C")
        for obs in observations:
            if obs["id"] != target["id"]:
                self.db.confirm_observation(self.epoch, obs["id"], "bob", "reviewer")
        self.db.confirm_result(self.epoch, "A", "bob", "reviewer")
        self.db.confirm_result(self.epoch, "B", "bob", "reviewer")
        self.db.return_observation(self.epoch, target["id"], "C-D 高差方向偏差", "bob", "reviewer")

        revised = self.db.revise_observation(self.epoch, target["id"], "alice", "editor",
                                             value=target["value"] + 0.05)
        self.assertEqual(revised["review_status"], "pending")
        self.assertEqual(revised["revision"], 1)
        self.assertIsNone(revised["residual"])

        # cannot resubmit while the returned record is not re-adjusted
        adjust = self.db.adjust(self.epoch, "alice", "editor")
        self.assertEqual(set(adjust["reopened_results"]), {"C", "D"})
        statuses = {r["point"]: r["review_status"] for r in self.db.results(self.epoch)}
        self.assertEqual(statuses["A"], "confirmed")
        self.assertEqual(statuses["B"], "confirmed")
        self.assertEqual(statuses["C"], "pending")
        self.assertEqual(statuses["D"], "pending")

        self.db.transition(self.epoch, "alice", "editor", "submit")
        # only the changed record needs re-confirmation
        after = {o["id"]: o["review_status"] for o in self.db.list_observations(self.epoch)}
        self.assertEqual(after[target["id"]], "pending")
        self.assertTrue(all(v == "confirmed" for k, v in after.items() if k != target["id"]))
        with self.assertRaisesRegex(DomainError, "未逐条确认"):
            self.db.transition(self.epoch, "bob", "reviewer", "approve")
        self.db.confirm_observation(self.epoch, target["id"], "bob", "reviewer")
        with self.assertRaisesRegex(DomainError, "精度结果未复核"):
            self.db.transition(self.epoch, "bob", "reviewer", "approve")
        self.db.confirm_result(self.epoch, "C", "bob", "reviewer")
        self.db.confirm_result(self.epoch, "D", "bob", "reviewer")
        approved = self.db.transition(self.epoch, "bob", "reviewer", "approve")
        self.assertEqual(approved["status"], "approved")
        published = self.db.transition(self.epoch, "bob", "reviewer", "publish")
        self.assertEqual(published["status"], "published")

    def test_new_observation_after_return_reopens_its_endpoints_only(self):
        self.db.adjust(self.epoch, "alice", "editor")
        self.db.transition(self.epoch, "alice", "editor", "submit")
        observations = self.db.list_observations(self.epoch)
        bad = observations[0]
        for obs in observations:
            if obs["id"] != bad["id"]:
                self.db.confirm_observation(self.epoch, obs["id"], "bob", "reviewer")
        for point in ("A", "B", "C", "D"):
            self.db.confirm_result(self.epoch, point, "bob", "reviewer")
        self.db.return_observation(self.epoch, bad["id"], "边长粗差", "bob", "reviewer")
        self.db.revise_observation(self.epoch, bad["id"], "alice", "editor", value=bad["value"])
        created = self.db.add_observation(self.epoch, "alice", "distance", "A", "D",
                                          math.hypot(150.0, 60.0), allow_duplicate=True)
        adjust = self.db.adjust(self.epoch, "alice", "editor")
        self.assertEqual(set(adjust["reopened_results"]), {"A", "B", "D"})
        self.assertEqual(next(r for r in self.db.results(self.epoch) if r["point"] == "C")["review_status"],
                         "confirmed")
        self.db.transition(self.epoch, "alice", "editor", "submit")
        pending_ids = {o["id"] for o in self.db.list_observations(self.epoch) if o["review_status"] == "pending"}
        self.assertEqual(pending_ids, {bad["id"], created["id"]})

    def test_review_guards_and_gates(self):
        # creator/submitter can never perform review actions
        self.db.adjust(self.epoch, "alice", "editor")
        self.db.transition(self.epoch, "alice", "editor", "submit")
        first = self.db.list_observations(self.epoch)[0]
        with self.assertRaises(DomainError) as cm:
            self.db.confirm_observation(self.epoch, first["id"], "alice", "reviewer")
        self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(DomainError) as cm:
            self.db.return_observation(self.epoch, first["id"], "x", "alice", "reviewer")
        self.assertEqual(cm.exception.status, 403)
        # editors and viewers cannot review
        with self.assertRaises(DomainError) as cm:
            self.db.confirm_observation(self.epoch, first["id"], "carol", "editor")
        self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(DomainError) as cm:
            self.db.confirm_result(self.epoch, "A", "carol", "viewer")
        self.assertEqual(cm.exception.status, 403)
        # cannot approve while observations are pending
        with self.assertRaisesRegex(DomainError, "未逐条确认"):
            self.db.transition(self.epoch, "bob", "reviewer", "approve")
        # the epoch-wide reject transition is replaced by single-item returns
        with self.assertRaisesRegex(DomainError, "单条观测执行退回"):
            self.db.transition(self.epoch, "bob", "reviewer", "reject")
        # cannot publish straight from review (skipping approval)
        with self.assertRaisesRegex(DomainError, "只有已批准成果可以发布"):
            self.db.transition(self.epoch, "bob", "reviewer", "publish")

    def test_submit_requires_adjustment_and_no_pending_returns(self):
        # a fresh epoch has neither observations nor an adjustment
        epoch = self.db.create_epoch("空期次", "alice")
        with self.assertRaisesRegex(DomainError, "没有观测"):
            self.db.transition(epoch, "alice", "editor", "submit")
        self.db.set_point(epoch, "alice", "A", 0, 0, 0, True, True, True)
        self.db.set_point(epoch, "alice", "B", 1, 0, 0, True, True, True)
        self.db.set_point(epoch, "alice", "C", 0.5, 0.9, 0, False, False, True)
        self.db.add_observation(epoch, "alice", "distance", "A", "B", 1.0, allow_duplicate=True)
        self.db.add_observation(epoch, "alice", "distance", "A", "C",
                                math.hypot(0.5, 0.9), allow_duplicate=True)
        self.db.add_observation(epoch, "alice", "distance", "B", "C",
                                math.hypot(0.5, 0.9), allow_duplicate=True)
        # observations exist but no adjustment has produced results yet
        with self.assertRaisesRegex(DomainError, "先执行平差"):
            self.db.transition(epoch, "alice", "editor", "submit")
        self.db.adjust(epoch, "alice", "editor")
        self.db.transition(epoch, "alice", "editor", "submit")
        pending = self.db.list_observations(epoch)[0]
        self.db.return_observation(epoch, pending["id"], "存疑", "bob", "reviewer")
        with self.assertRaisesRegex(DomainError, "尚未修订"):
            self.db.transition(epoch, "alice", "editor", "submit")


if __name__ == "__main__":
    unittest.main()
