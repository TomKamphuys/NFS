; Inno Setup script for the HALS Near Field Scanner.
;
; This wraps the PyInstaller output (dist\HALS Near Field Scanner) into a
; friendly Windows installer so that non-developer users can install the
; application without dealing with Python, uv, or virtual environments.
;
; Build locally (after running PyInstaller) with:
;     iscc packaging\installer.iss
;
; The version can be overridden from the command line, which is what the
; GitHub Actions workflow does:
;     iscc /DMyAppVersion=1.2.3 packaging\installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "HALS Near Field Scanner"
#define MyAppPublisher "HALS"
#define MyAppExeName "HALS Near Field Scanner.exe"
#define MyAppSourceDir "..\dist\HALS Near Field Scanner"

[Setup]
AppId={{9E4B2C1A-7D3E-4F5A-9B8C-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Install into Program Files by default; allow the installer to run without
; admin rights by falling back to a per-user install when necessary.
PrivilegesRequiredOverridesAllowed=dialog commandline
OutputDir=..\dist\installer
OutputBaseFilename=HALS-Near-Field-Scanner-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\images\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
LicenseFile=..\LICENSE

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
