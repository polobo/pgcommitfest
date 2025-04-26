import os
import shutil
from django.conf import settings
import requests
from django.contrib.auth.models import User
from django.db import models
from django.db import transaction, connection
from django.db.models import Q
from django.shortcuts import get_object_or_404

from datetime import datetime
import json
import subprocess
import re
import threading

from pgcommitfest.userprofile.models import UserProfile

from .util import DiffableModel, datetime_serializer

# We have few enough of these, and it's really the only thing we
# need to extend from the user model, so just create a separate
# class.
class Committer(models.Model):
    user = models.OneToOneField(
        User, null=False, blank=False, primary_key=True, on_delete=models.CASCADE
    )
    active = models.BooleanField(null=False, blank=False, default=True)

    def __str__(self):
        return str(self.user)

    @property
    def fullname(self):
        return "%s %s (%s)" % (
            self.user.first_name,
            self.user.last_name,
            self.user.username,
        )

    class Meta:
        ordering = ("user__last_name", "user__first_name")


class CommitFest(models.Model):
    STATUS_FUTURE = 1
    STATUS_OPEN = 2
    STATUS_INPROGRESS = 3
    STATUS_CLOSED = 4
    STATUS_PARKED = 5
    _STATUS_CHOICES = (
        (STATUS_FUTURE, "Future"),
        (STATUS_OPEN, "Open"),
        (STATUS_INPROGRESS, "In Progress"),
        (STATUS_CLOSED, "Closed"),
        (STATUS_PARKED, "Drafts"),
    )
    _STATUS_LABELS = (
        (STATUS_FUTURE, "default"),
        (STATUS_OPEN, "info"),
        (STATUS_INPROGRESS, "success"),
        (STATUS_CLOSED, "danger"),
        (STATUS_PARKED, "default"),
    )
    name = models.CharField(max_length=100, blank=False, null=False, unique=True)
    status = models.IntegerField(
        null=False, blank=False, default=1, choices=_STATUS_CHOICES
    )
    startdate = models.DateField(blank=True, null=True)
    enddate = models.DateField(blank=True, null=True)

    @property
    def statusstring(self):
        return [v for k, v in self._STATUS_CHOICES if k == self.status][0]

    @property
    def periodstring(self):
        # Current Workflow intent is to have all Committfest be time-bounded
        # but the information is just contextual so we still permit null
        if self.startdate and self.enddate:
            return "{0} - {1}".format(self.startdate, self.enddate)
        return ""

    @property
    def title(self):
        return "Commitfest %s" % self.name

    @property
    def isclosed(self):
        return self.status == self.STATUS_CLOSED

    @property
    def isopen(self):
        return self.status == self.STATUS_OPEN

    @property
    def isinprogress(self):
        return self.status == self.STATUS_INPROGRESS

    @property
    def isparked(self):
        return self.status == self.STATUS_PARKED

    def json(self):
        return {
            "id": self.id,
            "name": self.name,
            "status": self.statusstring,
            "startdate": self.startdate.isoformat(),
            "enddate": self.enddate.isoformat(),
        }

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Commitfests"
        ordering = ("-startdate",)


class Topic(models.Model):
    topic = models.CharField(max_length=100, blank=False, null=False)

    def __str__(self):
        return self.topic


class TargetVersion(models.Model):
    version = models.CharField(max_length=8, blank=False, null=False, unique=True)

    class Meta:
        ordering = [
            "-version",
        ]

    def __str__(self):
        return self.version


class Patch(models.Model, DiffableModel):
    name = models.CharField(
        max_length=500, blank=False, null=False, verbose_name="Description"
    )
    topic = models.ForeignKey(Topic, blank=False, null=False, on_delete=models.CASCADE)

    # One patch can be in multiple commitfests, if it has history
    commitfests = models.ManyToManyField(CommitFest, through="PatchOnCommitFest")

    # If there is a wiki page discussing this patch
    wikilink = models.URLField(blank=True, null=False, default="")

    # If there is a git repo about this patch
    gitlink = models.URLField(blank=True, null=False, default="")

    # Version targeted by this patch
    targetversion = models.ForeignKey(
        TargetVersion,
        blank=True,
        null=True,
        verbose_name="Target version",
        on_delete=models.CASCADE,
    )

    authors = models.ManyToManyField(User, related_name="patch_author", blank=True)
    reviewers = models.ManyToManyField(User, related_name="patch_reviewer", blank=True)

    committer = models.ForeignKey(
        Committer, blank=True, null=True, on_delete=models.CASCADE
    )

    # Users to be notified when something happens
    subscribers = models.ManyToManyField(
        User, related_name="patch_subscriber", blank=True
    )

    mailthread_set = models.ManyToManyField(
        "MailThread",
        related_name="patches",
        blank=False,
        db_table="commitfest_mailthread_patches",
    )

    # Datestamps for tracking activity
    created = models.DateTimeField(blank=False, null=False, auto_now_add=True)
    modified = models.DateTimeField(blank=False, null=False, auto_now_add=True)

    # Materialize the last time an email was sent on any of the threads
    # that's attached to this message.
    lastmail = models.DateTimeField(blank=True, null=True)

    # Pointer to the threadid/messageid containing the most recent patchset
    patchset_messageid = models.CharField(
        max_length=1000, blank=False, null=True, db_index=False
    )
    # Cache the timestamp of the patchset message to easily determine
    # whether a new patchset message should replace it.
    patchset_messagedate = models.DateTimeField(blank=True, null=True)

    map_manytomany_for_diff = {
        "authors": "authors_string",
        "reviewers": "reviewers_string",
    }

    def current_commitfest(self):
        return self.current_patch_on_commitfest().commitfest

    def current_patch_on_commitfest(self):
        # The unique partial index poc_enforce_maxoneoutcome_idx stores the PoC
        # No caching here (inside the instance) since the caller should just need
        # the PoC once per request.
        return get_object_or_404(
            PatchOnCommitFest, Q(patch=self) & ~Q(status=PatchOnCommitFest.STATUS_NEXT)
        )

    # Some accessors
    @property
    def authors_string(self):
        return ", ".join(
            [
                "%s %s (%s)" % (a.first_name, a.last_name, a.username)
                for a in self.authors.all()
            ]
        )

    @property
    def reviewers_string(self):
        return ", ".join(
            [
                "%s %s (%s)" % (a.first_name, a.last_name, a.username)
                for a in self.reviewers.all()
            ]
        )

    @property
    def history(self):
        # Need to wrap this in a function to make sure it calls
        # select_related() and doesn't generate a bazillion queries
        return self.patchhistory_set.select_related("by").all()

    def set_modified(self, newmod=None):
        # Set the modified date to newmod, but only if that's newer than
        # what's currently set. If newmod is not specified, use the
        # current timestamp.
        if not newmod:
            newmod = datetime.now()
        if not self.modified or newmod > self.modified:
            self.modified = newmod

    def update_lastmail(self):
        # Update the lastmail field, based on the newest email in any of
        # the threads attached to it.
        threads = list(self.mailthread_set.all())
        if len(threads) == 0:
            self.lastmail = None
        else:
            self.lastmail = max(threads, key=lambda t: t.latestmessage).latestmessage
    # XXX: make messageid an optional input to return a non-current patchset or to
    # facilitate confirmation that the current patch is the one being worked on...
    def get_attachments(self):
        """
        Return the actual attachments for the patch.
        """
        return [
            {
                "attachmentid": attachment.attachmentid,
                "filename": attachment.filename,
                "contenttype": attachment.contenttype,
                "ispatch": attachment.ispatch,
                "author": attachment.author,
                "date": attachment.date,
            }
            for attachment in MailThreadAttachment.objects.filter(
                mailthread__patches=self,
                messageid=self.patchset_messageid
            )
        ]

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "patches"


