Output demo/demo.gif

Set Padding 40
Set Width 1620
Set Height 1080
Set FontSize 29

Set Margin 60
Set BorderRadius 14

Set WindowBar Rings
Set WindowBarSize 40

Set CursorBlink false
Set Framerate 60
Set PlaybackSpeed 1.0

Set FontFamily "JetBrains Mono"
Set TypingSpeed 25ms

Require git
Require python3

# -----------------------------------------------------------------------------
# SETUP (Hidden)
# -----------------------------------------------------------------------------
Hide
Type@1ms "python3 -m pip install -e /vhs --break-system-packages" Enter
Type@1ms "rm -rf demo_env && mkdir demo_env" Enter
Type@1ms "cd demo_env" Enter
Type@1ms "git init -q" Enter
Type@1ms "git config user.email 'demo@pulsar.dev'" Enter
Type@1ms "git config user.name 'Pulsar Demo'" Enter
Type@1ms "mkdir -p ../origin.git && git init --bare ../origin.git" Enter
Type@1ms "git remote add origin ../origin.git" Enter
Type@1ms "git pulsar" Enter
Type@1ms "cat <<EOF > data_pipeline.py" Enter
Type@1ms "def process_data(data):" Enter
Type@1ms "    # TODO: Optimize this O(n^2) mess" Enter
Type@1ms "    results = []" Enter
Type@1ms "    for i in data:" Enter
Type@1ms "        for j in data:" Enter
Type@1ms "            results.append(i * j)" Enter
Type@1ms "    return results" Enter
Type@1ms "EOF" Enter
Type@1ms "git add . && git commit -m 'feat: initial pipeline'" Enter
Type@1ms "git pulsar now" Enter
Type@1ms "cp /vhs/pipeline_v2.py data_pipeline.py" Enter
Type@1ms "clear" Enter
Sleep 2s

Show

Type "# You've been optimizing this function for 45 minutes..." Enter
Sleep 1s

Type "cat data_pipeline.py" Enter
Sleep 2s
Type "clear" Enter

Type "# You haven't committed yet because it's still WIP." Enter
Sleep 1s
Type "git status" Enter
Sleep 2s

Type "# You're tired and try to clean up some temporary files..." Enter
Sleep 1s
Type "rm *.py" Enter
Sleep 0.5s
Type "ls" Enter
Sleep 1s
Type "# OOPS!" Enter
Sleep 0.3s
Type "# Did I just delete all my work??" Enter
Sleep 0.5s

Type "# 'git checkout .' won't save you." Enter
Sleep 0.5s
Type "# But git pulsar can." Enter
Sleep 1.5s
Type "git pulsar restore data_pipeline.py" Enter
Sleep 2s

Type "cat data_pipeline.py" Enter
Sleep 0.5s
Type "# Phew"
Sleep 3s
