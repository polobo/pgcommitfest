#!/bin/sh
set  -e

date

# sitting in working directory containing patches right now
echo "Applying patch $1 on $2 in $(pwd)"
git --version

inputfile="$1"
workdir="$(pwd)"
gitrepo="$2"
msgfile="$inputfile".msg
difffile="$inputfile".delta
infofile="$inputfile".info

if command -v gpatch >/dev/null 2>&1; then
	# gpatch is a GNU patch that is compatible with BSD patch, but has some
	# extra features. We use it if available. This is mostly for FreeBSD to
	# behave like Linux.
	PATCH_CMD=gpatch
else
	PATCH_CMD=patch
fi

git mailinfo "$msgfile" "$difffile" <"$inputfile" >"$infofile"

NAME=$(sed -n -e 's/^Author: //p' "$infofile")
EMAIL=$(sed -n -e 's/^Email: //p' "$infofile")
SUBJECT=$(sed -n -e 's/^Subject: //p' "$infofile")
DATE=$(sed -n -e 's/^Date: //p' "$infofile")
MESSAGE="$(cat "$msgfile")"
	MESSAGE="${SUBJECT:-"[PATCH]: $inputfile"}${MESSAGE:+

}${MESSAGE}"

inputfile="$workdir/$inputfile"

set -x


# XXX: can we assume if the .msg file is empty this will fail?
if [ ! -s "$msgfile" ]; then
    echo "Message file is empty. Skipping 'git am'."
else
    echo "=== using 'git am' to apply patch $inputfile ==="
    # git am usually does a decent job at applying a patch, as long as the
    # patch was createdwith git format-patch. It also automatically creates a
    # git commit, so we don't need to do that manually and can just continue
    # with the next patch if it succeeds.
    git -C "$gitrepo" am --3way "$inputfile" && exit 0
    # Okay it failed, let's clean up and try the next option.
    git -C "$gitrepo" reset HEAD .
    git -C "$gitrepo" checkout -- .
    git -C "$gitrepo" clean -fdx
fi

echo "=== using $PATCH_CMD to apply $inputfile ==="
if ! $PATCH_CMD -p1 --no-backup-if-mismatch -V none -f -N <"$inputfilef" && git -C "$gitrepo" add .; then
    git -C "$gitrepo" reset HEAD .
    git -C "$gitrepo" checkout -- .
    git -C "$gitrepo" clean -fdx
    # We use git apply as a last option, because it provides the best
    # output for conflicts. It also works well for patches that were
    # already applied.
    echo "=== using 'git apply' to apply patch $f ==="
    # --allow-empty (minimum version requirements...)
    git -C "$gitrepo" apply --3way "$inputfile" || { git -C "$gitrepo" diff && exit 1; }
fi

# Linked to apparently missing --allow-empty option in git apply...
# if git -C "$gitrepo" diff --cached; then
#     # No need to clutter the GitHub commit history  with commits that don't
#     # change anything, usually this happens if a subset of the patchset has
#     # already been applied.
#     echo "=== Patch was already applied, skipping commit ==="
#     exit 0
# fi

# set up the git user then commit
git -C "$gitrepo" config user.name "Commitfest Bot"
git -C "$gitrepo" config user.email "cfbot@cputube.org"
git -C "$gitrepo" commit -m "$MESSAGE" --author="${NAME:-Commitfest Bot} <${EMAIL:-cfbot@cputube.org}>" --date="${DATE:-now}"

