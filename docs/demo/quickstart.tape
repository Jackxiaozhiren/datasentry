# DataSentry quickstart demo — vhs tape
# https://github.com/charmbracelet/vhs
#
# Prereqs:
#   1. ./docs/demo/record-demo.sh        (creates demo-data/orders.csv)
#   2. pip install datasentry-ai         (or source .venv/bin/activate in the repo)
#   3. cd demo-data && vhs quickstart.tape
#
# The tape runs the real CLI; the story:
#   scan -> issues with evidence -> repair proposal -> apply (fingerprinted + rollback).
Output ../docs/demo/quickstart.gif

Set Shell bash
Set FontSize 18
Set Width 96
Set Height 26
Set Framerate 60
Set PlaybackSpeed 1.6
Set Padding 16
Set Margin 2

# ---- step 1: scan a dirty CSV -------------------------------------------
Type "datasentry scan orders.csv"
Sleep 300ms
Enter
Sleep 4s

# ---- step 2: what did it find? ------------------------------------------
Type "datasentry issues list --severity high"
Sleep 300ms
Enter
Sleep 3s

# ---- step 3: propose a repair for the top issue --------------------------
Type "datasentry repair propose \$(datasentry issues list --severity high | sed -n '1p' | awk '{print \$1}') --file orders.csv"
Sleep 300ms
Enter
Sleep 4s

# ---- step 4: apply it (fingerprinted copy + rollback artifact) ----------
Type "datasentry repair apply \$(datasentry issues list --severity high | sed -n '1p' | awk '{print \$1}') --file orders.csv"
Sleep 300ms
Enter
Sleep 4s
