from django.http import (
    HttpResponse,
)

from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST
from django.db.models import Q
from django.db import transaction

import json
from datetime import datetime
from django.contrib.auth.models import User

from .models import (
    Topic,
    Workflow,
    CfbotQueue,
    CfbotQueueItem,
    CfbotBranch,
    CfbotTask,
    PatchHistory,
    CfbotBranchHistory,
    MailThread,
    MailThreadAttachment,
    Patch,
    PatchOnCommitFest,
)

from .util import datetime_serializer

def apiResponse(request, payload, status=200, content_type="application/json"):
    response = HttpResponse(
        json.dumps(payload, default=datetime_serializer), status=status
    )
    response["Content-Type"] = content_type
    response["Access-Control-Allow-Origin"] = "*"
    return response


def optional_as_json(obj):
    if obj is None:
        return None
    return obj.json()


def build_item_object(item, is_current):
    """
    Build a consistent item object for API responses.
    """
    patch = get_object_or_404(Patch, pk=item.patch_id)
    return {
        "id": item.id,
        "is_current": is_current,
        "patch_id": item.patch_id,
        "message_id": item.message_id,
        "processed_date": item.processed_date,
        "ignore_date": item.ignore_date,
        "ll_prev": item.ll_prev,
        "ll_next": item.ll_next,
        "attachments": patch.get_attachments(),
        "last_base_commit_sha": item.last_base_commit_sha,
    }


def active_commitfests(request):
    payload = {
        "workflow": {
            "open": optional_as_json(Workflow.open_cf()),
            "inprogress": optional_as_json(Workflow.inprogress_cf()),
            "parked": optional_as_json(Workflow.parked_cf()),
        },
    }
    return apiResponse(request, payload)


def cfbot_get_and_move(request):
    queue = CfbotQueue.objects.first()
    if not queue:
        return apiResponse(request, {"error": "No queue found"}, status=404)

    returned, newcurrent = queue.get_and_move()
    if not returned:
        return apiResponse(request, {"error": "No items in the queue"}, status=404)

    payload = {
        "returned": build_item_object(returned, is_current=False),
        "newcurrent": build_item_object(newcurrent, is_current=True),
    }
    return apiResponse(request, payload)


def cfbot_get_queue(request):
    queue = CfbotQueue.objects.first()
    if not queue:
        return apiResponse(request, {"error": "No queue found"}, status=404)

    queuetable = []
    current_item = queue.get_first_item()
    while current_item:
        queuetable.append(
            build_item_object(
                current_item, is_current=current_item.id == queue.current_queue_item
            )
        )
        current_item = queue.items.filter(id=current_item.ll_next).first()

    return apiResponse(request, {"queuetable": queuetable})


def cfbot_peek(request):
    queue = CfbotQueue.objects.first()
    if not queue or not queue.current_queue_item:
        return apiResponse(request, {"item": None})  # Return empty response

    item = queue.peek()
    patch = get_object_or_404(Patch, pk=item.patch_id)

    payload = build_item_object(item, is_current=item.id == queue.current_queue_item)
    payload["attachments"] = patch.get_attachments()

    return apiResponse(request, {"item": payload})


def cfbot_branches(request):
    branches = CfbotBranch.objects.all()
    branch_list = [
        {
            "patch_id": branch.patch_id,
            "branch_id": branch.branch_id,
            "branch_name": branch.branch_name,
            "commit_id": branch.commit_id,
            "apply_url": branch.apply_url,
            "status": branch.status,
            "needs_rebase_since": branch.needs_rebase_since,
            "failing_since": branch.failing_since,
            "created": branch.created,
            "modified": branch.modified,
            "task_count": CfbotTask.objects.filter(branch_id=branch.branch_id).count(),
            "first_additions": branch.first_additions,
            "first_deletions": branch.first_deletions,
            "all_additions": branch.all_additions,
            "all_deletions": branch.all_deletions,
            "patch_count": branch.patch_count,
        }
        for branch in branches
    ]
    return apiResponse(request, {"branches": branch_list})


def cfbot_tasks(request):
    branch_id = request.GET.get("branch_id")
    tasks = CfbotTask.objects.filter(branch_id=branch_id).order_by('-modified') if branch_id else CfbotTask.objects.all()
    task_list = [
        {
            "task_id": task.task_id,
            "task_name": task.task_name,
            "patch_id": task.patch_id,
            "branch_id": task.branch_id,
            "position": task.position,
            "status": task.status,
            "created": task.created,
            "modified": task.modified,
            "payload": task.payload,
        }
        for task in tasks
    ]
    return apiResponse(request, {"tasks": task_list})


