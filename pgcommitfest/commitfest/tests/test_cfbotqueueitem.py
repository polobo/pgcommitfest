import unittest
from pgcommitfest.commitfest.models import CfbotQueue, CfbotQueueItem

class TestCfbotQueue(unittest.TestCase):
    def setUp(self):
        # Ensure only one CfbotQueue instance exists
        CfbotQueue.objects.all().delete()
        self.queue = CfbotQueue.objects.create(name="Test Queue")

    def test_insert_item(self):
        # Test inserting an item into the queue
        self.queue.insert_item(patch_id=1, message_id="msg1")
        first_item = self.queue.get_first_item()
        self.assertEqual(first_item.patch_id, 1)
        self.assertEqual(first_item.message_id, "msg1")
        self.assertEqual(self.queue.current_queue_item, first_item.pk)

    def test_insert_multiple_items(self):
        # Test inserting multiple items and maintaining order
        patchset1 = self.queue.insert_item(patch_id=1, message_id="msg1")
        patchset2 = self.queue.insert_item(patch_id=2, message_id="msg2")
        first_item = self.queue.get_first_item()
        self.assertEqual(first_item.patch_id, 1)
        second_item = CfbotQueueItem.objects.get(pk=first_item.ll_next)
        self.assertEqual(second_item.patch_id, 2)
        self.assertEqual(first_item.ll_next, second_item.pk)
        self.assertEqual(first_item.pk, second_item.ll_prev)
        self.assertEqual(second_item.ll_next, None)
        self.assertEqual(first_item.ll_prev, None)
        self.assertEqual(self.queue.current_queue_item, first_item.pk)

    def test_prevent_duplicate_patch_id(self):
        # Test that duplicate patch IDs are not allowed
        self.queue.insert_item(patch_id=1, message_id="msg1")
        self.queue.insert_item(patch_id=1, message_id="msg1")  # Should not add duplicate
        self.assertEqual(CfbotQueueItem.objects.filter(patch_id=1).count(), 1)

    def test_get_first_item(self):
        # Test retrieving the first item in the queue
        self.queue.insert_item(patch_id=1, message_id="msg1")
        self.queue.insert_item(patch_id=2, message_id="msg2")
        first_item = self.queue.get_first_item()
        self.assertEqual(first_item.patch_id, 1)

    def test_empty_queue(self):
        # Test behavior when the queue is empty
        self.assertIsNone(self.queue.get_first_item())
        self.assertEqual(self.queue.current_queue_item, None)

    def test_replace_only_patch_with_new_one(self):
        ps1 = self.queue.insert_item(patch_id=1, message_id="msg1")
        ps2 = self.queue.insert_item(patch_id=1, message_id="msg2")  # remove and replace
        self.assertEqual(CfbotQueueItem.objects.filter(patch_id=1).count(), 1)
        self.assertEqual(self.queue.current_queue_item, ps2.pk)

    def test_replace_pointedto_middle_patch(self):
        ps1 = self.queue.insert_item(patch_id=1, message_id="msg1")
        ps2 = self.queue.insert_item(patch_id=2, message_id="msg2")
        ps3 = self.queue.insert_item(patch_id=3, message_id="msg3")
        self.assertEqual(self.queue.current_queue_item, ps1.pk)
        ni = self.queue.get_and_move()
        self.assertEqual(ni.pk, ps1.pk)
        self.assertIsNotNone(ni.processed_date)
        self.assertEqual(self.queue.current_queue_item, ps3.pk)
        self.queue.remove_item(ps3.pk)
        self.assertEqual(self.queue.current_queue_item, ps2.pk)
        ni = self.queue.get_and_move()
        self.assertEqual(self.queue.current_queue_item, ps1.pk)

    def test_leave_data_for_display(self):
        ps1 = self.queue.insert_item(patch_id=1, message_id="msg1")
        ps2 = self.queue.insert_item(patch_id=2, message_id="msg2")
        ps3 = self.queue.insert_item(patch_id=3, message_id="msg3")
        ps4 = self.queue.insert_item(patch_id=4, message_id="msg4")
        ps5 = self.queue.insert_item(patch_id=5, message_id="msg5")
        self.queue.get_and_move()
        self.queue.get_and_move()

if __name__ == "__main__":
    unittest.main()
