from django.http import (
    HttpResponse,
)

import json
from datetime import datetime

from .models import (
    Workflow,
    CfbotQueue,
    CfbotQueueItem,
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