def update_task_status(request, task_id):
    if request.method != "GET":
        return apiResponse(request, {"error": "Invalid method"}, status=405)

    task = get_object_or_404(CfbotTask, task_id=task_id)
    new_status = request.GET.get("status")

    if new_status not in dict(CfbotTask.STATUS_CHOICES):
        return apiResponse(request, {"error": "Invalid status"}, status=400)

    task.status = new_status
    task.save()
    return apiResponse(request, {"message": f"Task {task_id} status updated to {new_status}."})


def process_branch(request, branch_id):
    if request.method != "GET":
        return apiResponse(request, {"error": "Invalid method"}, status=405)

    branch = get_object_or_404(CfbotBranch, branch_id=branch_id)
    branch_manager = Workflow.getBranchManager()
    new_branch, delay_for = branch_manager.process(branch)

    return apiResponse(request, {"message": f"Branch {new_branch.branch_name} has been created with status {new_branch.status}."})


def clear_queue(request):
    if request.method != "GET":
        return apiResponse(request, {"error": "Invalid method"}, status=405)

    queue = CfbotQueue.objects.first()
    if not queue:
        return apiResponse(request, {"error": "No queue found"}, status=404)

    queue.items.all().delete()
    queue.current_queue_item = None
    queue.save()

    return apiResponse(request, {"message": "Queue cleared successfully."})


def clear_branch_table(request):
    if request.method != "GET":
        return apiResponse(request, {"error": "Invalid method"}, status=405)

    CfbotBranch.objects.all().delete()
    CfbotTask.objects.all().delete()
    CfbotBranchHistory.objects.all().delete()
    return apiResponse(request, {"message": "Branch table and tasks cleared successfully."})


def add_test_data(request):
    queue = CfbotQueue.objects.first()
    if not queue:
        return apiResponse(request, {"error": "No queue found"}, status=404)

    # Add test data to the queue
    queue.insert_item(patch_id=8,   message_id="dgj-example@message-08")
    queue.insert_item(patch_id=2,   message_id="example@message-2")
    queue.insert_item(patch_id=3,   message_id="example@message-3")
    queue.insert_item(patch_id=4,   message_id="example@message-4")
    queue.insert_item(patch_id=7,   message_id="example@message-7")

    return apiResponse(request, {"message": "Test data added successfully."})


def create_branch(request):
    if request.method != "GET":
        return apiResponse(request, {"error": "Invalid method"}, status=405)

    patch_id = request.GET.get("patch_id")
    message_id = request.GET.get("message_id")

    if not patch_id or not message_id:
        return apiResponse(request, {"error": "Missing patch_id or message_id"}, status=400)

    # Create a new branch using CfbotBranch
    branch_name = f"branch_{patch_id}"
    apply_url = f"http://example.com/apply/{patch_id}"
    status = "new"

    # Get the corresponding queue item and use its get_attachments method
    queue = CfbotQueue.objects.first()
    if not queue:
        return apiResponse(request, {"error": "No queue found"}, status=404)

    queue_item = queue.items.filter(patch_id=patch_id).first()
    if not queue_item:
        return apiResponse(request, {"error": "No queue item found for the patch"}, status=404)

    patch = get_object_or_404(Patch, pk=patch_id)
    attachments = patch.get_attachments()

    branch, created = CfbotBranch.objects.update_or_create(
        patch_id=patch_id,
        defaults={
            "branch_id": patch_id,  # Using patch_id as branch_id for simplicity
            "branch_name": branch_name,
            "apply_url": apply_url,
            "status": status,
            "patch_count": len(attachments),
            "created": datetime.now(),
            "modified": datetime.now(),
        },
    )

    # Create a history item for the branch
    if created:
        CfbotBranchHistory.add_branch_to_history(branch)

    return apiResponse(request, {"branch_id": branch.branch_id, "message": f"Branch '{branch_name}' created for patch_id {patch_id} with message_id {message_id}."})


