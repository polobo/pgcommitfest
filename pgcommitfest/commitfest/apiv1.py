from django.http import (
    HttpResponse,
)
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404

import json
from datetime import datetime

from .models import (
    Workflow,
    CfbotQueue,
    CfbotQueueItem,
    CfbotBranch,
    CfbotTask,
)


def datetime_serializer(obj):
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%dT%H:%M:%S%z")
    raise TypeError("Type not serializable")


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
    return {
        "id": item.id,
        "is_current": is_current,
        "patch_id": item.patch_id,
        "message_id": item.message_id,
        "processed_date": item.processed_date,
        "ignore_date": item.ignore_date,
        "ll_prev": item.ll_prev,
        "ll_next": item.ll_next,
        "attachments": item.get_attachments(),
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
    if not queue:
        return apiResponse(request, {"error": "No queue found"}, status=404)

    item = queue.peek()
    if not item:
        return apiResponse(request, {"error": "No items in the queue"}, status=404)

    payload = build_item_object(item, is_current=item.id == queue.current_queue_item)
    return apiResponse(request, payload)


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
        }
        for branch in branches
    ]
    return apiResponse(request, {"branches": branch_list})


def cfbot_tasks(request):
    branch_id = request.GET.get("branch_id")
    tasks = CfbotTask.objects.filter(branch_id=branch_id) if branch_id else CfbotTask.objects.all()
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
