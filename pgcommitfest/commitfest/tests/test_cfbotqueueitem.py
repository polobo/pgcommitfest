import unittest
from pgcommitfest.commitfest.models import CfbotQueue, CfbotQueueItem, CfbotBranch, CfbotTask

class TestCfbotQueue(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        # Leave test data behind after all tests are executed
        CfbotQueue.objects.all().delete()
        queue = CfbotQueue.objects.create(name="Test Queue")
        queue.insert_item(patch_id=201, message_id="msg201")
        queue.insert_item(patch_id=202, message_id="msg202")
        queue.insert_item(patch_id=8,   message_id="dgj-example@message-08")
        queue.insert_item(patch_id=203, message_id="msg203")
        queue.insert_item(patch_id=204, message_id="msg204")
        queue.insert_item(patch_id=205, message_id="msg205")
        queue.get_and_move()
        queue.get_and_move()

    def setUp(self):
        CfbotQueue.objects.all().delete()
        self.queue = CfbotQueue.objects.create(name="Test Queue")

    def test_insert_item(self):
        # Test inserting an item into the queue
        self.queue.insert_item(patch_id=101, message_id="msg101")
        first_item = self.queue.get_first_item()
        self.assertEqual(first_item.patch_id, 101)
        self.assertEqual(first_item.message_id, "msg101")
        self.assertEqual(self.queue.current_queue_item, first_item.pk)

    def test_insert_multiple_items(self):
        # Test inserting multiple items and maintaining order
        patchset1 = self.queue.insert_item(patch_id=101, message_id="msg101")
        patchset2 = self.queue.insert_item(patch_id=102, message_id="msg102")
        first_item = self.queue.get_first_item()
        self.assertEqual(first_item.patch_id, 101)
        second_item = CfbotQueueItem.objects.get(pk=first_item.ll_next)
        self.assertEqual(second_item.patch_id, 102)
        self.assertEqual(first_item.ll_next, second_item.pk)
        self.assertEqual(first_item.pk, second_item.ll_prev)
        self.assertEqual(second_item.ll_next, None)
        self.assertEqual(first_item.ll_prev, None)
        self.assertEqual(self.queue.current_queue_item, first_item.pk)

    def test_prevent_duplicate_patch_id(self):
        # Test that duplicate patch IDs are not allowed
        self.queue.insert_item(patch_id=101, message_id="msg101")
        self.queue.insert_item(patch_id=101, message_id="msg101")  # Should not add duplicate
        self.assertEqual(CfbotQueueItem.objects.filter(patch_id=101).count(), 1)

    def test_get_first_item(self):
        # Test retrieving the first item in the queue
        self.queue.insert_item(patch_id=101, message_id="msg101")
        self.queue.insert_item(patch_id=102, message_id="msg102")
        first_item = self.queue.get_first_item()
        self.assertEqual(first_item.patch_id, 101)

    def test_empty_queue(self):
        # Test behavior when the queue is empty
        self.assertIsNone(self.queue.get_first_item())
        self.assertEqual(self.queue.current_queue_item, None)

    def test_replace_only_patch_with_new_one(self):
        ps1 = self.queue.insert_item(patch_id=101, message_id="msg101")
        ps2 = self.queue.insert_item(patch_id=101, message_id="msg102")  # remove and replace
        self.assertEqual(CfbotQueueItem.objects.filter(patch_id=101).count(), 1)
        self.assertEqual(self.queue.current_queue_item, ps2.pk)

    def test_replace_pointedto_middle_patch(self):
        ps1 = self.queue.insert_item(patch_id=101, message_id="msg101")
        ps2 = self.queue.insert_item(patch_id=102, message_id="msg102")
        ps3 = self.queue.insert_item(patch_id=103, message_id="msg103")
        self.assertEqual(self.queue.current_queue_item, ps1.pk)
        ret, nxt = self.queue.get_and_move()
        self.assertEqual(ret.pk, ps1.pk)
        self.assertIsNotNone(ret.processed_date)
        self.assertEqual(self.queue.current_queue_item, ps3.pk)
        self.queue.remove_item(ps3.pk)
        self.assertEqual(self.queue.current_queue_item, ps2.pk)
        ret, nxt = self.queue.get_and_move()
        self.assertEqual(self.queue.current_queue_item, ps1.pk)


if __name__ == "__main__":
    unittest.main()