class PatchOnCommitFest(models.Model):
    # NOTE! This is also matched by the commitfest_patchstatus table,
    # but we hardcoded it in here simply for performance reasons since
    # the data should be entirely static. (Yes, that's something we
    # might re-evaluate in the future)
    STATUS_REVIEW = 1
    STATUS_AUTHOR = 2
    STATUS_COMMITTER = 3
    STATUS_COMMITTED = 4
    STATUS_NEXT = 5
    STATUS_REJECTED = 6
    STATUS_RETURNED = 7
    STATUS_WITHDRAWN = 8
    _STATUS_CHOICES = (
        (STATUS_REVIEW, "Needs review"),
        (STATUS_AUTHOR, "Waiting on Author"),
        (STATUS_COMMITTER, "Ready for Committer"),
        (STATUS_COMMITTED, "Committed"),
        (STATUS_NEXT, "Moved to next CF"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_RETURNED, "Returned with feedback"),
        (STATUS_WITHDRAWN, "Withdrawn"),
    )
    _STATUS_LABELS = (
        (STATUS_REVIEW, "default"),
        (STATUS_AUTHOR, "primary"),
        (STATUS_COMMITTER, "info"),
        (STATUS_COMMITTED, "success"),
        (STATUS_NEXT, "warning"),
        (STATUS_REJECTED, "danger"),
        (STATUS_RETURNED, "danger"),
        (STATUS_WITHDRAWN, "danger"),
    )
    OPEN_STATUSES = [STATUS_REVIEW, STATUS_AUTHOR, STATUS_COMMITTER]

    @classmethod
    def OPEN_STATUS_CHOICES(cls):
        return [x for x in cls._STATUS_CHOICES if x[0] in cls.OPEN_STATUSES]

    patch = models.ForeignKey(Patch, blank=False, null=False, on_delete=models.CASCADE)
    commitfest = models.ForeignKey(
        CommitFest, blank=False, null=False, on_delete=models.CASCADE
    )
    enterdate = models.DateTimeField(blank=False, null=False)
    leavedate = models.DateTimeField(blank=True, null=True)

    status = models.IntegerField(
        blank=False, null=False, default=STATUS_REVIEW, choices=_STATUS_CHOICES
    )

    @property
    def is_closed(self):
        return self.status not in self.OPEN_STATUSES

    @property
    def is_open(self):
        return not self.is_closed

    @property
    def is_committed(self):
        return self.status == self.STATUS_COMMITTED

    @property
    def needs_committer(self):
        return self.status == self.STATUS_COMMITTER

    @property
    def statusstring(self):
        return [v for k, v in self._STATUS_CHOICES if k == self.status][0]

    class Meta:
        unique_together = (
            (
                "patch",
                "commitfest",
            ),
        )
        ordering = ("-commitfest__startdate",)


class PatchHistory(models.Model):
    patch = models.ForeignKey(Patch, blank=False, null=False, on_delete=models.CASCADE)
    date = models.DateTimeField(
        blank=False, null=False, auto_now_add=True, db_index=True
    )
    by = models.ForeignKey(User, blank=True, null=True, on_delete=models.CASCADE)
    by_cfbot = models.BooleanField(null=False, blank=False, default=False)
    what = models.CharField(max_length=500, null=False, blank=False)

    @property
    def by_string(self):
        if self.by_cfbot:
            return "CFbot"

        return "%s %s (%s)" % (self.by.first_name, self.by.last_name, self.by.username)

    def __str__(self):
        return "%s - %s" % (self.patch.name, self.date)

    class Meta:
        ordering = ("-date",)
        constraints = [
            models.CheckConstraint(
                check=(models.Q(by_cfbot=True) & models.Q(by__isnull=True))
                | (models.Q(by_cfbot=False) & models.Q(by__isnull=False)),
                name="check_by",
            ),
        ]

    def save_and_notify(
        self,
        prevcommitter=None,
        prevreviewers=None,
        prevauthors=None,
        authors_only=False,
    ):
        # Save this model, and then trigger notifications if there are any. There are
        # many different things that can trigger notifications, so try them all.
        self.save()

        recipients = []
        if not authors_only:
            recipients.extend(self.patch.subscribers.all())

            # Current or previous committer wants all notifications
            try:
                if (
                    self.patch.committer
                    and self.patch.committer.user.userprofile.notify_all_committer
                ):
                    recipients.append(self.patch.committer.user)
            except UserProfile.DoesNotExist:
                pass

            try:
                if (
                    prevcommitter
                    and prevcommitter.user.userprofile.notify_all_committer
                ):
                    recipients.append(prevcommitter.user)
            except UserProfile.DoesNotExist:
                pass

            # Current or previous reviewers wants all notifications
            recipients.extend(
                self.patch.reviewers.filter(userprofile__notify_all_reviewer=True)
            )
            if prevreviewers:
                # prevreviewers is a list
                recipients.extend(
                    User.objects.filter(
                        id__in=[p.id for p in prevreviewers],
                        userprofile__notify_all_reviewer=True,
                    )
                )

        # Current or previous authors wants all notifications
        recipients.extend(
            self.patch.authors.filter(userprofile__notify_all_author=True)
        )

        for u in set(recipients):
            if u != self.by:  # Don't notify for changes we make ourselves
                PendingNotification(history=self, user=u).save()


class MailThread(models.Model):
    # This class tracks mail threads from the main postgresql.org
    # mailinglist archives. For each thread, we store *one* messageid.
    # Using this messageid we can always query the archives for more
    # detailed information, which is done dynamically as the page
    # is loaded.
    # For threads in an active or future commitfest, we also poll
    # the archives to fetch "updated entries" at (ir)regular intervals
    # so we can keep track of when there was last a change on the
    # thread in question.
    messageid = models.CharField(max_length=1000, null=False, blank=False, unique=True)
    subject = models.CharField(max_length=500, null=False, blank=False)
    firstmessage = models.DateTimeField(null=False, blank=False)
    firstauthor = models.CharField(max_length=500, null=False, blank=False)
    latestmessage = models.DateTimeField(null=False, blank=False)
    latestauthor = models.CharField(max_length=500, null=False, blank=False)
    latestsubject = models.CharField(max_length=500, null=False, blank=False)
    latestmsgid = models.CharField(max_length=1000, null=False, blank=False)
    patchsetmsgid = models.CharField(max_length=1000, null=True, blank=False)

    def most_recent_patch_message_attachments(self):
        """Retrieve attachments for the most recent message, with patches, in the thread."""
        attachments = self.mailthreadattachment_set.order_by('-date', '-messageid', 'ispatch', 'filename')
        most_recent_messageid = None
        most_recent_attachments = []

        for attachment in attachments:
            if most_recent_messageid is None:
                most_recent_messageid = attachment.messageid
            if attachment.messageid == most_recent_messageid:
                most_recent_attachments.append(attachment)
            else:
                if not any(attachment.ispatch for attachment in most_recent_attachments):
                    most_recent_attachments.clear()
                    most_recent_messageid = attachment.messageid
                    most_recent_attachments.append(attachment)
                else:
                    break

        if not any(attachment.ispatch for attachment in most_recent_attachments):
            most_recent_attachments.clear()

        return most_recent_attachments

    def __str__(self):
        return self.subject

    class Meta:
        ordering = ("firstmessage",)


class MailThreadAttachment(models.Model):
    mailthread = models.ForeignKey(
        MailThread, null=False, blank=False, on_delete=models.CASCADE
    )
    messageid = models.CharField(max_length=1000, null=False, blank=False)
    attachmentid = models.IntegerField(null=False, blank=False)
    filename = models.CharField(max_length=1000, null=False, blank=True)
    date = models.DateTimeField(null=False, blank=False)
    author = models.CharField(max_length=500, null=False, blank=False)
    ispatch = models.BooleanField(null=True)
    contenttype = models.CharField(max_length=1000, null=True, blank=False)

    class Meta:
        ordering = ("-date", "messageid", "ispatch", "filename", "attachmentid")
        unique_together = (
            (
                "mailthread",
                "messageid",
                "attachmentid",
            ),
        )


