from django.http import (
    HttpResponse,
)

import json

from .models import (
    Workflow,
)


def apiResponse(request, payload, status=200, content_type="application/json"):
    response = HttpResponse(json.dumps(payload), status=status)
    response["Content-Type"] = content_type
    response["Access-Control-Allow-Origin"] = "*"
    return response


def optional_as_json(obj):
    if obj is None:
        return None
    return obj.json()


def open_cfs(request):
    payload = {
        "workflow": {
            "open": optional_as_json(Workflow.open_cf()),
            "inprogress": optional_as_json(Workflow.inprogress_cf()),
            "parked": optional_as_json(Workflow.parked_cf()),
        },
    }
    return apiResponse(request, payload)
