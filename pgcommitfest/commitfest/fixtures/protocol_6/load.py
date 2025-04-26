import os

from django.conf import settings
from pgcommitfest.commitfest.models import (
    BranchManager, CfbotTask, CfbotTaskArtifact, Notifier,
    AbstractPatchApplier, AbstractPatchCompiler, PatchOnCommitFest,
    AbstractPatchTester, Workflow, CommitFest,
    Patch, Topic, TargetVersion,
    CfbotQueue, CfbotQueueItem, CfbotBranch,
    MailThread, MailThreadAttachment
)
from datetime import datetime
import time


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

    # Set specific patches to None to prevent them from being processed
    patch1 = None
    patch2 = None
    patch3 = None
    #patch4 = None
    patch5 = None
    patch6 = None

    # Create threads for each patch using a comprehension
    patches = [patch1, patch2, patch3, patch4, patch5, patch6]
    threads = [
        None if patch is None else MailThread.objects.get_or_create(
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
        if patch is None:
            continue
        poc = PatchOnCommitFest(
            patch=patch, commitfest=draft_cf, enterdate=datetime.now()
        )
        poc.save()

    # Associate threads with their respective patches
    for patch, thread in zip(patches, threads):
        if patch is None:
            continue
        patch.mailthread_set.add(thread)

    # Add an attachment to each thread
    for patch, thread in zip(patches, threads):
        if patch is None:
            continue
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
        if patch is None:
            continue
        queue.insert_item(patch.id, patch.patchset_messageid)

    # new
    for patch in [patch3, patch4, patch5, patch6]:
        if patch is None:
            continue
        Workflow.createBranch(patch.id, patch.patchset_messageid)

    # applying-applied
    for patch in [patch4, patch5, patch6]:
        if patch is None:
            continue
        mock_apply(patch)

    # compiling-compiled
    for patch in [patch5, patch6]:
        if patch is None:
            continue
        mock_compile(patch)

    # testing-tested
    for patch in [patch6]:
        if patch is None:
            continue
        mock_test(patch)

class TestPatchApplier(AbstractPatchApplier):
    # Standard API is being tested, just need to implement constant results
    # def begin(self, branch):
    # def is_done(self, branch):
    # def did_fail(self, branch):

    def __init__(self):
        super().__init__()

    def initialize_directories(self, branch):
        pass

    def do_apply_async(self, branch, apply_task, signal_done):
        return

    def download_and_save(self, download_task, attachment):
        fixture_file = os.path.join(settings.BASE_DIR, "commitfest/fixtures/protocol_6/v1-0001-PATCH-protocol-6.patch")
        CfbotTaskArtifact.objects.create(
            task=download_task,
            name=attachment["filename"],
            path=fixture_file,
            size=os.path.getsize(fixture_file),
            body=None,
            payload=attachment,
        )
        attachment["download_result"] = "Success"
        return True

    def perform_apply(self, filename, payload):
        payload["apply_result"] = "Success"
        with open(os.path.join(settings.BASE_DIR, "commitfest/fixtures/protocol_6/apply.out"), "r") as stdout_file:
            payload["stdout"] = stdout_file.read()
        with open(os.path.join(settings.BASE_DIR, "commitfest/fixtures/protocol_6/apply.err"), "r") as stderr_file:
            payload["stderr"] = stderr_file.read()
        return True

    def convert_to_merge_commit(self, branch):
        return True

    def get_patch_count(self, branch):
        return 1

    def get_base_commit_sha(self, branch):
        return "e29df428a1dca4112aad640c889a9a54642759c9"

    def get_head_commit_sha(self,branch):
        return "473eb7bd581737e34ca4400bc02340cc1474a6cd"

    def git_shortstat(self, branch, from_commit, to_commit):
        return 10, 4

    def get_delay(self, branch):
        return None


class TestPatchCompiler(AbstractPatchCompiler):
    # Standard API is being tested, just need to implement constant results
    # def begin(self, branch):
    # def is_done(self, branch):
    # def did_fail(self, branch):
    def __init__(self):
        super().__init__()

    def get_delay(self, branch):
        return None

    def do_compile_async(self, branch, compile_task, signal_done):
        with open(os.path.join(settings.BASE_DIR, "commitfest/fixtures/protocol_6/configure.out"), "r") as stdout_file:
            compile_result = type("CompileResult", (object,), {
            "returncode": 0,
            "stdout": stdout_file.read(),
            "stderr": ""
            })()
        signal_done(branch, compile_task, compile_result)
        return

    def do_configure_sync(self, branch):
        with open(os.path.join(settings.BASE_DIR, "commitfest/fixtures/protocol_6/configure.out"), "r") as stdout_file:
            configure_result = type("ConfigureResult", (object,), {
            "returncode": 0,
            "stdout": stdout_file.read(),
            "stderr": ""
            })()
        return configure_result

class TestPatchTester(AbstractPatchTester):
    # Standard API is being tested, just need to implement constant results
    # def begin(self, branch):
    # def is_done(self, branch):
    # def did_fail(self, branch):
    def __init__(self):
        super().__init__()

    def get_delay(self, branch):
        return None

    def do_test_async(self, branch, test_task, signal_done):
        with open(os.path.join(settings.BASE_DIR, "commitfest/fixtures/protocol_6/test.out"), "r") as stdout_file:
            test_result = type("TestResult", (object,), {
            "returncode": 0,
            "stdout": stdout_file.read(),
            "stderr": ""
            })()
        signal_done(branch, test_task, test_result)
        return

branchManager = BranchManager(
    applier=TestPatchApplier(),
    burner=TestPatchCompiler(),
    tester=TestPatchTester(),
    notifier=Notifier(),
)

def mock_apply(patch_id):
    cfbot_branch = CfbotBranch.objects.filter(patch_id=patch_id).first()
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    time.sleep(0.5)
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    time.sleep(0.5)
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    cfbot_branch.save()

def mock_compile(patch_id):
    cfbot_branch = CfbotBranch.objects.filter(patch_id=patch_id).first()
    Workflow.processBranch(cfbot_branch, branchManager=branchManager) # init
    Workflow.processBranch(cfbot_branch, branchManager=branchManager) # sync config, async compile
    time.sleep(0.5) # wait for async compile to finish
    # should be enough...don't want to infinite loop or even wait too long
    #Workflow.processBranch(cfbot_branch, branchManager=branchManager) # compile done
    Workflow.processBranch(cfbot_branch, branchManager=branchManager) # compiled
    cfbot_branch.save()

def mock_test(patch_id):
    cfbot_branch = CfbotBranch.objects.filter(patch_id=patch_id).first()
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    time.sleep(0.5) # see mock_compile
    Workflow.processBranch(cfbot_branch, branchManager=branchManager)
    cfbot_branch.save()

