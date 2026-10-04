Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = folder

SYNC_MINUTES = 60

Set wmi = GetObject("winmgmts:\\.\root\cimv2")
appQuery = "SELECT * FROM Win32_Process WHERE Name='python.exe' AND CommandLine LIKE '%app.py%'"

' Already running? Just open the browser.
If wmi.ExecQuery(appQuery).Count > 0 Then
    sh.Run "http://127.0.0.1:8501"
    WScript.Quit
End If

Set env = sh.Environment("Process")
env("USE_MYSQL") = "1"
env("FLASK_DEBUG") = "0"
env("DB_HOST") = "127.0.0.1"
env("DB_PORT") = "3306"
env("DB_USER") = "root"
env("DB_NAME") = "prediction_system"

' Start MySQL if it is not running
If wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='mysqld.exe'").Count = 0 Then
    sh.Run """C:\xampp\mysql\bin\mysqld.exe"" --defaults-file=""C:\xampp\mysql\bin\my.ini"" --standalone", 0, False
    WScript.Sleep 10000
End If

' Start the app, then open the browser
sh.Run "cmd /c ""venv\Scripts\python.exe app.py >> app.log 2>&1""", 0, False
WScript.Sleep 6000
sh.Run "http://127.0.0.1:8501"

' Background sync: first after 2 minutes, then every SYNC_MINUTES
WScript.Sleep 120000
Do
    If wmi.ExecQuery(appQuery).Count = 0 Then Exit Do
    sh.Run "cmd /c sync.bat", 0, True
    WScript.Sleep CLng(SYNC_MINUTES) * 60000
Loop