#!/bin/bash
#
# Produce the documentation using `pdoc`.

set -e

# Extract version number from shellous __init__.py file.
version=$(sed -E -n 's/^ *__version__ *= *"([0-9]+\.[0-9]+\.[0-9]+)"/\1/p' shellous/__init__.py)
if [ -z "$version" ]; then
    echo "Unable to determine version number for shellous."
    exit 1
fi

# Produce documentation.
pdoc --footer-text "Version $version" -t ci/custom-template -o html/ shellous

# Clean up the set() `shellous.command._UnsetEnum` declarations in the documentation to make them more readable.
# Remove the line of badges/shields at the top of the readme.
sed -i '' \
  -e 's#<span class="n">shellous</span><span class="o">\.</span><span class="n">command</span><span class="o">\.</span><span class="n">_UnsetEnum</span>#<span class="n">Unset</span>#g' \
  -e '\#https://img.shields.io/#d' \
    html/shellous.html

exit 0
