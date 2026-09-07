@echo off
setlocal
if not defined OUTLINE_HOME set "OUTLINE_HOME=%USERPROFILE%\.claude\skills\wiki"
py "%OUTLINE_HOME%\outline.py" %*
