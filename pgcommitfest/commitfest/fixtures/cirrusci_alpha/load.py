import time
from datetime import datetime

import requests

from pgcommitfest.commitfest.apiv1 import doCreateBranch
from pgcommitfest.commitfest.branchmanager_template import BranchManager
from pgcommitfest.commitfest.cfbot_queue import CfbotQueue
from pgcommitfest.commitfest.models import (
    CfbotBranch,
    CfbotTask,
    CfbotTaskArtifact,
    CfbotTaskCommand,
    CommitFest,
    MailThread,
    MailThreadAttachment,
    Patch,
    PatchOnCommitFest,
    TargetVersion,
    Topic,
)


def create_patches():
    # Create a topic and target version for the patches
    topic, _ = Topic.objects.get_or_create(topic="Example Topic")
    target_version, _ = TargetVersion.objects.get_or_create(version="16.0")
    queue = CfbotQueue.retrieve()
    target_cf = CommitFest.objects.filter(status=CommitFest.STATUS_OPEN).first()

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
    patch4 = None
    patch5 = None
    # patch6 = None

    # Create threads for each patch using a comprehension
    patches = [patch1, patch2, patch3, patch4, patch5, patch6]
    threads = [
        None
        if patch is None
        else MailThread.objects.get_or_create(
            messageid=f"thread-message-id-{10000 + patch.id}",
            subject=f"Thread for {patch.name}",
            firstmessage="2023-01-01T00:00:00Z",
            firstauthor=f"Author {patch.id}",
            latestmessage="2023-01-01T00:00:00Z",
            latestauthor=f"Author {patch.id}",
            latestsubject=f"Thread for {patch.name}",
            latestmsgid=f"thread-message-id-{10000 + patch.id}",
        )[0]
        for patch in patches
    ]

    for patch in patches:
        if patch is None:
            continue
        poc = PatchOnCommitFest(
            patch=patch, commitfest=target_cf, enterdate=datetime.now()
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
            messageid=f"thread-message-id-{10000 + patch.id}",
            attachmentid=int(patch.id),
            filename="Test",
            date="2023-01-01T00:00:00Z",
            author=f"Author {patch.id}",
            ispatch=True,
            contenttype="text/x-diff",
        )
        patch.patchset_messageid = f"thread-message-id-{10000 + patch.id}"
        patch.patchset_messagedate = "2023-01-01T00:00:00Z"
        patch.lastmail = "2023-01-01T00:00:00Z"
        patch.save()

    for patch in [patch2, patch3, patch4, patch5, patch6]:
        if patch is None:
            continue
        queue.insert_item(patch.id, patch.patchset_messageid)

    # new
    for patch in [patch3, patch4, patch5, patch6]:
        if patch is None:
            continue
        doCreateBranch(patch.id, patch.patchset_messageid)

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


class CirrusCIPatchApplier(BranchManager.PatchApplierTemplate):
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

    def download_and_save(self, download_task, attachment, artifact_index, file_idx):
        CfbotTaskArtifact.objects.create(
            task=download_task,
            name=attachment["filename"],
            path="Test",
            size=1,
            body=None,
            payload=attachment,
            artifact_position=artifact_index,
            file_position=file_idx,
        )
        attachment["download_result"] = "Success"
        return True

    def perform_apply(self, filename, payload):
        payload["apply_result"] = "Success"
        payload["stdout"] = "Test"
        payload["stderr"] = ""
        return True

    def convert_to_merge_commit(self, branch):
        return True

    def get_patch_count(self, branch):
        return 1

    def get_base_commit_sha(self, branch):
        return "b8330624d5a18dab70ef287953309e593160300a"

    def get_head_commit_sha(self, branch):
        return "da8dfef78959a51a96746a972a6049b2ebb70e7a"

    def git_shortstat(self, branch, from_commit, to_commit):
        return 1, 1

    def get_delay(self, branch):
        return None


class CirrusCIPatchCompiler(BranchManager.PatchCompilerTemplate):
    # Standard API is being tested, just need to implement constant results
    # def begin(self, branch):
    # def is_done(self, branch):
    # def did_fail(self, branch):
    def __init__(self):
        super().__init__()

    # We do not actually do anything here so just
    # begin without creating any tasks.
    def begin(self, branch):
        """
        Create a compile task for the branch and mark existing tasks as completed.
        """
        # All tasks from the previous subsystem should have been removed leaving us with a clean slate
        existing_tasks = CfbotTask.objects.filter(branch_id=branch.branch_id).order_by(
            "position"
        )
        if existing_tasks:
            return False

        CfbotTask.objects.create(
            task_id=f"Compile {branch.branch_name}",
            task_name="Compile",
            patch=branch.patch,
            branch_id=branch.branch_id,
            position=1,
            status="COMPLETED",
            payload=None,
        )
        return True

    # Same for is_done.
    def is_done(self, branch):
        return True

    def get_delay(self, branch):
        return None

    def do_compile_async(self, branch, compile_task, signal_done):
        raise NotImplementedError("No configure, no compile...")

    def do_configure_sync(self, branch):
        return None


