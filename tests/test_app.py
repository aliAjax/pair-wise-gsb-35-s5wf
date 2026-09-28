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
        self.db.adjust(self.epoch, "alice", "editor")

    def tearDown(self):
        self.tmp.cleanup()

    def _confirm_all(self, reviewer="bob"):
        for obs in self.db.list_observations(self.epoch):
            self.db.confirm_observation(self.epoch, obs["id"], reviewer, "reviewer")
        for result in self.db.results(self.epoch):
            self.db.confirm_result(self.epoch, result["point"], reviewer, "reviewer")

    def test_adjust_review_publish_and_compare(self):
        result = self.db.results(self.epoch)
        self.assertTrue(result)
        self.db.transition(self.epoch, "alice", "editor", "submit")
        self._confirm_all("bob")
        approved = self.db.transition(self.epoch, "bob", "reviewer", "approve")
        self.assertEqual(approved["status"], "approved")
        published = self.db.transition(self.epoch, "bob", "reviewer", "publish")
        self.assertEqual(published["status"], "published")
        self.assertTrue(any(item["action"] == "epoch.publish" for item in self.db.audit(self.epoch)))

    def test_duplicate_and_role_conflict_are_rejected(self):
        with self.assertRaisesRegex(DomainError, "疑似重复"):
            self.db.add_observation(self.epoch, "alice", "distance", "A", "B", 100.0)
        with self.assertRaises(DomainError) as cm:
            self.db.adjust(self.epoch, "bob", "viewer")
        self.assertEqual(cm.exception.status, 403)

    def test_per_record_review_is_required_before_approval(self):
        self.db.transition(self.epoch, "alice", "editor", "submit")
        with self.assertRaisesRegex(DomainError, "9 条观测未逐条确认"):
            self.db.transition(self.epoch, "bob", "reviewer", "approve")
        obs = self.db.list_observations(self.epoch)
        for item in obs[:-1]:
            self.db.confirm_observation(self.epoch, item["id"], "bob", "reviewer")
        # 观测没确认完时优先报观测。
        with self.assertRaisesRegex(DomainError, "1 条观测未逐条确认"):
            self.db.transition(self.epoch, "bob", "reviewer", "approve")
        self.db.confirm_observation(self.epoch, obs[-1]["id"], "bob", "reviewer")
        # 观测确认完但精度结果没确认，仍然不能批准。
        with self.assertRaisesRegex(DomainError, "精度结果未确认"):
            self.db.transition(self.epoch, "bob", "reviewer", "approve")
        # 身份不对不能复核。
        with self.assertRaises(DomainError) as cm:
            self.db.confirm_result(self.epoch, obs[-1]["p1"], "alice", "editor")
        self.assertEqual(cm.exception.status, 403)
        self.db.confirm_result(self.epoch, obs[-1]["p1"], "bob", "reviewer")
        with self.assertRaisesRegex(DomainError, "精度结果未确认"):
            self.db.transition(self.epoch, "bob", "reviewer", "approve")
        # 创建/提交人不能复核自己的期次。
        with self.assertRaisesRegex(DomainError, "不能自行复核"):
            self.db.confirm_result(self.epoch, obs[-1]["p2"], "alice", "reviewer")

    def test_single_return_keeps_confirmed_records_locked(self):
        self.db.transition(self.epoch, "alice", "editor", "submit")
        observations = self.db.list_observations(self.epoch)
        target = next(o for o in observations if o["kind"] == "distance" and o["p1"] == "A" and o["p2"] == "B")
        locked = observations[1]
        for obs in observations:
            if obs["id"] != target["id"]:
                self.db.confirm_observation(self.epoch, obs["id"], "bob", "reviewer")
        for result in self.db.results(self.epoch):
            self.db.confirm_result(self.epoch, result["point"], "bob", "reviewer")

        with self.assertRaisesRegex(DomainError, "退回必须填写退回原因"):
            self.db.return_observation(self.epoch, target["id"], "  ", "bob", "reviewer")
        returned = self.db.return_observation(self.epoch, target["id"], "该边方向疑似有偏差", "bob", "reviewer")
        self.assertEqual(returned["epoch_status"], "draft")
        self.assertEqual(returned["affected_points"], ["A", "B"])

        # 整期回到可修改状态，已确认的观测锁定不能改。
        with self.assertRaisesRegex(DomainError, "锁定"):
            self.db.update_observation(self.epoch, locked["id"], "alice", "editor", {"value": 1.0})
        with self.assertRaisesRegex(DomainError, "锁定"):
            self.db.delete_observation(self.epoch, locked["id"], "alice", "editor")
        record = next(o for o in self.db.list_observations(self.epoch) if o["id"] == target["id"])
        self.assertEqual(record["review_status"], "returned")
        self.assertEqual(record["review_reason"], "该边方向疑似有偏差")
        # 复核启动后点数据也锁定。
        with self.assertRaisesRegex(DomainError, "点数据已锁定"):
            self.db.set_point(self.epoch, "alice", "C", 50, 51, 110)

        # 修正退回记录；变动观测端点的精度结果撤回，其他结果保留确认。
        updated = self.db.update_observation(self.epoch, target["id"], "alice", "editor", {"value": 100.002})
        self.assertEqual(updated["affected_points"], ["A", "B"])
        with self.assertRaisesRegex(DomainError, "重新平差"):
            self.db.transition(self.epoch, "alice", "editor", "submit")
        self.db.adjust(self.epoch, "alice", "editor")
        self.db.transition(self.epoch, "alice", "editor", "submit")

        states = {o["id"]: o["review_status"] for o in self.db.list_observations(self.epoch)}
        self.assertEqual(states[target["id"]], "pending")
        self.assertEqual(states[locked["id"]], "confirmed")
        result_states = {r["point"]: r["review_status"] for r in self.db.results(self.epoch)}
        self.assertEqual(result_states["A"], "pending")
        self.assertEqual(result_states["B"], "pending")
        self.assertEqual(result_states["C"], "confirmed")
        self.assertEqual(result_states["D"], "confirmed")

        # 只有变动记录和受影响精度结果需要重新确认，之后才能批准发布。
        self.db.confirm_observation(self.epoch, target["id"], "carol", "reviewer")
        self.db.confirm_result(self.epoch, "A", "carol", "reviewer")
        self.db.confirm_result(self.epoch, "B", "carol", "reviewer")
        approved = self.db.transition(self.epoch, "carol", "reviewer", "approve")
        self.assertEqual(approved["status"], "approved")
        with self.assertRaises(DomainError) as cm:
            self.db.transition(self.epoch, "alice", "editor", "publish")
        self.assertEqual(cm.exception.status, 409)
        published = self.db.transition(self.epoch, "carol", "reviewer", "publish")
        self.assertEqual(published["status"], "published")

    def test_new_observation_force_readjust_before_resubmit(self):
        self.db.transition(self.epoch, "alice", "editor", "submit")
        first = self.db.list_observations(self.epoch)[0]
        self.db.return_observation(self.epoch, first["id"], "需要补测", "bob", "reviewer")
        # 先修正退回记录；补录新观测同样要求重新平差，且补录记录为待复核。
        self.db.update_observation(self.epoch, first["id"], "alice", "editor",
                                   {"value": float(first["value"])})
        self.db.add_observation(self.epoch, "alice", "distance", "A", "D",
                                math.hypot(150.0, 60.0), allow_duplicate=True)
        with self.assertRaisesRegex(DomainError, "重新平差"):
            self.db.transition(self.epoch, "alice", "editor", "submit")
        self.db.adjust(self.epoch, "alice", "editor")
        self.db.transition(self.epoch, "alice", "editor", "submit")
        self.assertEqual(
            {o["review_status"] for o in self.db.list_observations(self.epoch)}, {"pending"}
        )

    def test_whole_epoch_reject_is_disabled(self):
        self.db.transition(self.epoch, "alice", "editor", "submit")
        with self.assertRaisesRegex(DomainError, "单条观测执行退回"):
            self.db.transition(self.epoch, "bob", "reviewer", "reject")


if __name__ == "__main__":
    unittest.main()
