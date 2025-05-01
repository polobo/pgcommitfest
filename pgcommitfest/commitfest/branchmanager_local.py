from django.conf import settings

import os
import re
import shutil
import subprocess

import requests

import pgcommitfest.commitfest.branchmanager_template as branchmanager_template

from .models import CfbotTaskArtifact


def get_branch_manager(branch):
    return branchmanager_template.BranchManager(
        getLocalPatchApplier(branch),
        getLocalPatchCompiler(branch),
        getLocalPatchTester(branch),
        getNotifier(),
    )


class LocalPatchApplier(branchmanager_template.BranchManager.PatchApplierTemplate):
    BASE_FILE_URL = settings.FILE_FETCH_URL_BASE
    APPLY_SCRIPT_SRC = "tools/postgres/"
    APPLY_SCRIPT_NAME = "apply-one-patch.sh"

    RE_ADDITIONS = re.compile(r"(\d+) insertion")
    RE_DELETIONS = re.compile(r"(\d+) deletion")

    def __init__(self, base_dir, branch_subdir, template_dir, working_dir, repo_dir):
        super().__init__()
        self.base_dir = base_dir
        self.branch_subdir = branch_subdir
        self.template_dir = template_dir
        self.working_dir = working_dir
        self.repo_dir = repo_dir

    def git_shortstat(self, branch, from_commit, to_commit):
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    self.repo_dir,
                    "diff",
                    "--shortstat",
                    from_commit,
                    to_commit,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
            )
            shortstat = result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to retrieve base commit SHA: {e.stderr.strip()}")

        additions = re.search(self.RE_ADDITIONS, shortstat)
        deletions = re.search(self.RE_DELETIONS, shortstat)

        if additions:
            additions = int(additions.group(1))
        else:
            additions = 0

        if deletions:
            deletions = int(deletions.group(1))
        else:
            deletions = 0

        return additions, deletions

    def initialize_directories(self, branch):
        """
        Check and clear the working and repository directories if they exist.
        Raise FileNotFoundError if they do not exist.
        """
        if not os.path.exists(self.base_dir):
            raise FileNotFoundError(f"Base directory '{self.base_dir}' does not exist.")

        """
        Ensure the template directory exists, is non-empty, and contains a .git directory.
        """
        if not os.path.exists(self.template_dir):
            raise FileNotFoundError(
                f"Template directory '{self.template_dir}' does not exist."
            )

        if not os.listdir(self.template_dir):
            raise ValueError(f"Template directory '{self.template_dir}' is empty.")

        git_dir = os.path.join(self.template_dir, ".git")
        if not os.path.exists(git_dir):
            raise FileNotFoundError(
                f"Template directory '{self.template_dir}' does not contain a .git directory."
            )

        if os.path.exists(os.path.join(self.base_dir, self.branch_subdir)):
            shutil.rmtree(os.path.join(self.base_dir, self.branch_subdir))

        os.makedirs(os.path.join(self.base_dir, self.branch_subdir))
        os.makedirs(self.working_dir)

        # Copy the template directory to the working directory
        shutil.copytree(self.template_dir, self.repo_dir)

        # Copy the apply script to the repository directory
        apply_script_path = os.path.join(
            settings.BASE_DIR, "..", self.APPLY_SCRIPT_SRC, self.APPLY_SCRIPT_NAME
        )
        if not os.path.exists(apply_script_path):
            raise FileNotFoundError(
                f"Apply script '{apply_script_path}' does not exist."
            )
        shutil.copy(apply_script_path, self.working_dir)

        # Set up the git user then commit
        subprocess.run(
            ["git", "-C", self.repo_dir, "config", "user.name", "Commitfest Bot"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", self.repo_dir, "config", "user.email", "cfbot@cputube.org"],
            check=True,
        )

        subprocess.call(
            [
                "git",
                "-C",
                self.repo_dir,
                "branch",
                "--quiet",
                "-D",
                f"cf/{branch.patch.id}",
            ]
        )
        subprocess.run(
            [
                "git",
                "-C",
                self.repo_dir,
                "checkout",
                "--quiet",
                "-b",
                f"cf/{branch.patch.id}",
            ],
            check=True,
        )

    def download_and_save(self, download_task, attachment):
        """
        Retrieve the contents at url_path and write them to a file in the working directory.
        """
        try:
            url_path = (
                self.BASE_FILE_URL
                + str(attachment["attachmentid"])
                + "/"
                + attachment["filename"]
            )
            file_path = os.path.join(self.working_dir, attachment["filename"])
            response = requests.get(url_path, stream=True)
            attachment["download_result"] = "Failed"
            response.raise_for_status()  # Raise an error for bad HTTP responses
            attachment["download_result"] = "Success"
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            CfbotTaskArtifact.objects.create(
                task=download_task,
                name=attachment["filename"],
                path=file_path,
                size=os.path.getsize(file_path),
                body=None,
                payload=attachment,
            )
            return True
        except Exception as e:
            print(f"Error downloading or saving file {attachment['filename']}: {e}")
            return False

    # XXX: handles/assumes .diff files only
    # For compressed files we can branch here to perform decompressions
    # and create new tasks for the contained files.
    def perform_apply(self, filename, payload):
        """
        Apply the patch file after ensuring it exists in the working directory.
        """
        file_path = os.path.join(self.working_dir, filename)
        if not os.path.exists(file_path):
            print(f"Error: File {file_path} does not exist in the working directory.")
            return False

        # Run the apply script with the filename as an argument
        try:
            result = subprocess.run(
                ["./" + self.APPLY_SCRIPT_NAME, filename, self.repo_dir],
                cwd=self.working_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            payload["apply_result"] = "Success"
            payload["stdout"] = result.stdout
            payload["stderr"] = result.stderr
            return True
        except subprocess.CalledProcessError as e:
            payload["apply_result"] = "Failure"
            payload["stdout"] = e.stdout
            payload["stderr"] = e.stderr
            return False

    def convert_to_merge_commit(self, branch):
        """
        Convert the branch to a merge commit.
        """
        msg_file = os.path.join(self.working_dir, "merge_commit_msg.txt")
        commit_id = self.get_head_commit_sha(branch)

        # Write a message to the msg_file
        with open(msg_file, "w") as f:
            f.write(f"Merge branch '{branch.branch_name}' into master\n\n")
            f.write(f"Patch ID: {branch.patch_id}\n")
            f.write(f"Branch ID: {branch.branch_id}\n")
            f.write(f"Commit ID: {commit_id}\n")

        reset_cmd = [
            "git",
            "-C",
            self.repo_dir,
            "reset",
            "origin/master",
            "--hard",
            "--quiet",
        ]
        merge_cmd = [
            "git",
            "-C",
            self.repo_dir,
            "merge",
            "--no-ff",
            "--quiet",
            "-F",
            msg_file,
            commit_id,
        ]

        try:
            subprocess.run(reset_cmd, check=True)
            subprocess.run(merge_cmd, check=True)
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to convert to merge commit: {e.stderr.strip()}")

        return True

    def get_delay(self, branch):
        return None

    def get_patch_count(self, branch):
        # In particular since an input file can be an archive of patches
        # we need to count the number of patches found in the directory
        # though possible this can be confirmed/gotten in other ways.
        # but this is consistent with context introspection other values get.
        """
        Count the number of files in the working directory with .diff or .patch extensions.
        """
        import os

        return sum(
            1
            for file in os.listdir(self.working_dir)
            if file.endswith((".diff", ".patch"))
        )

    def get_head_commit_sha(self, branch):
        """
        Simulate retrieving the merge commit SHA after a successful compilation.
        """
        try:
            result = subprocess.run(
                ["git", "-C", self.repo_dir, "rev-parse", "HEAD"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to retrieve base commit SHA: {e.stderr.strip()}")

    def get_base_commit_sha(self, branch):
        """
        Retrieve the base commit SHA from the template directory.
        """
        try:
            result = subprocess.run(
                ["git", "-C", self.repo_dir, "rev-parse", "origin/master"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise Exception(f"Failed to retrieve base commit SHA: {e.stderr.strip()}")


class LocalPatchCompiler(branchmanager_template.BranchManager.PatchCompilerTemplate):
    def __init__(self, working_dir, repo_dir):
        super().__init__()
        self.working_dir = working_dir
        self.repo_dir = repo_dir

    def do_configure_sync(self, branch):
        prefix_dir = os.path.join(self.working_dir, "install")
        configure_result = subprocess.run(
            ["meson", "setup", "build", f"--prefix={prefix_dir}"],
            cwd=self.repo_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return configure_result

    def do_compile_async(self, branch, compile_task, signal_done):
        build_dir = os.path.join(self.repo_dir, "build")
        ninja_result = subprocess.run(
            ["ninja"],
            cwd=build_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        signal_done(branch, compile_task, ninja_result)
        return

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
        return 60


class LocalPatchTester(branchmanager_template.BranchManager.PatchTesterTemplate):
    def __init__(self, working_dir, repo_dir):
        super().__init__()
        self.working_dir = working_dir
        self.repo_dir = repo_dir

    def do_test_async(self, branch, test_task, signal_done):
        build_dir = os.path.join(self.repo_dir, "build")
        test_result = subprocess.run(
            ["meson", "test"],
            cwd=build_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        signal_done(branch, test_task, test_result)
        return

    def get_delay(self, branch):
        return 60


def getLocalPatchApplier(branch):
    path_base = settings.LOCAL_PATCH_BURNER_DIR
    return LocalPatchApplier(
        path_base,
        str(branch.branch_id),
        os.path.join(path_base, "template", "postgres"),
        os.path.join(path_base, str(branch.branch_id), "work"),
        os.path.join(path_base, str(branch.branch_id), "postgres"),
    )


def getLocalPatchCompiler(branch):
    path_base = settings.LOCAL_PATCH_BURNER_DIR
    return LocalPatchCompiler(
        os.path.join(path_base, str(branch.branch_id), "work"),
        os.path.join(path_base, str(branch.branch_id), "postgres"),
    )


def getLocalPatchTester(branch):
    path_base = settings.LOCAL_PATCH_BURNER_DIR
    return LocalPatchTester(
        os.path.join(path_base, str(branch.branch_id), "work"),
        os.path.join(path_base, str(branch.branch_id), "postgres"),
    )


def getNotifier():
    return branchmanager_template.BranchManager.Notifier()