class MailThreadAnnotation(models.Model):
    mailthread = models.ForeignKey(
        MailThread, null=False, blank=False, on_delete=models.CASCADE
    )
    date = models.DateTimeField(null=False, blank=False, auto_now_add=True)
    user = models.ForeignKey(User, null=False, blank=False, on_delete=models.CASCADE)
    msgid = models.CharField(max_length=1000, null=False, blank=False)
    annotationtext = models.TextField(null=False, blank=False, max_length=2000)
    mailsubject = models.CharField(max_length=500, null=False, blank=False)
    maildate = models.DateTimeField(null=False, blank=False)
    mailauthor = models.CharField(max_length=500, null=False, blank=False)

    @property
    def user_string(self):
        return "%s %s (%s)" % (
            self.user.first_name,
            self.user.last_name,
            self.user.username,
        )

    class Meta:
        ordering = ("date",)


class PatchStatus(models.Model):
    status = models.IntegerField(null=False, blank=False, primary_key=True)
    statusstring = models.TextField(max_length=50, null=False, blank=False)
    sortkey = models.IntegerField(null=False, blank=False, default=10)


class PendingNotification(models.Model):
    history = models.ForeignKey(
        PatchHistory, blank=False, null=False, on_delete=models.CASCADE
    )
    user = models.ForeignKey(User, blank=False, null=False, on_delete=models.CASCADE)


# Ring Queue (linked list, behavioral jump of point from end to start of queue)
# Pointer is always pointing to the next item to be processed so peeking works
# directly.
class CfbotQueue(models.Model):
    name = models.CharField(max_length=255, null=False, blank=False, unique=True)
    current_queue_item = models.IntegerField(null=True, blank=True)  # Integer property
    weight = models.IntegerField(default=1, editable=False)  # Default to 1, not editable

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
            item_to_remove = CfbotQueueItem.objects.filter(queue=self, id=item_id).first()
            if not item_to_remove:
                raise ValueError(f"Item with patch_id {item_id} not found in the queue.")

            # Update the previous item's ll_next to skip the item being removed
            if item_to_remove.ll_prev:
                prev_item = CfbotQueueItem.objects.filter(id=item_to_remove.ll_prev).first()
                if prev_item:
                    prev_item.ll_next = item_to_remove.ll_next
                    prev_item.save()

            # Update the next item's ll_prev to skip the item being removed
            if item_to_remove.ll_next:
                next_item = CfbotQueueItem.objects.filter(id=item_to_remove.ll_next).first()
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
        end_of_list = None
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
                        ll_next=None
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
                    ll_next=target_item.ll_next if target_item.ll_next else 0
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
                name="unique_single_cfbot_queue"
            ),
            models.CheckConstraint(
                check=models.Q(weight=1),
                name="check_weight_equals_one"
            )
        ]


class CfbotQueueItem(models.Model):
    queue = models.ForeignKey(
        CfbotQueue, null=False, blank=False, on_delete=models.CASCADE, related_name="items"
    )
    patch_id = models.IntegerField(null=False, blank=False)
    message_id = models.TextField(null=False, blank=False)
    ignore_date = models.DateTimeField(null=True, blank=True)
    processed_date = models.DateTimeField(null=True, blank=True)
    ll_prev = models.IntegerField(null=True, blank=False)
    ll_next = models.IntegerField(null=True, blank=False)
    last_base_commit_sha = models.TextField(null=True, blank=False)

    def generaterowhtml(self):
        """
        Generate HTML for a row, truncating last_base_commit_sha to the first 10 characters.
        """
        return f"<tr><td>{self.patch_id}</td><td>{self.message_id}</td><td>{self.last_base_commit_sha[:10] if self.last_base_commit_sha else ''}</td></tr>"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["queue"],
                name="unique_last_item"
            ),
            models.UniqueConstraint(fields=["patch_id"], name="unique_patch_id")
        ]


class CfbotBranch(models.Model):
    STATUS_CHOICES = [
        ("new", "New"),
        ("testing", "Testing"),
        ("finished", "Finished"),
        ("failed", "Failed"),
        ("timeout", "Timeout"),
    ]

    patch = models.OneToOneField(
        Patch, on_delete=models.CASCADE, related_name="cfbot_branch", primary_key=True
    )
    branch_id = models.IntegerField(null=False)
    branch_name = models.TextField(null=False)
    commit_id = models.TextField(null=True, blank=True)
    apply_url = models.TextField(null=False)
    # Actually a postgres enum column
    status = models.TextField(null=False, blank=False)
    needs_rebase_since = models.DateTimeField(null=True, blank=True)
    failing_since = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    version = models.TextField(null=True, blank=True)
    patch_count = models.IntegerField(null=True, blank=True)
    first_additions = models.IntegerField(null=True, blank=True)
    first_deletions = models.IntegerField(null=True, blank=True)
    all_additions = models.IntegerField(null=True, blank=True)
    all_deletions = models.IntegerField(null=True, blank=True)
    base_commit_sha = models.TextField(null=True, blank=False)


    def save(self, *args, **kwargs):
        """Only used by the admin panel to save empty commit id as NULL

        The actual cfbot webhook doesn't use the django ORM to save the data.
        """

        if not self.commit_id:
            self.commit_id = None
        super(CfbotBranch, self).save(*args, **kwargs)


