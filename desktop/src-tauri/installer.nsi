!include "MUI2.nsh"
Name "Vibe-Trading"
OutFile "target\release\bundle\nsis\Vibe-Trading_0.1.10_x64-setup.exe"
InstallDir "$PROGRAMFILES64\Vibe-Trading"
RequestExecutionLevel admin

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File "target\release\vibe-trading-desktop.exe"
  CreateShortCut "$DESKTOP\Vibe-Trading.lnk" "$INSTDIR\vibe-trading-desktop.exe"
  CreateDirectory "$SMPROGRAMS\Vibe-Trading"
  CreateShortCut "$SMPROGRAMS\Vibe-Trading\Vibe-Trading.lnk" "$INSTDIR\vibe-trading-desktop.exe"
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
  Delete "$INSTDIR\vibe-trading-desktop.exe"
  Delete "$INSTDIR\uninstall.exe"
  Delete "$DESKTOP\Vibe-Trading.lnk"
  RMDir /r "$SMPROGRAMS\Vibe-Trading"
  RMDir "$INSTDIR"
SectionEnd
