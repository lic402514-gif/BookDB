; BookDB Windows 安装包（Inno Setup 6）
;
;     iscc packaging\installer.iss
;   或  bash packaging/make_installer.sh     ← 推荐（会先校验产物、再编译）
;
; 设计目标：**面向完全没有开发环境的普通 Windows 用户**
;   · 不需要 Python / uv / pip / Git / 源码
;   · 用户级安装（%LOCALAPPDATA%\Programs\BookDB），**不要求管理员权限**
;   · WebView2 缺失时自动经微软官方 bootstrapper 联网安装（用户无需理解）
;   · 程序与用户数据彻底分离；**卸载绝不删除用户的 Library**
;
; 数据边界（务必遵守）：
;   程序    {app}                              安装包创建、卸载包删除
;   设置    {userappdata}\BookDB\settings.json 只存"最近 Library 路径"等偏好，默认保留
;   数据   用户自选的 Library 目录              安装包**从不创建、从不删除**

#define AppName        "BookDB"
#define AppVersion     "0.1.0"
#define AppPublisher   "lic402514-gif"
#define AppURL         "https://github.com/lic402514-gif/BookDB"
#define AppExeName     "BookDB.exe"
;: dist/BookDB 的内容（由 packaging/build.sh 生成）
#define DistDir        "..\dist\BookDB"

[Setup]
AppId={{8F3A9C14-7B2E-4D51-9A6C-1E5D8B0F3A72}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}

; ── 用户级安装：不弹 UAC，装到当前用户目录
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes

; ── 产物
OutputDir=..\dist
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
SetupIconFile=..\packaging\assets\bookdb.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableDirPage=no
; 卸载时**只**做我们列举的事，不做隐式清理
Uninstallable=yes

[Languages]
; 正式版可以再加中文语言包；这里先用官方英文 + 明确的中文说明，避免依赖额外文件
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; 桌面快捷方式：默认勾选，但用户可以取消
Name: "desktopicon"; Description: "创建桌面快捷方式"; \
    GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
; 程序本体（PyInstaller onedir 产物；子目录用 recursesubdirs 保留结构）
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 包内使用说明
Source: "{#DistDir}\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
; WebView2 官方 bootstrapper（临时释放到 {tmp}，装完即在 [Run] 中运行并清理）
Source: "bootstrapper\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; \
    Flags: deleteafterinstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; ① WebView2：**检测到才装**。这是 GUI 的运行依赖，用户无需理解。
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; \
    Parameters: "/silent /install"; \
    StatusMsg: "正在安装 Microsoft Edge WebView2 运行环境…"; \
    Check: NeedsWebView2; Flags: waituntilterminated runhidden

; ② 装完可选立即启动
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; ⚠️ 只删**确知属于程序**的路径；**绝不**递归删除 {app} 的上级目录
; （用户可能把 Library 建在那里）。
;
; 说明：{app} 会由 Inno 自动移除，但 PyInstaller 的 `_internal` 里含
; 大量 .pyc/运行时文件，卸载时可能因清理顺序残留空目录或 __pycache__。
; 这里显式声明，确保卸载后不留残余。
; **注意不要动** {userappdata}\BookDB —— 那是用户的应用设置，默认保留。
Type: filesandordirs; Name: "{app}\_internal"

[UninstallRun]
; 故意为空 —— 卸载**不动**任何用户数据（既不删 Library，也默认不删 settings）

[Code]
const
  // WebView2 运行时注册表位置（机器级 / 用户级都要查）
  WV2_MACHINE = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WV2_USER    = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

/// 系统是否已安装 WebView2 运行时（有版本号即视为已装）
function WebView2Installed: Boolean;
var
  ver: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, WV2_MACHINE, 'pv', ver) and (ver <> '') and (ver <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, WV2_USER,    'pv', ver) and (ver <> '') and (ver <> '0.0.0.0'));
end;

/// [Run] 的 Check：**缺失才安装**
function NeedsWebView2: Boolean;
begin
  Result := not WebView2Installed;
end;

/// 安装前检查：目标目录里若混进了书库，明确提示（**只提示，不扫描、不删除**）
function NextButtonClick(CurPageID: Integer): Boolean;
var
  msg: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    if FileExists(ExpandConstant('{app}\book.toml')) or
       FileExists(ExpandConstant('{app}\book.db')) then
    begin
      msg := '所选目录中已存在书库文件（book.toml / book.db）。' + #13#10 + #13#10 +
             'BookDB 建议把**程序**和**书库**分开放置，例如：' + #13#10 +
             '  程序：' + ExpandConstant('{app}') + #13#10 +
             '  书库：D:\Books' + #13#10 + #13#10 +
             '继续安装不会删除这些文件，但仍建议另选目录。要继续吗？';
      if MsgBox(msg, mbConfirmation, MB_YESNO) = IDNO then
        Result := False;
    end;
  end;
end;

/// 安装结束后：若 WebView2 仍不可用（离线/安装失败），给出**明确、可操作**的提示
procedure CurStepChanged(CurStep: TSetupStep);
var
  msg: String;
begin
  if (CurStep = ssPostInstall) and (not WebView2Installed) then
  begin
    msg := '未能自动安装 Microsoft Edge WebView2 运行环境。' + #13#10 + #13#10 +
           'BookDB 的界面需要它才能显示。可能是当前网络无法访问微软服务器。' + #13#10 + #13#10 +
           '解决办法（任一）：' + #13#10 +
           '  1) 联网后重新运行本安装包（会自动重试）；' + #13#10 +
           '  2) 手动下载安装："WebView2 Runtime Evergreen Standalone Installer"' + #13#10 +
           '     地址：https://developer.microsoft.com/microsoft-edge/webview2/' + #13#10 + #13#10 +
           'BookDB 本身已安装完成，装好运行环境后即可正常使用。';
    MsgBox(msg, mbInformation, MB_OK);
  end;
end;

/// 卸载时：只做**提示**，绝不代用户删除数据
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  msg: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    msg := 'BookDB 已卸载。' + #13#10 + #13#10 +
           '你的书库数据未被删除 —— 它们存放在你自己选择的位置（程序不会记录其位置，也不会访问）。' + #13#10 + #13#10 +
           '如需彻底清理，请手动删除：' + #13#10 +
           '  · 书库目录（含 book.toml / book.db / 电子书）' + #13#10 +
           '  · 应用设置：' + ExpandConstant('{userappdata}\BookDB') + #13#10 + #13#10 +
           '（应用设置被保留，是为了重装 BookDB 后能记住你最近打开的书库。）';
    MsgBox(msg, mbInformation, MB_OK);
  end;
end;