class CirrusCIPatchTester(BranchManager.PatchTesterTemplate):
    # Standard API is being tested, just need to implement constant results
    # def begin(self, branch):
    # def is_done(self, branch):
    # def did_fail(self, branch):
    graphURL = "https://api.cirrus-ci.com/graphql"
    graphQL = """
query BuildResult($id: ID!) {
  build(id: $id) {
    id
    status
    buildCreatedTimestamp
    tasks {
      id
      name
      status
      durationInSeconds
      commands {
        name
        type
        status
        durationInSeconds
      }
      artifacts {
        name
        files {
          path
          size
        }
      }
    }
  }
}
    """

    def retrieveStatus(self, branch):
        build_id = "5308840457011200"
        response = requests.post(
            self.graphURL, json={"query": self.graphQL, "variables": {"id": build_id}}
        )

        if response.status_code == 200:
            data = response.json()
        else:
            raise Exception(
                f"GraphQL request failed with status code {response.status_code}"
            )

        if "errors" in data:
            raise Exception(f"GraphQL error: {data['errors']}")

        return data["data"]

    def __init__(self):
        super().__init__()

    def get_delay(self, branch):
        return 60

    def do_test_async(self, branch, test_task, signal_done):
        payload = self.retrieveStatus(branch)
        build = payload["build"]
        tasks = build.get("tasks", [])
        patch = branch.patch
        branch_id = branch.branch_id

        for idx, task in enumerate(tasks):
            cfbottask, _ = CfbotTask.objects.get_or_create(
                task_id=task["id"],
                defaults={
                    "task_name": task["name"],
                    "patch": patch,
                    "branch_id": branch_id,
                    "position": idx + 3,
                    "status": task["status"],
                    "payload": task,
                },
            )
            # Always update fields in case of existing
            cfbottask.task_name = task["name"]
            cfbottask.patch = patch
            cfbottask.branch_id = branch_id
            cfbottask.position = idx + 3
            cfbottask.status = task["status"]
            cfbottask.payload = task
            cfbottask.save()

            # Commands
            for cmd_idx, cmd in enumerate(task.get("commands", [])):
                cmd_obj, _ = CfbotTaskCommand.objects.get_or_create(
                    task=cfbottask,
                    name=cmd["name"],
                    defaults={
                        "status": cmd["status"],
                        "type": cmd["type"],
                        "duration": cmd.get("durationInSeconds"),
                        "payload": cmd,
                    },
                )
                cmd_obj.status = cmd["status"]
                cmd_obj.type = cmd["type"]
                cmd_obj.duration = cmd.get("durationInSeconds")
                cmd_obj.payload = cmd
                cmd_obj.position = cmd_idx + 1
                cmd_obj.save()

            # Artifacts
            for art_idx, artifact in enumerate(task.get("artifacts", [])):
                for file_idx, file in enumerate(artifact.get("files", [])):
                    art_obj, _ = CfbotTaskArtifact.objects.get_or_create(
                        task=cfbottask,
                        name=artifact["name"],
                        path=file["path"],
                        defaults={
                            "size": file.get("size", 0),
                            "payload": file,
                        },
                    )
                    art_obj.size = file.get("size", 0)
                    art_obj.payload = file
                    art_obj.artifact_position = art_idx + 1
                    art_obj.file_position = file_idx + 1
                    art_obj.save()

        test_result = type(
            "TestResult",
            (object,),
            {"returncode": 0, "stdout": "", "stderr": "", "content": payload},
        )()
        signal_done(branch, test_task, test_result)
        return


branchManager = BranchManager(
    applier=CirrusCIPatchApplier(),
    compiler=CirrusCIPatchCompiler(),
    tester=CirrusCIPatchTester(),
    notifier=BranchManager.Notifier(),
)


def mock_apply(patch_id):
    cfbot_branch = CfbotBranch.objects.filter(patch_id=patch_id).first()
    branchManager.process(cfbot_branch)
    time.sleep(0.5)
    branchManager.process(cfbot_branch)
    time.sleep(0.5)
    branchManager.process(cfbot_branch)
    cfbot_branch.save()


def mock_compile(patch_id):
    cfbot_branch = CfbotBranch.objects.filter(patch_id=patch_id).first()
    branchManager.process(cfbot_branch)  # init
    # branchManager.process(cfbot_branch)  # sync config, async compile
    # should be enough...don't want to infinite loop or even wait too long
    # Workflow.processBranch(cfbot_branch, branchManager=branchManager) # compile done
    branchManager.process(cfbot_branch)  # compiled
    cfbot_branch.save()


def mock_test(patch_id):
    cfbot_branch = CfbotBranch.objects.filter(patch_id=patch_id).first()
    branchManager.process(cfbot_branch)
    branchManager.process(cfbot_branch)
    time.sleep(2.0)  # see mock_compile
    branchManager.process(cfbot_branch)
    cfbot_branch.save()
