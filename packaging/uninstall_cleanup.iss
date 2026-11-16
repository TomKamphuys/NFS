; Delete only app-owned state. Saved measurement projects and shared library
; settings (for example .matplotlib and HALS\Studio) must remain untouched.
; Override these roots only in the isolated uninstall verification fixture.
#ifndef CleanupLocalRoot
  #define CleanupLocalRoot "{localappdata}"
#endif
#ifndef CleanupRoamingRoot
  #define CleanupRoamingRoot "{userappdata}"
#endif
#ifndef CleanupHomeRoot
  #define CleanupHomeRoot "{%USERPROFILE}"
#endif
#ifndef CleanupTempRoot
  #define CleanupTempRoot "{%TEMP|{localappdata}\Temp}"
#endif
#ifndef CleanupTmpRoot
  #define CleanupTmpRoot "{%TMP|{localappdata}\Temp}"
#endif

[UninstallDelete]
Type: filesandordirs; Name: "{#CleanupLocalRoot}\HALS Near Field Scanner"
Type: filesandordirs; Name: "{#CleanupRoamingRoot}\HALS Near Field Scanner"
Type: filesandordirs; Name: "{#CleanupHomeRoot}\.hals-near-field-scanner"
; Legacy builds used the system temp directory for the working project.
Type: filesandordirs; Name: "{#CleanupTempRoot}\HALS_working_project"
Type: filesandordirs; Name: "{#CleanupTmpRoot}\HALS_working_project"
; Also cover normal Windows temp when uninstall is launched by a process
; that inherited the frozen app's private TEMP environment.
Type: filesandordirs; Name: "{#CleanupLocalRoot}\Temp\HALS_working_project"
