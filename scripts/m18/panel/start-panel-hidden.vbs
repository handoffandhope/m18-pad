' Runs start-panel.cmd beside this file with no window at all (for a Startup-folder shortcut).
Set fso = CreateObject("Scripting.FileSystemObject")
CreateObject("WScript.Shell").Run """" & fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "start-panel.cmd") & """", 0, False
