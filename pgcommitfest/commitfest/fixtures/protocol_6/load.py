import os

from django.conf import settings
from pgcommitfest.commitfest.models import BranchManager, CfbotTask, Notifier, PatchOnCommitFest, Workflow, CommitFest, Patch, Topic, TargetVersion, CfbotQueue, CfbotQueueItem, CfbotBranch, MailThread, MailThreadAttachment
from datetime import datetime
from django.db import transaction

@transaction.atomic
def create_patches():
    # Create a topic and target version for the patches
    topic, _ = Topic.objects.get_or_create(topic="Example Topic")
    target_version, _ = TargetVersion.objects.get_or_create(version="16.0")
    queue = CfbotQueue.retrieve()
    draft_cf = Workflow.parked_cf()

    # Create a simple patch
    patch1, _ = Patch.objects.get_or_create(
        name="Simple Patch",
        topic=topic,
        targetversion=target_version,
    )

    # Create a patch that has been queued
    patch2, _ = Patch.objects.get_or_create(
        name="Queued Patch",
        topic=topic,
        targetversion=target_version,
    )

    # Create a patch that has a new branch
    patch3, _ = Patch.objects.get_or_create(
        name="Patch with New Branch",
        topic=topic,
        targetversion=target_version,
    )

    # Create a patch with an applied branch
    patch4, _ = Patch.objects.get_or_create(
        name="Patch with Applied Branch",
        topic=topic,
        targetversion=target_version,
    )

    # Create a patch with a compiled branch
    patch5, _ = Patch.objects.get_or_create(
        name="Patch with Compiled Branch",
        topic=topic,
        targetversion=target_version,
    )

    # Create a patch with a tested branch
    patch6, _ = Patch.objects.get_or_create(
        name="Patch with Tested Branch",
        topic=topic,
        targetversion=target_version,
    )

    # Create threads for each patch using a comprehension
    patches = [patch1, patch2, patch3, patch4, patch5, patch6]
    threads = [
        MailThread.objects.get_or_create(
            messageid=f"thread-message-id-{10000+patch.id}",
            subject=f"Thread for {patch.name}",
            firstmessage=f"2023-01-01T00:00:00Z",
            firstauthor=f"Author {patch.id}",
            latestmessage=f"2023-01-01T00:00:00Z",
            latestauthor=f"Author {patch.id}",
            latestsubject=f"Thread for {patch.name}",
            latestmsgid=f"thread-message-id-{10000+patch.id}",
        )[0]
        for patch in patches
    ]

    for patch in patches:
        poc = PatchOnCommitFest(
            patch=patch, commitfest=draft_cf, enterdate=datetime.now()
        )
        poc.save()

    # Associate threads with their respective patches
    for patch, thread in zip(patches, threads):
        patch.mailthread_set.add(thread)

    # Add an attachment to each thread
    for patch, thread in zip(patches, threads):
        MailThreadAttachment.objects.get_or_create(
            mailthread=thread,
            messageid=f"thread-message-id-{10000+patch.id}",
            attachmentid=int(patch.id),
            filename="v1-0001-PATCH-protocol-6.patch",
            date=f"2023-01-01T00:00:00Z",
            author=f"Author {patch.id}",
            ispatch=True,
            contenttype="text/x-diff",
        )
        patch.patchset_messageid = f"thread-message-id-{10000+patch.id}"
        patch.patchset_messagedate = f"2023-01-01T00:00:00Z"
        patch.lastmail = f"2023-01-01T00:00:00Z"
        patch.save()

    for patch in [patch2, patch3, patch4, patch5, patch6]:
        print(f"Adding patch {patch.id} to queue")
        queue.insert_item(patch.id, patch.patchset_messageid)

    # new
    for patch in [patch3, patch4, patch5, patch6]:
        Workflow.createBranch(patch.id, patch.patchset_messageid)

    # applying-applied
    for patch in [patch4, patch5, patch6]:
        mock_apply(patch)

    # compiling-compiled
    for patch in [patch5, patch6]:
        mock_compile(patch)

    # testing-tested
    for patch in [patch6]:
        mock_test(patch)

class TestPatchApplier:
    def begin(self, branch):
        patch = branch.patch
        attachments = patch.get_attachments()
        attachment = attachments[0] # known data
        attachment["date"] = attachment["date"].isoformat()
        attachment["download_result"] = "Success"
        task = CfbotTask.objects.create(
            task_id=attachment["filename"],
            task_name=f"Patchset File",
            patch_id=patch.id,
            branch_id=branch.branch_id,
            position=2,
            status="EXECUTING",
            payload=attachment,
        )
        return True

    def is_done(self, branch):
        tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
        for task in tasks:
            if task.task_name == "Patchset File":
                task.status = "COMPLETED"
                task.payload["apply_result"] = "Success"
                with open(os.path.join(settings.BASE_DIR, "commitfest/fixtures/protocol_6/apply.out"), "r") as stdout_file:
                    task.payload["stdout"] = stdout_file.read()
                with open(os.path.join(settings.BASE_DIR, "commitfest/fixtures/protocol_6/apply.err"), "r") as stderr_file:
                    task.payload["stderr"] = stderr_file.read()
                task.save()

        return True
    def did_fail(self, branch):
        apply_results = {
            "merge_commit_sha": "473eb7bd581737e34ca4400bc02340cc1474a6cd",
            "base_commit_sha": "e29df428a1dca4112aad640c889a9a54642759c9",
            "patch_count": 1,
            "first_additions": 10,
            "first_deletions": 4,
            "all_additions": 10,
            "all_deletions": 4,
        }
        CfbotTask.objects.create(
            task_id=f"Apply Result Payload",
            task_name="Apply Result",
            patch=branch.patch,
            branch_id=branch.branch_id,
            position=2,
            status="COMPLETED" ,
            payload=apply_results,
        )
        return False
    def get_delay(self, branch):
        return None

class TestPatchCompiler:
    def begin(self, branch):
        print(f"Beginning patch application for branch: {branch}")
        return True
    def is_done(self, branch):
        print(f"Checking if patch application is done for branch: {branch}")
        return True
    def did_fail(self, branch):
        print(f"Checking if patch application failed for branch: {branch}")
        return False
    def get_delay(self, branch):
        return None

class TestPatchTester:
    def begin(self, branch):
        print(f"Beginning patch application for branch: {branch}")
        return True
    def is_done(self, branch):
        print(f"Checking if patch application is done for branch: {branch}")
        return True
    def did_fail(self, branch):
        print(f"Checking if patch application failed for branch: {branch}")
        return False
    def get_delay(self, branch):
        return None

branchManager = BranchManager(
    applier=TestPatchApplier(),
    burner=TestPatchCompiler(),
    tester=TestPatchTester(),
    notifier=Notifier(),
)

def mock_apply(patch_id):
    cfbot_branch = CfbotBranch.objects.filter(patch_id=patch_id).first()
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    cfbot_branch.save()

def mock_compile(patch_id):
    cfbot_branch = CfbotBranch.objects.filter(patch_id=patch_id).first()
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    cfbot_branch.save()

def mock_test(patch_id):
    cfbot_branch = CfbotBranch.objects.filter(patch_id=patch_id).first()
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    cfbot_branch.save()

if __name__ == "__main__":
    create_patches()
