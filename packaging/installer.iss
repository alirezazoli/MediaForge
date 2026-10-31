; Inno Setup script for MediaForge Studio. Built by packaging/build.ps1, which passes the
; version and the build folders with /D, so nothing here has to be edited per release.
;
; Installs per user by default (no administrator prompt); "Install for all users" is
; offered on the first page. User data in %APPDATA%\MediaForge and the projects folder
; are never touched by the uninstaller.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef DistDir
  #error DistDir must be passed by build.ps1
#endif
#ifndef OutputDir
  #error OutputDir must be passed by build.ps1
#endif

#define AppName "MediaForge Studio"
#define AppExe "MediaForge.exe"

[Setup]
AppId={{8F4C2B1E-6A3D-4E5F-9B7C-2D1E0F3A4B5C}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=MediaForge
AppPublisherURL=https://github.com/alirezazoli/MediaForge
AppSupportURL=https://github.com/alirezazoli/MediaForge/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=MediaForge-Studio-{#AppVersion}-Setup
SetupIconFile=..\mediaforge\assets\mediaforge.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The PyInstaller folder, with ffmpeg and the licence texts already copied in.
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; The AppUserModelID matches runtime.APP_ID, so a pinned shortcut and the running window
; share one taskbar button.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "MediaForge.Studio"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon; AppUserModelID: "MediaForge.Studio"

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
