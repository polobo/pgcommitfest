from django.urls import path
from .apiv1 import (
    active_commitfests,
    cfbot_get_and_move,
    cfbot_get_queue,
    cfbot_peek,
    cfbot_branches,
    cfbot_tasks,
    update_task_status,  # Add this import
)

urlpatterns = [
    path("api/v1/active_commitfests", active_commitfests, name="active_commitfests"),
    path("api/v1/cfbot/get_and_move", cfbot_get_and_move, name="cfbot_get_and_move"),
    path("api/v1/cfbot/get_queue", cfbot_get_queue, name="cfbot_get_queue"),
    path("api/v1/cfbot/peek", cfbot_peek, name="cfbot_peek"),
    path("api/v1/cfbot/branches", cfbot_branches, name="cfbot_branches"),
    path("api/v1/cfbot/tasks", cfbot_tasks, name="cfbot_tasks"),
    path("api/v1/cfbot/tasks/<str:task_id>/update_status", update_task_status, name="update_task_status"),
]
