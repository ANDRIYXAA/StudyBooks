#!/bin/bash
# Downloads the open (CC BY / CC BY-NC-SA) reference textbooks and indexes them.
set -u
R=~/studybooks/data/refs-open
get() { mkdir -p "$R/$1"; [ -s "$R/$1/$2.pdf" ] || curl -sfL -A "Mozilla/5.0" -o "$R/$1/$2.pdf" "$3" || echo "FAILED $3"; }
O=https://assets.openstax.org/oscms-prodcms/media/documents
get math1 openstax-calculus-volume-1 $O/calculus-volume-1_-_WEB.pdf
get math1 openstax-calculus-volume-2 $O/calculus-volume-2_-_WEB.pdf
get math1 openstax-calculus-volume-3 $O/calculus-volume-3_-_WEB.pdf
get math1 openstax-precalculus-2e $O/precalculus-2e_-_WEB.pdf
get statics engineering-statics-baker-haynes https://engineeringstatics.org/pdf/statics.pdf
get statics openstax-university-physics-volume-1 $O/university-physics-volume-1_-_WEB.pdf
cd ~/studybooks && .venv/bin/python sb.py reindex-refs
