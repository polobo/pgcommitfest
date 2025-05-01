from django.db import transaction

import threading
import traceback
from datetime import datetime

from .cfbot_queue import CfbotQueue
from .models import CfbotBranchHistory, CfbotTask, CfbotTaskCommand


class BranchManager:
    """
    A class to manage branch operations.
    """

    def __init__(self, applier, compiler, tester, notifier):
        """
        Initialize the BranchManager with applier, compiler, tester, and notifier instances.
        """
        self.applier = applier
        self.compiler = compiler
        self.tester = tester
        self.notifier = notifier

    def clear_tasks(self, branch):
        """
        Clear all tasks associated with the given branch.
        """
        tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
        for task in tasks:
            task.delete()

    def process(self, branch):
        if not branch:
            raise ValueError("Branch cannot be None.")

        delay_for = 0
        """
        Process the given branch by creating a new branch instance with updated status.
        The input branch remains unaltered.
        """
        old_branch_status = branch.status
        if old_branch_status == "new":
            # Intentional non-clearing of tasks here.
            # We should fail to begin, and thus abort,
            # if tasks already exist.
            if self.applier.begin(branch):
                branch.status = "applying"
            else:
                # envrionmental issues, up to the point of retrieving files
                # returns just before the step of running apply-patches.sh
                # also aborts if there happen to be no patches recognized
                # in the task queue
                branch.status = "applying-aborted"
                delay_for = None

        elif old_branch_status == "applying":
            if self.applier.is_done(branch):
                if self.applier.did_fail(branch):
                    # XXX: true bit-rot
                    branch.status = "applying-failed"
                    delay_for = None

                else:
                    branch.status = "applied"
            else:
                delay_for = self.applier.get_delay(branch)

        elif old_branch_status == "applied":
            self.clear_tasks(branch)
            if self.compiler.begin(branch):
                branch.status = "compiling"
            else:
                # envrionmental issues, up to the point of retrieving files
                # returns just before the step of running apply-patches.sh
                branch.status = "compiling-aborted"
                delay_for = None

        elif old_branch_status == "compiling":
            # Run apply-patches.sh and return.  We are sync right now
            # so this should never actually return False, which would
            # require async processing where we simply want to try again
            if self.compiler.is_done(branch):
                if self.compiler.did_fail(branch):
                    branch.status = "compiling-failed"
                    delay_for = None

                else:
                    branch.status = "compiled"
            else:
                delay_for = self.compiler.get_delay(branch)

        elif old_branch_status == "compiled":
            self.clear_tasks(branch)
            if self.tester.begin(branch):
                branch.status = "testing"
            else:
                branch.status = "testing-aborted"
                delay_for = None

        elif old_branch_status == "testing":
            if self.tester.is_done(branch):
                if self.tester.did_fail(branch):
                    branch.status = "testing-failed"
                    delay_for = None
                else:
                    branch.status = "tested"
            else:
                delay_for = self.tester.get_delay(branch)

        elif old_branch_status == "tested":
            self.clear_tasks(branch)
            branch.status = "notifying"
            self.notifier.notify_branch_update(branch)
            self.notifier.notify_branch_tested(branch)
            branch.status = "finished"
            delay_for = None

        elif old_branch_status in {
            "finished",
            "applying-aborted",
            "applying-failed",
            "compiling-aborted",
            "compiling-failed",
            "testing-aborted",
            "testing-failed",
        }:
            # Didn't listen the first time be we don't enforce this
            delay_for = None

        else:
            raise ValueError(f"Unknown status: {old_branch_status}")

        self.notifier.notify_branch_update(branch)
        return branch, delay_for

    class PatchApplierTemplate:
        """
        A class responsible for applying patches to branches.
        """

        def __init__(self):
            pass

        @transaction.atomic
        def begin(self, branch):
            """
            Apply the patchset to the branch.
            """
            # We go first and do not expect any tasks for us to handle.  We create the patchset file tasks.
            existing_tasks = CfbotTask.objects.filter(
                branch_id=branch.branch_id
            ).order_by("position")
            if existing_tasks:
                return False

            download_task = CfbotTask.objects.create(
                task_id=f"Download-{branch.branch_id}",
                task_name="Download",
                patch=branch.patch,
                branch_id=branch.branch_id,
                position=1,
                status="EXECUTING",
                payload=None,
            )

            def run_download_task():
                self.initialize_directories(branch)

                patch = branch.patch
                attachments = patch.get_attachments()
                patch_count = 0
                fail_count = 0
                try:
                    for position, attachment in enumerate(attachments, start=1):
                        attachment["date"] = attachment[
                            "date"
                        ].isoformat()  # XXX: hack for JSONField usage
                        if attachment.get("ispatch") and fail_count == 0:
                            patch_count += 1
                            result = self.download_and_save(download_task, attachment)
                            if not result:
                                fail_count += 1
                            command = CfbotTaskCommand.objects.create(
                                task=download_task,
                                name=attachment["filename"],
                                status="COMPLETED" if result else "FAILED",
                                type="Patchset File",
                                duration=0,
                                payload=attachment,
                            )

                        else:
                            command = CfbotTaskCommand.objects.create(
                                task=download_task,
                                name=attachment["filename"],
                                status="IGNORED",
                                type="Other File",
                                duration=0,
                                payload=attachment,
                            )

                    if fail_count == 0:
                        apply_task = CfbotTask.objects.create(
                            task_id=f"Apply-{branch.branch_id}",
                            task_name="Apply",
                            patch=branch.patch,
                            branch_id=branch.branch_id,
                            position=2,
                            status="CREATED",
                            payload={},
                        )
                        for command in CfbotTaskCommand.objects.filter(
                            task=download_task, type="Patchset File"
                        ).order_by("name"):
                            CfbotTaskCommand.objects.create(
                                task=apply_task,
                                name=command.name,
                                status="CREATED",
                                type="Apply Patch",
                                duration=0,
                                payload={},
                            )
                    download_task.status = "COMPLETED" if fail_count == 0 else "FAILED"
                    download_task.save()
                except Exception as e:
                    # Handle exceptions and mark tasks as failed
                    for command in CfbotTaskCommand.objects.filter(
                        task=download_task, type="Patchset File"
                    ):
                        command.status = "ABORTED"
                        command.save()
                    # do this last so it refelcts the aborts on the file downloads
                    download_task.status = "ABORTED"
                    download_task.payload = {"error": str(e)}
                    download_task.save()

            threading.Thread(target=run_download_task).start()

            return True

        def is_done(self, branch):
            """
            Check if all tasks for the branch are completed and apply patches for tasks with the name 'Patchset File'.
            """
            tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
            if all(task.is_done() for task in tasks):
                return True

            apply_task = CfbotTask.objects.filter(
                branch_id=branch.branch_id, task_name="Apply"
            ).first()
            download_task = CfbotTask.objects.filter(
                branch_id=branch.branch_id, task_name="Download"
            ).first()
            if download_task and download_task.is_done() and branch.patch_count is None:

                def run_apply_task():
                    try:
                        has_failed = False
                        for command in CfbotTaskCommand.objects.filter(
                            task=apply_task, type="Apply Patch"
                        ).order_by("name"):
                            command.status = "EXECUTING"
                            command.save()
                            if not has_failed and self.perform_apply(
                                command.name, command.payload
                            ):
                                command.status = "COMPLETED"
                            else:
                                if has_failed:
                                    command.status = "IGNORED"
                                else:
                                    has_failed = True
                                    command.status = "FAILED"

                            command.save()
                        apply_task.status = "COMPLETED" if not has_failed else "FAILED"
                        apply_task.save()
                    except Exception as e:
                        for command in CfbotTaskCommand.objects.filter(
                            task=apply_task, type="Apply Patch", status="EXECUTING"
                        ):
                            command.status = "ABORTED"
                            command.save()
                        apply_task.status = "ABORTED"
                        apply_task.payload = {"error": str(e)}
                        apply_task.save()
                        print(f"Error in run_apply_task: {e}")
                        traceback.print_exc()

                branch.patch_count = CfbotTaskCommand.objects.filter(
                    task=apply_task, type="Apply Patch"
                ).count()
                branch.save()
                apply_task.status = "EXECUTING"
                apply_task.save()
                threading.Thread(target=run_apply_task).start()

            return False

        def did_fail(self, branch):
            """
            Apply the results of branch testing. Return True if any task is a failure.
            """
            tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
            if any(task.is_failure() for task in tasks):
                failed = True
            else:
                failed = False

            branch.patch_count = self.get_patch_count(branch)
            first_additions, first_deletions = self.git_shortstat(
                branch, "origin/master", "HEAD~%s" % (branch.patch_count - 1,)
            )
            all_additions, all_deletions = self.git_shortstat(
                branch, "origin/master", "HEAD"
            )

            if not failed:
                if self.convert_to_merge_commit(branch):
                    failed = False
                else:
                    failed = True

            if not failed:
                branch.commit_id = self.get_head_commit_sha(branch)
                branch.base_commit_sha = self.get_base_commit_sha(branch)

                apply_results = {
                    "merge_commit_sha": branch.commit_id,
                    "base_commit_sha": branch.base_commit_sha,
                    "patch_count": branch.patch_count,
                    "first_additions": first_additions,
                    "first_deletions": first_deletions,
                    "all_additions": all_additions,
                    "all_deletions": all_deletions,
                }

                branch.first_additions = apply_results["first_additions"]
                branch.first_deletions = apply_results["first_deletions"]
                branch.all_additions = apply_results["all_additions"]
                branch.all_deletions = apply_results["all_deletions"]

            return failed

        def signal_done_cb(self, branch, apply_task, apply_result):
            apply_task.payload = {
                "stdout": apply_result.stdout,
                "stderr": apply_result.stderr,
            }
            apply_task.status = (
                "COMPLETED" if apply_result.returncode == 0 else "FAILED"
            )
            apply_task.save()

        def initialize_directories(self, branch):
            raise NotImplementedError("Abstract Method")

        def download_and_save(self, attachment):
            raise NotImplementedError("Abstract Method")

        def perform_apply(self, filename, payload):
            raise NotImplementedError("Abstract Method")

        def convert_to_merge_commit(self, branch):
            raise NotImplementedError("Abstract Method")

        def get_patch_count(self, branch):
            raise NotImplementedError("Abstract Method")

        def get_base_commit_sha(self, branch):
            raise NotImplementedError("Abstract Method")

        def get_head_commit_sha(self, branch):
            raise NotImplementedError("Abstract Method")

        def git_shortstat(self, branch, from_commit, to_commit):
            raise NotImplementedError("Abstract Method")

        def get_delay(self, branch):
            raise NotImplementedError("Abstract Method")

    class PatchCompilerTemplate:
        """
        A class responsible for burning patches.
        """

        def __init__(self):
            pass

        def begin(self, branch):
            """
            Create a compile task for the branch and mark existing tasks as completed.
            """
            # All tasks from the previous subsystem should have been removed leaving us with a clean slate
            existing_tasks = CfbotTask.objects.filter(
                branch_id=branch.branch_id
            ).order_by("position")
            if existing_tasks:
                return False

            CfbotTask.objects.create(
                task_id=f"Compile {branch.branch_name}",
                task_name="Compile",
                patch=branch.patch,
                branch_id=branch.branch_id,
                position=1,
                status="CREATED",
                payload=None,
            )

            return True

        def is_done(self, branch):
            """
            Filter for "Compile", "Configure", and "Make" tasks, perform their respective work, and update their payloads.
            """
            configure_is_done = None
            make_is_done = None
            compile_is_done = None
            compile_task = None

            # Loop through tasks and update the *_is_done booleans appropriately
            tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
            for task in tasks:
                if task.task_name == "Meson Setup":
                    configure_is_done = task.is_done()
                elif task.task_name == "Ninja":
                    make_is_done = task.is_done()
                elif task.task_name == "Compile":
                    compile_is_done = task.is_done()
                    compile_task = task

            if compile_is_done is None:
                raise ValueError("Compile task not found.")

            if compile_is_done:
                return compile_is_done

            if compile_task.status == "CREATED":
                compile_task.status = "EXECUTING"
                compile_task.save()

            if configure_is_done is None:
                # Create "Configure" task
                configure_task = CfbotTask.objects.create(
                    task_id=f"Meson Setup {branch.branch_name}",
                    task_name="Meson Setup",
                    patch=branch.patch,
                    branch_id=branch.branch_id,
                    position=2,
                    status="EXECUTING",
                    payload=None,
                )

                try:
                    configure_result = self.do_configure_sync(branch)
                    configure_task.payload = {
                        "stdout": configure_result.stdout,
                        "stderr": configure_result.stderr,
                    }
                    configure_task.status = (
                        "COMPLETED" if configure_result.returncode == 0 else "FAILED"
                    )
                except Exception as e:
                    configure_task.payload = {"error": str(e)}
                    print(e)
                    configure_task.status = "FAILED"
                configure_task.save()

                if configure_task.status == "FAILED":
                    compile_task.status = "COMPLETED"
                    compile_task.save()
                    return True

            if make_is_done is None:
                # Create "Make" task
                make_task = CfbotTask.objects.create(
                    task_id=f"Ninja {branch.branch_name}",
                    task_name="Ninja",
                    patch=branch.patch,
                    branch_id=branch.branch_id,
                    position=3,
                    status="EXECUTING",
                    payload=None,
                )

                def run_make_task():
                    try:
                        self.do_compile_async(
                            branch, make_task, signal_done=self.signal_done_cb
                        )
                    except Exception as e:
                        make_task.payload = {"error": str(e)}
                        make_task.status = "FAILED"
                    make_task.save()

                threading.Thread(target=run_make_task).start()

            if make_is_done and configure_is_done:
                compile_task.status = "COMPLETED"
                compile_task.save()
                return True

            return False

        def did_fail(self, branch):
            """
            Apply the results of branch compilation. Return True if any task is a failure.
            """
            tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
            if any(task.is_failure() for task in tasks):
                failed = True
            else:
                failed = False

            return failed

        def signal_done_cb(self, branch, compile_task, compile_result):
            compile_task.payload = {
                "stdout": compile_result.stdout,
                "stderr": compile_result.stderr,
            }
            compile_task.status = (
                "COMPLETED" if compile_result.returncode == 0 else "FAILED"
            )
            compile_task.save()

        def get_delay(self, branch):
            raise NotImplementedError("Abstract Method")

        def do_compile_async(self, branch, compile_task, signal_done):
            raise NotImplementedError("Abstract Method")

        def do_configure_sync(self, branch):
            raise NotImplementedError("Abstract Method")

    class PatchTesterTemplate:
        """
        A class responsible for testing patches.
        """

        def __init__(self):
            pass

        def begin(self, branch):
            """
            Check if all tasks for the branch are completed and perform testing work.
            """
            # All tasks from the previous subsystem should have been removed leaving us with a clean slate
            existing_tasks = CfbotTask.objects.filter(
                branch_id=branch.branch_id
            ).order_by("position")
            if existing_tasks:
                return False

            CfbotTask.objects.create(
                task_id=f"Test {branch.branch_name}",
                task_name="Test",
                patch=branch.patch,
                branch_id=branch.branch_id,
                position=1,
                status="CREATED",
                payload=None,
            )

            return True

        def is_done(self, branch):
            """
            Create a test task for the branch and mark existing tasks as completed.
            """
            test_is_done = None
            testing_is_done = None
            testing_task = None

            # Loop through tasks and update the *_is_done booleans appropriately
            tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
            for task in tasks:
                if task.task_name == "Run Test":
                    test_is_done = task.is_done()
                elif task.task_name == "Test":
                    testing_is_done = task.is_done()
                    testing_task = task

            if testing_is_done is None:
                raise ValueError("Testing task not found.")

            if testing_is_done:
                return testing_is_done

            if testing_task.status == "CREATED":
                testing_task.status = "EXECUTING"
                testing_task.save()

            if test_is_done is None:
                # Create "Test" task
                test_task = CfbotTask.objects.create(
                    task_id=f"Meson Test {branch.branch_name}",
                    task_name="Run Test",
                    patch=branch.patch,
                    branch_id=branch.branch_id,
                    position=2,
                    status="EXECUTING",
                    payload=None,
                )

                def run_test_task():
                    try:
                        self.do_test_async(
                            branch, test_task, signal_done=self.signal_done_cb
                        )
                    except Exception as e:
                        test_task.payload = {"error": str(e)}
                        test_task.status = "FAILED"
                    test_task.save()

                    # within build_dir/meson-logs/testlog*
                    # there are three artifacts to collect as well
                    # need to either bring the concept over from cfbot
                    # or figure out something else.

                threading.Thread(target=run_test_task).start()

            if test_is_done:
                testing_task.status = "COMPLETED"
                testing_task.save()
                return True

            return False

        def did_fail(self, branch):
            """
            Apply the results of branch testing. Return True if any task is a failure.
            """
            tasks = CfbotTask.objects.filter(branch_id=branch.branch_id)
            if any(task.is_failure() for task in tasks):
                return True
            return False

        def signal_done_cb(self, branch, test_task, test_result):
            test_task.payload = {
                "stdout": test_result.stdout,
                "stderr": test_result.stderr,
            }
            test_task.status = "COMPLETED" if test_result.returncode == 0 else "FAILED"
            test_task.save()

        def do_test_async(self, branch, test_task, signal_done):
            raise NotImplementedError("Abstract Method")

        def get_delay(self, branch):
            raise NotImplementedError("Abstract Method")

    class Notifier:
        """
        A class responsible for sending notifications.
        """

        def notify_branch_update(self, branch):
            if branch.status in {"compiling-aborted", "compiling-failed"}:
                branch.needs_rebase_since = datetime.now()
                branch.failing_since = datetime.now()
                self.update_queue_ignore_date(branch)
            elif branch.status in {"testing-aborted", "testing-failed"}:
                branch.needs_rebase_since = None
                branch.failing_since = datetime.now()
                self.update_queue_ignore_date(branch)

            if branch.status in {"compiled", "compiling-failed"}:
                self.update_queue_latest_base_commit_sha(branch)

            branch.save()
            return CfbotBranchHistory.add_branch_to_history(branch)

        def notify_branch_tested(self, branch):
            pass

        def update_queue_latest_base_commit_sha(self, branch):
            """
            Update the queue item's last_base_commit_sha.
            """
            # Update the queue item's last_base_commit_sha
            queue = CfbotQueue.objects.first()
            if queue:
                queue_item = queue.items.filter(patch_id=branch.patch_id).first()
                if queue_item:
                    queue_item.last_base_commit_sha = branch.base_commit_sha
                    queue_item.save()

        def update_queue_ignore_date(self, branch):
            """
            Update the queue item's ignore_date.
            """
            # Update the queue item's ignore_date
            queue = CfbotQueue.objects.first()
            if queue:
                queue_item = queue.items.filter(patch_id=branch.patch_id).first()
                if queue_item:
                    queue_item.ignore_date = datetime.now()
                    queue_item.save()
