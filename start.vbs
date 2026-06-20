' SSH Key Manager - 无终端窗口启动脚本
' 双击此文件即可启动，不会弹出任何终端窗口

Set objShell = CreateObject("WScript.Shell")
strBaseDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' 优先使用 venv313 的 pythonw.exe（Python 3.13 + tkinter）
strPython = strBaseDir & "\venv313\Scripts\pythonw.exe"

' 回退到旧 venv
Set objFSO = CreateObject("Scripting.FileSystemObject")
If Not objFSO.FileExists(strPython) Then
    strPython = strBaseDir & "\venv\Scripts\pythonw.exe"
End If

' 回退到打包后的 exe
If Not objFSO.FileExists(strPython) Then
    strExe = strBaseDir & "\dist\SSHKeyManager\SSHKeyManager.exe"
    If objFSO.FileExists(strExe) Then
        objShell.Run Chr(34) & strExe & Chr(34), 0, False
        WScript.Quit
    End If
End If

' 回退到系统 pythonw
If Not objFSO.FileExists(strPython) Then
    strPython = "pythonw.exe"
End If

' 启动 main.py（无窗口）
objShell.Run Chr(34) & strPython & Chr(34) & " " & Chr(34) & strBaseDir & "\main.py" & Chr(34), 0, False
