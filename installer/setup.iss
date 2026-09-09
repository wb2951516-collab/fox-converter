; Fox Converter 安装包脚本（Inno Setup 6）
; 用法: ISCC.exe installer\setup.iss

#define MyAppName "Fox Converter"
#define MyAppVersion "2.1.1"
#define MyAppPublisher "Fox Converter"
#define MyAppExeName "FoxConverter.exe"

[Setup]
; 固定 GUID：同 AppId 覆盖安装/升级时识别为同一程序
AppId={{8E4B6C2A-9D31-4F57-B7E0-5A2C8F13D901}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; 每用户安装，无需管理员权限
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=build
OutputBaseFilename=FoxConverter_Setup_v{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
; 卸载时不清理用户数据（导出目录在 文档、配置在 %APPDATA%，均不在安装目录）

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "..\dist\FoxConverter.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 清理安装目录内可能生成的临时文件（用户数据不在安装目录，不受影响）
Type: filesandordirs; Name: "{app}"
