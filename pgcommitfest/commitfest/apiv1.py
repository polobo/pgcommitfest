from django.http import (
    HttpResponse,
)
from django.shortcuts import get_object_or_404

from datetime import datetime
import json

from .models import (
    CommitFest,
    PatchOnCommitFest,
    Workflow,
)


def datetime_serializer(obj):
    if isinstance(obj, datetime):
        return obj.strftime("%Y-%m-%dT%H:%M:%S%z")
    raise TypeError("Type not serializable")

def apiResponse(request, payload, status=200, content_type="application/json"):
    response = HttpResponse(json.dumps(payload, default=datetime_serializer), status=status)
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

def cf_patches(request, commitfest_id):
    cf = get_object_or_404(CommitFest, pk=commitfest_id)
    patches = Workflow.getOpenPoCs(cf)
    return apiResponse(request, patches)