class CfbotBranchHistory(models.Model):
    id = models.BigAutoField(primary_key=True)  # Auto-numbered primary key
    patch_id = models.IntegerField(null=False)
    branch_id = models.IntegerField(null=False)
    branch_name = models.TextField(null=False)
    commit_id = models.TextField(null=True, blank=True)
    apply_url = models.TextField(null=False)
    status = models.TextField(null=False)
    needs_rebase_since = models.DateTimeField(null=True, blank=True)
    failing_since = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField()
    modified = models.DateTimeField()
    version = models.TextField(null=True, blank=True)
    patch_count = models.IntegerField(null=True, blank=True)
    first_additions = models.IntegerField(null=True, blank=True)
    first_deletions = models.IntegerField(null=True, blank=True)
    all_additions = models.IntegerField(null=True, blank=True)
    all_deletions = models.IntegerField(null=True, blank=True)
    task_count = models.IntegerField(null=True, blank=True)
    base_commit_sha = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"Branch History for Patch ID {self.patch_id}, Branch ID {self.branch_id}"

    def add_branch_to_history(history_branch):

        cached_tasks = CfbotTask.objects.filter(branch_id=history_branch.branch_id)
        # Record all processing that happens in the history, even no-ops
        history = CfbotBranchHistory.objects.create(
            patch_id=history_branch.patch_id,
            branch_id=history_branch.branch_id,
            branch_name=history_branch.branch_name,
            commit_id=history_branch.commit_id,
            apply_url=history_branch.apply_url,
            status=history_branch.status,
            needs_rebase_since=history_branch.needs_rebase_since,
            failing_since=history_branch.failing_since,
            created=history_branch.created,
            modified=history_branch.modified,
            version=history_branch.version,
            patch_count=history_branch.patch_count,
            first_additions=history_branch.first_additions,
            first_deletions=history_branch.first_deletions,
            all_additions=history_branch.all_additions,
            all_deletions=history_branch.all_deletions,
            base_commit_sha=history_branch.base_commit_sha,
            task_count=len(cached_tasks),
        )
        taskarr = [{
                    "task_id": task.task_id,
                    "task_name": task.task_name,
                    "status": task.status,
                    "created": task.created.isoformat(),
                    "modified": task.modified.isoformat(),
                    "payload": task.payload,
                   } for task in cached_tasks]

        # Insert tasks into cfbotbranchhistorytask
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO commitfest_cfbotbranchhistorytask (history_id, branch_tasks)
                VALUES (%s, %s)
                """,
                [history.id, json.dumps(taskarr, default=datetime_serializer)]
            )

        return history

    class Meta:
        verbose_name_plural = "Cfbot Branch Histories"
        ordering = ("-modified",)


class CfbotTask(models.Model):
    STATUS_CHOICES = [
        ("CREATED", "Created"),
        ("NEEDS_APPROVAL", "Needs Approval"),
        ("TRIGGERED", "Triggered"),
        ("EXECUTING", "Executing"),
        ("FAILED", "Failed"),
        ("COMPLETED", "Completed"),
        ("SCHEDULED", "Scheduled"),
        ("ABORTED", "Aborted"),
        ("ERRORED", "Errored"),
        ("IGNORED", "Ignored"),
    ]

    # This id is only used by Django. Using text type for primary keys, has
    # historically caused problems.
    id = models.BigAutoField(primary_key=True)
    # This is the id used by the external CI system. Currently with CirrusCI
    # this is an integer, and thus we could probably store it as such. But
    # given that we might need to change CI providers at some point, and that
    # CI provider might use e.g. UUIDs, we prefer to consider the format of the
    # ID opaque and store it as text.
    # XXX: really want to scope this ID to either a specific platform or even
    # just the branch_id if a platform is not applicable.  This is being used
    # for more than just CirrusCI at this point.
    task_id = models.TextField(unique=False)
    task_name = models.TextField(null=False)
    patch = models.ForeignKey(
        Patch, on_delete=models.CASCADE, related_name="cfbot_tasks"
    )
    branch_id = models.IntegerField(null=False)
    position = models.IntegerField(null=False)
    # Actually a postgres enum column
    status = models.TextField(choices=STATUS_CHOICES, null=False)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    payload = models.JSONField(null=True, blank=True)

    def is_done(self):
        """
        Check if the task is in a terminal state.
        """
        return self.status in {"FAILED", "COMPLETED", "ABORTED", "ERRORED"}

    def is_failure(self):
        """
        Check if the task is in a failure state.
        """
        return self.status in {"ABORTED", "ERRORED", "FAILED"}

class CfbotTaskCommand(models.Model):
    task = models.ForeignKey(
        CfbotTask, null=False, blank=False, on_delete=models.CASCADE
    )
    name = models.TextField(null=False)
    status = models.TextField(null=False)
    type = models.TextField(null=False)
    duration = models.IntegerField(null=True)
    log = models.TextField(null=True, blank=True)
    payload = models.JSONField(null=True, blank=True)

class CfbotTaskArtifact(models.Model):
    task = models.ForeignKey(
        CfbotTask, null=False, blank=False, on_delete=models.CASCADE
    )
    name = models.TextField(null=False)
    path = models.TextField(null=False)
    size = models.IntegerField(null=False)
    body = models.TextField(null=True, blank=True)
    payload = models.JSONField(null=True, blank=True)

# Workflow provides access to the elements required to support
# the workflow this application is built for.  These elements exist
# independent of what the user is presently seeing on their page.
class Workflow(models.Model):
    def get_poc_for_patchid_or_404(patchid):
        return get_object_or_404(
            Patch.objects.select_related(), pk=patchid
        ).current_patch_on_commitfest()

    # At most a single Open CommitFest is allowed and this function returns it.
    def open_cf():
        cfs = list(CommitFest.objects.filter(status=CommitFest.STATUS_OPEN))
        return cfs[0] if len(cfs) == 1 else None

    # At most a single In Progress CommitFest is allowed and this function returns it.
    def inprogress_cf():
        cfs = list(CommitFest.objects.filter(status=CommitFest.STATUS_INPROGRESS))
        return cfs[0] if len(cfs) == 1 else None

    # At most a single Parked CommitFest is allowed and this function returns it.
    def parked_cf():
        cfs = list(CommitFest.objects.filter(status=CommitFest.STATUS_PARKED))
        return cfs[0] if len(cfs) == 1 else None

    # Returns whether the user is a committer in general and for this patch
    # since we retrieve all committers in order to answer these questions
    # provide that list as a third return value.  Passing None for both user
    # and patch still returns the list of committers.
    def isCommitter(user, patch):
        all_committers = Committer.objects.filter(active=True).order_by(
            "user__last_name", "user__first_name"
        )
        if not user and not patch:
            return False, False, all_committers

        committer = [c for c in all_committers if c.user == user]
        if len(committer) == 1:
            is_committer = True
            is_this_committer = committer[0] == patch.committer
        else:
            is_committer = is_this_committer = False
        return is_committer, is_this_committer, all_committers

    def getCommitfest(cfid):
        if cfid is None or cfid == "":
            return None
        try:
            int_cfid = int(cfid)
            cfs = list(CommitFest.objects.filter(id=int_cfid))
            if len(cfs) == 1:
                return cfs[0]
            else:
                return None
        except ValueError:
            return None

    # Implements a re-entrant Commitfest POC creation procedure.
    # Returns the new POC object.
    # Creates history and notifies as a side-effect.
    def createNewPOC(patch, commitfest, initial_status, by_user):
        poc, created = PatchOnCommitFest.objects.update_or_create(
            patch=patch,
            commitfest=commitfest,
            defaults=dict(
                enterdate=datetime.now(),
                status=initial_status,
                leavedate=None,
            ),
        )
        poc.patch.set_modified()
        poc.patch.save()
        poc.save()

        PatchHistory(
            patch=poc.patch,
            by=by_user,
            what="{} in {}".format(poc.statusstring, commitfest.name),
        ).save_and_notify()

        return poc

    # The rule surrounding patches is they may only be in one active
    # commitfest at a time.  The transition function takes a patch
    # open in one commitfest and associates it, with the same status,
    # in a new commitfest; then makes it inactive in the original.
    # Returns the new POC object.
    # Creates history and notifies as a side-effect.
    def transitionPatch(poc, target_cf, by_user):
        Workflow.userCanTransitionPatch(poc, target_cf, by_user)

        existing_status = poc.status

        # History looks cleaner if we've left the existing
        # commitfest entry before joining the new one.  Plus,
        # not allowed to change non-current commitfest status
        # and once the new POC is created it becomes current.

        Workflow.updatePOCStatus(poc, PatchOnCommitFest.STATUS_NEXT, by_user)

        new_poc = Workflow.createNewPOC(poc.patch, target_cf, existing_status, by_user)

        return new_poc

    def userCanTransitionPatch(poc, target_cf, user):
        # Policies not allowed to be broken by anyone.

        # Prevent changes to non-current commitfest for the patch
        # Meaning, status changed to Moved before/during transitioning
        # i.e., a concurrent action took place.
        if poc.commitfest != poc.patch.current_commitfest():
            raise Exception("Patch commitfest is not its current commitfest.")

        # The UI should be preventing people from trying to perform no-op requests
        if poc.commitfest.id == target_cf.id:
            raise Exception("Cannot transition to the same commitfest.")

        # This one is arguable but facilitates treating non-open status as final
        # A determined staff member can always change the status first.
        if poc.is_closed:
            raise Exception("Cannot transition a closed patch.")

        # We trust privileged users to make informed choices
        if user.is_staff:
            return

        if target_cf.isclosed:
            raise Exception("Cannot transition to a closed commitfest.")

        if target_cf.isinprogress:
            raise Exception("Cannot transition to an in-progress commitfest.")

        # Prevent users from moving closed patches, or moving open ones to
        # non-open commitfests.  The else clause should be a can't happen.
        if poc.is_open and target_cf.isopen:
            pass
        else:
            # Default deny policy basis
            raise Exception("Transition not permitted.")

    def userCanChangePOCStatus(poc, new_status, user):
        # Policies not allowed to be broken by anyone.

        # Prevent changes to non-current commitfest for the patch
        # Meaning, change status to Moved before/during transitioning
        if poc.commitfest != poc.patch.current_commitfest():
            raise Exception("Patch commitfest is not its current commitfest.")

        # The UI should be preventing people from trying to perform no-op requests
        if poc.status == new_status:
            raise Exception("Cannot change to the same status.")

        # We want commits to happen from, usually, In Progress commitfests,
        # or Open ones for exempt patches.  We accept Future ones too just because
        # they do represent a proper, if non-current, Commitfest.
        if (
            poc.commitfest.id == CommitFest.STATUS_PARKED
            and new_status == PatchOnCommitFest.STATUS_COMMITTED
        ):
            raise Exception("Cannot change status to committed in a parked commitfest.")

        # We trust privileged users to make informed choices
        if user.is_staff:
            return

        is_committer, is_this_committer, all_committers = Workflow.isCommitter(
            user, poc.patch
        )

        # XXX Not sure if we want to tighten this up to is_this_committer
        # with only the is_staff exemption
        if new_status == PatchOnCommitFest.STATUS_COMMITTED and not is_committer:
            raise Exception("Only a committer can set status to committed.")

        if new_status == PatchOnCommitFest.STATUS_REJECTED and not is_committer:
            raise Exception("Only a committer can set status to rejected.")

        if new_status == PatchOnCommitFest.STATUS_RETURNED and not is_committer:
            raise Exception("Only a committer can set status to returned.")

        if (
            new_status == PatchOnCommitFest.STATUS_WITHDRAWN
            and user not in poc.patch.authors.all()
        ):
            raise Exception("Only the author can set status to withdrawn.")

        # Prevent users from modifying closed patches
        # The else clause should be considered a can't happen
        if poc.is_open:
            pass
        else:
            raise Exception("Cannot change status of closed patch.")

    # Update the status of a PoC
    # Returns True if the status was changed, False for a same-status no-op.
    # Creates history and notifies as a side-effect.
    def updatePOCStatus(poc, new_status, by_user):
        # XXX Workflow disallows this no-op but not quite ready to enforce it.
        if poc.status == new_status:
            return False

        Workflow.userCanChangePOCStatus(poc, new_status, by_user)

        poc.status = new_status
        poc.leavedate = datetime.now() if not poc.is_open else None
        poc.patch.set_modified()
        poc.patch.save()
        poc.save()
        PatchHistory(
            patch=poc.patch,
            by=by_user,
            what="{} in {}".format(
                poc.statusstring,
                poc.commitfest.name,
            ),
        ).save_and_notify()

        return True

    @classmethod
    def getBranchManager(cls):
        """
        Retrieve an instance of BranchManager.
        """
        patchApplier = getLocalPatchApplier()
        patchBurner = getLocalPatchCompiler()
        patchTester = getLocalPatchTester()
        notifier = getNotifier()
        return BranchManager(patchApplier, patchBurner, patchTester, notifier)

    @staticmethod
    def createBranch(patch_id, message_id):
        if not patch_id or not message_id:
            raise ValueError("Patch ID and Message ID are required.")

        # Create a new branch using CfbotBranch
        branch_name = f"branch_{patch_id}"
        apply_url = f"http://example.com/apply/{patch_id}"
        status = "new"

        # Get the corresponding queue item and use its get_attachments method
        queue = CfbotQueue.objects.first()
        if not queue:
            raise ValueError("No queue found.")

        queue_item = queue.items.filter(patch_id=patch_id).first()
        if not queue_item:
            raise ValueError(f"No queue item found for patch ID {patch_id}.")

        branch, created = CfbotBranch.objects.update_or_create(
            patch_id=patch_id,
            defaults={
                "branch_id": patch_id,  # Using patch_id as branch_id for simplicity
                "branch_name": branch_name,
                "apply_url": apply_url,
                "status": status,
                "created": datetime.now(),
                "modified": datetime.now(),
            },
        )

        queue_item.processed_date = datetime.now()
        queue_item.save()

        # Create a history item for the branch
        if created:
            CfbotBranchHistory.add_branch_to_history(branch)

    def processBranch(branch, branchManager = None):
        if not branchManager:
            branchManager = Workflow.getBranchManager()
        return branchManager.process(branch)

class BranchManager:
    """
    A class to manage branch operations.
    """

    def __init__(self, applier, burner, tester, notifier):
        """
        Initialize the BranchManager with burner, tester, and notifier instances.
        """
        self.applier = applier
        self.burner = burner
        self.tester = tester
        self.notifier = notifier

    def clear_tasks(self, branch):
        """
        Clear all tasks associated with the given branch.
        """
        tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
        for task in tasks:
            task.delete()

    # try to make idiomatic usage
    # self.action.begin / failure to begin is action-aborted
    # self.action.is_done / success is "past tense" (applied, compiled, tested)
    # self.action.did_fail / failure is action-failed
    # need to pass in PatchApplier too then
    # Returns delay_for - the number of "seconds?" to wait before attempting the next
    # processing action on this branch.  0 means no delay needed.  Typically non-zero
    # only if the active action replies it is not done yet.  None means re-processing
    # is not required - the branch is at an end state, either finished or aborted/failed
    def process(self, branch):
        if not branch:
            raise ValueError("Branch cannot be None.")

        delay_for = 0
        """
        Process the given branch by creating a new branch instance with updated status.
        The input branch remains unaltered.
        """
        print(branch)
        old_branch = self.cloneBranch(branch)
        if old_branch.status == "new":
            # Intentional non-clearing of tasks here.
            # We should fail to begin, and thus abort,
            # if tasks already exist.
            if self.applier.begin(branch):
                branch.status = "applying"
            else:
                # envrionmental issues, up to the point of retrieving files
                # returns just before the step of running apply-patches.sh
                # also aborts if there happen to be no patches recognized
                # in the task queue
                branch.status = "applying-aborted"
                delay_for = None

        elif old_branch.status == "applying":
            if self.applier.is_done(branch):
                if self.applier.did_fail(branch):
                    # XXX: true bit-rot
                    branch.status = "applying-failed"
                    delay_for = None

                else:
                    branch.status = "applied"
            else:
                delay_for = self.applier.get_delay(branch)

        elif old_branch.status == "applied":
            self.clear_tasks(branch)
            if self.burner.begin(branch):
                branch.status = "compiling"
            else:
                # envrionmental issues, up to the point of retrieving files
                # returns just before the step of running apply-patches.sh
                branch.status = "compiling-aborted"
                delay_for = None

        elif old_branch.status == "compiling":
            # Run apply-patches.sh and return.  We are sync right now
            # so this should never actually return False, which would
            # require async processing where we simply want to try again
            if self.burner.is_done(branch):
                if self.burner.did_fail(branch):
                    branch.status = "compiling-failed"
                    delay_for = None

                else:
                    branch.status = "compiled"
            else:
                delay_for = self.burner.get_delay(branch)

        elif old_branch.status == "compiled":
            self.clear_tasks(branch)
            if self.tester.begin(branch):
                branch.status = "testing"
            else:
                branch.status = "testing-aborted"
                delay_for = None

        elif old_branch.status == "testing":
            if self.tester.is_done(branch):
                if self.tester.did_fail(branch):
                    branch.status = "testing-failed"
                    delay_for = None
                else:
                    branch.status = "tested"
            else:
                delay_for = self.tester.get_delay(branch)

        elif old_branch.status == "tested":
            self.clear_tasks(branch)
            branch.status = "notifying"
            self.notifier.notify_branch_update(branch)
            self.notifier.notify_branch_tested(branch)
            branch.status = "finished"
            delay_for = None

        elif old_branch.status in {"finished", "applying-aborted", "applying-failed", "compiling-aborted", "compiling-failed", "testing-aborted", "testing-failed"}:
            # Didn't listen the first time be we don't enforce this
            delay_for = None

        else:
            raise ValueError(f"Unknown status: {old_branch.status}")

        self.notifier.notify_branch_update(branch)
        return branch, delay_for

    def cloneBranch(self, branch):
        """
        Clone the given branch and return a new instance.
        """
        return CfbotBranch(
            patch=branch.patch,
            branch_id=branch.branch_id,
            branch_name=branch.branch_name,
            commit_id=branch.commit_id,
            apply_url=branch.apply_url,
            status=branch.status,
            needs_rebase_since=branch.needs_rebase_since,
            failing_since=branch.failing_since,
            version=branch.version,
            patch_count=branch.patch_count,
            first_additions=branch.first_additions,
            first_deletions=branch.first_deletions,
            all_additions=branch.all_additions,
            all_deletions=branch.all_deletions,
            base_commit_sha=branch.base_commit_sha,
        )


class AbstractPatchApplier:
    """
    A class responsible for applying patches to branches.
    """
    def __init__(self, template_dir, working_dir, repo_dir):
        """
        Initialize the PatchApplier with directory paths.
        """
        self.template_dir = template_dir
        self.working_dir = working_dir
        self.repo_dir = repo_dir

    @transaction.atomic
    def begin(self, branch):
        """
        Apply the patchset to the branch.
        """
        # We go first and do not expect any tasks for us to handle.  We create the patchset file tasks.
        existing_tasks = CfbotTask.objects.filter(branch_id=branch.branch_id).order_by('position')
        if existing_tasks:
            return False

        download_task = CfbotTask.objects.create(
            task_id=f"Download-{branch.branch_id}",
            task_name="Download",
            patch=branch.patch,
            branch_id=branch.branch_id,
            position=1,
            status="EXECUTING",
            payload=None,
        )

        def run_download_task():

            self.initialize_directories(branch)

            patch = branch.patch
            attachments = patch.get_attachments()
            patch_count = 0
            fail_count = 0
            try:
                for position, attachment in enumerate(attachments, start=1):
                    attachment["date"] = attachment["date"].isoformat() # XXX: hack for JSONField usage
                    if attachment.get("ispatch") and fail_count == 0:
                        patch_count += 1
                        result = self.download_and_save(download_task, attachment)
                        if not result: fail_count += 1
                        command = CfbotTaskCommand.objects.create(
                            task=download_task,
                            name=attachment["filename"],
                            status="COMPLETED" if result else "FAILED",
                            type="Patchset File",
                            duration=0,
                            payload=attachment,
                        )

                    else:
                        command = CfbotTaskCommand.objects.create(
                            task=download_task,
                            name=attachment["filename"],
                            status="IGNORED",
                            type="Other File",
                            duration=0,
                            payload=attachment,
                        )

                if fail_count == 0:
                    apply_task = CfbotTask.objects.create(
                        task_id=f"Apply-{branch.branch_id}",
                        task_name="Apply",
                        patch=branch.patch,
                        branch_id=branch.branch_id,
                        position=2,
                        status="CREATED",
                        payload={},
                    )
                    for command in CfbotTaskCommand.objects.filter(task=download_task, type="Patchset File").order_by('name'):
                        CfbotTaskCommand.objects.create(
                            task=apply_task,
                            name=command.name,
                            status="CREATED",
                            type="Apply Patch",
                            duration=0,
                            payload={},
                        )
                download_task.status = "COMPLETED" if fail_count == 0 else "FAILED"
                download_task.save()
            except Exception as e:
                # Handle exceptions and mark tasks as failed
                for command in CfbotTaskCommand.objects.filter(task=download_task, type="Patchset File"):
                    command.status = "ABORTED"
                    command.save()
                # do this last so it refelcts the aborts on the file downloads
                download_task.status = "ABORTED"
                download_task.payload = {"error": str(e)}
                download_task.save()


        threading.Thread(target=run_download_task).start()

        return True

    def is_done(self, branch):
        """
        Check if all tasks for the branch are completed and apply patches for tasks with the name 'Patchset File'.
        """
        tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
        if all(task.is_done() for task in tasks):
            return True

        apply_task = CfbotTask.objects.filter(branch_id=branch.branch_id, task_name="Apply").first()
        download_task = CfbotTask.objects.filter(branch_id=branch.branch_id, task_name="Download").first()
        if download_task and download_task.is_done() and branch.patch_count is None:
            def run_apply_task():
                try:
                    has_failed = False
                    for command in CfbotTaskCommand.objects.filter(task=apply_task, type="Apply Patch").order_by('name'):
                        command.status = "EXECUTING"
                        command.save()
                        if not has_failed and self.perform_apply(command.name, command.payload):
                            command.status = "COMPLETED"
                        else:
                            if has_failed:
                                command.status = "IGNORED"
                            else:
                                has_failed = True
                                command.status = "FAILED"

                        command.save()
                    apply_task.status = "COMPLETED" if not has_failed else "FAILED"
                    apply_task.save()
                except Exception as e:
                    for command in CfbotTaskCommand.objects.filter(task=apply_task, type="Apply Patch", status="EXECUTING"):
                        command.status = "ABORTED"
                        command.save()
                    apply_task.status = "ABORTED"
                    apply_task.payload = {"error": str(e)}
                    apply_task.save()

            branch.patch_count = CfbotTaskCommand.objects.filter(
                task=apply_task,
                type="Apply Patch"
            ).count()
            branch.save()
            apply_task.status = "EXECUTING"
            apply_task.save()
            threading.Thread(target=run_apply_task).start()

        return False

    def did_fail(self, branch):
        """
        Apply the results of branch testing. Return True if any task is a failure.
        """
        tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
        if any(task.is_failure() for task in tasks):
            failed = True
        else:
            failed = False

        branch.patch_count = self.get_patch_count(branch)
        first_additions, first_deletions = self.git_shortstat(branch, "origin/master", "HEAD~%s" % (branch.patch_count - 1,))
        all_additions, all_deletions = self.git_shortstat(branch, "origin/master", "HEAD")

        if not failed:
            if self.convert_to_merge_commit(branch):
                failed = False
            else:
                failed = True

        if not failed:
            branch.commit_id = self.get_head_commit_sha(branch)
            branch.base_commit_sha = self.get_base_commit_sha(branch)

            apply_results = {
                "merge_commit_sha": branch.commit_id,
                "base_commit_sha": branch.base_commit_sha,
                "patch_count": branch.patch_count,
                "first_additions": first_additions,
                "first_deletions": first_deletions,
                "all_additions": all_additions,
                "all_deletions": all_deletions,
            }

            branch.first_additions = apply_results["first_additions"]
            branch.first_deletions = apply_results["first_deletions"]
            branch.all_additions = apply_results["all_additions"]
            branch.all_deletions = apply_results["all_deletions"]

        return failed

    def signal_done_cb(self, branch, apply_task, apply_result):
        apply_task.payload = {
            "stdout": apply_result.stdout,
            "stderr": apply_result.stderr,
        }
        apply_task.status = "COMPLETED" if apply_result.returncode == 0 else "FAILED"
        apply_task.save()

    def initialize_directories(self, branch):
        raise NotImplementedError("Abstract Method")

    def download_and_save(self, attachment):
        raise NotImplementedError("Abstract Method")

    def perform_apply(self, filename, payload):
        raise NotImplementedError("Abstract Method")

    def convert_to_merge_commit(self, branch):
        raise NotImplementedError("Abstract Method")

    def get_patch_count(self, branch):
        raise NotImplementedError("Abstract Method")

    def get_base_commit_sha(self, branch):
        raise NotImplementedError("Abstract Method")

    def get_head_commit_sha(self,branch):
        raise NotImplementedError("Abstract Method")

    def git_shortstat(self, branch, from_commit, to_commit):
        raise NotImplementedError("Abstract Method")

    def get_delay(self, branch):
        raise NotImplementedError("Abstract Method")



class AbstractPatchCompiler:
    """
    A class responsible for burning patches.
    """
    def __init__(self, working_dir, repo_dir):
        self.working_dir = working_dir
        self.repo_dir = repo_dir

    def begin(self, branch):
        """
        Create a compile task for the branch and mark existing tasks as completed.
        """
        # All tasks from the previous subsystem should have been removed leaving us with a clean slate
        existing_tasks = CfbotTask.objects.filter(branch_id=branch.branch_id).order_by('position')
        if existing_tasks:
            return False

        CfbotTask.objects.create(
            task_id=f"Compile {branch.branch_name}",
            task_name="Compile",
            patch=branch.patch,
            branch_id=branch.branch_id,
            position=1,
            status="CREATED",
            payload=None,
        )

        return True

    def is_done(self, branch):
        """
        Filter for "Compile", "Configure", and "Make" tasks, perform their respective work, and update their payloads.
        """
        configure_is_done = None
        make_is_done = None
        compile_is_done = None
        compile_task = None

        # Loop through tasks and update the *_is_done booleans appropriately
        tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
        for task in tasks:
            if task.task_name == "Meson Setup":
                configure_is_done = task.is_done()
            elif task.task_name == "Ninja":
                make_is_done = task.is_done()
            elif task.task_name == "Compile":
                compile_is_done = task.is_done()
                compile_task = task

        if compile_is_done is None:
            raise ValueError("Compile task not found.")

        if compile_is_done:
            return compile_is_done

        if compile_task.status == "CREATED":
            compile_task.status = "EXECUTING"
            compile_task.save()

        if configure_is_done is None:
            # Create "Configure" task
            configure_task = CfbotTask.objects.create(
                task_id=f"Meson Setup {branch.branch_name}",
                task_name="Meson Setup",
                patch=branch.patch,
                branch_id=branch.branch_id,
                position=2,
                status="EXECUTING",
                payload=None,
            )

            try:
                configure_result = self.do_configure_sync(branch)
                print(configure_result)
                configure_task.payload = {
                    "stdout": configure_result.stdout,
                    "stderr": configure_result.stderr,
                }
                configure_task.status = "COMPLETED" if configure_result.returncode == 0 else "FAILED"
            except Exception as e:
                configure_task.payload = {"error": str(e)}
                print(e)
                configure_task.status = "FAILED"
            configure_task.save()

            if configure_task.status == "FAILED":
                compile_task.status = "COMPLETED"
                compile_task.save()
                return True

        if make_is_done is None:
            # Create "Make" task
            make_task = CfbotTask.objects.create(
                task_id=f"Ninja {branch.branch_name}",
                task_name="Ninja",
                patch=branch.patch,
                branch_id=branch.branch_id,
                position=3,
                status="EXECUTING",
                payload=None,
            )
            def run_make_task():
                try:
                    self.do_compile_async(branch, make_task, signal_done=self.signal_done_cb)
                except Exception as e:
                    make_task.payload = {"error": str(e)}
                    make_task.status = "FAILED"
                make_task.save()


            threading.Thread(target=run_make_task).start()

        if make_is_done and configure_is_done:
            compile_task.status = "COMPLETED"
            compile_task.save()
            return True

        return False

    def did_fail(self, branch):
        """
        Apply the results of branch compilation. Return True if any task is a failure.
        """
        tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
        if any(task.is_failure() for task in tasks):
            failed = True
        else:
            failed = False

        return failed

    def signal_done_cb(self, branch, compile_task, compile_result):
        compile_task.payload = {
            "stdout": compile_result.stdout,
            "stderr": compile_result.stderr,
        }
        compile_task.status = "COMPLETED" if compile_result.returncode == 0 else "FAILED"
        compile_task.save()

    def get_delay(self, branch):
        raise NotImplementedError("Abstract Method")

    def do_compile_async(self, branch, compile_task, signal_done):
        raise NotImplementedError("Abstract Method")

    def do_configure_sync(self, branch):
        raise NotImplementedError("Abstract Method")


class AbstractPatchTester:
    """
    A class responsible for testing patches.
    """
    def __init__(self, working_dir, repo_dir):
        self.working_dir = working_dir
        self.repo_dir = repo_dir

    def begin(self, branch):
        """
        Check if all tasks for the branch are completed and perform testing work.
        """
        # All tasks from the previous subsystem should have been removed leaving us with a clean slate
        existing_tasks = CfbotTask.objects.filter(branch_id=branch.branch_id).order_by('position')
        if existing_tasks:
            return False

        CfbotTask.objects.create(
            task_id=f"Test {branch.branch_name}",
            task_name="Test",
            patch=branch.patch,
            branch_id=branch.branch_id,
            position=1,
            status="CREATED",
            payload=None,
        )

        return True

    def is_done(self, branch):
        """
        Create a test task for the branch and mark existing tasks as completed.
        """
        test_is_done = None
        testing_is_done = None
        testing_task = None

        # Loop through tasks and update the *_is_done booleans appropriately
        tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
        for task in tasks:
            if task.task_name == "Run Test":
                test_is_done = task.is_done()
            elif task.task_name == "Test":
                testing_is_done = task.is_done()
                testing_task = task

        if testing_is_done is None:
            raise ValueError("Testing task not found.")

        if testing_is_done:
            return testing_is_done

        if testing_task.status == "CREATED":
            testing_task.status = "EXECUTING"
            testing_task.save()

        if test_is_done is None:
            # Create "Test" task
            test_task = CfbotTask.objects.create(
                task_id=f"Meson Test {branch.branch_name}",
                task_name="Run Test",
                patch=branch.patch,
                branch_id=branch.branch_id,
                position=2,
                status="EXECUTING",
                payload=None,
            )
            def run_test_task():
                try:
                    self.do_test_async(branch, test_task, signal_done=self.signal_done_cb)
                except Exception as e:
                    test_task.payload = {"error": str(e)}
                    test_task.status = "FAILED"
                test_task.save()

                # within build_dir/meson-logs/testlog*
                # there are three artifacts to collect as well
                # need to either bring the concept over from cfbot
                # or figure out something else.


            threading.Thread(target=run_test_task).start()

        if test_is_done:
            testing_task.status = "COMPLETED"
            testing_task.save()
            return True

        return False

    def did_fail(self, branch):
        """
        Apply the results of branch testing. Return True if any task is a failure.
        """
        tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
        if any(task.is_failure() for task in tasks):
            return True
        return False

    def signal_done_cb(self, branch, test_task, test_result):
        test_task.payload = {
            "stdout": test_result.stdout,
            "stderr": test_result.stderr,
        }
        test_task.status = "COMPLETED" if test_result.returncode == 0 else "FAILED"
        test_task.save()

    def do_test_async(self, branch, test_task, signal_done):
        raise NotImplementedError("Abstract Method")

    def get_delay(self, branch):
        raise NotImplementedError("Abstract Method")



class Notifier:
    """
    A class responsible for sending notifications.
    """
    def notify_branch_update(self, branch):
        if branch.status in {"compiling-aborted", "compiling-failed"}:
            branch.needs_rebase_since = datetime.now()
            branch.failing_since = datetime.now()
            self.update_queue_ignore_date(branch)
        elif branch.status in {"testing-aborted", "testing-failed"}:
            branch.needs_rebase_since = None
            branch.failing_since = datetime.now()
            self.update_queue_ignore_date(branch)

        if branch.status in {"compiled", "compiling-failed"}:
            self.update_queue_latest_base_commit_sha(branch)

        branch.save()
        return CfbotBranchHistory.add_branch_to_history(branch)

    def notify_branch_tested(self, branch):
        pass

    def update_queue_latest_base_commit_sha(self, branch):
        """
        Update the queue item's last_base_commit_sha.
        """
        # Update the queue item's last_base_commit_sha
        queue = CfbotQueue.objects.first()
        if queue:
            queue_item = queue.items.filter(patch_id=branch.patch_id).first()
            if queue_item:
                queue_item.last_base_commit_sha = branch.base_commit_sha
                queue_item.save()

    def update_queue_ignore_date(self, branch):
        """
        Update the queue item's ignore_date.
        """
        # Update the queue item's ignore_date
        queue = CfbotQueue.objects.first()
        if queue:
            queue_item = queue.items.filter(patch_id=branch.patch_id).first()
            if queue_item:
                queue_item.ignore_date = datetime.now()
                queue_item.save()


class LocalPatchApplier(AbstractPatchApplier):
    BASE_FILE_URL = "http://localhost:8001/message-id/attachment/"
    APPLY_SCRIPT_SRC = "tools/postgres/"
    APPLY_SCRIPT_NAME = "apply-one-patch.sh"

    RE_ADDITIONS = re.compile(r"(\d+) insertion")
    RE_DELETIONS = re.compile(r"(\d+) deletion")

    def __init__(self, template_dir, working_dir, repo_dir):
        super().__init__()
        self.template_dir = template_dir
        self.working_dir = working_dir
        self.repo_dir = repo_dir

    def git_shortstat(self, branch, from_commit, to_commit):
        try:
            result = subprocess.run(
                ["git", "-C", self.repo_dir, "diff", "--shortstat", from_commit, to_commit],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
            )
            shortstat = result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to retrieve base commit SHA: {e.stderr.strip()}")

        additions = re.search(self.RE_ADDITIONS, shortstat)
        deletions = re.search(self.RE_DELETIONS, shortstat)

        if additions:
            additions = int(additions.group(1))
        else:
            additions = 0

        if deletions:
            deletions = int(deletions.group(1))
        else:
            deletions = 0

        return additions, deletions

    def initialize_directories(self, branch):
        """
        Check and clear the working and repository directories if they exist.
        Raise FileNotFoundError if they do not exist.
        """
        if not os.path.exists(self.working_dir):
            raise FileNotFoundError(f"Working directory '{self.working_dir}' does not exist.")

        if not os.path.exists(self.repo_dir):
            raise FileNotFoundError(f"Repository directory '{self.repo_dir}' does not exist.")

        """
        Ensure the template directory exists, is non-empty, and contains a .git directory.
        """
        if not os.path.exists(self.template_dir):
            raise FileNotFoundError(f"Template directory '{self.template_dir}' does not exist.")

        if not os.listdir(self.template_dir):
            raise ValueError(f"Template directory '{self.template_dir}' is empty.")

        git_dir = os.path.join(self.template_dir, '.git')
        if not os.path.exists(git_dir):
            raise FileNotFoundError(f"Template directory '{self.template_dir}' does not contain a .git directory.")

        shutil.rmtree(self.working_dir)
        shutil.rmtree(self.repo_dir)
        os.makedirs(self.working_dir)
        # Copy the template directory to the working directory
        shutil.copytree(self.template_dir, self.repo_dir)

        # Copy the apply script to the repository directory
        apply_script_path = os.path.join(settings.BASE_DIR, '..' , self.APPLY_SCRIPT_SRC, self.APPLY_SCRIPT_NAME)
        if not os.path.exists(apply_script_path):
            raise FileNotFoundError(f"Apply script '{apply_script_path}' does not exist.")
        shutil.copy(apply_script_path, self.working_dir)

        # Set up the git user then commit
        subprocess.run(["git", "-C", self.repo_dir, "config", "user.name", "Commitfest Bot"], check=True)
        subprocess.run(["git", "-C", self.repo_dir, "config", "user.email", "cfbot@cputube.org"], check=True)

        subprocess.call(["git", "-C", self.repo_dir, "branch", "--quiet", '-D', f"cf/{branch.patch.id}"])
        subprocess.run(["git", "-C", self.repo_dir, "checkout", "--quiet", '-b', f"cf/{branch.patch.id}"], check=True)

    def download_and_save(self, download_task, attachment):
        """
        Retrieve the contents at url_path and write them to a file in the working directory.
        """
        try:
            url_path = self.BASE_FILE_URL + str(attachment["attachmentid"]) + "/" + attachment["filename"]
            file_path = os.path.join(self.working_dir, attachment["filename"])
            response = requests.get(url_path, stream=True)
            attachment["download_result"] = "Failed"
            response.raise_for_status()  # Raise an error for bad HTTP responses
            attachment["download_result"] = "Success"
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            return True
        except Exception as e:
            print(f"Error downloading or saving file {attachment['filename']}: {e}")
            return False

    # XXX: handles/assumes .diff files only
    # For compressed files we can branch here to perform decompressions
    # and create new tasks for the contained files.
    def perform_apply(self, filename, payload):
        """
        Apply the patch file after ensuring it exists in the working directory.
        """
        file_path = os.path.join(self.working_dir, filename)
        if not os.path.exists(file_path):
            print(f"Error: File {file_path} does not exist in the working directory.")
            return False

        # Run the apply script with the filename as an argument
        try:
            result = subprocess.run(
                ['./' + self.APPLY_SCRIPT_NAME, filename, self.repo_dir],
                cwd=self.working_dir,
                check=True,
                capture_output=True,
                text=True
            )
            payload["apply_result"] = "Success"
            payload["stdout"] = result.stdout
            payload["stderr"] = result.stderr
            return True
        except subprocess.CalledProcessError as e:
            payload["apply_result"] = "Failure"
            payload["stdout"] = e.stdout
            payload["stderr"] = e.stderr
            return False

    def convert_to_merge_commit(self, branch):
        """
        Convert the branch to a merge commit.
        """
        msg_file = os.path.join(self.working_dir, "merge_commit_msg.txt")
        commit_id = self.get_head_commit_sha()

        # Write a message to the msg_file
        with open(msg_file, "w") as f:
            f.write(f"Merge branch '{branch.branch_name}' into master\n\n")
            f.write(f"Patch ID: {branch.patch_id}\n")
            f.write(f"Branch ID: {branch.branch_id}\n")
            f.write(f"Commit ID: {commit_id}\n")

        reset_cmd = ["git", "-C", self.repo_dir, "reset", "origin/master", "--hard", "--quiet"]
        merge_cmd = ["git", "-C", self.repo_dir, "merge", "--no-ff", "--quiet", "-F", msg_file, commit_id]

        try:
            subprocess.run(reset_cmd, check=True)
            subprocess.run(merge_cmd, check=True)
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to convert to merge commit: {e.stderr.strip()}")

        return True

    def get_delay(self, branch):
        return None

    def get_patch_count(self, branch):
        # In particular since an input file can be an archive of patches
        # we need to count the number of patches found in the directory
        # though possible this can be confirmed/gotten in other ways.
        # but this is consistent with context introspection other values get.
        """
        Count the number of files in the working directory with .diff or .patch extensions.
        """
        import os
        return sum(1 for file in os.listdir(self.working_dir) if file.endswith((".diff", ".patch")))

    def get_head_commit_sha(self, branch):
        """
        Simulate retrieving the merge commit SHA after a successful compilation.
        """
        try:
            result = subprocess.run(
                ["git", "-C", self.repo_dir, "rev-parse", "HEAD"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to retrieve base commit SHA: {e.stderr.strip()}")

    def get_base_commit_sha(self, branch):
        """
        Retrieve the base commit SHA from the template directory.
        """
        try:
            result = subprocess.run(
                ["git", "-C", self.repo_dir, "rev-parse", "origin/master"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to retrieve base commit SHA: {e.stderr.strip()}")


class LocalPatchCompiler(AbstractPatchCompiler):
    def __init__(self, working_dir, repo_dir):
        super().__init__()
        self.working_dir = working_dir
        self.repo_dir = repo_dir

    def do_configure_sync(self, branch):
        prefix_dir = os.path.join(self.working_dir, "install")
        configure_result = subprocess.run(
            ["meson", "setup", "build", f"--prefix={prefix_dir}"],
            cwd=self.repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return configure_result

    def do_compile_async(self, branch, compile_task, signal_done):
        build_dir = os.path.join(self.repo_dir, "build")
        ninja_result = subprocess.run(
            ["ninja"],
            cwd=build_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        signal_done(branch, compile_task, ninja_result)
        return

    def signal_done_cb(self, branch, compile_task, compile_result):
        compile_task.payload = {
            "stdout": compile_result.stdout,
            "stderr": compile_result.stderr,
        }
        compile_task.status = "COMPLETED" if compile_result.returncode == 0 else "FAILED"

        compile_task.save()

    def get_delay(self, branch):
        return 60
class LocalPatchTester(AbstractPatchTester):
    def __init__(self, working_dir, repo_dir):
        super().__init__()
        self.working_dir = working_dir
        self.repo_dir = repo_dir

    def do_compile_async(self, branch, test_task, signal_done):
        build_dir = os.path.join(self.repo_dir, "build")
        test_result = subprocess.run(
            ["meson", "test"],
            cwd=build_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        signal_done(branch, test_task, test_result)
        return

    def signal_done_cb(self, branch, test_task, test_result):
        test_task.payload = {
            "stdout": test_result.stdout,
            "stderr": test_result.stderr,
        }
        test_task.status = "COMPLETED" if test_result.returncode == 0 else "FAILED"

        test_task.save()

    def get_delay(self, branch):
        return 60

path_template_dir = "/home/davidj/cfapp-temp/template/postgres/"
path_working_dir = "/home/davidj/cfapp-temp/work/"
path_repo_dir = "/home/davidj/cfapp-temp/postgres/"

def getLocalPatchApplier():
    return LocalPatchApplier(
        path_template_dir,
        path_working_dir,
        path_repo_dir,
    )

def getLocalPatchCompiler():
    return LocalPatchCompiler(
        path_working_dir,
        path_repo_dir,
    )

def getLocalPatchTester():
    return LocalPatchTester(
        path_working_dir,
        path_repo_dir,
    )

def getNotifier():
    return Notifier()
