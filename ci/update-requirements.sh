#!/bin/bash
#
# Update requirements-dev.txt to synchronize with "uv.lock".

if [ ! -f "./ci/requirements-dev.txt" ]; then
    echo "Wrong directory."
    exit 1
fi

uv export --quiet --frozen --no-annotate --format requirements-txt --only-dev --output-file ./ci/requirements-dev.txt
uv export --quiet --frozen --no-annotate --format requirements-txt --only-group uvloop --output-file ./ci/requirements-uvloop.txt

exit 0
