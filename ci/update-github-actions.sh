#!/bin/bash
#
# Update github actions from dependabot.
# (Compatible with bash 3.2.)

IFS=$'\n' read -r -d '' -a branches < <(git branch -r --list "origin/dependabot/github_actions/*" | sed 's~[ ]*origin/~~')
git pull --no-rebase origin "${branches[@]}"
exit 0
