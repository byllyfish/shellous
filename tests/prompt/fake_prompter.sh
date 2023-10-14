#!/usr/bin/env bash
#
# This bash script is used to test the send/expect features
# of the `shellous.Prompt` class.

set -eu

CORRECT_PASSWORD="abc123"

# If the first argument is "--check", ask whether you really want
# to continue.

check="${1:-}"

if [ "$check" = "--check" ] && [ $((RANDOM % 2)) -eq 0 ]; then
    read -p "Do you want to continue? [Yn] " -r _ignore
fi

read -p "Name: " -r name

while true; do
    read -p "Password: " -r -s password
    if [ "$password" = "$CORRECT_PASSWORD" ]; then
        break
    fi
    printf "\nWrong password.\n"
done

printf "\n\nWelcome %s.\n" "$name"

while true; do
    read -p "prompt> " -r command || break
    if [ "$command" = "quit" ]; then
        break
    fi
    echo "You typed: $command"
done

exit 0
