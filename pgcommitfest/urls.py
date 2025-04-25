from django.contrib import admin
from django.urls import re_path

import pgcommitfest.auth
import pgcommitfest.commitfest.ajax as ajax
import pgcommitfest.commitfest.apiv1 as apiv1
import pgcommitfest.commitfest.lookups as lookups
import pgcommitfest.commitfest.reports as reports
import pgcommitfest.commitfest.views as views
import pgcommitfest.userprofile.views
from pgcommitfest.commitfest.apiv1 import enqueue_patch

# Uncomment the next two lines to enable the admin:
# from django.contrib import admin
admin.autodiscover()


urlpatterns = [
    re_path(r"^$", views.home),
    re_path(r"^api/v1/commitfest/active$", apiv1.active_commitfests),
    re_path(r"^api/v1/commitfest/fetch_open_patches$", apiv1.fetch_open_patches),
    re_path(r"^api/v1/commitfest/remove_all_patches$", apiv1.remove_all_patches),
    re_path(r"^api/v1/cfbot/get_and_move$", apiv1.cfbot_get_and_move),
    re_path(r"^api/v1/cfbot/get_queue$", apiv1.cfbot_get_queue),
    re_path(r"^api/v1/cfbot/peek$", apiv1.cfbot_peek),
    re_path(r"^api/v1/cfbot/branches$", apiv1.cfbot_branches),
    re_path(r"^api/v1/cfbot/tasks$", apiv1.cfbot_tasks),
    re_path(r"^api/v1/cfbot/task/([^/]+)/update_status$", apiv1.update_task_status),
    re_path(r"^api/v1/cfbot/task/([^/]+)/commands$", apiv1.fetch_task_commands),
    re_path(r"^api/v1/cfbot/task/([^/]+)/artifacts$", apiv1.fetch_task_artifacts),
    re_path(r"^api/v1/cfbot/branches/(\d+)/process_branch$", apiv1.process_branch),
    re_path(r"^api/v1/cfbot/branch_history$", apiv1.fetch_branch_history),
    re_path(r"^api/test/cfbot/clear_queue$", apiv1.clear_queue),
    re_path(r"^api/test/cfbot/add_test_data$", apiv1.add_test_data),
    re_path(r"^api/test/cfbot/clear_branch_table$", apiv1.clear_branch_table),
    re_path(r"^api/test/cfbot/create_branch$", apiv1.create_branch),
    re_path(r"^api/test/cfbot/clear_branch_history$", apiv1.clear_branch_history),
    re_path(r"^api/test/cfapp/create_patch$", apiv1.create_patch),
    re_path(r"^workflow/$", views.workflow),
    re_path(r"^workflow-reference/$", views.workflow_reference),
    re_path(r"^me/$", views.me),
    re_path(r"^archive/$", views.archive),
    re_path(r"^activity(?P<rss>\.rss)?/", views.activity),
    re_path(r"^(\d+)/$", views.commitfest),
    re_path(r"^(open|inprogress|current)/(.*)$", views.redir),
    re_path(r"^(?P<cfid>\d+)/activity(?P<rss>\.rss)?/$", views.activity),
    re_path(r"^(\d+)/(\d+)/$", views.patch_legacy_redirect),
    re_path(r"^patch/(\d+)/$", views.patch),
    re_path(r"^patch/(\d+)/edit/$", views.patchform),
    re_path(r"^(\d+)/new/$", views.newpatch),
    re_path(r"^patch/(\d+)/status/(review|author|committer)/$", views.status),
    re_path(r"^patch/(\d+)/close/(reject|withdrawn|feedback|committed)/$", views.close),
    re_path(r"^patch/(\d+)/transition/$", views.transition),
    re_path(r"^patch/(\d+)/reviewer/(become|remove)/$", views.reviewer),
    re_path(r"^patch/(\d+)/committer/(become|remove)/$", views.committer),
    re_path(r"^patch/(\d+)/(un)?subscribe/$", views.subscribe),
    re_path(r"^patch/(\d+)/(comment|review)/", views.comment),
    re_path(r"^(\d+)/send_email/$", views.send_email),
    re_path(r"^patch/(\d+)/send_email/$", views.send_patch_email),
    re_path(r"^(\d+)/reports/authorstats/$", reports.authorstats),
    re_path(r"^search/$", views.global_search),
    re_path(r"^ajax/(\w+)/$", ajax.main),
    re_path(r"^lookups/user/$", lookups.userlookup),
    re_path(r"^thread_notify/$", views.thread_notify),
    re_path(r"^cfbot_notify/$", views.cfbot_notify),
    re_path(r"^cfbot_queue/$", views.cfbot_queue),
    # Legacy email POST route. This can be safely removed in a few days from
    # the first time this is deployed. It's only puprose is not breaking
    # submissions from a previous page lood, during the deploy of the new
    # /patch/(\d+) routes. It would be a shame if someone lost their well
    # written email because of this.
    re_path(r"^\d+/(\d+)/send_email/$", views.send_patch_email),
    # Auth system integration
    re_path(r"^(?:account/)?login/?$", pgcommitfest.auth.login),
    re_path(r"^(?:account/)?logout/?$", pgcommitfest.auth.logout),
    re_path(r"^auth_receive/$", pgcommitfest.auth.auth_receive),
    re_path(r"^auth_api/$", pgcommitfest.auth.auth_api),
    # Account management
    re_path(r"^account/profile/$", pgcommitfest.userprofile.views.userprofile),
    # Examples:
    # re_path(r'^$', 'pgpgcommitfest.commitfest.views.home', name='home),
    # re_path(r'^pgcommitfest/', include('pgcommitfest.foo.urls)),
    # Uncomment the admin/doc line below to enable admin documentation:
    # re_path(r'^admin/doc/', include('django.contrib.admindocs.urls)),
    # Uncomment the next line to enable the admin:
    re_path(r"^admin/", admin.site.urls),
    re_path(r"^api/v1/cfbot/enqueue_patch$", enqueue_patch),
]
