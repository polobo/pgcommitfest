from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import json
from datetime import datetime

from .models import (
    CommitFest,
    MailThread,
    MailThreadAttachment,
    Patch,
    PatchHistory,
    PatchOnCommitFest,
    Topic,
)
from .util import apiResponse


@csrf_exempt
@require_POST
@transaction.atomic
def create_patch(request):
    """
    Create a new patch with placeholder values for required fields.
    """
    body_string = request.body.decode("utf-8")
    body_json = json.loads(body_string)

    target_cf = CommitFest.objects.filter(status=CommitFest.STATUS_OPEN).first()

    mailthread = MailThread.objects.create(
        messageid=body_json.get("thread_message_id"),
        subject=body_json.get("thread_subject_line"),
        firstmessage=body_json.get("thread_message_date"),
        firstauthor=body_json.get("thread_from_author"),
        latestmsgid=body_json.get("most_recent_message_id"),
        latestmessage=body_json.get("most_recent_message_date"),
        latestauthor=body_json.get("most_recent_from_author"),
        latestsubject=body_json.get("most_recent_subject_line"),
        patchsetmsgid=body_json.get("patch_message_id"),
    )

    for attachment in body_json.get("fileset"):
        MailThreadAttachment.objects.get_or_create(
            mailthread=mailthread,
            messageid=body_json.get("patch_message_id"),
            attachmentid=attachment["attachment_id"],
            filename=attachment["filename"],
            contenttype=attachment["content_type"],
            ispatch=attachment["is_patch"],
            author=body_json.get("patch_from_author"),
            date=body_json.get("patch_message_date"),
        )

    topic = Topic.objects.filter(id=1).first()  # Miscellaneous for now
    # Create a new Patch instance with required fields set to None
    patch = Patch.objects.create(
        name=body_json.get("thread_subject_line"),
        topic=topic,
        patchset_messageid=body_json.get("patch_message_id", None),
        patchset_messagedate=body_json.get("patch_message_date", None),
        lastmail=body_json.get("most_recent_message_date", None),
    )

    poc = PatchOnCommitFest(patch=patch, commitfest=target_cf, enterdate=datetime.now())
    poc.save()
    PatchHistory(
        patch=patch, by_cfbot=True, what="Patch created from mail thread"
    ).save()

    mailthread.patches.add(patch)
    mailthread.save()

    return apiResponse(
        request,
        {"patch_id": patch.id, "message": "Patch created with placeholder values."},
    )
