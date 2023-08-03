#!/bin/bash
#
# Update requirements-dev.txt to synchronize with "poetry.lock".

if [ ! -f "./ci/requirements-dev.txt" ]; then
    echo "Wrong directory."
    exit 1
fi

HEADER="# $(poetry --version) export at $(date)"

echo "$HEADER" > ./ci/requirements-dev.txt
poetry export --with dev >> ./ci/requirements-dev.txt

exit 0
