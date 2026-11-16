#!/bin/sh
# Build-time only (Containerfile.yougori's OS stages): keeps what the Debian packages installed in
# this stage say of their licences before the stage removes /usr/share/doc. Two files a stage, as
# the VM's image has a bound on its number of entries:
#   /usr/share/licenses/debian/packages.tsv            every installed package: name, version, source
#                                                      package and its version (the source is at
#                                                      https://snapshot.debian.org/package/SOURCE/VERSION/)
#   /usr/share/licenses/debian/copyright-STAGE.txt.gz  the copyright file of every package this stage
#                                                      installed, one after another
# A stage before this one removed its own packages' files from /usr/share/doc, so each stage's record
# holds the packages it added, and together they hold every package of the image. A package this
# stage added without a copyright file stops the build. Usage: sh THIS STAGE
set -eu
stage=$1
out=/usr/share/licenses/debian
mkdir -p "$out"
before=$(mktemp)
if [ -f "$out/packages.tsv" ]; then cut -f1 "$out/packages.tsv" > "$before"; fi
dpkg-query -W -f '${Package}\t${Version}\t${source:Package}\t${source:Version}\n' | sort > "$out/packages.tsv"
records=$(mktemp)
for package in $(cut -f1 "$out/packages.tsv" | sort -u); do
  file=/usr/share/doc/$package/copyright
  if [ -f "$file" ]; then
    printf '==================== %s\n' "$package" >> "$records"
    cat "$file" >> "$records"
    printf '\n' >> "$records"
  elif grep -qx "$package" "$before"; then
    : # An earlier stage's package: its record is that stage's.
  elif [ -L "/usr/share/doc/$package" ]; then
    # Debian links a package's documentation to its source sibling's: that record stands for it.
    printf '==================== %s\nSee the record of %s (its documentation folder links there).\n\n' \
      "$package" "$(basename "$(readlink "/usr/share/doc/$package")")" >> "$records"
  else
    echo "No copyright file for $package, which the $stage stage installed." >&2
    exit 1
  fi
done
gzip -9n < "$records" > "$out/copyright-$stage.txt.gz"
rm -f "$records" "$before"
