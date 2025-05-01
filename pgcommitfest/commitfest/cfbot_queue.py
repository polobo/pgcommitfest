from django.db import connection, models, transaction

from datetime import datetime


# Ring Queue (linked list, behavioral jump of point from end to start of queue)
# Pointer is always pointing to the next item to be processed so peeking works
# directly.
class CfbotQueue(models.Model):
    name = models.CharField(max_length=255, null=False, blank=False, unique=True)
    current_queue_item = models.IntegerField(null=True, blank=True)  # Integer property
    weight = models.IntegerField(
        default=1, editable=False
    )  # Default to 1, not editable

    def save(self, *args, **kwargs):
        if CfbotQueue.objects.exclude(pk=self.pk).exists():
            raise Exception("Only one CfbotQueue instance is allowed.")
        if self.weight != 1:
            raise ValueError("The weight field can only have a value of 1.")
        super(CfbotQueue, self).save(*args, **kwargs)

    def remove_item(self, item_id):
        """
        Remove the item with the given patch_id from the queue, splicing it out of the linked list.
        """
        # Defer constraints until the end of the transaction
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")

            # Find the item to remove
            item_to_remove = CfbotQueueItem.objects.filter(
                queue=self, id=item_id
            ).first()
            if not item_to_remove:
                raise ValueError(
                    f"Item with patch_id {item_id} not found in the queue."
                )

            # Update the previous item's ll_next to skip the item being removed
            if item_to_remove.ll_prev:
                prev_item = CfbotQueueItem.objects.filter(
                    id=item_to_remove.ll_prev
                ).first()
                if prev_item:
                    prev_item.ll_next = item_to_remove.ll_next
                    prev_item.save()

            # Update the next item's ll_prev to skip the item being removed
            if item_to_remove.ll_next:
                next_item = CfbotQueueItem.objects.filter(
                    id=item_to_remove.ll_next
                ).first()
                if next_item:
                    next_item.ll_prev = item_to_remove.ll_prev
                    next_item.save()

            # If the item being removed is the current queue item, update the queue's pointer
            # While the list is doubly linked usage is unidirectional, and always
            # points to the next item to be processed.
            if self.current_queue_item == item_to_remove.pk:
                if item_to_remove.ll_next:
                    self.current_queue_item = item_to_remove.ll_next
                else:
                    # we happen to be the end of the list, move the pointer to the start
                    first_item = self.get_first_item()
                    if first_item == item_to_remove:
                        # we are also the front, so set the pointer to None
                        self.current_queue_item = None
                    else:
                        self.current_queue_item = first_item.pk
                self.save()

            # Finally, delete the item
            item_to_remove.delete()

    def insert_item(self, patch_id, message_id):
        # Check if the item exists
        existing_patch = CfbotQueueItem.objects.filter(patch_id=patch_id)
        if len(existing_patch) == 1:
            if existing_patch[0].message_id == message_id:
                return existing_patch[0]
            else:
                # Remove and replace. New patch sets get a new start in the queue.
                self.remove_item(existing_patch[0].id)

        # Walk the queue to find the first item with a non-null processed_date
        first_item = self.get_first_item()
        loop_item = first_item
        current_item = None
        previous_item = None
        target_item = None
        while loop_item:
            if not self.current_queue_item:
                # can't happen; first_item and thus loop_item should be None
                break

            if loop_item.patch_id == patch_id:
                # can't happen; either early exit for same message_id or we were removed
                pass

            # Next up, cannot move pointer off of the current item, and no where to go
            if not loop_item.ll_next and not loop_item.ll_prev:
                target_item = loop_item
                break

            # This is basically a special case where the current_queue_item is
            # at the head of the list.  Our loop started on it, immediately set
            # current_item to loop_item, then we got all the way to the end of
            # the list, started over and found current_item.  But as the head
            # of the linked list its pointer ll_prev is None, so we needed to
            # remember or otherwise know what the end of the list is.
            #
            # Tail of the Ring Queue, No Not Processed Items
            if current_item and loop_item.id == self.current_queue_item:
                target_item = previous_item
                break

            # Likewise as the above, but we happend to find a real processed item
            # at the start of the linked list.  We code to add after the target
            # item so our target must be the end_of_list if ll_prev is None
            #
            # Last of the Processed Items (previous_item processed is None)
            if current_item and loop_item.processed_date is not None:
                target_item = previous_item
                break

            # Begin scanning for not processed items with the next item
            # since we cannot move the pointer off the current item anyway
            if not current_item and loop_item.id == self.current_queue_item:
                current_item = loop_item

            # Linked list ends, but queue is circular; capture the end just
            # in case the first list entry matches our search criteria.
            previous_item = loop_item
            if loop_item.ll_next is None:
                loop_item = first_item
            else:
                loop_item = self.items.filter(id=loop_item.ll_next).first()

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL DEFERRED")

                # Special case if the queue is empty
                if not self.current_queue_item:
                    new_item = CfbotQueueItem.objects.create(
                        queue=self,
                        patch_id=patch_id,
                        message_id=message_id,
                        ll_prev=None,
                        ll_next=None,
                    )
                    self.current_queue_item = new_item.id
                    self.save()
                    return new_item

                # Create the new item
                new_item = CfbotQueueItem.objects.create(
                    queue=self,
                    patch_id=patch_id,
                    message_id=message_id,
                    ll_prev=target_item.id,
                    # use 0 here and replace with none later to appease partial unique index
                    ll_next=target_item.ll_next if target_item.ll_next else 0,
                )

                # Update the links
                if target_item.ll_next:
                    next_queue_item = CfbotQueueItem.objects.get(id=target_item.ll_next)
                    next_queue_item.ll_prev = new_item.id
                    next_queue_item.save()

                target_item.ll_next = new_item.id
                target_item.save()

                if not self.current_queue_item:
                    self.current_queue_item = new_item.id
                    self.save()

                if new_item.ll_next == 0:
                    # If the new item is the last item, set its ll_next to None
                    new_item.ll_next = None
                    new_item.save()

                return new_item

    def get_and_move(self):
        """
        Move the current_queue_item to the next linked list ID, wrapping back to the front if ll_next is None.
        """
        if not self.current_queue_item:
            return None, None  # No items in the queue

        current_item = CfbotQueueItem.objects.get(id=self.current_queue_item)
        if not current_item:
            raise ValueError("Current queue item does not exist.")

        if current_item.ll_next:
            self.current_queue_item = current_item.ll_next
        else:
            # Wrap back to the front of the queue
            first_item = self.get_first_item()
            self.current_queue_item = first_item.pk if first_item.pk else None

        next_item = CfbotQueueItem.objects.get(id=self.current_queue_item)

        self.save()

        # Update the processed_date of the returned item
        current_item.processed_date = datetime.now()
        current_item.save()

        # Skip ignored items
        if current_item.ignore_date:
            return self.get_and_move()
        else:
            return current_item, next_item

    def get_first_item(self):
        # Return the first item in the queue where ll_prev is None
        return self.items.filter(ll_prev__isnull=True).first()

    def peek(self):
        """
        Return the current queue item without moving the pointer.
        """
        if not self.current_queue_item:
            return None  # No items in the queue
        return CfbotQueueItem.objects.get(id=self.current_queue_item)

    def retrieve():
        return CfbotQueue.objects.first()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["weight"],
                condition=models.Q(id__isnull=False),
                name="unique_single_cfbot_queue",
            ),
            models.CheckConstraint(
                check=models.Q(weight=1), name="check_weight_equals_one"
            ),
        ]


class CfbotQueueItem(models.Model):
    queue = models.ForeignKey(
        CfbotQueue,
        null=False,
        blank=False,
        on_delete=models.CASCADE,
        related_name="items",
    )
    patch_id = models.IntegerField(null=False, blank=False)
    message_id = models.TextField(null=False, blank=False)
    ignore_date = models.DateTimeField(null=True, blank=True)
    processed_date = models.DateTimeField(null=True, blank=True)
    ll_prev = models.IntegerField(null=True, blank=False)
    ll_next = models.IntegerField(null=True, blank=False)
    last_base_commit_sha = models.TextField(null=True, blank=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["patch_id"], name="unique_patch_id"),
        ]