def fetch_branch_history(request):
    branch_id = request.GET.get("branch_id")
    if not branch_id:
        return apiResponse(request, {"error": "Missing branch_id"}, status=400)

    history = CfbotBranchHistory.objects.filter(branch_id=branch_id).order_by("-modified")
    history_list = [
        {
            "branch_id": entry.branch_id,
            "status": entry.status,
            "modified": entry.modified,
            "commit_id": entry.commit_id,
            "base_commit_sha": entry.base_commit_sha,
            "task_count": entry.task_count,
        }
        for entry in history
    ]

    return apiResponse(request, {"history": history_list})


def clear_branch_history(request):
    if request.method != "GET":
        return apiResponse(request, {"error": "Invalid method"}, status=405)

    branch_id = request.GET.get("branch_id")
    if not branch_id:
        return apiResponse(request, {"error": "Missing branch_id"}, status=400)

    deleted_count, _ = CfbotBranchHistory.objects.filter(branch_id=branch_id).delete()
    return apiResponse(request, {"message": f"Cleared {deleted_count} history entries for branch_id {branch_id}."})

@csrf_exempt
@require_POST
@transaction.atomic
def create_patch(request):
    """
    Create a new patch with placeholder values for required fields.
    """
    body_string = request.body.decode("utf-8")
    body_json = json.loads(body_string)

    draft_cf = Workflow.parked_cf()

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
        MailThreadAttachment.objects.create(
            mailthread=mailthread,
            messageid=body_json.get("patch_message_id"),
            attachmentid=attachment["attachment_id"],
            filename=attachment["filename"],
            contenttype=attachment["content_type"],
            ispatch=attachment["is_patch"],
            author=body_json.get("patch_from_author"),
            date=body_json.get("patch_message_date"),
        )

    topic = Topic.objects.filter(id=3).first()  # Miscellaneous for now
    # Create a new Patch instance with required fields set to None
    patch = Patch.objects.create(
        name=body_json.get("thread_subject_line"),
        topic=topic,
        patchset_messageid=body_json.get("patch_message_id", None),
        patchset_messagedate=body_json.get("patch_message_date", None),
        lastmail=body_json.get("most_recent_message_date", None),
    )

    poc = PatchOnCommitFest(
        patch=patch, commitfest=draft_cf, enterdate=datetime.now()
    )
    poc.save()
    automation_user = User.objects.get(username="admin")
    PatchHistory(
        patch=patch, by_cfbot=True, what="Patch created from mail thread"
    ).save()

    mailthread.patches.add(patch)
    mailthread.save()

    return apiResponse(request, {"patch_id": patch.id, "message": "Patch created with placeholder values."})

@csrf_exempt
def fetch_open_patches(request):
    """
    Fetch all open patches.
    """
    open_patches = Patch.objects.filter(
        Q(patchoncommitfest__status__in=PatchOnCommitFest.OPEN_STATUSES)
    ).distinct()

    patch_list = [
        {
            "id": patch.id,
            "name": patch.name,
            "status": patch.current_patch_on_commitfest().statusstring,
            "attachment_count": MailThreadAttachment.objects.filter(
                mailthread__patches=patch,
                messageid=patch.patchset_messageid
            ).count(),
            "patchset_messagedate": patch.patchset_messagedate,
            "in_queue": CfbotQueueItem.objects.filter(patch_id=patch.id).exists(),  # New field
            "has_branch": CfbotBranch.objects.filter(patch_id=patch.id).exists(),  # New field
        }
        for patch in open_patches
    ]

    return apiResponse(request, {"patches": patch_list})

@csrf_exempt
@require_POST
def remove_all_patches(request):
    """
    Remove all patches.
    """
    #MailThreadAttachment.objects.all().delete()
    #PatchOnCommitFest.objects.all().delete()
    #Patch.objects.all().delete()
    return apiResponse(request, {"message": "All patches removed successfully."})

@csrf_exempt
@require_POST
def enqueue_patch(request):
    """
    Enqueue a patch into the CfbotQueue.
    """
    body = json.loads(request.body)
    patch_id = body.get("patch_id")

    if not patch_id:
        return apiResponse(request, {"error": "Missing patch_id"}, status=400)

    queue = CfbotQueue.objects.first()
    if not queue:
        return apiResponse(request, {"error": "No queue found"}, status=404)

    patch = get_object_or_404(Patch, pk=patch_id)
    if CfbotQueueItem.objects.filter(patch_id=patch_id).exists():
        return apiResponse(request, {"error": "Patch is already in the queue"}, status=400)

    queue.insert_item(patch_id=patch_id, message_id=patch.patchset_messageid)
    return apiResponse(request, {"success": True, "message": "Patch enqueued successfully."})
