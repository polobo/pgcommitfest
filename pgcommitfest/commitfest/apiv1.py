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

    payload = {"returned": {
        "id": returned.id,
        "patch_id": returned.patch_id,
        "message_id": returned.message_id,
        "processed_date": returned.processed_date,  # Already updated
        "ignore_date": returned.ignore_date,
        "ll_prev": returned.ll_prev,
        "ll_next": returned.ll_next,
    }, "newcurrent": {
        "id": newcurrent.id,
        "patch_id": newcurrent.patch_id,
        "message_id": newcurrent.message_id,
        "processed_date": newcurrent.processed_date,
        "ignore_date": newcurrent.ignore_date,
        "ll_prev": newcurrent.ll_prev,
        "ll_next": newcurrent.ll_next,
    }}
    return apiResponse(request, payload)


def cfbot_get_queue(request):
    queue = CfbotQueue.objects.first()
    if not queue:
        return apiResponse(request, {"error": "No queue found"}, status=404)

    queuetable = []
    current_item = queue.get_first_item()
    while current_item:
        queuetable.append({
            "id": current_item.id,
            "is_current": current_item.id == queue.current_queue_item,
            "patch_id": current_item.patch_id,
            "message_id": current_item.message_id,
            "processed_date": current_item.processed_date,
            "ignore_date": current_item.ignore_date,
            "ll_prev": current_item.ll_prev,
            "ll_next": current_item.ll_next,
        })
        current_item = queue.items.filter(id=current_item.ll_next).first()

    return apiResponse(request, {"queuetable": queuetable})


def cfbot_peek(request):
    queue = CfbotQueue.objects.first()
    if not queue:
        return apiResponse(request, {"error": "No queue found"}, status=404)

    item = queue.peek()
    if not item:
        return apiResponse(request, {"error": "No items in the queue"}, status=404)

    payload = {
        "id": item.id,
        "is_current": item.id == queue.current_queue_item,
        "patch_id": item.patch_id,
        "message_id": item.message_id,
        "processed_date": item.processed_date,
        "ignore_date": item.ignore_date,
        "ll_prev": item.ll_prev,
        "ll_next": item.ll_next,
    }
    return apiResponse(request, payload)
