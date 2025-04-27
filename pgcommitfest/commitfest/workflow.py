import os
import shutil
import traceback
from django.conf import settings
import requests
from django.contrib.auth.models import User
from django.db import models
from django.db import transaction, connection
from django.db.models import Q
from django.shortcuts import get_object_or_404

from datetime import datetime
import json
import subprocess
import re
import threading

from pgcommitfest.userprofile.models import UserProfile

from .util import DiffableModel, datetime_serializer

from .models import *


# Workflow provides access to the elements required to support
# the workflow this application is built for.  These elements exist
# independent of what the user is presently seeing on their page.
class Workflow:
    def get_poc_for_patchid_or_404(patchid):
        return get_object_or_404(
            Patch.objects.select_related(), pk=patchid
        ).current_patch_on_commitfest()

    # At most a single Open CommitFest is allowed and this function returns it.
    def open_cf():
        cfs = list(CommitFest.objects.filter(status=CommitFest.STATUS_OPEN))
        return cfs[0] if len(cfs) == 1 else None

    # At most a single In Progress CommitFest is allowed and this function returns it.
    def inprogress_cf():
        cfs = list(CommitFest.objects.filter(status=CommitFest.STATUS_INPROGRESS))
        return cfs[0] if len(cfs) == 1 else None

    # At most a single Parked CommitFest is allowed and this function returns it.
    def parked_cf():
        cfs = list(CommitFest.objects.filter(status=CommitFest.STATUS_PARKED))
        return cfs[0] if len(cfs) == 1 else None

    # Returns whether the user is a committer in general and for this patch
    # since we retrieve all committers in order to answer these questions
    # provide that list as a third return value.  Passing None for both user
    # and patch still returns the list of committers.
    def isCommitter(user, patch):
        all_committers = Committer.objects.filter(active=True).order_by(
            "user__last_name", "user__first_name"
        )
        if not user and not patch:
            return False, False, all_committers

        committer = [c for c in all_committers if c.user == user]
        if len(committer) == 1:
            is_committer = True
            is_this_committer = committer[0] == patch.committer
        else:
            is_committer = is_this_committer = False
        return is_committer, is_this_committer, all_committers

    def getCommitfest(cfid):
        if cfid is None or cfid == "":
            return None
        try:
            int_cfid = int(cfid)
            cfs = list(CommitFest.objects.filter(id=int_cfid))
            if len(cfs) == 1:
                return cfs[0]
            else:
                return None
        except ValueError:
            return None

    # Implements a re-entrant Commitfest POC creation procedure.
    # Returns the new POC object.
    # Creates history and notifies as a side-effect.
    def createNewPOC(patch, commitfest, initial_status, by_user):
        poc, created = PatchOnCommitFest.objects.update_or_create(
            patch=patch,
            commitfest=commitfest,
            defaults=dict(
                enterdate=datetime.now(),
                status=initial_status,
                leavedate=None,
            ),
        )
        poc.patch.set_modified()
        poc.patch.save()
        poc.save()

        PatchHistory(
            patch=poc.patch,
            by=by_user,
            what="{} in {}".format(poc.statusstring, commitfest.name),
        ).save_and_notify()

        return poc

    # The rule surrounding patches is they may only be in one active
    # commitfest at a time.  The transition function takes a patch
    # open in one commitfest and associates it, with the same status,
    # in a new commitfest; then makes it inactive in the original.
    # Returns the new POC object.
    # Creates history and notifies as a side-effect.
    def transitionPatch(poc, target_cf, by_user):
        Workflow.userCanTransitionPatch(poc, target_cf, by_user)

        existing_status = poc.status

        # History looks cleaner if we've left the existing
        # commitfest entry before joining the new one.  Plus,
        # not allowed to change non-current commitfest status
        # and once the new POC is created it becomes current.

        Workflow.updatePOCStatus(poc, PatchOnCommitFest.STATUS_NEXT, by_user)

        new_poc = Workflow.createNewPOC(poc.patch, target_cf, existing_status, by_user)

        return new_poc

    def userCanTransitionPatch(poc, target_cf, user):
        # Policies not allowed to be broken by anyone.

        # Prevent changes to non-current commitfest for the patch
        # Meaning, status changed to Moved before/during transitioning
        # i.e., a concurrent action took place.
        if poc.commitfest != poc.patch.current_commitfest():
            raise Exception("Patch commitfest is not its current commitfest.")

        # The UI should be preventing people from trying to perform no-op requests
        if poc.commitfest.id == target_cf.id:
            raise Exception("Cannot transition to the same commitfest.")

        # This one is arguable but facilitates treating non-open status as final
        # A determined staff member can always change the status first.
        if poc.is_closed:
            raise Exception("Cannot transition a closed patch.")

        # We trust privileged users to make informed choices
        if user.is_staff:
            return

        if target_cf.isclosed:
            raise Exception("Cannot transition to a closed commitfest.")

        if target_cf.isinprogress:
            raise Exception("Cannot transition to an in-progress commitfest.")

        # Prevent users from moving closed patches, or moving open ones to
        # non-open commitfests.  The else clause should be a can't happen.
        if poc.is_open and target_cf.isopen:
            pass
        else:
            # Default deny policy basis
            raise Exception("Transition not permitted.")

    def userCanChangePOCStatus(poc, new_status, user):
        # Policies not allowed to be broken by anyone.

        # Prevent changes to non-current commitfest for the patch
        # Meaning, change status to Moved before/during transitioning
        if poc.commitfest != poc.patch.current_commitfest():
            raise Exception("Patch commitfest is not its current commitfest.")

        # The UI should be preventing people from trying to perform no-op requests
        if poc.status == new_status:
            raise Exception("Cannot change to the same status.")

        # We want commits to happen from, usually, In Progress commitfests,
        # or Open ones for exempt patches.  We accept Future ones too just because
        # they do represent a proper, if non-current, Commitfest.
        if (
            poc.commitfest.id == CommitFest.STATUS_PARKED
            and new_status == PatchOnCommitFest.STATUS_COMMITTED
        ):
            raise Exception("Cannot change status to committed in a parked commitfest.")

        # We trust privileged users to make informed choices
        if user.is_staff:
            return

        is_committer, is_this_committer, all_committers = Workflow.isCommitter(
            user, poc.patch
        )

        # XXX Not sure if we want to tighten this up to is_this_committer
        # with only the is_staff exemption
        if new_status == PatchOnCommitFest.STATUS_COMMITTED and not is_committer:
            raise Exception("Only a committer can set status to committed.")

        if new_status == PatchOnCommitFest.STATUS_REJECTED and not is_committer:
            raise Exception("Only a committer can set status to rejected.")

        if new_status == PatchOnCommitFest.STATUS_RETURNED and not is_committer:
            raise Exception("Only a committer can set status to returned.")

        if (
            new_status == PatchOnCommitFest.STATUS_WITHDRAWN
            and user not in poc.patch.authors.all()
        ):
            raise Exception("Only the author can set status to withdrawn.")

        # Prevent users from modifying closed patches
        # The else clause should be considered a can't happen
        if poc.is_open:
            pass
        else:
            raise Exception("Cannot change status of closed patch.")

    # Update the status of a PoC
    # Returns True if the status was changed, False for a same-status no-op.
    # Creates history and notifies as a side-effect.
    def updatePOCStatus(poc, new_status, by_user):
        # XXX Workflow disallows this no-op but not quite ready to enforce it.
        if poc.status == new_status:
            return False

        Workflow.userCanChangePOCStatus(poc, new_status, by_user)

        poc.status = new_status
        poc.leavedate = datetime.now() if not poc.is_open else None
        poc.patch.set_modified()
        poc.patch.save()
        poc.save()
        PatchHistory(
            patch=poc.patch,
            by=by_user,
            what="{} in {}".format(
                poc.statusstring,
                poc.commitfest.name,
            ),
        ).save_and_notify()

        return True


    def createBranch(patch_id, message_id):
        if not patch_id or not message_id:
            raise ValueError("Patch ID and Message ID are required.")

        # Create a new branch using CfbotBranch
        branch_name = f"branch_{patch_id}"
        apply_url = f"http://example.com/apply/{patch_id}"
        status = "new"

        # Get the corresponding queue item and use its get_attachments method
        queue = CfbotQueue.objects.first()
        if not queue:
            raise ValueError("No queue found.")

        queue_item = queue.items.filter(patch_id=patch_id).first()
        if not queue_item:
            raise ValueError(f"No queue item found for patch ID {patch_id}.")

        branch, created = CfbotBranch.objects.update_or_create(
            patch_id=patch_id,
            defaults={
                "branch_id": patch_id,  # Using patch_id as branch_id for simplicity
                "branch_name": branch_name,
                "apply_url": apply_url,
                "status": status,
                "created": datetime.now(),
                "modified": datetime.now(),
            },
        )

        queue_item.processed_date = datetime.now()
        queue_item.save()

        # Create a history item for the branch
        if created:
            CfbotBranchHistory.add_branch_to_history(branch)

        return branch

    def processBranch(branch, branchManager):
        if not branchManager:
            raise ValueError("BranchManager instance is required.")
        return branchManager.process(branch)

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

        elif old_branch_status in {"finished", "applying-aborted", "applying-failed", "compiling-aborted", "compiling-failed", "testing-aborted", "testing-failed"}:
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
            existing_tasks = CfbotTask.objects.filter(branch_id=branch.branch_id).order_by('position')
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
                        attachment["date"] = attachment["date"].isoformat() # XXX: hack for JSONField usage
                        if attachment.get("ispatch") and fail_count == 0:
                            patch_count += 1
                            result = self.download_and_save(download_task, attachment)
                            if not result: fail_count += 1
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
                        for command in CfbotTaskCommand.objects.filter(task=download_task, type="Patchset File").order_by('name'):
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
                    for command in CfbotTaskCommand.objects.filter(task=download_task, type="Patchset File"):
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

            apply_task = CfbotTask.objects.filter(branch_id=branch.branch_id, task_name="Apply").first()
            download_task = CfbotTask.objects.filter(branch_id=branch.branch_id, task_name="Download").first()
            if download_task and download_task.is_done() and branch.patch_count is None:
                def run_apply_task():
                    try:
                        has_failed = False
                        for command in CfbotTaskCommand.objects.filter(task=apply_task, type="Apply Patch").order_by('name'):
                            command.status = "EXECUTING"
                            command.save()
                            if not has_failed and self.perform_apply(command.name, command.payload):
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
                        for command in CfbotTaskCommand.objects.filter(task=apply_task, type="Apply Patch", status="EXECUTING"):
                            command.status = "ABORTED"
                            command.save()
                        apply_task.status = "ABORTED"
                        apply_task.payload = {"error": str(e)}
                        apply_task.save()
                        print(f"Error in run_apply_task: {e}")
                        traceback.print_exc()

                branch.patch_count = CfbotTaskCommand.objects.filter(
                    task=apply_task,
                    type="Apply Patch"
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
            first_additions, first_deletions = self.git_shortstat(branch, "origin/master", "HEAD~%s" % (branch.patch_count - 1,))
            all_additions, all_deletions = self.git_shortstat(branch, "origin/master", "HEAD")

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
            apply_task.status = "COMPLETED" if apply_result.returncode == 0 else "FAILED"
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

        def get_head_commit_sha(self,branch):
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
            existing_tasks = CfbotTask.objects.filter(branch_id=branch.branch_id).order_by('position')
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
                    configure_task.status = "COMPLETED" if configure_result.returncode == 0 else "FAILED"
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
                        self.do_compile_async(branch, make_task, signal_done=self.signal_done_cb)
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
            compile_task.status = "COMPLETED" if compile_result.returncode == 0 else "FAILED"
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
            existing_tasks = CfbotTask.objects.filter(branch_id=branch.branch_id).order_by('position')
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
                        self.do_test_async(branch, test_task, signal_done=self.signal_done_cb)
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

